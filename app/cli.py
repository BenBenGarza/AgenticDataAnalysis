"""Terminal chat with the agent: `python -m app.cli [--show-sql]`.

A thin consumer of Agent.ask() events; the web UI will consume the same events.
"""

import argparse
import sys

from app.agent import (
    Agent, ProgressDelta, TextDelta, ToolFinished, ToolStarted, TurnFailed, TurnFinished, Usage,
)
from app.config import load_settings

DIM, RED, RESET = "\033[2m", "\033[31m", "\033[0m"


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the GA4 analysis agent.")
    parser.add_argument("--show-sql", action="store_true", help="print each query the agent runs")
    args = parser.parse_args()

    agent = Agent(load_settings())
    print("Ask a question about the Google Merchandise Store data (Ctrl-D to quit, /reset to start over).")

    while True:
        try:
            question = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not question:
            continue
        if question == "/reset":
            agent.messages.clear()
            print(f"{DIM}Conversation cleared.{RESET}")
            continue

        print("\nAgent> ", end="")
        for event in agent.ask(question):
            _render(event, args.show_sql)


def _render(event, show_sql: bool) -> None:
    match event:
        case TextDelta(text=text):
            print(text, end="", flush=True)
        case ProgressDelta(text=text):
            print(f"{DIM}{text}{RESET}", end="", flush=True)
        case ToolStarted(purpose=purpose, query=query):
            print(f"\n{DIM}  ▸ query: {purpose}{RESET}", flush=True)
            if show_sql:
                print(f"{DIM}{_indent(query, '      ')}{RESET}")
        case ToolFinished(result=result, error=None):
            note = f", showing {len(result.rows)}" if result.truncated else ""
            print(f"{DIM}    ✓ {result.total_rows} rows{note}, "
                  f"{result.bytes_processed / 1024**2:.0f} MB scanned{RESET}")
        case ToolFinished(error=error):
            print(f"{DIM}    ✗ {error}{RESET}")
        case TurnFinished(usage=usage):
            print(f"\n{DIM}[{_usage_line(usage)}]{RESET}")
        case TurnFailed(message=message, usage=usage):
            print(f"\n{RED}Error: {message}{RESET}", file=sys.stderr)
            print(f"{DIM}[{_usage_line(usage)}]{RESET}")


def _usage_line(usage: Usage) -> str:
    return (f"{usage.model_calls} model calls · input {usage.input_tokens:,} "
            f"+ cache read {usage.cache_read_tokens:,} / write {usage.cache_write_tokens:,} "
            f"· output {usage.output_tokens:,} tokens")


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line for line in text.strip().splitlines())


if __name__ == "__main__":
    main()
