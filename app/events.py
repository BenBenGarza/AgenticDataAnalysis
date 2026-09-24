"""Events produced while answering a question.

The agent yields these as it works; ChatSession passes them on, and each client (web API,
terminal) decides how to present them. They are the vocabulary shared by all layers.
"""

from dataclasses import dataclass, field
from typing import Any

# One entry of the conversation history: {"role": ..., "content": ...}, exactly as sent to the
# Messages API. Plain JSON-compatible data, so it can be stored and reloaded unchanged.
Message = dict[str, Any]


@dataclass
class TextDelta:
    """A piece of the answer, streamed as the model writes it."""

    text: str


@dataclass
class ProgressDelta:
    """A short note the model writes while working (between tool calls); not part of the answer."""

    text: str


@dataclass
class ToolStarted:
    tool_use_id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolFinished:
    """A tool call completed: `output` is its structured result, or `error` says why not."""

    tool_use_id: str
    name: str
    output: object | None = None
    error: str | None = None


@dataclass(frozen=True)
class TokenPrices:
    """US dollars per million tokens."""

    input: float
    output: float
    cache_write: float
    cache_read: float


@dataclass
class Usage:
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    cost_usd: float = 0.0  # estimate from list prices

    def add(
        self,
        prices: TokenPrices,
        input_tokens: int,
        cache_read_tokens: int | None,
        cache_write_tokens: int | None,
        output_tokens: int,
    ) -> None:
        cache_read_tokens = cache_read_tokens or 0
        cache_write_tokens = cache_write_tokens or 0
        self.input_tokens += input_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_write_tokens += cache_write_tokens
        self.output_tokens += output_tokens
        self.model_calls += 1
        self.cost_usd += (
            input_tokens * prices.input
            + cache_read_tokens * prices.cache_read
            + cache_write_tokens * prices.cache_write
            + output_tokens * prices.output
        ) / 1_000_000


@dataclass
class TurnFinished:
    usage: Usage
    # The messages this turn appended to the history (user question through final answer).
    new_messages: list[Message]


@dataclass
class TurnFailed:
    message: str
    usage: Usage = field(default_factory=Usage)


Event = TextDelta | ProgressDelta | ToolStarted | ToolFinished | TurnFinished | TurnFailed
