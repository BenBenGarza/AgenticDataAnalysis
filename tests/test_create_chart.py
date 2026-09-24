import json

import pytest

from app.tools.base import ToolContext, ToolError
from app.tools.create_chart import CreateChartTool, Series, build_chart

MONTHLY = [
    {"month": "2020-11", "revenue": 144260.0, "orders": 2054},
    {"month": "2020-12", "revenue": 160555.0, "orders": 2434},
    {"month": "2021-01", "revenue": 57350.0, "orders": 1204},
]
BY_DEVICE = [
    {"month": "2020-11", "device": "desktop", "revenue": 80.0},
    {"month": "2020-11", "device": "mobile", "revenue": 60.0},
    {"month": "2020-12", "device": "desktop", "revenue": 90.0},
    {"month": "2020-12", "device": "mobile", "revenue": 70.0},
    {"month": "2021-01", "device": "desktop", "revenue": 30.0},  # no mobile row this month
]


def _results(**named_rows: list[dict]) -> dict[str, str]:
    """Stored run_sql results keyed by result_id, in the shape run_sql produces."""
    return {
        result_id: json.dumps({"result_id": result_id, "columns": list(rows[0]), "rows": rows})
        for result_id, rows in named_rows.items()
    }


def _spec(**overrides) -> dict:
    spec = {
        "query_id": "q1",
        "type": "line",
        "title": "Revenue by month",
        "x": "month",
        "y": ["revenue"],
    }
    return {**spec, **overrides}


def test_one_series_per_y_column_in_query_order():
    chart = build_chart(
        _spec(y=["revenue", "orders"], value_format="currency"), _results(q1=MONTHLY)
    )

    assert chart.labels == ["2020-11", "2020-12", "2021-01"]
    assert chart.series == [
        Series("revenue", [144260.0, 160555.0, 57350.0]),
        Series("orders", [2054.0, 2434.0, 1204.0]),
    ]
    assert (chart.type, chart.value_format, chart.y_label) == ("line", "currency", None)


def test_series_column_pivots_groups_and_leaves_missing_cells_empty():
    chart = build_chart(_spec(type="stacked_bar", series="device"), _results(q1=BY_DEVICE))

    assert chart.labels == ["2020-11", "2020-12", "2021-01"]
    assert chart.series == [
        Series("desktop", [80.0, 90.0, 30.0]),
        Series("mobile", [60.0, 70.0, None]),
    ]


def test_tool_reports_what_it_showed():
    context = ToolContext(tool_use_id="c1", results=_results(q1=MONTHLY))
    outcome = CreateChartTool().run(_spec(), context)
    assert outcome.content.startswith("Chart shown to the user: 'Revenue by month' (line, 3 points")
    assert outcome.output.title == "Revenue by month"


@pytest.mark.parametrize(
    "overrides, results, message",
    [
        ({"query_id": "nope"}, None, "No run_sql result has result_id 'nope'"),
        ({"type": "pie"}, None, "'type' must be one of"),
        ({"value_format": "bytes"}, None, "'value_format' must be one of"),
        ({"title": " "}, None, "'title' must be a non-empty string"),
        ({"x": "day"}, None, "'x' column 'day' is not in the result"),
        ({"y": []}, None, "'y' must be a non-empty list"),
        ({"y": ["month"]}, None, "Column 'month' has non-numeric values"),
        ({"series": "month", "y": ["revenue", "orders"]}, None, "exactly one 'y' column"),
        ({}, {"q1": [{"month": "2020-11", "revenue": 1.0}]}, "at least 2 x values"),
        ({"x": "device", "y": ["revenue"]}, {"q1": BY_DEVICE}, "Values in 'device' repeat"),
        ({"series": "device"}, {"q1": BY_DEVICE + BY_DEVICE[:1]}, "More than one row for"),
    ],
)
def test_invalid_requests_explain_what_to_fix(overrides, results, message):
    stored = _results(**(results or {"q1": MONTHLY}))
    with pytest.raises(ToolError, match=message):
        build_chart(_spec(**overrides), stored)


def test_too_many_series_suggests_grouping_into_other():
    rows = [{"month": m, "product": f"p{i}", "revenue": 1.0} for m in ("a", "b") for i in range(9)]
    with pytest.raises(ToolError, match="9 series is too many.*'Other'"):
        build_chart(_spec(x="month", series="product"), _results(q1=rows))


def test_failed_query_results_are_not_chartable():
    # Only successful results are passed in; a result that isn't run_sql output is rejected.
    with pytest.raises(ToolError, match="No run_sql result"):
        build_chart(_spec(), {"q1": "Chart shown to the user"})
