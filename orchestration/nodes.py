"""
LangGraph node functions.
Each function: (state: AgentState) -> dict (partial state update).
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import List

import structlog

from core.state import AgentState, MarketEvent, TradeExecution, TradeSignal
from orchestration.supervisor import SupervisorAgent

log = structlog.get_logger(__name__)

# Module-level supervisor instance (stateless, reuse across calls)
_supervisor = SupervisorAgent()


def supervisor_node(state: AgentState) -> dict:
    """
    Routes the graph to the appropriate next node.
    Claude 4.5 Sonnet reads state and returns a route_decision tool call.
    """
    result = _supervisor.decide(state)
    return {
        **result,
        "iteration_count": state.get("iteration_count", 0) + 1,
    }


async def ingestion_node(state: AgentState) -> dict:
    """
    Runs the Grok scanner to collect new market events.
    Publishes events to Redis and appends to state.
    """
    from core.redis_client import get_redis
    from ingestion.grok_agent import GrokIngestionAgent
    from kill_switch.monitor import KillSwitchMonitor

    redis = await get_redis()
    agent = GrokIngestionAgent(redis)

    # Extract symbols of interest from existing state
    existing_symbols: List[str] = []
    for event in state.get("market_events", [])[-10:]:  # Last 10 events
        existing_symbols.extend(event.get("symbols_affected", []))
    symbols = list(set(existing_symbols)) or ["SPY", "QQQ", "GLD", "USO"]

    try:
        new_events = await agent.run_full_scan(symbols=symbols)

        # Check for CRITICAL events — flag for kill switch monitor
        for event in new_events:
            if hasattr(event, 'disruption_severity') and event.disruption_severity == "critical":
                await KillSwitchMonitor.mark_critical_event(redis)
                log.warning("critical_event_detected_in_ingestion", event_id=event.event_id)

        # Convert Pydantic models to TypedDict-compatible dicts
        event_dicts: List[MarketEvent] = [e.model_dump() for e in new_events]

        # Persist to PostgreSQL
        try:
            from logging_sinks.postgres_sink import PostgresSink
            from config.settings import settings
            sink = PostgresSink(settings.database_url)
            for ed in event_dicts:
                await sink.write_market_event(ed)
        except Exception as exc:
            log.warning("event_db_write_failed", error=str(exc))

        # Generate trade signals from high-confidence events
        signals = _generate_signals_from_events(new_events)

        log.info("ingestion_node_complete", new_events=len(event_dicts), signals=len(signals))

        return {
            "market_events": state.get("market_events", []) + event_dicts,
            "trade_signals": state.get("trade_signals", []) + signals,
            "error": None,
        }

    except Exception as exc:
        log.error("ingestion_node_error", error=str(exc))
        return {"error": str(exc)}


async def execution_node(state: AgentState) -> dict:
    """
    Processes pending trade signals through the Claude execution agent.
    Checks kill switch before doing anything.
    """
    from core.redis_client import get_redis
    from execution.alpaca_mcp_server import AlpacaMCPServer
    from execution.execution_agent import ExecutionAgent

    redis = await get_redis()

    # Hard kill switch check
    if state.get("kill_switch_active") or not state.get("execution_enabled"):
        log.warning("execution_node_blocked_by_kill_switch")
        return {"error": "Execution blocked: kill switch active or not enabled"}

    enabled = await redis.get_execution_status()
    if not enabled:
        log.warning("execution_node_blocked_redis_flag")
        return {"kill_switch_active": True, "execution_enabled": False}

    signals = state.get("trade_signals", [])
    already_executed_ids = {e["signal_id"] for e in state.get("trade_executions", [])}
    pending = [s for s in signals if s["signal_id"] not in already_executed_ids]

    if not pending:
        log.info("no_pending_signals")
        return {}

    mcp_server = AlpacaMCPServer(redis)
    agent = ExecutionAgent(mcp_server, redis)

    new_executions: List[TradeExecution] = []
    for signal in pending[:3]:  # Process max 3 signals per cycle
        execution = await agent.execute_signal(signal, state)
        if execution:
            new_executions.append(execution)
            # Persist to PostgreSQL
            try:
                from logging_sinks.postgres_sink import PostgresSink
                from config.settings import settings
                sink = PostgresSink(settings.database_url)
                await sink.write_trade_execution(execution)
            except Exception as exc:
                log.warning("execution_db_write_failed", error=str(exc))

    log.info("execution_node_complete", executions=len(new_executions))
    return {
        "trade_executions": state.get("trade_executions", []) + new_executions,
        "error": None,
    }


async def visualization_node(state: AgentState) -> dict:
    """
    Updates the dashboard state.
    Publishes a summary to Redis so the FastAPI SSE endpoint can push it to Streamlit.
    """
    from core.redis_client import CHANNEL_EXECUTION_SIGNALS, get_redis

    redis = await get_redis()

    summary = {
        "type": "state_update",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "portfolio_value": state.get("current_portfolio_value", 0),
        "drawdown_pct": state.get("drawdown_pct", 0),
        "kill_switch_active": state.get("kill_switch_active", False),
        "events_count": len(state.get("market_events", [])),
        "signals_count": len(state.get("trade_signals", [])),
        "executions_count": len(state.get("trade_executions", [])),
        "last_event": state.get("market_events", [{}])[-1] if state.get("market_events") else None,
    }

    await redis.publish("dashboard:state_updates", summary)
    log.debug("visualization_node_published_update")
    return {}


async def kill_switch_node(state: AgentState) -> dict:
    """
    Emergency halt. Sets Redis kill flag and cancels all open orders.
    Only reached when supervisor routes to "kill_switch".
    """
    from alpaca.trading.client import TradingClient
    from config.settings import settings
    from core.redis_client import get_redis

    redis = await get_redis()

    trading = TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.is_paper_trading,
    )

    await redis.set_execution_status(False)

    try:
        cancelled = trading.cancel_orders()
        count = len(cancelled) if cancelled else 0
        log.critical("kill_switch_node_cancelled_orders", count=count)
    except Exception as exc:
        log.error("kill_switch_cancel_failed", error=str(exc))

    return {
        "kill_switch_active": True,
        "execution_enabled": False,
        "next_node": "end",
    }


def route_from_supervisor(state: AgentState) -> str:
    """
    LangGraph conditional edge function.
    Maps state["next_node"] to a registered node name.
    """
    next_node = state.get("next_node", "end")
    valid_nodes = {"ingestion", "execution", "visualization", "kill_switch", "end"}
    if next_node not in valid_nodes:
        log.warning("invalid_next_node", next_node=next_node, falling_back_to="end")
        return "end"
    return next_node


# ── Signal generation helper ───────────────────────────────────────────

def _generate_signals_from_events(events) -> List[TradeSignal]:
    """
    Convert high-confidence market events into preliminary trade signals.
    The execution agent performs the final reasoning — this is a first pass.
    """
    signals = []
    for event in events:
        confidence = getattr(event, 'confidence', 0)
        symbols = getattr(event, 'symbols_affected', [])
        severity = getattr(event, 'disruption_severity', 'low')

        if confidence < 0.7 or not symbols:
            continue
        if severity not in ("high", "critical"):
            continue

        for symbol in symbols[:2]:  # Max 2 symbols per event
            direction = "sell" if severity == "critical" else "buy"
            signals.append(
                TradeSignal(
                    signal_id=str(uuid.uuid4()),
                    symbol=symbol.upper(),
                    direction=direction,
                    rationale=(
                        f"{event.category} event detected with {confidence:.0%} confidence. "
                        f"Severity: {severity}. Summary: {getattr(event, 'summary', '')[:300]}"
                    ),
                    confidence=confidence,
                    suggested_qty=10,  # Conservative default; execution agent refines
                    suggested_limit_price=None,
                    generated_by="ingestion_node",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )

    return signals
