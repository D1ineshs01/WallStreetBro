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
SCAN_INTERVAL_SECONDS = 900          # 15 minutes
MARKET_OPEN  = dt_time(9, 30)        # 9:30 AM ET
MARKET_CLOSE = dt_time(16, 0)        # 4:00 PM ET


def _is_market_hours() -> bool:
    """Return True if current UTC time falls within US market hours (Mon–Fri)."""
    # Convert UTC to approximate ET (UTC-4 during EDT, UTC-5 during EST)
    # Using UTC-4 (EDT) as a conservative approximation
    from datetime import timedelta
    et_now = datetime.now(timezone.utc) - timedelta(hours=4)
    if et_now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= et_now.time() <= MARKET_CLOSE

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
    from kill_switch.monitor import KillSwitchMonitor
    from logging_sinks.postgres_sink import PostgresSink
    from orchestration.graph import build_graph, get_postgres_checkpointer

    log.info("agent_loop_starting")

    # ── Infrastructure setup ───────────────────────────────────────────
    redis = await get_redis()

    db = PostgresSink(settings.database_url)
    await db.init_db()
    log.info("database_initialized")

    # ── Fail-closed: ensure execution is explicitly disabled on startup ─
    import os
    current_status = await redis.get_execution_status()
    if not current_status:
        if os.environ.get("AUTO_ENABLE_TRADING", "").lower() == "true":
            await redis.set_execution_status(True)
            log.info("execution_auto_enabled", hint="AUTO_ENABLE_TRADING=true")
        else:
            log.warning(
                "execution_disabled_on_startup",
                hint="Run: redis-cli SET agent:execution_status 1  to enable trading",
            )

    # ── Kill switch monitor (background) ──────────────────────────────
    from alpaca.trading.client import TradingClient
    trading_client = TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.is_paper_trading,
    )
    kill_monitor = KillSwitchMonitor(redis, trading_client)
    monitor_task = asyncio.create_task(kill_monitor.run())
    log.info("kill_switch_monitor_started")

    # ── Build LangGraph with PostgreSQL checkpointer ──────────────────
    checkpointer = await get_postgres_checkpointer(settings.database_url)
    graph = build_graph(checkpointer=checkpointer)

    # ── Run the agent loop ─────────────────────────────────────────────
    state = initial_state()
    config = {"configurable": {"thread_id": "main"}}

    log.info("agent_loop_running", mode="continuous")

    try:
        # LangGraph runs until it reaches END node (or is cancelled)
        # We wrap in a loop so it restarts after each complete cycle
        while True:
            log.info("agent_cycle_start")
            async for node_name, node_state in graph.astream(state, config=config):
                log.debug("node_executed", node=list(node_name.keys())[0] if node_name else "unknown")
                # Merge the returned state chunk back
                if isinstance(node_state, dict):
                    state.update(node_state)

            log.info("agent_cycle_complete", iteration=state.get("iteration_count", 0))
            # Reset for next cycle (keep history but reset pipeline)
            state["market_events"] = state.get("market_events", [])[-50:]  # Keep last 50
            state["trade_signals"] = []  # Clear processed signals
            state["next_node"] = "ingestion"
            state["error"] = None

            # ── Scenario 3: wait 15 minutes, skip outside market hours ──
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)
            if not _is_market_hours():
                log.info("outside_market_hours_skipping_cycle")
                continue

    except asyncio.CancelledError:
        log.info("agent_loop_cancelled")
    finally:
        monitor_task.cancel()
        await db.close()


# ── FastAPI Server ─────────────────────────────────────────────────────
async def run_api() -> None:
    """Run the FastAPI backend server."""
    import os
    from dashboard.api.app import app

    port = int(os.environ.get("PORT", 8000))
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
