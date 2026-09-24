"""The run_sql tool: lets the model query the GA4 dataset through the guarded BigQueryRunner."""

import json
from typing import Any

from anthropic.types.beta import BetaToolParam

from app.bigquery import BigQueryRunner, BigQueryUnavailable, QueryRejected, QueryResult
from app.tools.base import ToolContext, ToolError, ToolOutcome, ToolUnavailable

NAME = "run_sql"

# Tool inputs are short SQL strings, so eager input streaming would buy little latency,
# and without it the API validates tool inputs against this schema before we see them.
DEFINITION: BetaToolParam = {
    "name": NAME,
    "description": (
        "Run one read-only BigQuery Standard SQL SELECT statement against the GA4 ecommerce "
        "sample and return the result rows as JSON. Queries are dry-run first: non-SELECT "
        "statements and queries that would scan too many bytes are rejected with an explanation. "
        "Only the first rows are returned; 'truncated' tells you if there were more. "
        "The result's 'result_id' identifies it, e.g. for create_chart."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A single SELECT statement (CTEs allowed). "
                "Always filter on _TABLE_SUFFIX.",
            },
            "purpose": {
                "type": "string",
                "description": "One short sentence describing what this query checks; "
                "shown to the user.",
            },
        },
        "required": ["query", "purpose"],
        "additionalProperties": False,
    },
}


class RunSqlTool:
    name = NAME
    definition = DEFINITION

    def __init__(self, runner: BigQueryRunner):
        self._runner = runner

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolOutcome:
        query = tool_input.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolError("'query' must be a non-empty SQL string.")
        try:
            result = self._runner.run(query)
        except QueryRejected as exc:
            raise ToolError(str(exc)) from exc
        except BigQueryUnavailable as exc:
            raise ToolUnavailable(str(exc)) from exc

        content = json.dumps(result_json(result, context.tool_use_id))
        return ToolOutcome(content=content, output=result)


def result_json(result: QueryResult, result_id: str) -> dict[str, Any]:
    """A query result as the model sees it, as it is stored, and as the UI receives it."""
    return {
        "result_id": result_id,
        "columns": result.columns,
        "rows": result.rows,
        "rows_returned": len(result.rows),
        "total_rows": result.total_rows,
        "truncated": result.truncated,
        "mb_scanned": result.mb_scanned,
    }


def parse_result(content: str) -> dict[str, Any] | None:
    """A stored run_sql result (the result_json() shape), or None if the content isn't one."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or "columns" not in data or "rows" not in data:
        return None
    return data
