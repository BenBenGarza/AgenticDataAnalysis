import json

import pytest
from fastapi.testclient import TestClient

from app.api import MAX_QUESTION_CHARS, create_app
from app.config import Settings
from app.session import ChatSession
from app.storage import ConversationStore
from tests.fakes import FAILING_QUESTION, FakeAgent


@pytest.fixture
def client(tmp_path):
    store = ConversationStore(tmp_path / "app.db")
    session = ChatSession(Settings(gcp_project="test"), store, agent_factory=FakeAgent)
    with TestClient(create_app(session)) as client:
        yield client
    store.close()


def _chat(client: TestClient, question: str) -> list[tuple[str, dict]]:
    """POST a question and parse the Server-Sent Events stream into (event, data) pairs."""
    response = client.post("/api/chat", json={"question": question})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        name_line, data_line = block.split("\n")
        events.append((name_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))))
    return events


def test_empty_session_on_first_start(client):
    assert client.get("/api/session").json() == {"conversation": None, "turns": []}
    assert client.get("/api/conversations").json() == []


def test_chat_streams_events_in_order_and_saves_the_conversation(client):
    events = _chat(client, "Revenue by channel?")

    assert [name for name, _ in events] == ["query_started", "query_finished", "text", "done"]
    started, finished, text, done = (data for _, data in events)
    assert started == {"id": started["id"], "purpose": "check Revenue by channel?", "sql": "SELECT 1 AS x"}
    assert finished == {"id": started["id"], "columns": ["x"], "rows": [{"x": 1}],
                        "total_rows": 1, "truncated": False, "mb_scanned": 1.0}
    assert text == {"text": "answer to Revenue by channel?"}
    assert done["conversation"]["title"] == "Revenue by channel?"

    session = client.get("/api/session").json()
    assert session["conversation"]["id"] == done["conversation"]["id"]
    assert session["turns"] == [{"question": "Revenue by channel?", "answer": "answer to Revenue by channel?",
                                 "queries": [{"purpose": "check Revenue by channel?", "query": "SELECT 1 AS x"}]}]


def test_failed_turn_streams_an_error_and_saves_nothing(client):
    assert _chat(client, FAILING_QUESTION) == [("error", {"message": "boom"})]
    assert client.get("/api/conversations").json() == []


@pytest.mark.parametrize("question", ["", "   ", "x" * (MAX_QUESTION_CHARS + 1)])
def test_invalid_questions_are_rejected_before_any_work(client, question):
    assert client.post("/api/chat", json={"question": question}).status_code == 422
    assert client.get("/api/conversations").json() == []


def test_new_chat_open_and_history(client):
    first = _chat(client, "first")[-1][1]["conversation"]
    assert client.post("/api/session/new").status_code == 204
    assert client.get("/api/session").json() == {"conversation": None, "turns": []}
    second = _chat(client, "second")[-1][1]["conversation"]

    assert [c["id"] for c in client.get("/api/conversations").json()] == [second["id"], first["id"]]

    opened = client.post(f"/api/conversations/{first['id']}/open").json()
    assert opened["conversation"]["id"] == first["id"]
    assert [t["question"] for t in opened["turns"]] == ["first"]
    assert client.get("/api/session").json()["conversation"]["id"] == first["id"]


def test_delete_hides_the_conversation(client):
    conversation = _chat(client, "q")[-1][1]["conversation"]

    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204
    assert client.get("/api/conversations").json() == []
    assert client.get("/api/session").json()["conversation"] is None


@pytest.mark.parametrize("method, path", [
    ("post", "/api/conversations/999/open"),
    ("delete", "/api/conversations/999"),
])
def test_unknown_conversation_is_404(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation 999 not found"}
