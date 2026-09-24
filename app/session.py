"""A user's chat session: the active conversation, backed by persistent storage.

Connects the Agent (which only knows an in-memory message list) to the ConversationStore.
Both the terminal client and the web API drive the app through this class.

One answer at a time: while a turn is running, the session refuses anything that would change
it (another question, switching, a new chat, deleting). Several browser tabs share this one
session, so the rule has to be enforced here rather than trusted to each client.
"""

import logging
import threading
import time
from collections.abc import Callable, Generator
from contextlib import closing
from typing import Protocol

from app.display import DisplayTurn, to_display_turns
from app.events import Event, Message, ToolStarted, TurnFailed, TurnFinished, Usage
from app.storage import DEFAULT_USER_ID, Conversation, ConversationStore


class ConversationAgent(Protocol):
    """What the session needs from an agent (app.agent.Agent in production, fakes in tests)."""

    messages: list[Message]

    def ask(self, question: str) -> Generator[Event, None, None]: ...


# Builds an agent that continues from the given message history.
AgentFactory = Callable[[list[Message]], ConversationAgent]

logger = logging.getLogger(__name__)

BUSY_MESSAGE = "An answer is in progress. Wait for it to finish or press Stop."
SAVE_FAILED_MESSAGE = "The answer couldn't be saved, so it was discarded. Please retry."


class SessionBusy(Exception):
    """The session can't change while an answer is in progress."""

    def __init__(self) -> None:
        super().__init__(BUSY_MESSAGE)


class ChatSession:
    def __init__(
        self,
        store: ConversationStore,
        agent_factory: AgentFactory,
        model: str,
        user_id: int = DEFAULT_USER_ID,
    ):
        self._store = store
        self._agent_factory = agent_factory
        self._model = model  # recorded on new conversations
        self._user_id = user_id
        self.conversation: Conversation | None = store.get_active_conversation(user_id)
        self._agent = self._new_agent()
        self._busy = False
        self._busy_lock = threading.Lock()  # requests are handled on different threads

    def ask(self, question: str) -> Generator[Event, None, None]:
        """Start a turn. Raises SessionBusy right away if another answer is in progress.

        The returned generator streams the turn's events; the turn is saved before TurnFinished
        is passed on. Closing it early (e.g. the client disconnected) rolls the turn back.
        """
        self._ensure_idle()
        return self._run_turn(question)

    def _run_turn(self, question: str) -> Generator[Event, None, None]:
        # Checked again here because two requests can both pass the check in ask() before
        # either starts streaming.
        if not self._claim():
            yield TurnFailed(BUSY_MESSAGE)
            return
        try:
            yield from self._stream_turn(question)
        finally:
            self._busy = False

    def _claim(self) -> bool:
        """Mark the session busy. False if another turn already holds it."""
        with self._busy_lock:
            if self._busy:
                return False
            self._busy = True
            return True

    def _stream_turn(self, question: str) -> Generator[Event, None, None]:
        started = time.monotonic()
        tools_used: list[str] = []
        conversation = self.conversation  # save to where the turn started, whatever happens
        with closing(self._agent.ask(question)) as events:
            for event in events:
                if isinstance(event, ToolStarted):
                    tools_used.append(event.name)
                elif isinstance(event, TurnFinished):
                    saved = self._try_save_turn(conversation, question, event)
                    if saved is None:
                        yield TurnFailed(SAVE_FAILED_MESSAGE, event.usage)
                        return
                    conversation = saved
                    _log_turn("finished", conversation, started, tools_used, event.usage)
                elif isinstance(event, TurnFailed):
                    _log_turn("failed", conversation, started, tools_used, event.usage)
                yield event

    def _try_save_turn(
        self, conversation: Conversation | None, question: str, turn: TurnFinished
    ) -> Conversation | None:
        """Save a finished turn. On failure, discard it from memory too and return None."""
        try:
            return self._save_turn(conversation, question, turn.new_messages)
        except Exception:
            logger.exception("Could not save the turn")
            # Reload the history from storage so memory and database agree; an unsaved answer
            # is never reported as done.
            self._agent = self._new_agent()
            return None

    def new_chat(self) -> None:
        self._ensure_idle()
        self._store.set_active_conversation(self._user_id, None)
        self.conversation = None
        self._agent = self._new_agent()

    def open(self, conversation_id: int) -> None:
        self._ensure_idle()
        self._store.set_active_conversation(self._user_id, conversation_id)
        self.conversation = self._store.get_conversation(self._user_id, conversation_id)
        self._agent = self._new_agent()

    def delete(self, conversation_id: int) -> None:
        self._ensure_idle()
        self._store.delete_conversation(self._user_id, conversation_id)
        if self.conversation and self.conversation.id == conversation_id:
            self.conversation = None
            self._agent = self._new_agent()

    def list_conversations(self) -> list[Conversation]:
        return self._store.list_conversations(self._user_id)

    def display_turns(self) -> list[DisplayTurn]:
        return to_display_turns(self._agent.messages)

    def _ensure_idle(self) -> None:
        if self._busy:
            raise SessionBusy()

    def _new_agent(self) -> ConversationAgent:
        messages = self._store.load_messages(self.conversation.id) if self.conversation else []
        return self._agent_factory(messages)

    def _save_turn(
        self, conversation: Conversation | None, question: str, messages: list[Message]
    ) -> Conversation:
        # A conversation is created by its first completed turn, so empty chats never reach
        # history.
        if conversation is None:
            conversation = self._store.create_conversation(self._user_id, question, self._model)
            self._store.set_active_conversation(self._user_id, conversation.id)
            self.conversation = conversation
        self._store.append_messages(conversation.id, messages)
        return conversation


def _log_turn(
    outcome: str,
    conversation: Conversation | None,
    started: float,
    tools_used: list[str],
    usage: Usage,
) -> None:
    logger.info(
        "turn %s: conversation=%s duration=%.1fs model_calls=%d tools=%s tokens(in=%d "
        "cache_read=%d cache_write=%d out=%d) cost=$%.3f",
        outcome,
        conversation.id if conversation else "new",
        time.monotonic() - started,
        usage.model_calls,
        ",".join(tools_used) or "none",
        usage.input_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
        usage.output_tokens,
        usage.cost_usd,
    )
