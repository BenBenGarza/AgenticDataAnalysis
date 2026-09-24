"""Run a fixed set of example conversations through the real app and write a report.

The conversations go through the same ChatSession, Agent, tools and storage as the web app, using a
temporary database (saved conversations are untouched) and high effort. Every run calls Claude and
BigQuery, so it costs real money (about $1-1.50) and a few GB of BigQuery quota.

Usage:
    .venv/bin/python -m scripts.run_example_conversations [output.md]
"""

import sys
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.bigquery import QueryResult
from app.bootstrap import build_session
from app.config import load_settings
from app.events import TextDelta, ToolFinished, ToolStarted, TurnFailed, TurnFinished, Usage
from app.session import ChatSession
from app.tools import run_sql
from app.tools.create_chart import Chart

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "example-conversations.md"
PREVIEW_ROWS = 5

# (what the conversation tests, questions asked in order within one conversation)
CONVERSATIONS: list[tuple[str, list[str]]] = [
    (
        "Channel attribution, and a follow-up that depends on context",
        [
            "Which traffic channels drove the most revenue in December 2020?",
            "How does that compare to November?",
        ],
    ),
    (
        "A time-series chart, then a drill-down",
        [
            "Show me the daily revenue trend for December 2020.",
            "Which products sold best during the busiest week?",
        ],
    ),
    (
        "Funnel analysis",
        ["What does the purchase funnel look like, and where do users drop off?"],
    ),
    (
        "Comparing segments",
        ["How do desktop, mobile and tablet compare on conversion rate and average order value?"],
    ),
    (
        "A ranking, then a breakdown",
        [
            "Which countries bring the most revenue?",
            "Break the top 3 down by month.",
        ],
    ),
    (
        "Honesty about what the data can't answer",
        [
            "What was our revenue in March 2022?",
            "What is our customer lifetime value?",
        ],
    ),
]


@dataclass
class Query:
    purpose: str
    sql: str
    result: QueryResult | None = None
    error: str | None = None


@dataclass
class Turn:
    question: str
    answer: str = ""
    queries: list[Query] = field(default_factory=list)
    charts: list[Chart] = field(default_factory=list)
    chart_errors: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    seconds: float = 0.0
    failure: str | None = None


def run_turn(session: ChatSession, question: str) -> Turn:
    turn = Turn(question)
    pending: dict[str, Query] = {}
    started = time.monotonic()
    for event in session.ask(question):
        match event:
            case TextDelta(text=text):
                turn.answer += text
            case ToolStarted(tool_use_id=id_, name=run_sql.NAME, input=tool_input):
                pending[id_] = Query(tool_input.get("purpose", ""), tool_input.get("query", ""))
                turn.queries.append(pending[id_])
            case ToolFinished(output=Chart() as chart):
                turn.charts.append(chart)
            case ToolFinished(tool_use_id=id_, output=QueryResult() as result):
                pending[id_].result = result
            case ToolFinished(tool_use_id=id_, error=str() as error):
                if id_ in pending:
                    pending[id_].error = error
                else:
                    turn.chart_errors.append(error)
            case TurnFinished(usage=usage):
                turn.usage = usage
            case TurnFailed(message=message, usage=usage):
                turn.failure, turn.usage = message, usage
    turn.seconds = time.monotonic() - started
    return turn


def main(output: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = replace(load_settings(), effort="high", database_path=Path(tmp) / "report.db")
        session = build_session(settings)
        results: list[tuple[str, list[Turn]]] = []
        for label, questions in CONVERSATIONS:
            session.new_chat()
            turns = []
            for question in questions:
                print(f"> {question}", flush=True)
                turn = run_turn(session, question)
                status = turn.failure or "ok"
                print(f"  {status}: {turn.seconds:.0f}s, ${turn.usage.cost_usd:.3f}", flush=True)
                turns.append(turn)
            results.append((label, turns))
    output.write_text(render_report(results, settings.model, settings.effort))
    print(f"Wrote {output}")


# ---- Report --------------------------------------------------------------------------------


def render_report(results: list[tuple[str, list[Turn]]], model: str, effort: str) -> str:
    all_turns = [turn for _, turns in results for turn in turns]
    lines = [
        "# Example conversations",
        "",
        f"Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by "
        "`scripts/run_example_conversations.py`: every question below went through the real app "
        f"(same agent, tools and prompt), with `{model}` at `{effort}` effort and a fresh "
        "database. "
        "Answers, queries and charts are exactly what the app produced; nothing was edited.",
        "",
        "## Summary",
        "",
        "| # | What it tests | Questions | Queries | Charts | Time | Cost |",
        "|---|---|---|---|---|---|---|",
    ]
    for number, (label, turns) in enumerate(results, start=1):
        lines.append(
            f"| {number} | {label} | {len(turns)} | {sum(len(t.queries) for t in turns)} | "
            f"{sum(len(t.charts) for t in turns)} | {sum(t.seconds for t in turns):.0f}s | "
            f"${sum(t.usage.cost_usd for t in turns):.2f} |"
        )
    total_gb = (
        sum(q.result.bytes_processed for t in all_turns for q in t.queries if q.result) / 1024**3
    )
    failed_queries = sum(1 for t in all_turns for q in t.queries if q.error)
    lines += [
        f"| | **Total** | **{len(all_turns)}** | **{sum(len(t.queries) for t in all_turns)}** | "
        f"**{sum(len(t.charts) for t in all_turns)}** | "
        f"**{sum(t.seconds for t in all_turns):.0f}s** | "
        f"**${sum(t.usage.cost_usd for t in all_turns):.2f}** |",
        "",
        f"- BigQuery scanned **{total_gb:.2f} GB** in total (the free tier allows 1 TB per month).",
        f"- Queries rejected by BigQuery (the error goes back to the agent): **{failed_queries}**. "
        f"Failed answers: **{sum(1 for t in all_turns if t.failure)}**.",
        "- Cost is estimated from token usage at list prices; time is wall-clock per answer.",
        "",
    ]
    for number, (label, turns) in enumerate(results, start=1):
        lines += [f"## {number}. {label}", ""]
        for turn in turns:
            lines += render_turn(turn)
    return "\n".join(lines) + "\n"


def render_turn(turn: Turn) -> list[str]:
    usage = turn.usage
    lines = [
        f"### Q: {turn.question}",
        "",
        f"*{turn.seconds:.0f}s · ${usage.cost_usd:.3f} · {usage.model_calls} model calls · "
        f"{plural(len(turn.queries), 'query', 'queries')} · tokens: {usage.input_tokens:,} input, "
        f"{usage.cache_read_tokens:,} cache read, {usage.cache_write_tokens:,} cache write, "
        f"{usage.output_tokens:,} output*",
        "",
    ]
    for query in turn.queries:
        lines += render_query(query)
    for chart in turn.charts:
        lines += render_chart(chart)
    for error in turn.chart_errors:
        lines += [f"> Chart request rejected and retried by the agent: {error}", ""]
    if turn.failure:
        lines += [f"> **Failed:** {turn.failure}", ""]
    lines += ["**Answer:**", "", turn.answer.strip(), "", "---", ""]
    return lines


def render_query(query: Query) -> list[str]:
    if query.error:
        status = f"error returned to the agent: {query.error}"
    elif query.result:
        rows = plural(query.result.total_rows, "row", "rows")
        status = f"{rows}, {query.result.mb_scanned} MB scanned"
    else:
        status = "no result"
    lines = [
        "<details>",
        f"<summary>Query: {escape_html(query.purpose)} ({escape_html(status)})</summary>",
        "",
        "```sql",
        query.sql.strip(),
        "```",
        "",
    ]
    if query.result and query.result.rows:
        lines += markdown_table(query.result.columns, query.result.rows[:PREVIEW_ROWS])
        if query.result.total_rows > PREVIEW_ROWS:
            lines.append(f"\n*First {PREVIEW_ROWS} of {query.result.total_rows} rows.*")
        lines.append("")
    lines += ["</details>", ""]
    return lines


def render_chart(chart: Chart) -> list[str]:
    """A Mermaid chart (GitHub renders these) when the type maps cleanly, else the data table."""
    lines = [f"**Chart ({chart.type.replace('_', ' ')}):** {chart.title}", ""]
    if chart.type == "stacked_bar":
        columns = ["", *(series.name for series in chart.series)]
        rows = [
            {"": label, **{s.name: s.values[i] for s in chart.series}}
            for i, label in enumerate(chart.labels)
        ]
        return lines + markdown_table(columns, rows) + [""]
    orientation = " horizontal" if chart.type == "horizontal_bar" else ""
    mark = "line" if chart.type == "line" else "bar"
    labels = ", ".join(f'"{mermaid_text(label)}"' for label in chart.labels)
    lines += [
        "```mermaid",
        f"xychart-beta{orientation}",
        f'    title "{mermaid_text(chart.title)}"',
        f"    x-axis [{labels}]",
    ]
    for series in chart.series:
        values = ", ".join("0" if v is None else f"{v:g}" for v in series.values)
        lines.append(f"    {mark} [{values}]")
    lines += ["```", ""]
    if len(chart.series) > 1:
        lines += ["Series, in order: " + ", ".join(s.name for s in chart.series) + ".", ""]
    return lines


def markdown_table(columns: list[str], rows: list[dict[str, Any]]) -> list[str]:
    header = "| " + " | ".join(columns) + " |"
    divider = "|" + "---|" * len(columns)
    body = ["| " + " | ".join(cell(row.get(column)) for column in columns) + " |" for row in rows]
    return [header, divider, *body]


def cell(value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.4g}" if abs(value) < 1 else f"{value:,.2f}"
    return "" if value is None else str(value).replace("|", "\\|")


def mermaid_text(text: str) -> str:
    # Mermaid treats <...> as HTML and drops it (GA4's "<Other>" would vanish).
    return text.replace('"', "'").replace("<", "‹").replace(">", "›")


def plural(count: int, singular: str, plural_form: str) -> str:
    return f"{count} {singular if count == 1 else plural_form}"


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT)
