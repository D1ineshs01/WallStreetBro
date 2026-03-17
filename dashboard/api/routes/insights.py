"""
SSE (Server-Sent Events) endpoint for streaming Grok market intelligence
and dashboard state updates to the Streamlit frontend.
"""

import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from core.redis_client import ALL_MARKET_CHANNELS, CHANNEL_KILL_SWITCH

router = APIRouter()


@router.get("/stream")
async def stream_insights(request: Request):
    """
    SSE endpoint. Subscribes to all market_events:* Redis channels
    plus the kill switch channel and dashboard state updates.
    Yields each message as a Server-Sent Event.

    Clients consume this with:
        const evtSource = new EventSource('/api/v1/insights/stream');
        evtSource.addEventListener('supply_chain', handler);
    """
    redis = request.app.state.redis

    channels = ALL_MARKET_CHANNELS + [
        CHANNEL_KILL_SWITCH,
        "dashboard:state_updates",
    ]

    async def event_generator():
        try:
            async for message in redis.subscribe(*channels):
                if await request.is_disconnected():
                    break

                category = message.get("category") or message.get("event") or "update"
                yield {
                    "event": category,
                    "data": json.dumps(message),
                    "retry": 5000,  # Client reconnect delay in ms
                }
        except Exception:
            # Client disconnected or Redis error — stop cleanly
            return

    return EventSourceResponse(event_generator())


@router.get("/recent")
async def get_recent_insights(limit: int = 50):
    """Return recent market events from PostgreSQL for initial dashboard load."""
    from logging_sinks.postgres_sink import PostgresSink
    from config.settings import settings

    sink = PostgresSink(settings.database_url)
    try:
        events = await sink.get_recent_events(limit=limit)
        return {"events": events}
    except Exception as exc:
        return {"events": [], "error": str(exc)}
    finally:
        await sink.close()
