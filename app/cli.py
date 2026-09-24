"""Terminal chat with the agent: `python -m app.cli [--show-sql]`.

A thin consumer of ChatSession events; the web UI will consume the same events.
Conversations are saved, so quitting and restarting continues where you left off.
"""

import argparse
import sys

from app.agent import (
    ProgressDelta, TextDelta, ToolFinished, ToolStarted, TurnFailed, TurnFinished, Usage,
)
from app.config import load_settings
from app.session import ChatSession
from app.storage import ConversationNotFound, ConversationStore

DIM, RED, RESET = "\033[2m", "\033[31m", "\033[0m"

HELP = """Commands:
  /new         start a new conversation
  /list        list saved conversations
  /open ID     continue a saved conversation
  /delete ID   delete a saved conversation
  /help        show this help
  Ctrl-D       quit"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the GA4 analysis agent.")
    parser.add_argument("--show-sql", action="store_true", help="print each query the agent runs")
    args = parser.parse_args()

    settings = load_settings()
    session = ChatSession(settings, ConversationStore(settings.database_path))
    print("Ask a question about the Google Merchandise Store data (/help for commands).")
    if session.conversation:
        _show_conversation(session, last_turns=1)

    while True:
        try:
            line = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line.startswith("/"):
            _run_command(session, line)
            continue

        print("\nAgent> ", end="")
        for event in session.ask(line):
            _render(event, args.show_sql)


def _run_command(session: ChatSession, line: str) -> None:
    command, _, argument = line.partition(" ")
    try:
        match command:
            case "/new":
                session.new_chat()
                print(f"{DIM}Started a new conversation.{RESET}")
            case "/list":
                _list_conversations(session)
            case "/open":
                session.open(int(argument))
                _show_conversation(session)
            case "/delete":
                session.delete(int(argument))
                print(f"{DIM}Deleted conversation {argument}.{RESET}")
            case _:
                print(HELP)
    except ValueError:
        print(f"{RED}Usage: {command} ID (see /list){RESET}")
    except ConversationNotFound:
        print(f"{RED}No conversation {argument} (see /list).{RESET}")


def _list_conversations(session: ChatSession) -> None:
    conversations = session.list_conversations()
    if not conversations:
        print(f"{DIM}No saved conversations yet.{RESET}")
    active_id = session.conversation.id if session.conversation else None
    for c in conversations:
        marker = "*" if c.id == active_id else " "
        print(f"{marker} {c.id:>3}  {c.updated_at[:16].replace('T', ' ')}  {c.title}")


def _show_conversation(session: ChatSession, last_turns: int | None = None) -> None:
    turns = session.display_turns()
    shown = turns[-last_turns:] if last_turns else turns
    print(f"{DIM}Continuing \"{session.conversation.title}\" ({len(turns)} earlier questions"
          f"{', showing the last' if len(shown) < len(turns) else ''}). /new to start fresh.{RESET}")
    for turn in shown:
        print(f"\nYou> {turn.question}")
        for query in turn.queries:
            print(f"{DIM}  ▸ query: {query['purpose']}{RESET}")
        print(f"\nAgent> {turn.answer}")


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
