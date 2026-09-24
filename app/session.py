"""A user's chat session: the active conversation, backed by persistent storage.

Connects the Agent (which only knows an in-memory message list) to the ConversationStore.
Both the terminal client and the web API drive the app through this class.
"""

from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from typing import Any

from app.agent import Agent, Event, TurnFinished
from app.config import Settings
from app.storage import DEFAULT_USER_ID, Conversation, ConversationStore


@dataclass
class DisplayTurn:
    """One question and its answer, as shown to the user when a conversation is reopened."""
    question: str
    answer: str
    queries: list[dict[str, str]]  # {"purpose": ..., "query": ...} in the order they ran


class ChatSession:
    def __init__(self, settings: Settings, store: ConversationStore,
                 user_id: int = DEFAULT_USER_ID, agent_factory=Agent):
        self._settings = settings
        self._store = store
        self._user_id = user_id
        self._agent_factory = agent_factory  # replaceable in tests
        self.conversation = store.get_active_conversation(user_id)
        self._agent = self._new_agent()

    def ask(self, question: str) -> Iterator[Event]:
        """Run one turn; the turn is saved before TurnFinished is passed on.

        Closing this generator early (e.g. the client disconnected) closes the agent's turn
        too, which rolls it back; nothing is saved.
        """
        with closing(self._agent.ask(question)) as events:
            for event in events:
                if isinstance(event, TurnFinished):
                    self._save_turn(question, event.new_messages)
                yield event

    def new_chat(self) -> None:
        self._store.set_active_conversation(self._user_id, None)
        self.conversation = None
        self._agent = self._new_agent()

    def open(self, conversation_id: int) -> None:
        self._store.set_active_conversation(self._user_id, conversation_id)
        self.conversation = self._store.get_conversation(self._user_id, conversation_id)
        self._agent = self._new_agent()

    def delete(self, conversation_id: int) -> None:
        self._store.delete_conversation(self._user_id, conversation_id)
        if self.conversation and self.conversation.id == conversation_id:
            self.conversation = None
            self._agent = self._new_agent()

    def list_conversations(self) -> list[Conversation]:
        return self._store.list_conversations(self._user_id)

    def display_turns(self) -> list[DisplayTurn]:
        return to_display_turns(self._agent.messages)

    def _new_agent(self) -> Agent:
        messages = self._store.load_messages(self.conversation.id) if self.conversation else []
        return self._agent_factory(self._settings, messages)

    def _save_turn(self, question: str, messages: list[dict[str, Any]]) -> None:
        # A conversation is created by its first completed turn, so empty chats never reach history.
        if self.conversation is None:
            self.conversation = self._store.create_conversation(
                self._user_id, question, self._settings.model
            )
            self._store.set_active_conversation(self._user_id, self.conversation.id)
        self._store.append_messages(self.conversation.id, messages)


def to_display_turns(messages: list[dict[str, Any]]) -> list[DisplayTurn]:
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
            if block["type"] == "tool_use":
                turn.queries.append({"purpose": block["input"].get("purpose", ""),
                                     "query": block["input"].get("query", "")})
            elif block["type"] == "text":
                # Text written before a query and the final answer are separate blocks.
                turn.answer = f"{turn.answer}\n\n{block['text']}" if turn.answer else block["text"]
    return turns
