"""
FastAPI application factory.

Serves as the data routing layer between Redis/PostgreSQL/Alpaca
and the Streamlit frontend. Exposes:
  - REST endpoints for historical data and portfolio state
  - SSE stream for real-time Grok intelligence and state updates
  - WebSocket for live tick data
"""

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dashboard.api.routes.insights import router as insights_router
from dashboard.api.routes.market_data import router as market_data_router
from dashboard.api.routes.portfolio import router as portfolio_router
from dashboard.api.websocket.ticker import router as ticker_router

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of shared resources."""
    # ── Startup ────────────────────────────────────────────────────────
    log.info("fastapi_startup")

    # Redis — optional: endpoints that need it will handle None gracefully
    try:
        from core.redis_client import get_redis
        app.state.redis = await get_redis()
        log.info("redis_connected")
    except Exception as exc:
        log.warning("redis_unavailable_dashboard_readonly", error=str(exc))
        app.state.redis = None

    # PostgreSQL — optional: endpoints that need it will handle None gracefully
    try:
        from config.settings import settings
        from logging_sinks.postgres_sink import PostgresSink
        app.state.db = PostgresSink(settings.database_url)
        await app.state.db.init_db()
        log.info("postgres_connected")
    except Exception as exc:
        log.warning("postgres_unavailable_dashboard_readonly", error=str(exc))
        app.state.db = None

    log.info("fastapi_ready", host="0.0.0.0", port=8000)
    yield

    # ── Shutdown ───────────────────────────────────────────────────────
    log.info("fastapi_shutdown")
    if app.state.redis:
        await app.state.redis.disconnect()
    if app.state.db:
        await app.state.db.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Wall Street Bro API",
        description="Real-time financial intelligence and trading dashboard backend",
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS — tighten origins in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Register routers ───────────────────────────────────────────────
    app.include_router(market_data_router, prefix="/api/v1/market", tags=["Market Data"])
    app.include_router(portfolio_router, prefix="/api/v1/portfolio", tags=["Portfolio"])
    app.include_router(insights_router, prefix="/api/v1/insights", tags=["Intelligence"])
    app.include_router(ticker_router, tags=["WebSocket"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "wall-street-bro-api"}

    return app


# Module-level app instance for uvicorn
app = create_app()
