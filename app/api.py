"""HTTP API over ChatSession.

A thin layer: request validation, JSON shapes, and streaming agent events to the browser as
Server-Sent Events. All chat logic lives in ChatSession / Agent. Single user, one question at a
time (see README), so one ChatSession serves every request.
"""

import json
from collections.abc import AsyncIterator, Generator
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from app.bigquery import QueryResult
from app.events import (
    Event,
    ProgressDelta,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnFailed,
    TurnFinished,
)
from app.session import ChatSession, SessionBusy
from app.storage import Conversation, ConversationNotFound
from app.tools import create_chart, run_sql

MAX_QUESTION_CHARS = 4_000
STATIC_DIR = Path(__file__).resolve().parent / "static"


class ChatRequest(BaseModel):
    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
    ]


def create_app(session: ChatSession) -> FastAPI:
    app = FastAPI(title="GA4 Analysis Agent")

    @app.exception_handler(ConversationNotFound)
    async def conversation_not_found(request: Request, exc: ConversationNotFound) -> JSONResponse:
        return JSONResponse({"detail": f"Conversation {exc} not found"}, status_code=404)

    @app.exception_handler(SessionBusy)
    async def session_busy(request: Request, exc: SessionBusy) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/session")
    def get_session() -> dict[str, Any]:
        return _session_view(session)

    @app.post("/api/chat")
    def chat(request: ChatRequest) -> StreamingResponse:
        return StreamingResponse(
            _stream(session.ask(request.question), session),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.post("/api/session/new", status_code=204)
    def new_chat() -> Response:
        session.new_chat()
        return Response(status_code=204)

    @app.get("/api/conversations")
    def list_conversations() -> list[dict[str, Any]]:
        return [_conversation_json(c) for c in session.list_conversations()]

    @app.post("/api/conversations/{conversation_id}/open")
    def open_conversation(conversation_id: int) -> dict[str, Any]:
        session.open(conversation_id)
        return _session_view(session)

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: int) -> Response:
        session.delete(conversation_id)
        return Response(status_code=204)

    # The chat UI. Mounted last so the /api routes above take precedence.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app


async def _stream(events: Generator[Event, None, None], session: ChatSession) -> AsyncIterator[str]:
    """Pull events in a worker thread (the agent is blocking code) and format them as SSE.

    Starlette's own iterate_in_threadpool never closes the iterator, so we close it
    ourselves: if the client disconnects mid-turn, closing it rolls the turn back.
    """
    try:
        while (event := await run_in_threadpool(next, events, None)) is not None:
            if (message := _to_sse(event, session)) is not None:
                yield message
    finally:
        events.close()


def _to_sse(event: Event, session: ChatSession) -> str | None:
    """Format an event for the browser, or None for events the UI doesn't show."""
    match event:
        case TextDelta(text=text):
            return _sse("text", {"text": text})
        case ProgressDelta(text=text):
            return _sse("progress", {"text": text})
        case ToolStarted(tool_use_id=id_, name=run_sql.NAME, input=tool_input):
            return _sse(
                "query_started",
                {
                    "id": id_,
                    "purpose": tool_input.get("purpose", ""),
                    "sql": tool_input.get("query", ""),
                },
            )
        case ToolFinished(tool_use_id=id_, name=run_sql.NAME, error=str() as error):
            return _sse("query_finished", {"id": id_, "error": error})
        case ToolFinished(tool_use_id=id_, name=run_sql.NAME, output=QueryResult() as result):
            return _sse("query_finished", {"id": id_, **run_sql.result_json(result, id_)})
        case ToolFinished(tool_use_id=id_, output=create_chart.Chart() as chart):
            return _sse("chart", {"id": id_, "chart": asdict(chart)})
        case ToolStarted() | ToolFinished():
            # Starting a chart, or a chart request the model got wrong and will retry.
            return None
        case TurnFinished(usage=usage) if session.conversation is not None:
            # ChatSession saved the turn before passing this on, so the conversation exists.
            return _sse(
                "done",
                {"conversation": _conversation_json(session.conversation), "usage": asdict(usage)},
            )
        case TurnFailed(message=message):
            return _sse("error", {"message": message})
    raise TypeError(f"Unhandled event: {event!r}")


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _session_view(session: ChatSession) -> dict[str, Any]:
    conversation = session.conversation
    return {
        "conversation": _conversation_json(conversation) if conversation else None,
        "turns": [asdict(turn) for turn in session.display_turns()],
    }


def _conversation_json(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }
