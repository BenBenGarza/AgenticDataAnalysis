"""The user-facing view of a saved conversation, rebuilt from its stored API history.

The history (see app.storage) is the single source of truth: questions, answer text, queries
with their results, and charts are all derived from it, so nothing else needs to be saved.
"""

from dataclasses import dataclass
from typing import Any

from app.events import Message
from app.history import failed_tool_results, successful_tool_results
from app.tools import create_chart, run_sql
from app.tools.base import ToolError


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class QueryBlock:
    id: str
    purpose: str
    sql: str
    result: dict[str, Any] | None  # run_sql.result_json() shape; None if the query failed
    error: str | None
    type: str = "query"


@dataclass
class ChartBlock:
    id: str
    chart: create_chart.Chart
    type: str = "chart"


DisplayBlock = TextBlock | QueryBlock | ChartBlock


@dataclass
class DisplayTurn:
    """One question and its answer, as shown when a conversation is reopened: the answer's
    text, queries and charts in the order they were produced."""

    question: str
    blocks: list[DisplayBlock]


def to_display_turns(messages: list[Message]) -> list[DisplayTurn]:
    """Rebuild the user-facing view from the stored API history (the single source of truth).

    A turn starts at a user message whose content is a plain string (the question); user
    messages carrying tool results belong to the turn in progress. Query results and charts
    are rebuilt from the stored tool results, so nothing else needs to be saved.
    """
    results = successful_tool_results(messages)
    errors = failed_tool_results(messages)
    turns: list[DisplayTurn] = []
    for message in messages:
        content = message["content"]
        if message["role"] == "user":
            if isinstance(content, str):
                turns.append(DisplayTurn(question=content, blocks=[]))
            continue
        blocks = turns[-1].blocks
        for block in content:
            if block["type"] == "text":
                _add_text(blocks, block["text"])
            elif block["type"] == "tool_use" and block["name"] == run_sql.NAME:
                result = results.get(block["id"])
                blocks.append(
                    QueryBlock(
                        id=block["id"],
                        purpose=block["input"].get("purpose", ""),
                        sql=block["input"].get("query", ""),
                        result=run_sql.parse_result(result) if result is not None else None,
                        error=errors.get(block["id"]),
                    )
                )
            elif (
                block["type"] == "tool_use"
                and block["name"] == create_chart.NAME
                and block["id"] in results  # only charts that were shown
                and (chart := _rebuild_chart(block["input"], results))
            ):
                blocks.append(ChartBlock(id=block["id"], chart=chart))
    return turns


def _add_text(blocks: list[DisplayBlock], text: str) -> None:
    # Consecutive text blocks (e.g. separated only by thinking) read as one passage.
    if blocks and isinstance(blocks[-1], TextBlock):
        blocks[-1].text = "\n\n".join(filter(None, [blocks[-1].text, text]))
    else:
        blocks.append(TextBlock(text))


def _rebuild_chart(spec: dict[str, Any], results: dict[str, str]) -> create_chart.Chart | None:
    try:
        return create_chart.build_chart(spec, results)
    except ToolError:
        return None  # it was valid when shown; if its source result is unreadable, skip it
