from dataclasses import replace

import pytest

from app.agent import TurnFailed, TurnFinished, Usage
from app.config import Settings
from app.session import ChatSession, DisplayTurn, to_display_turns
from app.storage import ConversationStore

SETTINGS = Settings(gcp_project="test-project")


class FakeAgent:
    """Answers every question with one query and a text answer, like a real turn."""

    fail_next = False

    def __init__(self, settings, messages):
        self.messages = list(messages)

    def ask(self, question):
        if FakeAgent.fail_next:
            FakeAgent.fail_next = False
            yield TurnFailed("boom")
            return
        n = len(self.messages)
        new = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "", "signature": f"sig{n}"},
                {"type": "tool_use", "id": f"toolu_{n}", "name": "run_sql",
                 "input": {"query": "SELECT 1", "purpose": f"check {question}"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"toolu_{n}", "content": "{}"},
            ]},
            {"role": "assistant", "content": [{"type": "text", "text": f"answer to {question}"}]},
        ]
        self.messages.extend(new)
        yield TurnFinished(Usage(), new)


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(tmp_path / "app.db")
    yield store
    store.close()


def _ask(session: ChatSession, question: str) -> list:
    return list(session.ask(question))


def test_first_completed_turn_creates_and_activates_the_conversation(store):
    session = ChatSession(SETTINGS, store, agent_factory=FakeAgent)
    assert session.conversation is None

    _ask(session, "Revenue by channel?")

    assert session.conversation.title == "Revenue by channel?"
    assert store.get_active_conversation(1).id == session.conversation.id
    assert len(store.load_messages(session.conversation.id)) == 4


def test_failed_turn_saves_nothing(store):
    session = ChatSession(SETTINGS, store, agent_factory=FakeAgent)
    FakeAgent.fail_next = True

    _ask(session, "This fails")

    assert session.conversation is None
    assert store.list_conversations(1) == []


def test_restart_continues_the_active_conversation(store):
    first_run = ChatSession(SETTINGS, store, agent_factory=FakeAgent)
    _ask(first_run, "Q1")
    _ask(first_run, "Q2")

    restarted = ChatSession(SETTINGS, store, agent_factory=FakeAgent)
    assert restarted.conversation.id == first_run.conversation.id
    assert [t.question for t in restarted.display_turns()] == ["Q1", "Q2"]

    _ask(restarted, "Q3")
    assert len(store.load_messages(restarted.conversation.id)) == 12
    assert len(store.list_conversations(1)) == 1


def test_new_chat_then_open_switches_conversations(store):
    session = ChatSession(SETTINGS, store, agent_factory=FakeAgent)
    _ask(session, "first topic")
    first_id = session.conversation.id

    session.new_chat()
    assert session.conversation is None and session.display_turns() == []
    _ask(session, "second topic")
    assert session.conversation.id != first_id

    session.open(first_id)
    assert [t.question for t in session.display_turns()] == ["first topic"]
    assert store.get_active_conversation(1).id == first_id


def test_deleting_the_open_conversation_starts_a_new_chat(store):
    session = ChatSession(SETTINGS, store, agent_factory=FakeAgent)
    _ask(session, "q")

    session.delete(session.conversation.id)

    assert session.conversation is None
    assert session.display_turns() == []
    assert store.list_conversations(1) == []


def test_conversation_records_the_model(store):
    session = ChatSession(replace(SETTINGS, model="claude-opus-5-5"), store, agent_factory=FakeAgent)
    _ask(session, "q")
    assert session.conversation.model == "claude-opus-5-5"


def test_display_turns_rebuilds_questions_queries_and_answers():
    messages = [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "t1", "name": "run_sql", "input": {"query": "SELECT 1", "purpose": "p1"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": "Final answer."},
        ]},
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": [{"type": "text", "text": "Direct answer."}]},
    ]

    assert to_display_turns(messages) == [
        DisplayTurn("Q1", "Let me check.\n\nFinal answer.", [{"purpose": "p1", "query": "SELECT 1"}]),
        DisplayTurn("Q2", "Direct answer.", []),
    ]
