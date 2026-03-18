"""
Grok Intelligence Ingestion Agent.

Uses xAI's Agent Tools API (/v1/responses) with web_search and x_search
tools to continuously scan X (Twitter) and the web for:
  - Supply chain disruptions
  - Geopolitical events
  - Macroeconomic data releases
  - Retail sentiment shifts

NOTE: The old search_parameters / extra_body approach was deprecated by xAI
(410 Gone). This implementation uses the current Agent Tools API.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
import structlog
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
        # Use httpx directly — xAI Agent Tools API is at /v1/responses,
        # not the OpenAI-compatible /v1/chat/completions endpoint.
        self._http = httpx.AsyncClient(
            base_url=settings.xai_base_url,
            headers={
                "Authorization": f"Bearer {settings.xai_api_key}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )
        self._rate_limiter = TokenBucketRateLimiter(
            rate_per_minute=settings.grok_rate_limit_rpm,
            buffer_pct=settings.rate_limit_buffer_pct,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    def _rolling_window(self, minutes: int = 15) -> tuple[str, str]:
        """Return ISO 8601 from_date and to_date for the last N minutes."""
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(minutes=minutes)
        return from_dt.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def _call_grok(
        self,
        user_message: str,
        search_sources: Optional[list] = None,  # kept for API compat, ignored
        from_date: Optional[str] = None,         # kept for API compat, date is in prompt
        to_date: Optional[str] = None,           # kept for API compat, date is in prompt
    ) -> str:
        """
        Call xAI Agent Tools API (/v1/responses) with web_search + x_search tools.
        The old search_parameters / extra_body approach returned 410 Gone.
        """
        await self._rate_limiter.acquire()

        body: dict = {
            "model": settings.grok_model,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "tools": [
                {"type": "web_search"},
                {"type": "x_search"},
            ],
        }

        log.debug("grok_api_call", model=settings.grok_model, from_date=from_date, to_date=to_date)

        try:
            resp = await self._http.post("/responses", json=body)
            resp.raise_for_status()
            data = resp.json()

            # Track usage metrics
            usage = data.get("usage") or {}
            total_tokens = (
                usage.get("total_tokens")
                or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
            )
            if total_tokens:
                await self.redis.increment_grok_requests(1)
                await self.redis.increment_grok_tokens(total_tokens)

            # Extract text from Agent Tools response format:
            # { "output": [{ "type": "message", "content": [{ "type": "output_text", "text": "..." }] }] }
            for item in data.get("output", []):
                if item.get("type") == "message":
                    for content_block in item.get("content", []):
                        if content_block.get("type") == "output_text":
                            return content_block.get("text", "[]")

            # Fallback: some models may return OpenAI-style choices
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "[]")

            log.warning("grok_response_no_text", raw=str(data)[:300])
            return "[]"

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            body_text = exc.response.text[:500]
            log.error(
                "grok_api_error",
                model=settings.grok_model,
                status_code=status_code,
                body=body_text,
            )
            if "rate_limit" in body_text.lower() or status_code == 429:
                raise GrokRateLimitError(f"Grok rate limit: {exc}") from exc
            raise
        except Exception as exc:
            log.error("grok_api_error", model=settings.grok_model, error=str(exc))
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
