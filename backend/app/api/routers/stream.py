"""Server-Sent Events stream for live job/status updates."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import current_user
from app.services import events

router = APIRouter(tags=["stream"])


@router.get("/events/stream", dependencies=[Depends(current_user)])
async def stream(request: Request) -> EventSourceResponse:
    q = events.subscribe()

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    # Poll the thread-safe queue without blocking the event loop.
                    payload = await asyncio.to_thread(q.get, True, 15.0)
                    yield {"event": "update", "data": payload}
                except Exception:
                    # Timeout → heartbeat keeps the connection alive.
                    yield {"event": "ping", "data": "{}"}
        finally:
            events.unsubscribe(q)

    return EventSourceResponse(gen())
