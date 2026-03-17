"""
Alpaca MCP Server.

Exposes Alpaca trading operations as MCP tools that Claude can discover
and call autonomously. Each tool handler runs pre-trade risk checks
before forwarding to the Alpaca API.

Run standalone:  python -m execution.alpaca_mcp_server
Or integrated:   server = AlpacaMCPServer(...); await server.run()
"""

import json
from typing import Any

import structlog
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from config.settings import settings
from core.exceptions import AlpacaExecutionError, KillSwitchActivatedError
from core.redis_client import RedisClient
from core.state import AgentState
from execution.risk import RiskEngine

log = structlog.get_logger(__name__)

# ── Timeframe mapping ──────────────────────────────────────────────────
TIMEFRAME_MAP = {
    "1Min": TimeFrame.Minute,
    "5Min": TimeFrame(5, TimeFrame.Unit.Minute),
    "15Min": TimeFrame(15, TimeFrame.Unit.Minute),
    "1Hour": TimeFrame.Hour,
    "1Day": TimeFrame.Day,
}

SIDE_MAP = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}
TIF_MAP = {
    "day": TimeInForce.DAY,
    "gtc": TimeInForce.GTC,
    "opg": TimeInForce.OPG,
    "ioc": TimeInForce.IOC,
}


class AlpacaMCPServer:
    """
    Wraps Alpaca as callable tool handlers.
    These handlers are invoked by the ExecutionAgent when Claude
    outputs a tool_use block referencing one of the registered tools.
    """

    def __init__(self, redis: RedisClient):
        self.trading = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.is_paper_trading,
        )
        self.data = StockHistoricalDataClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )
        self.redis = redis
        self.risk = RiskEngine(self.trading, self.data, redis)

    async def handle_tool_call(
        self,
        tool_name: str,
        tool_input: dict,
        state: AgentState,
    ) -> str:
        """
        Route a tool_name + input dict to the appropriate handler.
        Returns a JSON string result.
        """
        handlers = {
            "get_account": self._get_account,
            "get_quote": self._get_quote,
            "get_position": self._get_position,
            "get_bars": self._get_bars,
            "list_open_orders": self._list_open_orders,
            "place_order": lambda inp: self._place_order(inp, state),
            "cancel_order": self._cancel_order,
            "cancel_all_orders": self._cancel_all_orders,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        try:
            result = await handler(tool_input) if tool_name not in ("place_order",) else await handler(tool_input)
            return json.dumps(result, default=str)
        except KillSwitchActivatedError as exc:
            log.error("kill_switch_blocked_order", tool=tool_name, error=str(exc))
            return json.dumps({"error": "KILL_SWITCH_ACTIVE", "message": str(exc)})
        except Exception as exc:
            log.error("tool_handler_error", tool=tool_name, error=str(exc))
            return json.dumps({"error": str(exc)})

    async def _get_account(self, _: dict) -> dict:
        account = self.trading.get_account()
        return {
            "id": str(account.id),
            "buying_power": str(account.buying_power),
            "cash": str(account.cash),
            "portfolio_value": str(account.portfolio_value),
            "equity": str(account.equity),
            "status": str(account.status),
            "pattern_day_trader": account.pattern_day_trader,
        }

    async def _get_quote(self, inp: dict) -> dict:
        symbol = inp["symbol"]
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = self.data.get_stock_latest_quote(req)
        q = quotes[symbol]
        return {
            "symbol": symbol,
            "bid": str(q.bid_price),
            "ask": str(q.ask_price),
            "bid_size": q.bid_size,
            "ask_size": q.ask_size,
        }

    async def _get_position(self, inp: dict) -> dict:
        symbol = inp["symbol"]
        try:
            pos = self.trading.get_open_position(symbol)
            return {
                "symbol": symbol,
                "qty": str(pos.qty),
                "side": str(pos.side),
                "avg_entry_price": str(pos.avg_entry_price),
                "current_price": str(pos.current_price),
                "unrealized_pl": str(pos.unrealized_pl),
                "unrealized_plpc": str(pos.unrealized_plpc),
            }
        except Exception:
            return {"symbol": symbol, "qty": "0", "message": "No open position"}

    async def _get_bars(self, inp: dict) -> dict:
        from alpaca.data.requests import StockBarsRequest
        symbol = inp["symbol"]
        timeframe_str = inp.get("timeframe", "1Day")
        limit = inp.get("limit", 50)
        tf = TIMEFRAME_MAP.get(timeframe_str, TimeFrame.Day)

        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, limit=limit)
        bars = self.data.get_stock_bars(req)
        bar_list = bars[symbol] if symbol in bars else []

        return {
            "symbol": symbol,
            "timeframe": timeframe_str,
            "bars": [
                {
                    "timestamp": str(b.timestamp),
                    "open": str(b.open),
                    "high": str(b.high),
                    "low": str(b.low),
                    "close": str(b.close),
                    "volume": b.volume,
                }
                for b in bar_list
            ],
        }

    async def _list_open_orders(self, _: dict) -> dict:
        orders = self.trading.get_orders()
        return {
            "orders": [
                {
                    "id": str(o.id),
                    "symbol": o.symbol,
                    "qty": str(o.qty),
                    "side": str(o.side),
                    "type": str(o.order_type),
                    "status": str(o.status),
                    "limit_price": str(o.limit_price) if o.limit_price else None,
                    "created_at": str(o.created_at),
                }
                for o in orders
            ]
        }

    async def _place_order(self, inp: dict, state: AgentState) -> dict:
        symbol = inp["symbol"]
        qty = inp["qty"]
        side_str = inp["side"]
        order_type_str = inp["type"]
        tif_str = inp["time_in_force"]
        limit_price = inp.get("limit_price")
        stop_price = inp.get("stop_price")

        # Pre-trade risk checks
        await self.risk.run_all_checks(
            symbol=symbol,
            qty=qty,
            order_type=order_type_str,
            limit_price=limit_price,
            state=state,
        )

        side = SIDE_MAP[side_str]
        tif = TIF_MAP[tif_str]

        # Build the appropriate order request
        if order_type_str == "market":
            req = MarketOrderRequest(symbol=symbol, qty=qty, side=side, time_in_force=tif)
        elif order_type_str == "limit":
            req = LimitOrderRequest(
                symbol=symbol, qty=qty, side=side, time_in_force=tif, limit_price=limit_price
            )
        elif order_type_str == "stop":
            req = StopOrderRequest(
                symbol=symbol, qty=qty, side=side, time_in_force=tif, stop_price=stop_price
            )
        elif order_type_str == "stop_limit":
            req = StopLimitOrderRequest(
                symbol=symbol, qty=qty, side=side, time_in_force=tif,
                limit_price=limit_price, stop_price=stop_price,
            )
        else:
            raise AlpacaExecutionError(f"Unknown order type: {order_type_str}")

        order = self.trading.submit_order(req)
        log.info(
            "order_submitted",
            order_id=str(order.id),
            symbol=symbol,
            qty=qty,
            side=side_str,
            type=order_type_str,
            rationale=inp.get("rationale", ""),
        )

        return {
            "order_id": str(order.id),
            "symbol": symbol,
            "qty": str(order.qty),
            "side": str(order.side),
            "type": str(order.order_type),
            "status": str(order.status),
            "created_at": str(order.created_at),
        }

    async def _cancel_order(self, inp: dict) -> dict:
        order_id = inp["order_id"]
        self.trading.cancel_order_by_id(order_id)
        log.info("order_cancelled", order_id=order_id)
        return {"cancelled": order_id}

    async def _cancel_all_orders(self, _: dict) -> dict:
        cancelled = self.trading.cancel_orders()
        count = len(cancelled) if cancelled else 0
        log.info("all_orders_cancelled", count=count)
        return {"cancelled_count": count}
