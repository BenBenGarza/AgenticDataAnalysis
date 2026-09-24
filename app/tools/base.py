"""The contract between the agent loop and the tools it can call.

The agent only knows this interface: adding a tool means writing a new Tool, not editing
the loop.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from anthropic.types.beta import BetaToolParam


class ToolError(Exception):
    """The tool call could not be completed. The message is returned to the model to act on."""


@dataclass
class ToolOutcome:
    content: str  # what the model sees as the tool result
    output: object  # structured result for the user-facing clients (e.g. a QueryResult)


class Tool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def definition(self) -> BetaToolParam:
        """The tool's name, description and input schema, as sent to the Messages API."""
        ...

    def run(self, tool_input: dict[str, Any]) -> ToolOutcome:
        """Execute one call. Raises ToolError for failures the model can recover from."""
        ...
