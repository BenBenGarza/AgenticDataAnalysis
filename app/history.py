"""Read-only helpers over a conversation's message history (the list sent to the API)."""

from collections.abc import Iterator
from typing import Any

from app.events import Message


def successful_tool_results(messages: list[Message]) -> dict[str, str]:
    """tool_use_id -> content of every successful tool result in the history."""
    return {
        b["tool_use_id"]: b["content"] for b in _tool_results(messages) if not b.get("is_error")
    }


def failed_tool_results(messages: list[Message]) -> dict[str, str]:
    """tool_use_id -> error message of every failed tool result in the history."""
    return {b["tool_use_id"]: b["content"] for b in _tool_results(messages) if b.get("is_error")}


def _tool_results(messages: list[Message]) -> Iterator[dict[str, Any]]:
    for message in messages:
        if message["role"] == "user" and isinstance(message["content"], list):
            yield from (b for b in message["content"] if b.get("type") == "tool_result")
