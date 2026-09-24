"""The agentic loop, implemented directly on the Messages API.

One call to Agent.ask() handles one user message:

    append user message
    repeat (bounded):
        stream the model's response         -> yield TextDelta / ProgressDelta events
        if it requested tools: run them     -> yield ToolStarted / ToolFinished events
                               append results and loop
        else: done                          -> yield TurnFinished

The conversation history lives on the Agent, so follow-up questions see earlier queries and
results. If a turn fails midway, the history is rolled back to where it was before the turn,
so a dangling tool_use without its tool_result can never poison the next request.
"""

from collections.abc import Generator, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import anthropic
from anthropic.types.beta import (
    BetaMessage,
    BetaMessageParam,
    BetaTextBlockParam,
    BetaThinkingConfigAdaptiveParam,
)

from app.config import Settings
from app.events import (
    Event,
    Message,
    ProgressDelta,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnFailed,
    TurnFinished,
    Usage,
)
from app.history import successful_tool_results
from app.tools.base import Tool, ToolContext, ToolError

MAX_OUTPUT_TOKENS = 64_000
BETAS = [
    # Retry a refused request on Anthropic's recommended fallback model instead of failing.
    "server-side-fallback-2026-07-01",
    # Return the notes written between tool calls ("found X, now checking Y") as progress
    # updates; otherwise they arrive as empty thinking blocks and the chat goes quiet.
    "thinking-display-updates-2026-08-18",
    # Allows setting what happens to stored thinking blocks when the system prompt or tools
    # changed since they were written (see THINKING below).
    "thinking-binding-controls-2026-08-01",
]
# Thinking blocks are tied to the exact system prompt and tools they were produced with. When
# either changes (a new tool, a prompt edit), older saved conversations would be rejected;
# "drop_block" drops their stale thinking instead, so they continue normally.
THINKING: BetaThinkingConfigAdaptiveParam = {
    "type": "adaptive",
    "display": "updates",
    "block_binding": {"prefix_mismatch_behavior": "drop_block"},
}


class AgentError(Exception):
    pass


class Agent:
    def __init__(
        self,
        settings: Settings,
        client: anthropic.Anthropic,
        tools: Sequence[Tool],
        system_prompt: str,
        messages: list[Message] | None = None,
    ):
        self._settings = settings
        self._client = client
        self._tools = {tool.name: tool for tool in tools}
        self._tool_definitions = [tool.definition for tool in tools]
        # Explicit breakpoint on the large, static system prompt; the top-level cache_control
        # in _stream_response additionally caches the growing conversation.
        self._system: list[BetaTextBlockParam] = [
            {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
        ]
        # Plain JSON-compatible dicts, exactly as sent to the API, so the history can be saved
        # and reloaded (e.g. from app.storage) without changing a byte.
        self.messages: list[Message] = list(messages or [])

    def ask(self, question: str) -> Generator[Event, None, None]:
        checkpoint = len(self.messages)
        usage = Usage()
        completed = False
        self.messages.append({"role": "user", "content": question})
        try:
            yield from self._run_loop(usage)
            completed = True
        except AgentError as exc:
            yield TurnFailed(str(exc), usage)
        except anthropic.APIError as exc:
            yield TurnFailed(f"Claude API error: {exc}", usage)
        finally:
            # Also runs if the consumer abandons the generator (e.g. client disconnects).
            if not completed:
                del self.messages[checkpoint:]
        if completed:
            # Yielded only after the turn is final, so a consumer that stops reading here
            # can't trigger the rollback above.
            yield TurnFinished(usage, self.messages[checkpoint:])

    def _run_loop(self, usage: Usage) -> Generator[Event, None, None]:
        for _ in range(self._settings.max_agent_steps):
            response = yield from self._stream_response()
            usage.add(
                response.usage.input_tokens,
                response.usage.cache_read_input_tokens,
                response.usage.cache_creation_input_tokens,
                response.usage.output_tokens,
            )
            self.messages.append(
                {"role": "assistant", "content": [_to_request_json(b) for b in response.content]}
            )

            if response.stop_reason == "refusal":
                raise AgentError("Claude declined to answer this request.")
            if response.stop_reason == "max_tokens":
                raise AgentError("The response hit the output token limit before finishing.")

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                return

            tool_results = yield from self._run_tools(tool_uses)
            # All results for one assistant turn go back in a single user message.
            self.messages.append({"role": "user", "content": tool_results})

        raise AgentError(
            f"Stopped after {self._settings.max_agent_steps} steps without a final answer. "
            "Try a narrower question."
        )

    def _stream_response(self) -> Generator[Event, None, BetaMessage]:
        """Stream one model response, yielding text as it arrives; returns the final message."""
        with self._client.beta.messages.stream(
            model=self._settings.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=self._system,
            tools=self._tool_definitions,
            # The history is kept as plain dicts so it can be stored as JSON; they have the
            # shape of BetaMessageParam, which is what the SDK expects here.
            messages=cast(list[BetaMessageParam], self.messages),
            cache_control={"type": "ephemeral"},
            thinking=THINKING,
            output_config={"effort": self._settings.effort},
            fallbacks="default",
            betas=BETAS,
        ) as stream:
            for event in stream:
                if event.type == "text":
                    yield TextDelta(event.text)
                elif event.type == "thinking" and event.thinking:
                    yield ProgressDelta(event.thinking)
            return stream.get_final_message()

    def _run_tools(self, tool_uses: list[Any]) -> Generator[Event, None, list[dict[str, Any]]]:
        """Run the requested tools concurrently; returns tool_result blocks in request order."""
        for block in tool_uses:
            yield ToolStarted(block.id, block.name, block.input)

        results = successful_tool_results(self.messages)
        with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
            outcomes = list(pool.map(lambda block: self._run_tool(block, results), tool_uses))

        tool_results = []
        for finished, result_block in outcomes:
            yield finished
            tool_results.append(result_block)
        return tool_results

    def _run_tool(self, block: Any, results: dict[str, str]) -> tuple[ToolFinished, dict[str, Any]]:
        try:
            tool = self._tools.get(block.name)
            if tool is None:
                raise ToolError(f"Unknown tool: {block.name}")
            outcome = tool.run(block.input, ToolContext(block.id, results))
        except ToolError as exc:
            return (
                ToolFinished(block.id, block.name, error=str(exc)),
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(exc),
                    "is_error": True,
                },
            )
        return (
            ToolFinished(block.id, block.name, output=outcome.output),
            {"type": "tool_result", "tool_use_id": block.id, "content": outcome.content},
        )


def _to_request_json(block: Any) -> dict[str, Any]:
    """Serialize a response content block the same way the SDK does when sending it back
    (anthropic._utils._json.openapi_model_dump), so stored history equals sent history."""
    dumped: dict[str, Any] = block.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
        exclude=getattr(block, "__api_exclude__", None),
    )
    return dumped
