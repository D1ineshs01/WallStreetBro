"""
Async Redis client with connection pooling, Pub/Sub helpers,
and typed key accessors for the kill switch and metrics.
"""

import json
import asyncio
from datetime import datetime, timezone
from typing import AsyncIterator, Optional
import redis.asyncio as aioredis
import structlog

from core.exceptions import RedisConnectionError

log = structlog.get_logger(__name__)

# ── Channel constants ──────────────────────────────────────────────────
CHANNEL_SUPPLY_CHAIN = "market_events:supply_chain_disruptions"
CHANNEL_GEOPOLITICAL = "market_events:geopolitical"
CHANNEL_MACRO = "market_events:macro"
CHANNEL_SENTIMENT = "market_events:sentiment"
CHANNEL_EXECUTION_SIGNALS = "execution:signals"
CHANNEL_KILL_SWITCH = "system:kill_switch"

# ── Key constants ──────────────────────────────────────────────────────
AGENT_EXECUTION_STATUS_KEY = "agent:execution_status"
AGENT_MANUAL_KILL_KEY = "agent:manual_kill"
METRICS_GROK_REQUEST_COUNT = "metrics:grok:request_count"
METRICS_GROK_TOKEN_COUNT = "metrics:grok:token_count"
METRICS_PORTFOLIO_PEAK_VALUE = "metrics:portfolio:peak_value"

ALL_MARKET_CHANNELS = [
    CHANNEL_SUPPLY_CHAIN,
    CHANNEL_GEOPOLITICAL,
    CHANNEL_MACRO,
    CHANNEL_SENTIMENT,
]


class RedisClient:
    """
    Async Redis client wrapping redis.asyncio.
    Provides typed helpers for Pub/Sub and kill switch state.
    """

    def __init__(self, url: str, password: Optional[str] = None):
        self._url = url
        self._password = password
        self._pool: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        try:
            self._pool = aioredis.from_url(
                self._url,
                password=self._password or None,
                encoding="utf-8",
                decode_responses=True,
                max_connections=20,
            )
            await self._pool.ping()
            log.info("redis_connected", url=self._url)
        except Exception as exc:
            raise RedisConnectionError(f"Cannot connect to Redis at {self._url}: {exc}") from exc

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.aclose()
            log.info("redis_disconnected")

    def _client(self) -> aioredis.Redis:
        if not self._pool:
            raise RedisConnectionError("Redis client is not connected. Call connect() first.")
        return self._pool

    # ── Pub/Sub ────────────────────────────────────────────────────────

    async def publish(self, channel: str, message: dict) -> None:
        """Serialize message dict to JSON and publish to channel."""
        message.setdefault("published_at", datetime.now(timezone.utc).isoformat())
        payload = json.dumps(message)
        try:
            await self._client().publish(channel, payload)
            log.debug("redis_published", channel=channel)
        except Exception as exc:
            raise RedisConnectionError(f"Failed to publish to {channel}: {exc}") from exc

    async def subscribe(self, *channels: str) -> AsyncIterator[dict]:
        """
        Subscribe to one or more channels and yield parsed message dicts.
        This is an async generator — iterate with `async for msg in client.subscribe(...)`.
        """
        pubsub = self._client().pubsub()
        await pubsub.subscribe(*channels)
        log.info("redis_subscribed", channels=list(channels))
        try:
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    try:
                        yield json.loads(raw["data"])
                    except json.JSONDecodeError:
                        log.warning("redis_bad_json", channel=raw.get("channel"), data=raw["data"])
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()

    # ── Kill Switch State ──────────────────────────────────────────────

    async def set_execution_status(self, enabled: bool) -> None:
        """Set agent:execution_status. False = trading halted (fail-closed)."""
        await self._client().set(AGENT_EXECUTION_STATUS_KEY, "1" if enabled else "0")
        log.info("execution_status_set", enabled=enabled)

    async def get_execution_status(self) -> bool:
        """
        Read agent:execution_status. Returns False if key is missing or "0".
        Fail-closed: any uncertainty = halted.
        """
        try:
            val = await self._client().get(AGENT_EXECUTION_STATUS_KEY)
            return val == "1"
        except Exception as exc:
            log.error("execution_status_read_failed", error=str(exc))
            return False  # Fail closed

    async def set_manual_kill(self, active: bool) -> None:
        await self._client().set(AGENT_MANUAL_KILL_KEY, "1" if active else "0")

    async def get_manual_kill(self) -> bool:
        val = await self._client().get(AGENT_MANUAL_KILL_KEY)
        return val == "1"

    # ── Metrics ────────────────────────────────────────────────────────

    async def increment_grok_requests(self, count: int = 1) -> None:
        await self._client().incrby(METRICS_GROK_REQUEST_COUNT, count)

    async def increment_grok_tokens(self, count: int) -> None:
        await self._client().incrby(METRICS_GROK_TOKEN_COUNT, count)

    async def get_grok_request_count(self) -> int:
        val = await self._client().get(METRICS_GROK_REQUEST_COUNT)
        return int(val) if val else 0

    async def get_grok_token_count(self) -> int:
        val = await self._client().get(METRICS_GROK_TOKEN_COUNT)
        return int(val) if val else 0

    async def set_peak_portfolio_value(self, value: float) -> None:
        await self._client().set(METRICS_PORTFOLIO_PEAK_VALUE, str(value))

    async def get_peak_portfolio_value(self) -> float:
        val = await self._client().get(METRICS_PORTFOLIO_PEAK_VALUE)
        return float(val) if val else 0.0

    # ── Generic helpers ────────────────────────────────────────────────

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        await self._client().set(key, value, ex=ex)

    async def get(self, key: str) -> Optional[str]:
        return await self._client().get(key)

    async def delete(self, *keys: str) -> None:
        await self._client().delete(*keys)


# ── Module-level singleton factory ─────────────────────────────────────

_redis_instance: Optional[RedisClient] = None


async def get_redis() -> RedisClient:
    """Return the connected module-level Redis singleton."""
    global _redis_instance
    if _redis_instance is None:
        from config.settings import settings
        _redis_instance = RedisClient(
            url=settings.redis_url,
            password=settings.redis_password,
        )
        await _redis_instance.connect()
    return _redis_instance
