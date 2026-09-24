import itertools
import sqlite3

import pytest

from app import storage
from app.storage import DEFAULT_USER_ID, ConversationNotFound, ConversationStore

USER = DEFAULT_USER_ID
MODEL = "claude-opus-5-5"


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(tmp_path / "app.db")
    yield store
    store.close()


def _add_user(store: ConversationStore, user_id: int) -> None:
    with store._db:
        store._db.execute(
            "INSERT INTO users (id, name, created_at) VALUES (?, 'other', '2026-01-01')", (user_id,)
        )


def test_default_user_is_created_once(tmp_path):
    path = tmp_path / "app.db"
    ConversationStore(path).close()
    store = ConversationStore(path)  # reopening must not insert a second user
    users = [tuple(row) for row in store._db.execute("SELECT id, name FROM users")]
    assert users == [(DEFAULT_USER_ID, "default")]
    store.close()


def test_messages_round_trip_unchanged_and_in_order(store):
    conversation = store.create_conversation(USER, "Which channels drove revenue?", MODEL)
    turn_1 = [
        {"role": "user", "content": "Which channels drove revenue?"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "abc=="},
            {"type": "tool_use", "id": "toolu_1", "name": "run_sql",
             "input": {"query": "SELECT 1", "purpose": "check"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"rows": [{"x": 1}]}'},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "Organic search — 15% of revenue"}]},
    ]
    turn_2 = [
        {"role": "user", "content": "And in November?"},
        {"role": "assistant", "content": [{"type": "text", "text": "Similar."}]},
    ]

    store.append_messages(conversation.id, turn_1)
    store.append_messages(conversation.id, turn_2)

    assert store.load_messages(conversation.id) == turn_1 + turn_2


def test_failed_append_writes_nothing(store):
    conversation = store.create_conversation(USER, "q", MODEL)
    store.append_messages(conversation.id, [{"role": "user", "content": "q"}])

    with pytest.raises(sqlite3.IntegrityError):  # 'system' violates the role CHECK
        store.append_messages(conversation.id, [
            {"role": "assistant", "content": "partial"},
            {"role": "system", "content": "invalid"},
        ])

    assert store.load_messages(conversation.id) == [{"role": "user", "content": "q"}]


def test_history_is_newest_first_and_updated_by_new_messages(store, monkeypatch):
    # A clock that advances one second per call, so ordering doesn't depend on real timing.
    ticks = itertools.count()
    monkeypatch.setattr(storage, "_now", lambda: f"2026-01-01T00:00:{next(ticks):02d}.000+00:00")

    first = store.create_conversation(USER, "first", MODEL)
    second = store.create_conversation(USER, "second", MODEL)
    assert [c.id for c in store.list_conversations(USER)] == [second.id, first.id]

    store.append_messages(first.id, [{"role": "user", "content": "follow-up"}])
    assert [c.id for c in store.list_conversations(USER)] == [first.id, second.id]


def test_title_is_single_line_and_truncated(store):
    assert store.create_conversation(USER, "  Revenue\n by   channel ", MODEL).title == "Revenue by channel"
    long_title = store.create_conversation(USER, "x" * 200, MODEL).title
    assert len(long_title) == 80 and long_title.endswith("…")


def test_active_conversation_lifecycle(store):
    assert store.get_active_conversation(USER) is None

    conversation = store.create_conversation(USER, "q", MODEL)
    store.set_active_conversation(USER, conversation.id)
    assert store.get_active_conversation(USER) == conversation

    store.set_active_conversation(USER, None)  # "new chat"
    assert store.get_active_conversation(USER) is None


def test_delete_hides_the_conversation_but_keeps_all_data(store):
    conversation = store.create_conversation(USER, "q", MODEL)
    store.append_messages(conversation.id, [{"role": "user", "content": "q"}])
    store.set_active_conversation(USER, conversation.id)

    store.delete_conversation(USER, conversation.id)

    # Hidden everywhere...
    assert store.list_conversations(USER) == []
    assert store.get_active_conversation(USER) is None
    with pytest.raises(ConversationNotFound):
        store.get_conversation(USER, conversation.id)
    with pytest.raises(ConversationNotFound):
        store.set_active_conversation(USER, conversation.id)
    with pytest.raises(ConversationNotFound):
        store.delete_conversation(USER, conversation.id)

    # ...but nothing was removed from the database.
    deleted_at = store._db.execute(
        "SELECT deleted_at FROM conversations WHERE id = ?", (conversation.id,)
    ).fetchone()[0]
    assert deleted_at is not None
    assert store.load_messages(conversation.id) == [{"role": "user", "content": "q"}]


def test_deleting_one_conversation_keeps_another_active(store):
    kept = store.create_conversation(USER, "kept", MODEL)
    deleted = store.create_conversation(USER, "deleted", MODEL)
    store.set_active_conversation(USER, kept.id)

    store.delete_conversation(USER, deleted.id)

    assert store.get_active_conversation(USER).id == kept.id


def test_database_rejects_a_hard_delete_that_would_orphan_messages(store):
    conversation = store.create_conversation(USER, "q", MODEL)
    store.append_messages(conversation.id, [{"role": "user", "content": "q"}])

    with pytest.raises(sqlite3.IntegrityError):
        with store._db:
            store._db.execute("DELETE FROM conversations WHERE id = ?", (conversation.id,))


def test_conversations_are_scoped_to_their_user(store):
    _add_user(store, 2)
    theirs = store.create_conversation(2, "not yours", MODEL)

    assert store.list_conversations(USER) == []
    with pytest.raises(ConversationNotFound):
        store.get_conversation(USER, theirs.id)
    with pytest.raises(ConversationNotFound):
        store.set_active_conversation(USER, theirs.id)
    with pytest.raises(ConversationNotFound):
        store.delete_conversation(USER, theirs.id)


def test_everything_survives_a_restart(tmp_path):
    path = tmp_path / "app.db"
    store = ConversationStore(path)
    conversation = store.create_conversation(USER, "q", MODEL)
    store.append_messages(conversation.id, [{"role": "user", "content": "q"}])
    store.set_active_conversation(USER, conversation.id)
    store.close()

    reopened = ConversationStore(path)
    assert reopened.get_active_conversation(USER).id == conversation.id
    assert reopened.load_messages(conversation.id) == [{"role": "user", "content": "q"}]
    reopened.close()
