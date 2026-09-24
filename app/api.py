"""HTTP API over ChatSession.

A thin layer: request validation, JSON shapes, and streaming agent events to the browser as
Server-Sent Events. All chat logic lives in ChatSession / Agent. Single user, one question at a
time (see README), so one ChatSession serves every request.
"""

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from app.agent import (
    Event, ProgressDelta, TextDelta, ToolFinished, ToolStarted, TurnFailed, TurnFinished,
)
from app.session import ChatSession
from app.storage import Conversation, ConversationNotFound

MAX_QUESTION_CHARS = 4_000


class ChatRequest(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                               max_length=MAX_QUESTION_CHARS)]


def create_app(session: ChatSession) -> FastAPI:
    app = FastAPI(title="GA4 Analysis Agent")

    @app.exception_handler(ConversationNotFound)
    async def conversation_not_found(request: Request, exc: ConversationNotFound) -> JSONResponse:
        return JSONResponse({"detail": f"Conversation {exc} not found"}, status_code=404)

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

    return app


async def _stream(events: Iterator[Event], session: ChatSession) -> AsyncIterator[str]:
    """Pull events in a worker thread (the agent is blocking code) and format them as SSE.

    Starlette's own iterate_in_threadpool never closes the iterator, so we close it
    ourselves: if the client disconnects mid-turn, closing it rolls the turn back.
    """
    try:
        while (event := await run_in_threadpool(next, events, None)) is not None:
            yield _to_sse(event, session)
    finally:
        events.close()


def _to_sse(event: Event, session: ChatSession) -> str:
    match event:
        case TextDelta(text=text):
            return _sse("text", {"text": text})
        case ProgressDelta(text=text):
            return _sse("progress", {"text": text})
        case ToolStarted(tool_use_id=id_, purpose=purpose, query=query):
            return _sse("query_started", {"id": id_, "purpose": purpose, "sql": query})
        case ToolFinished(tool_use_id=id_, error=str() as error):
            return _sse("query_finished", {"id": id_, "error": error})
        case ToolFinished(tool_use_id=id_, result=result):
            return _sse("query_finished", {
                "id": id_,
                "columns": result.columns,
                "rows": result.rows,
                "total_rows": result.total_rows,
                "truncated": result.truncated,
                "mb_scanned": round(result.bytes_processed / 1024**2, 1),
            })
        case TurnFinished(usage=usage):
            # ChatSession saved the turn before passing this on, so the conversation exists.
            return _sse("done", {"conversation": _conversation_json(session.conversation),
                                 "usage": asdict(usage)})
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
    return {"id": conversation.id, "title": conversation.title,
            "created_at": conversation.created_at, "updated_at": conversation.updated_at}
