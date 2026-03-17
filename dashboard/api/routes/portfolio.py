"""
REST endpoints for portfolio data: account, positions, and execution history.
"""

from fastapi import APIRouter, HTTPException, Request

from config.settings import settings

router = APIRouter()


@router.get("/account")
async def get_account():
    """Return current Alpaca account status."""
    from alpaca.trading.client import TradingClient

    client = TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.is_paper_trading,
    )
    try:
        acc = client.get_account()
        return {
            "buying_power": str(acc.buying_power),
            "cash": str(acc.cash),
            "portfolio_value": str(acc.portfolio_value),
            "equity": str(acc.equity),
            "status": str(acc.status),
            "pattern_day_trader": acc.pattern_day_trader,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/positions")
async def get_positions():
    """Return all open positions."""
    from alpaca.trading.client import TradingClient

    client = TradingClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
        paper=settings.is_paper_trading,
    )
    try:
        positions = client.get_all_positions()
        return {
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": str(p.qty),
                    "side": str(p.side),
                    "avg_entry_price": str(p.avg_entry_price),
                    "current_price": str(p.current_price),
                    "unrealized_pl": str(p.unrealized_pl),
                    "unrealized_plpc": str(p.unrealized_plpc),
                    "market_value": str(p.market_value),
                }
                for p in positions
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/executions")
async def get_executions(limit: int = 100):
    """Return recent trade executions from PostgreSQL."""
    from logging_sinks.postgres_sink import PostgresSink

    sink = PostgresSink(settings.database_url)
    try:
        executions = await sink.get_recent_executions(limit=limit)
        return {"executions": executions}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        await sink.close()


@router.post("/kill-switch/{action}")
async def set_kill_switch(action: str, request: Request):
    """
    Manual kill switch toggle from the dashboard.
    action: "activate" or "deactivate"
    """
    if action not in ("activate", "deactivate"):
        raise HTTPException(status_code=400, detail="action must be 'activate' or 'deactivate'")

    redis = request.app.state.redis
    if action == "activate":
        await redis.set_execution_status(False)
        await redis.set_manual_kill(True)
        return {"status": "kill_switch_activated"}
    else:
        await redis.set_manual_kill(False)
        # Note: execution status is NOT automatically re-enabled.
        # Operator must explicitly re-enable trading after reviewing.
        return {"status": "manual_kill_cleared", "note": "Set execution_status=1 in Redis to resume trading"}
