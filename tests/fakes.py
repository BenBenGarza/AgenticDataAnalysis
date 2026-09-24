"""Test doubles that stand in for the real Agent (no Claude or BigQuery calls)."""

from app.agent import TextDelta, ToolFinished, ToolStarted, TurnFailed, TurnFinished, Usage
from app.bigquery_tool import QueryResult

FAILING_QUESTION = "fail"


class FakeAgent:
    """Answers every question with one query and a text answer, shaped like a real turn.

    The question FAILING_QUESTION produces a failed turn instead.
    """

    def __init__(self, settings, messages):
        self.messages = list(messages)

    def ask(self, question):
        if question == FAILING_QUESTION:
            yield TurnFailed("boom")
            return
        n = len(self.messages)
        tool_id = f"toolu_{n}"
        new = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "", "signature": f"sig{n}"},
                {"type": "tool_use", "id": tool_id, "name": "run_sql",
                 "input": {"query": "SELECT 1 AS x", "purpose": f"check {question}"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tool_id, "content": '{"rows": [{"x": 1}]}'},
            ]},
            {"role": "assistant", "content": [{"type": "text", "text": f"answer to {question}"}]},
        ]
        yield ToolStarted(tool_id, f"check {question}", "SELECT 1 AS x")
        yield ToolFinished(tool_id, result=QueryResult(["x"], [{"x": 1}], 1, 1024**2))
        yield TextDelta(f"answer to {question}")
        self.messages.extend(new)
        yield TurnFinished(Usage(), new)
