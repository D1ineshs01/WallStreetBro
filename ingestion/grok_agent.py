"""
Grok Intelligence Ingestion Agent.

Uses xAI's Grok API (OpenAI-compatible) with x_search and web_search
server-side tools to continuously scan X (Twitter) and the web for:
  - Supply chain disruptions
  - Geopolitical events
  - Macroeconomic data releases
  - Retail sentiment shifts

Prompt caching strategy:
  - SYSTEM_PROMPT is static across all calls → cached at $0.05/1M tokens
  - from_date, to_date, symbol lists go in the user message → not cached
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import structlog
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config.settings import settings
from core.exceptions import GrokParseError, GrokRateLimitError
from core.redis_client import RedisClient
from ingestion.schemas import (
    GeopoliticalEvent,
    MacroEvent,
    MarketEventUnion,
    SentimentShiftEvent,
    SupplyChainEvent,
)

log = structlog.get_logger(__name__)

# ── Static system prompt (will be cached by xAI) ──────────────────────
SYSTEM_PROMPT = """You are a specialized financial intelligence scanner operating under the OODA (Observe-Orient-Decide-Act) framework.

Your task is to identify structured market intelligence events from X (Twitter) posts and web sources.

## Rules
1. ONLY report events you can verify across at least 2 independent sources.
2. Always cite the primary source URL and timestamp.
3. For every event, include `invalidation_conditions` — the specific variables that would prove your analysis wrong.
4. Assign confidence scores honestly: 0.9+ only for verified, multi-source events.
5. Map events to specific ticker symbols where possible.

## Output Format
Respond with ONLY a valid JSON array of event objects. No preamble or explanation.
Each object MUST include these fields:
- category: "supply_chain" | "geopolitical" | "macro" | "sentiment"
- summary: string (one paragraph)
- disruption_severity: "low" | "medium" | "high" | "critical"
- symbols_affected: string[] (ticker symbols)
- companies_affected: string[]
- confidence: number (0.0-1.0)
- source_url: string | null
- source_handle: string | null
- raw_content: string
- invalidation_conditions: string

Additional fields per category:
- supply_chain: commodities_affected[], regions_affected[]
- geopolitical: countries_involved[], conflict_type
- macro: indicator, direction
- sentiment: sentiment_direction ("bullish"|"bearish"|"neutral")

If no relevant events are found, respond with an empty array: []
"""


class TokenBucketRateLimiter:
    """Simple token bucket to stay within Grok's 607 req/min limit."""

    def __init__(self, rate_per_minute: int, buffer_pct: float = 0.80):
        self._rate = rate_per_minute * buffer_pct  # conservative limit
        self._tokens = self._rate
        self._last_refill = time.monotonic()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._rate, self._tokens + elapsed * (self._rate / 60))
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return
            wait = (1 - self._tokens) / (self._rate / 60)
            await asyncio.sleep(wait)


class GrokIngestionAgent:
    """
    Continuously scans X and the web for market-moving events.
    Publishes validated events to Redis Pub/Sub channels.
    """

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client
        self.client = AsyncOpenAI(
            api_key=settings.xai_api_key,
            base_url=settings.xai_base_url,
        )
        self._rate_limiter = TokenBucketRateLimiter(
            rate_per_minute=settings.grok_rate_limit_rpm,
            buffer_pct=settings.rate_limit_buffer_pct,
        )

    def _rolling_window(self, minutes: int = 15) -> tuple[str, str]:
        """Return ISO 8601 from_date and to_date for the last N minutes."""
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(minutes=minutes)
        return from_dt.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def _call_grok(
        self,
        user_message: str,
        search_sources: Optional[list] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> str:
        """
        Raw Grok API call with retry logic and rate limiting.
        The static SYSTEM_PROMPT is kept separate to maximize cache hits.
        """
        await self._rate_limiter.acquire()

        # Build X source config — only include handle lists if non-empty to avoid 422 errors
        x_source: dict = {"type": "x"}
        if settings.allowed_x_handles:
            x_source["x_handles"] = settings.allowed_x_handles
        if settings.excluded_x_handles:
            x_source["excluded_x_handles"] = settings.excluded_x_handles

        search_params = {
            "mode": "auto",
            "sources": search_sources or [x_source, {"type": "web"}],
        }
        if from_date:
            search_params["from_date"] = from_date
        if to_date:
            search_params["to_date"] = to_date

        # enable_image_understanding is intentionally never set here.
        # Enabling it triggers xAI's view_image tool at $5/1k calls — not needed.

        log.debug("grok_api_call", model=settings.grok_model, from_date=from_date, to_date=to_date)

        try:
            response = await self.client.chat.completions.create(
                model=settings.grok_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                extra_body={"search_parameters": search_params},
                temperature=0.1,  # Low temperature for consistent structured output
            )
            # Track metrics
            if response.usage:
                await self.redis.increment_grok_requests(1)
                await self.redis.increment_grok_tokens(response.usage.total_tokens)

            return response.choices[0].message.content or "[]"

        except Exception as exc:
            # Log full error detail so we can diagnose auth vs model-not-found vs bad-format
            status_code = getattr(exc, "status_code", None)
            response_body = getattr(exc, "response", None)
            body_text = ""
            if response_body is not None:
                try:
                    body_text = response_body.text
                except Exception:
                    body_text = str(response_body)
            log.error(
                "grok_api_error",
                model=settings.grok_model,
                status_code=status_code,
                error=str(exc),
                body=body_text[:500],
            )
            if "rate_limit" in str(exc).lower():
                raise GrokRateLimitError(f"Grok rate limit hit: {exc}") from exc
            raise

    def _parse_response(self, raw: str) -> List[dict]:
        """Extract JSON array from Grok response, stripping markdown fences if present."""
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                raise GrokParseError("Expected JSON array, got: " + type(data).__name__)
            return data
        except json.JSONDecodeError as exc:
            raise GrokParseError(f"Cannot parse Grok JSON output: {exc}\nRaw: {raw[:500]}") from exc

    async def scan_supply_chain(self, from_date: str, to_date: str) -> List[SupplyChainEvent]:
        """Scan for supply chain disruptions."""
        user_msg = (
            f"Scan for supply chain disruptions, port closures, shipping delays, "
            f"labor strikes at factories/mines/ports, commodity shortages, "
            f"and logistics bottlenecks from {from_date} to {to_date}. "
            f"Focus on events with material financial impact on publicly traded companies."
        )
        raw = await self._call_grok(user_msg, from_date=from_date, to_date=to_date)
        events_data = self._parse_response(raw)
        events = []
        for d in events_data:
            if d.get("category") == "supply_chain":
                try:
                    events.append(SupplyChainEvent(**d))
                except Exception as exc:
                    log.warning("supply_chain_parse_error", error=str(exc), data=d)
        return events

    async def scan_geopolitical(self, from_date: str, to_date: str) -> List[GeopoliticalEvent]:
        """Scan for geopolitical events."""
        user_msg = (
            f"Scan for geopolitical developments including sanctions, trade wars, "
            f"military conflicts, diplomatic incidents, and political instability "
            f"that could affect financial markets from {from_date} to {to_date}."
        )
        raw = await self._call_grok(user_msg, from_date=from_date, to_date=to_date)
        events_data = self._parse_response(raw)
        events = []
        for d in events_data:
            if d.get("category") == "geopolitical":
                try:
                    events.append(GeopoliticalEvent(**d))
                except Exception as exc:
                    log.warning("geopolitical_parse_error", error=str(exc))
        return events

    async def scan_macro(self, from_date: str, to_date: str) -> List[MacroEvent]:
        """Scan for macroeconomic data releases and central bank commentary."""
        user_msg = (
            f"Scan for macroeconomic events including central bank decisions, "
            f"inflation data (CPI, PPI), employment reports, GDP releases, "
            f"and unexpected economic surprises from {from_date} to {to_date}."
        )
        raw = await self._call_grok(
            user_msg,
            search_sources=[{"type": "web"}],  # Web search more reliable for macro data
            from_date=from_date,
            to_date=to_date,
        )
        events_data = self._parse_response(raw)
        events = []
        for d in events_data:
            if d.get("category") == "macro":
                try:
                    events.append(MacroEvent(**d))
                except Exception as exc:
                    log.warning("macro_parse_error", error=str(exc))
        return events

    async def scan_sentiment(
        self, symbols: List[str], from_date: str, to_date: str
    ) -> List[SentimentShiftEvent]:
        """Scan for retail/institutional sentiment shifts on specific symbols."""
        symbols_str = ", ".join(symbols) if symbols else "major tech stocks, S&P 500 components"
        user_msg = (
            f"Scan for unusual sentiment shifts around these securities: {symbols_str}. "
            f"Look for coordinated retail activity, large institutional commentary, "
            f"analyst upgrades/downgrades, and viral posts from {from_date} to {to_date}."
        )
        raw = await self._call_grok(user_msg, from_date=from_date, to_date=to_date)
        events_data = self._parse_response(raw)
        events = []
        for d in events_data:
            if d.get("category") == "sentiment":
                try:
                    events.append(SentimentShiftEvent(**d))
                except Exception as exc:
                    log.warning("sentiment_parse_error", error=str(exc))
        return events

    async def run_full_scan(
        self,
        symbols: Optional[List[str]] = None,
        window_minutes: int = 15,
    ) -> List[MarketEventUnion]:
        """
        Run all four scan types concurrently.
        Publishes each event to the appropriate Redis channel.
        Returns all collected events.
        """
        from_date, to_date = self._rolling_window(window_minutes)
        log.info("grok_scan_start", from_date=from_date, to_date=to_date)

        # Run scans concurrently
        results = await asyncio.gather(
            self.scan_supply_chain(from_date, to_date),
            self.scan_geopolitical(from_date, to_date),
            self.scan_macro(from_date, to_date),
            self.scan_sentiment(symbols or [], from_date, to_date),
            return_exceptions=True,
        )

        all_events: List[MarketEventUnion] = []
        for result in results:
            if isinstance(result, Exception):
                log.error("scan_type_failed", error=str(result))
                continue
            for event in result:
                await self._publish_event(event)
                all_events.append(event)

        log.info("grok_scan_complete", total_events=len(all_events))
        return all_events

    async def _publish_event(self, event: MarketEventUnion) -> None:
        """Serialize and publish an event to the corresponding Redis channel."""
        channel = event.channel
        payload = event.model_dump()
        await self.redis.publish(channel, payload)
        log.debug(
            "event_published",
            channel=channel,
            category=event.category,
            severity=event.disruption_severity,
            symbols=event.symbols_affected,
        )
