import datetime
import decimal
import json
from types import SimpleNamespace
from typing import Any

import pytest
from google.api_core import exceptions as gcp_exceptions

from app.bigquery import BigQueryRunner, QueryRejected
from app.tools.base import ToolError
from app.tools.run_sql import RunSqlTool

GB = 1024**3


class FakeBigQueryClient:
    """Answers dry runs with a statement type and size, and real runs with scripted rows."""

    def __init__(
        self,
        statement_type: str = "SELECT",
        scanned_bytes: int = 10 * 1024**2,
        rows: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ):
        self._statement_type = statement_type
        self._bytes = scanned_bytes
        self._rows = rows if rows is not None else [{"n": 1}]
        self._error = error
        self.real_runs: list[Any] = []  # job configs of non-dry-run queries

    def query(self, sql: str, job_config: Any) -> SimpleNamespace:
        if self._error:
            raise self._error
        if not job_config.dry_run:
            self.real_runs.append(job_config)
        return SimpleNamespace(
            statement_type=self._statement_type,
            total_bytes_processed=self._bytes,
            result=self._result,
        )

    def _result(self, max_results: int) -> "FakeRowIterator":
        columns = list(self._rows[0]) if self._rows else []
        return FakeRowIterator(columns, self._rows[:max_results], total_rows=len(self._rows))


class FakeRowIterator:
    """Like BigQuery's RowIterator: a schema, the full row count, and the fetched rows."""

    def __init__(self, columns: list[str], rows: list[dict[str, Any]], total_rows: int):
        self.schema = [SimpleNamespace(name=c) for c in columns]
        self.total_rows = total_rows
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


def _runner(client: FakeBigQueryClient, max_rows: int = 200) -> BigQueryRunner:
    return BigQueryRunner(client, max_bytes_billed=2 * GB, max_rows=max_rows)


# ---- BigQueryRunner guardrails -----------------------------------------------------------


@pytest.mark.parametrize("statement_type", ["DELETE", "INSERT", "CREATE_TABLE", "SCRIPT"])
def test_only_select_statements_run(statement_type):
    client = FakeBigQueryClient(statement_type=statement_type)

    with pytest.raises(
        QueryRejected, match=f"Only SELECT queries are allowed \\(got {statement_type}\\)"
    ):
        _runner(client).run("...")
    assert client.real_runs == []


def test_queries_over_the_byte_limit_are_rejected_before_running():
    client = FakeBigQueryClient(scanned_bytes=3 * GB)

    with pytest.raises(QueryRejected, match="would scan 3.00 GB, over the 2.00 GB limit"):
        _runner(client).run("SELECT *")
    assert client.real_runs == []


def test_real_run_is_capped_by_maximum_bytes_billed():
    client = FakeBigQueryClient()
    _runner(client).run("SELECT 1")
    assert client.real_runs[0].maximum_bytes_billed == 2 * GB


def test_bigquery_errors_become_messages_the_model_can_act_on():
    error = gcp_exceptions.BadRequest(
        "400 bad", errors=[{"message": "Unrecognized name: evnt_name; Did you mean event_name?"}]
    )

    with pytest.raises(QueryRejected, match="Did you mean event_name"):
        _runner(FakeBigQueryClient(error=error)).run("SELECT evnt_name")


def test_results_are_truncated_and_converted_to_json_values():
    rows = [{"day": datetime.date(2020, 12, 1), "revenue": decimal.Decimal("12.50")}] * 5

    result = _runner(FakeBigQueryClient(rows=rows), max_rows=2).run("SELECT ...")

    assert result.columns == ["day", "revenue"]
    assert result.rows == [{"day": "2020-12-01", "revenue": 12.5}] * 2
    assert result.total_rows == 5 and result.truncated
    assert result.mb_scanned == 10.0


# ---- RunSqlTool --------------------------------------------------------------------------


def test_tool_returns_json_for_the_model_and_the_result_for_the_ui():
    outcome = RunSqlTool(_runner(FakeBigQueryClient(rows=[{"n": 1}]))).run(
        {"query": "SELECT 1 AS n", "purpose": "check"}
    )

    assert json.loads(outcome.content) == {
        "columns": ["n"],
        "rows": [{"n": 1}],
        "rows_returned": 1,
        "total_rows": 1,
        "truncated": False,
        "mb_scanned": 10.0,
    }
    assert outcome.output.rows == [{"n": 1}]


def test_tool_turns_rejections_into_tool_errors():
    tool = RunSqlTool(_runner(FakeBigQueryClient(statement_type="DELETE")))
    with pytest.raises(ToolError, match="Only SELECT"):
        tool.run({"query": "DELETE FROM t", "purpose": "x"})


@pytest.mark.parametrize("tool_input", [{}, {"query": ""}, {"query": 42}])
def test_tool_rejects_missing_or_invalid_query(tool_input):
    with pytest.raises(ToolError, match="non-empty SQL string"):
        RunSqlTool(_runner(FakeBigQueryClient())).run(tool_input)
