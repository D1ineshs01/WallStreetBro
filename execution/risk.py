"""
Pre-trade risk engine.
All checks must pass before any order is forwarded to Alpaca.

Check order:
  1. Kill switch (Redis)
  2. Buying power (Alpaca account)
  3. Position size limit (settings)
  4. Portfolio drawdown (Redis metrics)
"""

import structlog
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from config.settings import settings
from core.exceptions import (
    InsufficientBuyingPowerError,
    KillSwitchActivatedError,
    MaxDrawdownBreachedError,
    PositionSizeLimitError,
)
from core.redis_client import RedisClient
from core.state import AgentState

log = structlog.get_logger(__name__)


class RiskEngine:
    """
    Stateless pre-trade risk checker.
    All checks are designed to fail-closed: any exception = halt.
    """

    def __init__(self, trading_client: TradingClient, data_client: StockHistoricalDataClient, redis: RedisClient):
        self.trading = trading_client
        self.data = data_client
        self.redis = redis

    async def check_kill_switch(self) -> None:
        """Raise KillSwitchActivatedError if execution is halted."""
        enabled = await self.redis.get_execution_status()
        if not enabled:
            raise KillSwitchActivatedError(
                "Trading is halted. Set agent:execution_status=1 in Redis to resume."
            )

    async def get_current_ask(self, symbol: str) -> float:
        """Fetch the latest ask price for a symbol from Alpaca market data."""
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self.data.get_stock_latest_quote(req)
            quote = quotes[symbol]
            return float(quote.ask_price)
        except Exception as exc:
            log.error("ask_price_fetch_failed", symbol=symbol, error=str(exc))
            raise InsufficientBuyingPowerError(
                f"Cannot fetch ask price for {symbol}: {exc}"
            ) from exc

    async def calculate_required_buying_power(
        self,
        symbol: str,
        qty: int,
        order_type: str,
        limit_price: Optional[float] = None,
    ) -> float:
        """
        Calculate worst-case buying power required for an order.
        Formula: MAX(limit_price, current_ask * 1.03) * qty
        For market orders: current_ask * 1.03 * qty
        """
        current_ask = await self.get_current_ask(symbol)
        effective_price = current_ask * 1.03  # 3% buffer per Alpaca short-sell rule

        if limit_price and limit_price > effective_price:
            effective_price = limit_price

        required = effective_price * qty
        log.debug(
            "buying_power_required",
            symbol=symbol,
            qty=qty,
            ask=current_ask,
            effective_price=effective_price,
            required=required,
        )
        return required

    async def check_buying_power(self, required: float) -> None:
        """Raise InsufficientBuyingPowerError if account cannot cover the order."""
        account = self.trading.get_account()
        available = float(account.buying_power)
        if available < required:
            raise InsufficientBuyingPowerError(
                f"Insufficient buying power: need ${required:,.2f}, have ${available:,.2f}"
            )
        log.debug("buying_power_ok", required=required, available=available)

    async def check_position_size(self, symbol: str, qty: int) -> None:
        """Ensure the new position does not exceed max_position_size_usd."""
        ask = await self.get_current_ask(symbol)
        position_value = ask * qty
        if position_value > settings.max_position_size_usd:
            raise PositionSizeLimitError(
                f"Order value ${position_value:,.2f} exceeds max position size "
                f"${settings.max_position_size_usd:,.2f} for {symbol}"
            )
        log.debug("position_size_ok", symbol=symbol, value=position_value)

    async def check_drawdown(self, state: AgentState) -> None:
        """Raise MaxDrawdownBreachedError if portfolio drawdown exceeds configured threshold."""
        drawdown = state.get("drawdown_pct", 0.0)
        if drawdown >= settings.max_drawdown_pct:
            raise MaxDrawdownBreachedError(
                f"Portfolio drawdown {drawdown:.1%} >= max {settings.max_drawdown_pct:.1%}. "
                "Kill switch should be active."
            )
        log.debug("drawdown_ok", drawdown=drawdown, max=settings.max_drawdown_pct)

    async def run_all_checks(
        self,
        symbol: str,
        qty: int,
        order_type: str,
        limit_price: Optional[float],
        state: AgentState,
    ) -> None:
        """
        Run all pre-trade checks in sequence.
        Any failure raises a domain exception — callers should NOT catch these;
        let them propagate to cancel the trade.
        """
        log.info("risk_checks_start", symbol=symbol, qty=qty, type=order_type)

        # 1. Kill switch (fastest check, no network call)
        await self.check_kill_switch()

        # 2. Drawdown (Redis read, fast)
        await self.check_drawdown(state)

        # 3. Position size (requires Alpaca quote)
        await self.check_position_size(symbol, qty)

        # 4. Buying power (requires Alpaca account + quote)
        required = await self.calculate_required_buying_power(
            symbol, qty, order_type, limit_price
        )
        await self.check_buying_power(required)

        log.info("risk_checks_passed", symbol=symbol, qty=qty)
