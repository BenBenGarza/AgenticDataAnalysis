"""Read-only, cost-capped BigQuery execution.

Every query goes through a free dry run first. The dry run is BigQuery's own parser,
so it gives us two guarantees without hand-written SQL parsing:
  * statement_type must be SELECT (no DML/DDL, no scripts), and
  * the bytes to be scanned are known before anything is billed.
"""

import datetime
import decimal
import warnings
from dataclasses import dataclass
from typing import Any

from google.api_core import exceptions as gcp_exceptions
from google.cloud import bigquery

# Results are small; the REST fallback is fine. Silence the Storage API hint.
warnings.filterwarnings("ignore", message="BigQuery Storage module not found")


class QueryRejected(Exception):
    """The query is invalid or not allowed. The message is written for the model to act on."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
    bytes_processed: int

    @property
    def truncated(self) -> bool:
        return self.total_rows > len(self.rows)

    @property
    def mb_scanned(self) -> float:
        return round(self.bytes_processed / 1024**2, 1)


class BigQueryRunner:
    def __init__(self, client: bigquery.Client, max_bytes_billed: int, max_rows: int):
        self._client = client
        self._max_bytes = max_bytes_billed
        self._max_rows = max_rows

    def run(self, sql: str) -> QueryResult:
        estimated_bytes = self._dry_run(sql)
        if estimated_bytes > self._max_bytes:
            raise QueryRejected(
                f"Query would scan {_gb(estimated_bytes)}, over the {_gb(self._max_bytes)} limit. "
                "Narrow the _TABLE_SUFFIX date range or select fewer columns "
                "(event_params is the heaviest)."
            )

        config = bigquery.QueryJobConfig(maximum_bytes_billed=self._max_bytes)
        try:
            job = self._client.query(sql, job_config=config)
            row_iter = job.result(max_results=self._max_rows)
        except gcp_exceptions.GoogleAPICallError as exc:
            raise QueryRejected(f"BigQuery error: {_error_message(exc)}") from exc

        columns = [field.name for field in row_iter.schema]
        rows = [{name: _to_json_value(row[name]) for name in columns} for row in row_iter]
        return QueryResult(
            columns=columns,
            rows=rows,
            total_rows=row_iter.total_rows or len(rows),
            bytes_processed=job.total_bytes_processed or 0,
        )

    def _dry_run(self, sql: str) -> int:
        config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        try:
            job = self._client.query(sql, job_config=config)
        except gcp_exceptions.GoogleAPICallError as exc:
            raise QueryRejected(f"Invalid SQL: {_error_message(exc)}") from exc
        if job.statement_type != "SELECT":
            raise QueryRejected(f"Only SELECT queries are allowed (got {job.statement_type}).")
        return job.total_bytes_processed or 0


def _to_json_value(value: Any) -> Any:
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, list):
        return [_to_json_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_json_value(v) for k, v in value.items()}
    return value


def _error_message(exc: gcp_exceptions.GoogleAPICallError) -> str:
    # The first error entry carries BigQuery's precise message (with line:column).
    errors = getattr(exc, "errors", None) or []
    return str(errors[0].get("message", exc)) if errors else str(exc.message)


def _gb(n_bytes: int) -> str:
    return f"{n_bytes / 1024**3:.2f} GB"
