import json
import sqlite3

import pytest

from app.events import ToolStarted, TurnFailed, TurnFinished
from app.session import (
    BUSY_MESSAGE,
    SAVE_FAILED_MESSAGE,
    ChartBlock,
    ChatSession,
    DisplayTurn,
    SessionBusy,
    TextBlock,
    to_display_turns,
)
from app.storage import ConversationStore
from tests.fakes import FAILING_QUESTION, FakeAgent

MODEL = "claude-opus-5-5"


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(tmp_path / "app.db")
    yield store
    store.close()


def _ask(session: ChatSession, question: str) -> list:
    return list(session.ask(question))


def test_first_completed_turn_creates_and_activates_the_conversation(store):
    session = ChatSession(store, FakeAgent, MODEL)
    assert session.conversation is None

    _ask(session, "Revenue by channel?")

    assert session.conversation.title == "Revenue by channel?"
    assert store.get_active_conversation(1).id == session.conversation.id
    assert len(store.load_messages(session.conversation.id)) == 4


def test_failed_turn_saves_nothing(store):
    session = ChatSession(store, FakeAgent, MODEL)

    _ask(session, FAILING_QUESTION)

    assert session.conversation is None
    assert store.list_conversations(1) == []


def test_restart_continues_the_active_conversation(store):
    first_run = ChatSession(store, FakeAgent, MODEL)
    _ask(first_run, "Q1")
    _ask(first_run, "Q2")

    restarted = ChatSession(store, FakeAgent, MODEL)
    assert restarted.conversation.id == first_run.conversation.id
    assert [t.question for t in restarted.display_turns()] == ["Q1", "Q2"]

    _ask(restarted, "Q3")
    assert len(store.load_messages(restarted.conversation.id)) == 12
    assert len(store.list_conversations(1)) == 1


def test_new_chat_then_open_switches_conversations(store):
    session = ChatSession(store, FakeAgent, MODEL)
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
    session = ChatSession(store, FakeAgent, MODEL)
    _ask(session, "q")

    session.delete(session.conversation.id)

    assert session.conversation is None
    assert session.display_turns() == []
    assert store.list_conversations(1) == []


def test_conversation_records_the_model(store):
    session = ChatSession(store, FakeAgent, "some-model")
    _ask(session, "q")
    assert session.conversation.model == "some-model"


def test_abandoning_a_turn_midway_saves_nothing(store):
    # e.g. the browser disconnects while the answer is streaming
    session = ChatSession(store, FakeAgent, MODEL)
    events = session.ask("q")
    assert isinstance(next(events), ToolStarted)  # the turn is under way
    events.close()

    assert session.conversation is None
    assert store.list_conversations(1) == []


def test_session_cannot_change_while_an_answer_is_in_progress(store):
    session = ChatSession(store, FakeAgent, MODEL)
    _ask(session, "first")
    first_id = session.conversation.id
    session.new_chat()

    turn = session.ask("second")
    next(turn)  # the answer has started

    for change in (
        lambda: session.open(first_id),
        session.new_chat,
        lambda: session.delete(first_id),
        lambda: session.ask("another"),
    ):
        with pytest.raises(SessionBusy):
            change()

    list(turn)  # the answer finishes...
    session.open(first_id)  # ...and the session can change again
    assert session.conversation.id == first_id


def test_two_turns_started_together_only_one_runs(store):
    session = ChatSession(store, FakeAgent, MODEL)
    first = session.ask("first")  # both pass the up-front check before either starts
    second = session.ask("second")

    next(first)
    assert list(second) == [TurnFailed(BUSY_MESSAGE)]
    assert isinstance(list(first)[-1], TurnFinished)


@pytest.mark.parametrize("question", ["normal", FAILING_QUESTION])
def test_session_is_free_again_after_a_turn_ends_or_is_abandoned(store, question):
    session = ChatSession(store, FakeAgent, MODEL)

    _ask(session, question)
    session.new_chat()  # would raise if still busy

    abandoned = session.ask("q")
    next(abandoned)
    abandoned.close()
    session.new_chat()


def test_a_turn_that_cannot_be_saved_is_reported_and_discarded(store, monkeypatch):
    session = ChatSession(store, FakeAgent, MODEL)
    _ask(session, "saved")

    def disk_full(*args):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(store, "append_messages", disk_full)
    events = _ask(session, "not saved")

    assert events[-1] == TurnFailed(SAVE_FAILED_MESSAGE)
    assert [t.question for t in session.display_turns()] == ["saved"]  # memory matches storage

    monkeypatch.undo()
    _ask(session, "saved again")  # the session keeps working
    assert [t.question for t in session.display_turns()] == ["saved", "saved again"]


def test_display_turns_rebuild_text_queries_and_charts_in_order():
    rows = [{"day": "2020-12-01", "revenue": 10.0}, {"day": "2020-12-02", "revenue": 12.5}]
    result = json.dumps({"result_id": "t1", "columns": ["day", "revenue"], "rows": rows})
    chart_input = {
        "query_id": "t1",
        "type": "line",
        "title": "Revenue",
        "x": "day",
        "y": ["revenue"],
    }
    messages = [
        {"role": "user", "content": "Q1"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "run_sql",
                    "input": {"query": "SELECT 1", "purpose": "p1"},
                },
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "run_sql",
                    "input": {"query": "SELECT bad", "purpose": "p2"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": result},
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": "Invalid SQL",
                    "is_error": True,
                },
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t3", "name": "create_chart", "input": chart_input},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t3", "content": "Chart shown"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": "Final answer."},
            ],
        },
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": [{"type": "text", "text": "Direct answer."}]},
    ]

    first, second = to_display_turns(messages)

    assert first.question == "Q1"
    text, query, failed_query, chart, answer = first.blocks
    assert text == TextBlock("Let me check.")
    assert (query.purpose, query.sql, query.result["rows"], query.error) == (
        "p1",
        "SELECT 1",
        rows,
        None,
    )
    assert (failed_query.result, failed_query.error) == (None, "Invalid SQL")
    assert isinstance(chart, ChartBlock)
    assert chart.chart.labels == ["2020-12-01", "2020-12-02"]
    assert chart.chart.series[0].values == [10.0, 12.5]
    assert answer == TextBlock("Final answer.")
    assert second == DisplayTurn("Q2", [TextBlock("Direct answer.")])


def test_display_skips_charts_that_failed():
    messages = [
        {"role": "user", "content": "Q"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "create_chart",
                    "input": {
                        "query_id": "missing",
                        "type": "bar",
                        "title": "t",
                        "x": "a",
                        "y": ["b"],
                    },
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "No result",
                    "is_error": True,
                },
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Answer."}]},
    ]
    assert to_display_turns(messages)[0].blocks == [TextBlock("Answer.")]
