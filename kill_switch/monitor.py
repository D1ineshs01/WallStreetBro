"""
Kill Switch Monitor.

Runs as an asyncio background task alongside the main agent loop.
Continuously evaluates portfolio health and system metrics every 5 seconds.
Any condition breach immediately halts all trading activity.

Design principles:
- Fail-closed: any exception in a check = trigger kill switch
- Runs independently of LangGraph context (no access to agent state)
- Deterministic: operates on Redis/Alpaca data, not LLM output
- Must be started BEFORE the execution agent is allowed to trade
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import structlog
from alpaca.trading.client import TradingClient

from config.settings import settings
from core.redis_client import (
    CHANNEL_KILL_SWITCH,
    METRICS_GROK_REQUEST_COUNT,
    RedisClient,
)

log = structlog.get_logger(__name__)

MONITOR_INTERVAL_SECONDS = 5
MACRO_CRITICAL_WINDOW_SECONDS = 60  # Check for CRITICAL events in last 60s


class KillSwitchMonitor:
    """
    Background monitor that enforces hard risk limits.
    Triggered automatically — no human approval needed to halt trading.
    Human approval IS needed to resume (set agent:execution_status=1 in Redis).
    """

    def __init__(self, redis: RedisClient, trading_client: TradingClient):
        self.redis = redis
        self.trading = trading_client
        self._running = False
        self._last_critical_event_ts: Optional[datetime] = None

    async def run(self) -> None:
        """Main monitoring loop. Runs until cancelled."""
        self._running = True
        log.info("kill_switch_monitor_started", interval_s=MONITOR_INTERVAL_SECONDS)

        while self._running:
            try:
                await self._check_conditions()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # Any unexpected error in the monitor = fail closed
                log.critical(
                    "kill_switch_monitor_error",
                    error=str(exc),
                    action="triggering_kill_switch",
                )
                await self._trigger_kill_switch(f"Monitor error: {exc}")

            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)

        log.info("kill_switch_monitor_stopped")

    def stop(self) -> None:
        self._running = False

    async def _check_conditions(self) -> None:
        """Evaluate all kill switch conditions. Trigger if any fail."""
        checks = [
            ("drawdown", self._check_drawdown),
            ("rate_limit", self._check_rate_limits),
            ("macro_disruption", self._check_macro_disruption),
            ("manual_kill", self._check_manual_kill),
        ]

        for name, check_fn in checks:
            try:
                is_safe = await check_fn()
                if not is_safe:
                    await self._trigger_kill_switch(f"Condition breached: {name}")
                    return
            except Exception as exc:
                log.error("kill_switch_check_failed", check=name, error=str(exc))
                await self._trigger_kill_switch(f"Check '{name}' threw exception: {exc}")
                return

    async def _check_drawdown(self) -> bool:
        """
        Returns True if portfolio drawdown is within acceptable limits.
        Fetches current portfolio value from Alpaca and peak value from Redis.
        """
        try:
            account = self.trading.get_account()
            current_value = float(account.portfolio_value)

            peak_value = await self.redis.get_peak_portfolio_value()
            if peak_value == 0.0:
                # First run: set peak to current value
                await self.redis.set_peak_portfolio_value(current_value)
                return True

            # Update peak if portfolio grew
            if current_value > peak_value:
                await self.redis.set_peak_portfolio_value(current_value)
                peak_value = current_value

            drawdown = (peak_value - current_value) / peak_value if peak_value > 0 else 0.0

            log.debug("drawdown_check", current=current_value, peak=peak_value, drawdown=f"{drawdown:.2%}")

            if drawdown >= settings.max_drawdown_pct:
                log.warning(
                    "drawdown_limit_breached",
                    drawdown=f"{drawdown:.2%}",
                    limit=f"{settings.max_drawdown_pct:.2%}",
                )
                return False

            return True
        except Exception as exc:
            log.error("drawdown_check_error", error=str(exc))
            return False  # Fail closed

    async def _check_rate_limits(self) -> bool:
        """
        Returns True if Grok API request rate is within safe bounds.
        Uses a 1-minute sliding window counter stored in Redis.
        """
        try:
            count = await self.redis.get_grok_request_count()
            buffer_limit = settings.grok_rate_limit_rpm * settings.rate_limit_buffer_pct

            if count > buffer_limit:
                log.warning(
                    "rate_limit_buffer_exceeded",
                    count=count,
                    buffer_limit=buffer_limit,
                )
                return False

            return True
        except Exception as exc:
            log.error("rate_limit_check_error", error=str(exc))
            return True  # Don't fail closed on metric errors

    async def _check_macro_disruption(self) -> bool:
        """
        Returns False if a CRITICAL severity macro event was received in the last 60 seconds.
        Reads from Redis key set by the ingestion node when publishing CRITICAL events.
        """
        try:
            val = await self.redis.get("kill_switch:last_critical_event")
            if not val:
                return True

            last_critical = datetime.fromisoformat(val)
            now = datetime.now(timezone.utc)
            age_seconds = (now - last_critical).total_seconds()

            if age_seconds <= MACRO_CRITICAL_WINDOW_SECONDS:
                log.warning(
                    "critical_macro_event_detected",
                    age_seconds=age_seconds,
                    event_time=val,
                )
                return False

            return True
        except Exception as exc:
            log.error("macro_check_error", error=str(exc))
            return True  # Don't halt on timestamp parse errors

    async def _check_manual_kill(self) -> bool:
        """Returns False if a human has manually activated the kill switch."""
        manual = await self.redis.get_manual_kill()
        if manual:
            log.warning("manual_kill_active")
        return not manual

    async def _trigger_kill_switch(self, reason: str) -> None:
        """
        Halt all trading immediately:
        1. Set Redis execution status to False
        2. Publish kill switch notification
        3. Cancel all open Alpaca orders
        4. Log the event
        """
        log.critical("kill_switch_triggered", reason=reason)

        # 1. Halt execution flag
        await self.redis.set_execution_status(False)

        # 2. Publish notification to all subscribers
        await self.redis.publish(
            CHANNEL_KILL_SWITCH,
            {
                "event": "kill_switch_triggered",
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        # 3. Cancel all open orders
        orders_cancelled = 0
        try:
            cancelled = self.trading.cancel_orders()
            orders_cancelled = len(cancelled) if cancelled else 0
            log.info("kill_switch_orders_cancelled", count=orders_cancelled)
        except Exception as exc:
            log.error("kill_switch_cancel_orders_failed", error=str(exc))

        # 4. Log to PostgreSQL (best effort — don't fail if DB is down)
        try:
            from logging_sinks.postgres_sink import PostgresSink
            from config.settings import settings as s
            sink = PostgresSink(s.database_url)
            await sink.write_kill_switch_event(
                reason=reason,
                orders_cancelled=orders_cancelled,
            )
        except Exception as exc:
            log.error("kill_switch_db_log_failed", error=str(exc))

    @staticmethod
    async def mark_critical_event(redis: RedisClient) -> None:
        """
        Called by the ingestion node when a CRITICAL severity event is detected.
        Sets a Redis key that the monitor checks each cycle.
        """
        await redis.set(
            "kill_switch:last_critical_event",
            datetime.now(timezone.utc).isoformat(),
            ex=300,  # Auto-expire after 5 minutes
        )
        log.warning("critical_event_flagged_for_kill_switch")
