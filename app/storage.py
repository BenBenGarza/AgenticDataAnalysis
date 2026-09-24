"""Persistent conversation history in SQLite.

Stores each conversation's API message history exactly as sent to the model, so a
conversation can be reloaded after a restart and continued. Histories are append-only:
completed turns are added, earlier messages are never rewritten (the API ties thinking
blocks to the exact earlier conversation, and prompt caching relies on the same prefix).

Nothing is ever physically deleted. Deleting a conversation sets deleted_at, which hides
it everywhere; the foreign keys have no ON DELETE actions, so an accidental hard DELETE
of a conversation that has messages is rejected by the database.

Single-user today: a default user is created on first start. Every query is still scoped
by user_id so adding users later needs no schema change.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_USER_ID = 1
TITLE_MAX_CHARS = 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                      INTEGER PRIMARY KEY,
    name                    TEXT NOT NULL,
    active_conversation_id  INTEGER REFERENCES conversations(id),
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    title       TEXT NOT NULL,
    model       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT            -- NULL while visible; set instead of deleting the row
);
CREATE INDEX IF NOT EXISTS conversations_by_user ON conversations(user_id, updated_at);

CREATE TABLE IF NOT EXISTS messages (
    conversation_id  INTEGER NOT NULL REFERENCES conversations(id),
    position         INTEGER NOT NULL,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content_json     TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (conversation_id, position)
);
"""

Message = dict[str, Any]  # {"role": ..., "content": ...} as sent to the Messages API


@dataclass(frozen=True)
class Conversation:
    id: int
    title: str
    model: str
    created_at: str
    updated_at: str


class ConversationNotFound(Exception):
    pass


class ConversationStore:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The app handles one request at a time, but the web server may run it on a
        # different thread than the one that opened the connection.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite
        with self._db:
            self._db.executescript(SCHEMA)
            self._db.execute(
                "INSERT OR IGNORE INTO users (id, name, created_at) VALUES (?, 'default', ?)",
                (DEFAULT_USER_ID, _now()),
            )

    def close(self) -> None:
        self._db.close()

    # ---- Conversations -------------------------------------------------------------

    def create_conversation(self, user_id: int, title: str, model: str) -> Conversation:
        now = _now()
        title = _make_title(title)
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO conversations (user_id, title, model, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, title, model, now, now),
            )
        return Conversation(cursor.lastrowid, title, model, now, now)

    def get_conversation(self, user_id: int, conversation_id: int) -> Conversation:
        row = self._db.execute(
            "SELECT id, title, model, created_at, updated_at FROM conversations "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (conversation_id, user_id),
        ).fetchone()
        if row is None:
            raise ConversationNotFound(conversation_id)
        return Conversation(**row)

    def list_conversations(self, user_id: int) -> list[Conversation]:
        rows = self._db.execute(
            "SELECT id, title, model, created_at, updated_at FROM conversations "
            "WHERE user_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        return [Conversation(**row) for row in rows]

    def delete_conversation(self, user_id: int, conversation_id: int) -> None:
        """Soft delete: hide the conversation from every read; its rows stay in the database."""
        with self._db:
            cursor = self._db.execute(
                "UPDATE conversations SET deleted_at = ? "
                "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
                (_now(), conversation_id, user_id),
            )
            if cursor.rowcount == 0:
                raise ConversationNotFound(conversation_id)  # rolls back the transaction
            self._db.execute(
                "UPDATE users SET active_conversation_id = NULL "
                "WHERE id = ? AND active_conversation_id = ?",
                (user_id, conversation_id),
            )

    # ---- Active conversation -------------------------------------------------------

    def get_active_conversation(self, user_id: int) -> Conversation | None:
        row = self._db.execute(
            "SELECT active_conversation_id FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None or row["active_conversation_id"] is None:
            return None
        return self.get_conversation(user_id, row["active_conversation_id"])

    def set_active_conversation(self, user_id: int, conversation_id: int | None) -> None:
        """Make a conversation active, or pass None to start a new chat."""
        if conversation_id is not None:
            self.get_conversation(user_id, conversation_id)  # must exist and belong to the user
        with self._db:
            self._db.execute(
                "UPDATE users SET active_conversation_id = ? WHERE id = ?",
                (conversation_id, user_id),
            )

    # ---- Messages ------------------------------------------------------------------

    def append_messages(self, conversation_id: int, messages: list[Message]) -> None:
        """Append one completed turn's messages, atomically."""
        now = _now()
        with self._db:
            next_position = self._db.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            self._db.executemany(
                "INSERT INTO messages (conversation_id, position, role, content_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(conversation_id, next_position + i, m["role"], json.dumps(m["content"]), now)
                 for i, m in enumerate(messages)],
            )
            self._db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
            )

    def load_messages(self, conversation_id: int) -> list[Message]:
        rows = self._db.execute(
            "SELECT role, content_json FROM messages WHERE conversation_id = ? ORDER BY position",
            (conversation_id,),
        ).fetchall()
        return [{"role": row["role"], "content": json.loads(row["content_json"])} for row in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _make_title(first_question: str) -> str:
    title = " ".join(first_question.split())  # collapse newlines and repeated spaces
    return title if len(title) <= TITLE_MAX_CHARS else title[: TITLE_MAX_CHARS - 1] + "…"
