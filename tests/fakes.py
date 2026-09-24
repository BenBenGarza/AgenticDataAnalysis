"""Test doubles: no Claude or BigQuery calls anywhere in the test suite."""

import copy
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

from anthropic.types.beta import BetaMessage

from app.bigquery import QueryResult
from app.events import (
    Event,
    Message,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnFailed,
    TurnFinished,
    Usage,
)
from app.tools import create_chart, run_sql
from app.tools.base import ToolContext, ToolError, ToolOutcome

# ---- A stand-in for the whole Agent (used by session and API tests) ----------------------

FAILING_QUESTION = "fail"
CRASHING_QUESTION = "crash"
CHART_QUESTION = "chart it"


class FakeAgent:
    """Answers every question with one query and a text answer, shaped like a real turn.

    FAILING_QUESTION produces a failed turn instead, CRASHING_QUESTION raises an unexpected
    exception, and CHART_QUESTION also charts the result.
    """

    def __init__(self, messages: list[Message]):
        self.messages = list(messages)

    def ask(self, question: str) -> Iterator[Event]:
        if question == FAILING_QUESTION:
            yield TurnFailed("boom")
            return
        if question == CRASHING_QUESTION:
            raise RuntimeError("unexpected bug")
        n = len(self.messages)
        query_id, chart_id = f"toolu_{n}", f"toolu_{n}_chart"
        query_input = {"query": "SELECT day, revenue", "purpose": f"check {question}"}
        result = QueryResult(
            ["day", "revenue"],
            [{"day": "d1", "revenue": 1.0}, {"day": "d2", "revenue": 2.0}],
            total_rows=2,
            bytes_processed=1024**2,
        )
        stored_result = json.dumps(run_sql.result_json(result, query_id))
        new: list[Message] = [
            {"role": "user", "content": question},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": f"sig{n}"},
                    tool_use(query_id, "run_sql", query_input),
                ],
            },
            {"role": "user", "content": [tool_result(query_id, stored_result)]},
        ]
        yield ToolStarted(query_id, "run_sql", query_input)
        yield ToolFinished(query_id, "run_sql", output=result)

        if question == CHART_QUESTION:
            chart_input = {
                "query_id": query_id,
                "type": "bar",
                "title": "Revenue",
                "x": "day",
                "y": ["revenue"],
            }
            chart = create_chart.build_chart(chart_input, {query_id: stored_result})
            new += [
                {"role": "assistant", "content": [tool_use(chart_id, "create_chart", chart_input)]},
                {"role": "user", "content": [tool_result(chart_id, "Chart shown")]},
            ]
            yield ToolStarted(chart_id, "create_chart", chart_input)
            yield ToolFinished(chart_id, "create_chart", output=chart)

        new.append({"role": "assistant", "content": [text(f"answer to {question}")]})
        yield TextDelta(f"answer to {question}")
        self.messages.extend(new)
        yield TurnFinished(Usage(), new)


# ---- Fakes for testing the Agent loop itself ---------------------------------------------


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(tool_use_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}


def tool_result(tool_use_id: str, content: str) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}


def reply(*blocks: dict[str, Any], stop_reason: str | None = None) -> BetaMessage:
    """A model response built with the SDK's real response type."""
    if stop_reason is None:
        stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return BetaMessage.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "content": [{"type": "thinking", "thinking": "", "signature": "sig"}, *blocks],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 0,
            },
        }
    )


class FakeAnthropic:
    """Replays scripted responses (or raises scripted exceptions) and records each request."""

    def __init__(self, *script: BetaMessage | Exception):
        self._script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **request: Any) -> "FakeStream":
        # Snapshot: the agent keeps appending to the same messages list afterwards.
        self.requests.append(copy.deepcopy(request))
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return FakeStream(step)


class FakeStream:
    """Mimics the SDK's message stream: iterate for text events, then get_final_message()."""

    def __init__(self, message: BetaMessage):
        self._message = message

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def __iter__(self) -> Iterator[SimpleNamespace]:
        for block in self._message.content:
            if block.type == "text":
                yield SimpleNamespace(type="text", text=block.text)

    def get_final_message(self) -> BetaMessage:
        return self._message


class FakeTool:
    """Returns 'result for <key>'; keys listed in `errors` raise ToolError instead."""

    def __init__(self, name: str = "lookup", errors: dict[str, str] | None = None):
        self.name = name
        self.definition = {
            "name": name,
            "description": "test tool",
            "input_schema": {"type": "object"},
        }
        self._errors = errors or {}
        self.calls: list[dict[str, Any]] = []
        self.contexts: list[ToolContext] = []

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolOutcome:
        self.calls.append(tool_input)
        self.contexts.append(context)
        key = tool_input.get("key", "")
        if key in self._errors:
            raise ToolError(self._errors[key])
        return ToolOutcome(content=f"result for {key}", output={"key": key})
