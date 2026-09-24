"""A user's chat session: the active conversation, backed by persistent storage.

Connects the Agent (which only knows an in-memory message list) to the ConversationStore.
Both the terminal client and the web API drive the app through this class.

One answer at a time: while a turn is running, the session refuses anything that would change
it (another question, switching, a new chat, deleting). Several browser tabs share this one
session, so the rule has to be enforced here rather than trusted to each client.
"""

import threading
from collections.abc import Callable, Generator
from contextlib import closing
from dataclasses import dataclass
from typing import Protocol

from app.events import Event, Message, TurnFailed, TurnFinished
from app.storage import DEFAULT_USER_ID, Conversation, ConversationStore
from app.tools import run_sql


class ConversationAgent(Protocol):
    """What the session needs from an agent (app.agent.Agent in production, fakes in tests)."""

    messages: list[Message]

    def ask(self, question: str) -> Generator[Event, None, None]: ...


# Builds an agent that continues from the given message history.
AgentFactory = Callable[[list[Message]], ConversationAgent]

BUSY_MESSAGE = "An answer is in progress. Wait for it to finish or press Stop."


class SessionBusy(Exception):
    """The session can't change while an answer is in progress."""

    def __init__(self) -> None:
        super().__init__(BUSY_MESSAGE)


@dataclass
class DisplayTurn:
    """One question and its answer, as shown to the user when a conversation is reopened."""

    question: str
    answer: str
    queries: list[dict[str, str]]  # {"purpose": ..., "query": ...} in the order they ran


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
        # Claim the session when the turn actually starts. Checked again here because two
        # requests can both pass the check in ask() before either starts streaming.
        with self._busy_lock:
            if self._busy:
                yield TurnFailed(BUSY_MESSAGE)
                return
            self._busy = True
        try:
            conversation = self.conversation  # save to where the turn started, whatever happens
            with closing(self._agent.ask(question)) as events:
                for event in events:
                    if isinstance(event, TurnFinished):
                        conversation = self._save_turn(conversation, question, event.new_messages)
                    yield event
        finally:
            self._busy = False

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


def to_display_turns(messages: list[Message]) -> list[DisplayTurn]:
    """Rebuild the user-facing view from the stored API history (the single source of truth).

    A turn starts at a user message whose content is a plain string (the question); user
    messages carrying tool results belong to the turn in progress.
    """
    turns: list[DisplayTurn] = []
    for message in messages:
        content = message["content"]
        if message["role"] == "user":
            if isinstance(content, str):
                turns.append(DisplayTurn(question=content, answer="", queries=[]))
            continue
        turn = turns[-1]
        for block in content:
            if block["type"] == "tool_use" and block["name"] == run_sql.NAME:
                turn.queries.append(
                    {
                        "purpose": block["input"].get("purpose", ""),
                        "query": block["input"].get("query", ""),
                    }
                )
            elif block["type"] == "text":
                # Text written before a query and the final answer are separate blocks.
                turn.answer = "\n\n".join(filter(None, [turn.answer, block["text"]]))
    return turns
