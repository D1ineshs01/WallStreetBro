"""
Wall Street Bro — Unified Entry Point

Usage:
  python main.py --mode all      # Run agent loop + FastAPI (default)
  python main.py --mode agent    # Run LangGraph agent loop only
  python main.py --mode api      # Run FastAPI backend only

Streamlit dashboard runs separately:
  streamlit run dashboard/frontend/app.py
"""

import argparse
import asyncio
from datetime import datetime, timezone, time as dt_time

import structlog
import uvicorn

# ── Scenario 3: 15-minute scan interval, market hours only ────────────
SCAN_INTERVAL_SECONDS = 900          # 15 minutes between scans
MARKET_OPEN  = dt_time(9, 15)        # Start 15 min before market open (9:30 ET)
MARKET_CLOSE = dt_time(16, 0)        # 4:00 PM ET


def _et_now():
    """Current time in US Eastern (approximated as UTC-4 for EDT)."""
    from datetime import timedelta
    return datetime.now(timezone.utc) - timedelta(hours=4)


def _is_market_hours() -> bool:
    """Return True if within active trading window (Mon–Fri, 9:15–16:00 ET)."""
    et = _et_now()
    if et.weekday() >= 5:      # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= et.time() <= MARKET_CLOSE


def _seconds_until_market_open() -> float:
    """
    Return seconds until the next 9:15 AM ET on a weekday.
    Called when outside market hours so the loop can sleep precisely.
    """
    from datetime import timedelta
    et = _et_now()
    # Find next weekday at MARKET_OPEN
    days_ahead = 0
    while True:
        candidate = (et + timedelta(days=days_ahead)).replace(
            hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0
        )
        if candidate > et and candidate.weekday() < 5:
            break
        days_ahead += 1
        if days_ahead > 7:   # Safety: never loop more than a week
            return 3600.0

    return (candidate - et).total_seconds()

log = structlog.get_logger(__name__)


# ── Logging setup ──────────────────────────────────────────────────────
def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


# ── Agent Loop ─────────────────────────────────────────────────────────
async def run_agent_loop() -> None:
    """
    Initialize all agents and run the LangGraph loop continuously.
    The kill switch monitor runs as a background task alongside the loop.
    """
    from config.settings import settings
    from core.redis_client import get_redis
    from core.state import initial_state
    from logging_sinks.postgres_sink import PostgresSink
    from orchestration.graph import build_graph, get_postgres_checkpointer

    log.info("agent_loop_starting")

    # ── Log active config so Railway logs show the exact model/key being used ─
    key_preview = settings.xai_api_key[:8] + "..." if settings.xai_api_key else "NOT SET"
    log.info(
        "active_config",
        grok_model=settings.grok_model,
        xai_key_prefix=key_preview,
        supervisor_model=settings.supervisor_model,
        execution_model=settings.execution_model,
        is_paper_trading=settings.is_paper_trading,
    )

    # ── Infrastructure setup ───────────────────────────────────────────
    redis = await get_redis()

    db = PostgresSink(settings.database_url)
    await db.init_db()
    log.info("database_initialized")

    # ── Build LangGraph with PostgreSQL checkpointer ──────────────────
    checkpointer = await get_postgres_checkpointer(settings.database_url)
    graph = build_graph(checkpointer=checkpointer)

    # ── Run the agent loop ─────────────────────────────────────────────
    state = initial_state()
    config = {"configurable": {"thread_id": "main"}}

    log.info("agent_loop_running", mode="continuous")

    try:
        while True:
            # ── Gate: only run during market hours ─────────────────────
            if not _is_market_hours():
                wait = _seconds_until_market_open()
                log.info(
                    "outside_market_hours_sleeping",
                    resume_in_minutes=round(wait / 60, 1),
                )
                await asyncio.sleep(wait)
                continue

            log.info("agent_cycle_start")
            try:
                async for chunk in graph.astream(state, config=config):
                    node = list(chunk.keys())[0] if chunk else "unknown"
                    node_state = list(chunk.values())[0] if chunk else {}
                    log.debug("node_executed", node=node)
                    if isinstance(node_state, dict):
                        state.update(node_state)

                log.info("agent_cycle_complete", iteration=state.get("iteration_count", 0))
                state["market_events"] = state.get("market_events", [])[-50:]
                state["trade_signals"] = []
                state["next_node"] = "ingestion"
                state["error"] = None

            except Exception as cycle_exc:
                log.error("agent_cycle_error", error=str(cycle_exc), exc_info=True)
                state["error"] = str(cycle_exc)
                state["next_node"] = "ingestion"

            # ── Wait 15 minutes before next scan ───────────────────────
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    except asyncio.CancelledError:
        log.info("agent_loop_cancelled")
    finally:
        await db.close()


# ── FastAPI Server ─────────────────────────────────────────────────────
async def run_api() -> None:
    """Run the FastAPI backend server."""
    import os
    from dashboard.api.app import app

    port = int(os.environ.get("FASTAPI_PORT", 8000))
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    log.info("fastapi_starting", host="0.0.0.0", port=port)
    await server.serve()


# ── Combined Mode ──────────────────────────────────────────────────────
async def run_all() -> None:
    """Run the agent loop and FastAPI concurrently."""
    await asyncio.gather(
        run_agent_loop(),
        run_api(),
        return_exceptions=True,
    )


# ── Entry Point ────────────────────────────────────────────────────────
def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser(
        description="Wall Street Bro — Autonomous Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --mode all        # Run everything (default)
  python main.py --mode agent      # Agent loop only
  python main.py --mode api        # FastAPI only

  # Run Streamlit dashboard in a separate terminal:
  streamlit run dashboard/frontend/app.py
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["agent", "api", "all"],
        default="all",
        help="Which components to run (default: all)",
    )
    args = parser.parse_args()

    log.info("wall_street_bro_starting", mode=args.mode)

    if args.mode == "agent":
        asyncio.run(run_agent_loop())
    elif args.mode == "api":
        asyncio.run(run_api())
    else:
        asyncio.run(run_all())


if __name__ == "__main__":
    main()
