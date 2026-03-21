"""
LangGraph node functions.
Each function: (state: AgentState) -> dict (partial state update).
"""

import asyncio
import uuid
from datetime import datetime, timezone, date
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

    redis = await get_redis()
    agent = GrokIngestionAgent(redis)

    # Extract symbols of interest from existing state
    existing_symbols: List[str] = []
    for event in state.get("market_events", [])[-10:]:  # Last 10 events
        existing_symbols.extend(event.get("symbols_affected", []))
    symbols = list(set(existing_symbols))

    try:
        new_events = await agent.run_full_scan(symbols=symbols)

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

    signals = state.get("trade_signals", [])
    already_executed_ids = {e["signal_id"] for e in state.get("trade_executions", [])}
    pending = [s for s in signals if s["signal_id"] not in already_executed_ids]

    if not pending:
        log.info("no_pending_signals")
        return {}

    mcp_server = AlpacaMCPServer(redis)
    agent = ExecutionAgent(mcp_server, redis)

    new_executions: List[TradeExecution] = []
    for signal in pending[:1]:  # Process 1 signal per cycle to control API costs
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
        "events_count": len(state.get("market_events", [])),
        "signals_count": len(state.get("trade_signals", [])),
        "executions_count": len(state.get("trade_executions", [])),
        "last_event": state.get("market_events", [{}])[-1] if state.get("market_events") else None,
    }

    await redis.publish("dashboard:state_updates", summary)
    log.debug("visualization_node_published_update")
    return {}



def route_from_supervisor(state: AgentState) -> str:
    """
    LangGraph conditional edge function.
    Maps state["next_node"] to a registered node name.
    """
    next_node = state.get("next_node", "end")
    valid_nodes = {"ingestion", "execution", "visualization", "end"}
    if next_node not in valid_nodes:
        log.warning("invalid_next_node", next_node=next_node, falling_back_to="end")
        return "end"
    return next_node


# ── Signal generation helper ───────────────────────────────────────────

def _generate_signals_from_events(events) -> List[TradeSignal]:
    """
    Convert market events into trade signals for Claude to evaluate.

    Every event is treated as a potential opportunity — volatile and critical
    events often produce the biggest price moves and best risk/reward setups.
    Claude (the execution agent) performs the full risk/reward analysis and
    decides whether to trade, which direction, and what size.

    Direction is set to "buy" as a starting suggestion. Claude will override
    if a sell or volatility play is more appropriate given the event context.
    """
    signals = []
    for event in events:
        confidence = getattr(event, 'confidence', 0)
        symbols = getattr(event, 'symbols_affected', [])
        severity = getattr(event, 'disruption_severity', 'low')
        category = getattr(event, 'category', '')
        summary = getattr(event, 'summary', '')
        invalidation = getattr(event, 'invalidation_conditions', '')

        # Include all events with any meaningful confidence
        if confidence < 0.7 or not symbols:
            continue

        for symbol in symbols[:3]:  # Up to 3 symbols per event
            signals.append(
                TradeSignal(
                    signal_id=str(uuid.uuid4()),
                    symbol=symbol.upper(),
                    direction="buy",  # Execution agent will override based on R:R analysis
                    rationale=(
                        f"[{severity.upper()}] {category} event — confidence {confidence:.0%}.\n"
                        f"Summary: {summary[:400]}\n"
                        f"Invalidation: {invalidation[:200]}\n"
                        f"Instruction: Perform a full risk-to-reward analysis. "
                        f"Volatile events create opportunity. Consider both the affected "
                        f"symbol and related volatility plays (VIX, inverse ETFs, safe-haven "
                        f"assets). Execute if R:R >= 2:1."
                    ),
                    confidence=confidence,
                    suggested_qty=10,  # Execution agent refines based on account size
                    suggested_limit_price=None,
                    generated_by="ingestion_node",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )

    return signals


