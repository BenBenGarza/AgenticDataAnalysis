"""The create_chart tool: shows the user a chart of an earlier query result.

The model never supplies data points. It names a run_sql result and the columns to plot, and
the chart is built from that stored result, so a chart can only show what BigQuery returned.
The same build_chart() rebuilds charts from saved history when a conversation is reopened.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from anthropic.types.beta import BetaToolParam

from app.tools import run_sql
from app.tools.base import ToolContext, ToolError, ToolOutcome

NAME = "create_chart"
CHART_TYPES = ("line", "bar", "horizontal_bar", "stacked_bar")
VALUE_FORMATS = ("number", "currency", "percent")
MAX_SERIES = 8  # the UI's categorical palette has 8 validated colors; more are unreadable

DEFINITION: BetaToolParam = {
    "name": NAME,
    "description": (
        "Show the user a chart of an earlier run_sql result. Use it when a visual makes the "
        "answer clearer: a trend over time (line), a comparison across categories (bar, or "
        "horizontal_bar for long labels and rankings), or composition (stacked_bar). Don't "
        "chart a single number or a two-value comparison. The data comes from the stored query "
        "result, not from you: shape, aggregate and order the rows in SQL first, then pick the "
        "columns to plot."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query_id": {
                "type": "string",
                "description": "The result_id of an earlier run_sql result in this conversation.",
            },
            "type": {"type": "string", "enum": list(CHART_TYPES)},
            "title": {
                "type": "string",
                "description": "Short title saying what is shown, including the period.",
            },
            "x": {
                "type": "string",
                "description": "Column for the x-axis (dates or categories); rows are plotted "
                "in the order the query returned them.",
            },
            "y": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": MAX_SERIES,
                "description": "Numeric column(s) to plot, one series each.",
            },
            "series": {
                "type": "string",
                "description": "Optional column whose values split one y column into several "
                "series (for results with one row per x value and group).",
            },
            "value_format": {
                "type": "string",
                "enum": list(VALUE_FORMATS),
                "description": "How to format values. 'percent' expects fractions (0.25 = 25%).",
            },
            "y_label": {"type": "string", "description": "Optional y-axis label."},
        },
        "required": ["query_id", "type", "title", "x", "y"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class Series:
    name: str
    values: list[float | None]


@dataclass(frozen=True)
class Chart:
    type: str
    title: str
    labels: list[str]  # x-axis values, in query order
    series: list[Series]
    value_format: str
    y_label: str | None


class CreateChartTool:
    name = NAME
    definition = DEFINITION

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolOutcome:
        chart = build_chart(tool_input, context.results)
        content = (
            f"Chart shown to the user: {chart.title!r} ({chart.type}, {len(chart.labels)} "
            f"points, {len(chart.series)} series). Refer to it in your answer rather than "
            "repeating every value."
        )
        return ToolOutcome(content=content, output=chart)


def build_chart(spec: dict[str, Any], results: Mapping[str, str]) -> Chart:
    """Validate a chart request against the stored query result and build the chart.

    Raises ToolError with a message the model can act on.
    """
    query_id = spec.get("query_id")
    content = results.get(query_id) if isinstance(query_id, str) else None
    parsed = run_sql.parse_result(content) if content is not None else None
    if parsed is None:
        raise ToolError(
            f"No run_sql result has result_id {query_id!r}. Use the result_id of a query that "
            "already returned rows (charts can't use queries from the same response)."
        )
    columns, rows = parsed["columns"], parsed["rows"]

    chart_type = _one_of(spec.get("type"), CHART_TYPES, "type")
    value_format = _one_of(spec.get("value_format", "number"), VALUE_FORMATS, "value_format")
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ToolError("'title' must be a non-empty string.")
    x = _column(spec.get("x"), columns, "x")
    y_spec = spec.get("y")
    if not isinstance(y_spec, list) or not y_spec:
        raise ToolError("'y' must be a non-empty list of column names.")
    y = [_column(column, columns, "y") for column in y_spec]
    series_column = spec.get("series")

    if series_column is None:
        labels, series = _wide(rows, x, y)
    else:
        if len(y) != 1:
            raise ToolError("With 'series', give exactly one 'y' column.")
        labels, series = _long(rows, x, y[0], _column(series_column, columns, "series"))

    if len(labels) < 2:
        raise ToolError("A chart needs at least 2 x values; state a single value in the text.")
    if len(series) > MAX_SERIES:
        raise ToolError(
            f"{len(series)} series is too many to tell apart; keep the top {MAX_SERIES - 1} and "
            "group the rest as 'Other' in SQL, or chart fewer groups."
        )
    y_label = spec.get("y_label")
    return Chart(
        chart_type,
        title.strip(),
        labels,
        series,
        value_format,
        y_label if isinstance(y_label, str) and y_label.strip() else None,
    )


def _wide(rows: list[dict[str, Any]], x: str, y: list[str]) -> tuple[list[str], list[Series]]:
    """One row per x value; each y column is a series."""
    labels = [_label(row[x]) for row in rows]
    if len(set(labels)) != len(labels):
        raise ToolError(
            f"Values in '{x}' repeat. Aggregate to one row per '{x}' in SQL, or use 'series' "
            "to split the rows into groups."
        )
    return labels, [Series(column, [_number(row[column], column) for row in rows]) for column in y]


def _long(
    rows: list[dict[str, Any]], x: str, y: str, series_column: str
) -> tuple[list[str], list[Series]]:
    """One row per (x, group): pivot so each group becomes a series. Missing cells stay empty."""
    labels: dict[str, None] = {}  # insertion-ordered sets
    names: dict[str, None] = {}
    cells: dict[tuple[str, str], float | None] = {}
    for row in rows:
        label, name = _label(row[x]), _label(row[series_column])
        labels[label] = names[name] = None
        if (label, name) in cells:
            raise ToolError(f"More than one row for {x}={label!r}, {series_column}={name!r}.")
        cells[(label, name)] = _number(row[y], y)
    series = [Series(name, [cells.get((label, name)) for label in labels]) for name in names]
    return list(labels), series


def _one_of(value: Any, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ToolError(f"'{field}' must be one of: {', '.join(allowed)}.")
    return str(value)


def _column(name: Any, columns: list[str], field: str) -> str:
    if name not in columns:
        raise ToolError(f"'{field}' column {name!r} is not in the result: {', '.join(columns)}.")
    return str(name)


def _number(value: Any, column: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ToolError(f"Column {column!r} has non-numeric values (e.g. {value!r}).")
    return float(value)


def _label(value: Any) -> str:
    return "(null)" if value is None else str(value)
