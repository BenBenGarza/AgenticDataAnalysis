import json

import pytest
from fastapi.testclient import TestClient

from app.api import MAX_QUESTION_CHARS, STREAM_FAILED_MESSAGE, create_app
from app.session import ChatSession
from app.storage import ConversationStore
from tests.fakes import CHART_QUESTION, CRASHING_QUESTION, FAILING_QUESTION, FakeAgent


@pytest.fixture
def session(tmp_path):
    store = ConversationStore(tmp_path / "app.db")
    yield ChatSession(store, FakeAgent, "test-model")
    store.close()


@pytest.fixture
def client(session):
    with TestClient(create_app(session)) as client:
        yield client


def _chat(client: TestClient, question: str) -> list[tuple[str, dict]]:
    """POST a question and parse the Server-Sent Events stream into (event, data) pairs."""
    response = client.post("/api/chat", json={"question": question})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        name_line, data_line = block.split("\n")
        events.append(
            (name_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: ")))
        )
    return events


def test_empty_session_on_first_start(client):
    assert client.get("/api/session").json() == {"conversation": None, "turns": []}
    assert client.get("/api/conversations").json() == []


def test_chat_streams_events_in_order_and_saves_the_conversation(client):
    events = _chat(client, "Revenue by channel?")

    assert [name for name, _ in events] == ["query_started", "query_finished", "text", "done"]
    started, finished, text, done = (data for _, data in events)
    rows = [{"day": "d1", "revenue": 1.0}, {"day": "d2", "revenue": 2.0}]
    assert started == {
        "id": started["id"],
        "purpose": "check Revenue by channel?",
        "sql": "SELECT day, revenue",
    }
    assert finished == {
        "id": started["id"],
        "result_id": started["id"],
        "columns": ["day", "revenue"],
        "rows": rows,
        "rows_returned": 2,
        "total_rows": 2,
        "truncated": False,
        "mb_scanned": 1.0,
    }
    assert text == {"text": "answer to Revenue by channel?"}
    assert done["conversation"]["title"] == "Revenue by channel?"

    # Reopening shows the same query (with its rows) and answer, rebuilt from the history.
    session = client.get("/api/session").json()
    assert session["conversation"]["id"] == done["conversation"]["id"]
    (turn,) = session["turns"]
    query, answer = turn["blocks"]
    assert (query["type"], query["sql"], query["result"]["rows"]) == (
        "query",
        "SELECT day, revenue",
        rows,
    )
    assert answer == {"type": "text", "text": "answer to Revenue by channel?"}


def test_chart_is_streamed_with_its_data_and_rebuilt_when_reopened(client):
    events = _chat(client, CHART_QUESTION)

    assert [name for name, _ in events] == [
        "query_started",
        "query_finished",
        "chart",
        "text",
        "done",
    ]
    chart_event = events[2][1]
    expected_chart = {
        "type": "bar",
        "title": "Revenue",
        "labels": ["d1", "d2"],
        "series": [{"name": "revenue", "values": [1.0, 2.0]}],
        "value_format": "number",
        "y_label": None,
    }
    assert chart_event["chart"] == expected_chart

    (turn,) = client.get("/api/session").json()["turns"]
    assert [block["type"] for block in turn["blocks"]] == ["query", "chart", "text"]
    assert turn["blocks"][1] == {"type": "chart", "id": chart_event["id"], "chart": expected_chart}


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


@pytest.mark.parametrize(
    "method, path",
    [
        ("post", "/api/conversations/999/open"),
        ("delete", "/api/conversations/999"),
    ],
)
def test_unknown_conversation_is_404(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation 999 not found"}


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("post", "/api/chat", {"question": "another"}),
        ("post", "/api/session/new", None),
        ("post", "/api/conversations/1/open", None),
        ("delete", "/api/conversations/1", None),
    ],
)
def test_changes_are_refused_with_409_while_an_answer_is_in_progress(
    client, session, method, path, body
):
    _chat(client, "first")
    answer_in_progress = session.ask("second")  # e.g. streaming to another browser tab
    next(answer_in_progress)

    response = client.request(method.upper(), path, json=body)

    assert response.status_code == 409
    assert "An answer is in progress" in response.json()["detail"]
    answer_in_progress.close()


def test_unexpected_error_mid_stream_reaches_the_browser_as_an_error_event(client):
    assert _chat(client, CRASHING_QUESTION) == [("error", {"message": STREAM_FAILED_MESSAGE})]
    assert client.post("/api/session/new").status_code == 204  # the session was released


def test_health_check(client):
    assert client.get("/api/health").json() == {"status": "ok"}
