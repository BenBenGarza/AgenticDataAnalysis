"""The agentic loop, implemented directly on the Messages API.

One call to Agent.ask() handles one user message:

    append user message
    repeat (bounded):
        stream Claude's response            -> yield TextDelta events
        if it requested tools: run them     -> yield ToolStarted / ToolFinished events
                               append results and loop
        else: done                          -> yield TurnFinished

The conversation history lives on the Agent, so follow-up questions see earlier queries and
results. If a turn fails midway, the history is rolled back to where it was before the turn,
so a dangling tool_use without its tool_result can never poison the next request.
"""

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import anthropic

from app.bigquery_tool import BigQueryRunner, QueryRejected, QueryResult
from app.config import Settings
from app.prompts import build_system_prompt

MAX_OUTPUT_TOKENS = 64_000
BETAS = [
    # Retry a refused request on Anthropic's recommended fallback model instead of failing.
    "server-side-fallback-2026-07-01",
    # Return the notes written between tool calls ("found X, now checking Y") as progress
    # updates; otherwise they arrive as empty thinking blocks and the chat goes quiet.
    "thinking-display-updates-2026-08-18",
]

# Tool inputs are short SQL strings, so eager input streaming would buy little latency
# and we keep the API's server-side schema validation of tool inputs instead.
RUN_SQL_TOOL = {
    "name": "run_sql",
    "description": (
        "Run one read-only BigQuery Standard SQL SELECT statement against the GA4 ecommerce "
        "sample and return the result rows as JSON. Queries are dry-run first: non-SELECT "
        "statements and queries that would scan too many bytes are rejected with an explanation. "
        "Only the first rows are returned; 'truncated' tells you if there were more."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A single SELECT statement (CTEs allowed). Always filter on _TABLE_SUFFIX.",
            },
            "purpose": {
                "type": "string",
                "description": "One short sentence describing what this query checks; shown to the user.",
            },
        },
        "required": ["query", "purpose"],
        "additionalProperties": False,
    },
}


# ---- Events streamed to the caller -------------------------------------------------------

@dataclass
class TextDelta:
    text: str


@dataclass
class ProgressDelta:
    """A short note the model writes while working (between tool calls); not part of the answer."""
    text: str


@dataclass
class ToolStarted:
    tool_use_id: str
    purpose: str
    query: str


@dataclass
class ToolFinished:
    tool_use_id: str
    result: QueryResult | None = None
    error: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0

    def add(self, usage: Any) -> None:
        self.input_tokens += usage.input_tokens
        self.cache_read_tokens += usage.cache_read_input_tokens or 0
        self.cache_write_tokens += usage.cache_creation_input_tokens or 0
        self.output_tokens += usage.output_tokens
        self.model_calls += 1


@dataclass
class TurnFinished:
    usage: Usage
    # The messages this turn appended to the history (user question through final answer).
    new_messages: list[dict[str, Any]]


@dataclass
class TurnFailed:
    message: str
    usage: Usage = field(default_factory=Usage)


Event = TextDelta | ProgressDelta | ToolStarted | ToolFinished | TurnFinished | TurnFailed


class AgentError(Exception):
    pass


# ---- Agent -------------------------------------------------------------------------------

class Agent:
    def __init__(self, settings: Settings, messages: list[dict[str, Any]] | None = None,
                 client: anthropic.Anthropic | None = None, sql: BigQueryRunner | None = None):
        self._settings = settings
        self._client = client or anthropic.Anthropic()
        self._sql = sql or BigQueryRunner(
            settings.gcp_project, settings.max_bytes_billed, settings.max_result_rows
        )
        # Explicit breakpoint on the large, static system prompt; the top-level cache_control
        # in _stream_response additionally caches the growing conversation.
        self._system = [{"type": "text", "text": build_system_prompt(),
                         "cache_control": {"type": "ephemeral"}}]
        # Plain JSON-compatible dicts, exactly as sent to the API, so the history can be saved
        # and reloaded (e.g. from app.storage) without changing a byte.
        self.messages: list[dict[str, Any]] = list(messages or [])

    def ask(self, question: str) -> Iterator[Event]:
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

    def _run_loop(self, usage: Usage) -> Iterator[Event]:
        for _ in range(self._settings.max_agent_steps):
            response = yield from self._stream_response()
            usage.add(response.usage)
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

    def _stream_response(self) -> Iterator[Event]:
        """Stream one model response, yielding text as it arrives; returns the final message."""
        with self._client.beta.messages.stream(
            model=self._settings.model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=self._system,
            tools=[RUN_SQL_TOOL],
            messages=self.messages,
            cache_control={"type": "ephemeral"},
            thinking={"type": "adaptive", "display": "updates"},
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

    def _run_tools(self, tool_uses: list[Any]) -> Iterator[Event]:
        """Run the requested queries concurrently; returns tool_result blocks in request order."""
        for block in tool_uses:
            yield ToolStarted(block.id, block.input.get("purpose", ""), block.input.get("query", ""))

        with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
            outcomes = list(pool.map(self._execute_tool, tool_uses))

        tool_results = []
        for block, (finished, result_block) in zip(tool_uses, outcomes):
            yield finished
            tool_results.append(result_block)
        return tool_results

    def _execute_tool(self, block: Any) -> tuple[ToolFinished, dict[str, Any]]:
        if block.name != RUN_SQL_TOOL["name"]:
            error = f"Unknown tool: {block.name}"
        else:
            try:
                result = self._sql.run(block.input["query"])
            except QueryRejected as exc:
                error = str(exc)
            else:
                content = json.dumps({
                    "columns": result.columns,
                    "rows": result.rows,
                    "rows_returned": len(result.rows),
                    "total_rows": result.total_rows,
                    "truncated": result.truncated,
                    "mb_scanned": round(result.bytes_processed / 1024**2, 1),
                })
                return (ToolFinished(block.id, result=result),
                        {"type": "tool_result", "tool_use_id": block.id, "content": content})

        return (ToolFinished(block.id, error=error),
                {"type": "tool_result", "tool_use_id": block.id, "content": error, "is_error": True})


def _to_request_json(block: Any) -> dict[str, Any]:
    """Serialize a response content block the same way the SDK does when sending it back
    (see anthropic._utils._json.openapi_model_dump), so stored history is identical to sent history."""
    return block.model_dump(mode="json", by_alias=True, exclude_unset=True,
                            exclude=getattr(block, "__api_exclude__", None))
