"""
Tests for the kill switch monitor and risk engine.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.exceptions import (
    KillSwitchActivatedError,
    InsufficientBuyingPowerError,
    MaxDrawdownBreachedError,
)


# ── Kill Switch Monitor Tests ──────────────────────────────────────────

class TestKillSwitchMonitor:

    @pytest.mark.asyncio
    async def test_trigger_sets_redis_flag(self):
        """Kill switch trigger must set execution status to False."""
        from kill_switch.monitor import KillSwitchMonitor

        redis = AsyncMock()
        redis.set_execution_status = AsyncMock()
        redis.publish = AsyncMock()
        redis.get_peak_portfolio_value = AsyncMock(return_value=100_000.0)
        redis.get_manual_kill = AsyncMock(return_value=False)

        trading = MagicMock()
        trading.get_account.return_value = MagicMock(portfolio_value=95_000.0)
        trading.cancel_orders.return_value = []

        monitor = KillSwitchMonitor(redis, trading)
        await monitor._trigger_kill_switch("test_reason")

        redis.set_execution_status.assert_called_once_with(False)
        redis.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_drawdown_check_breach(self):
        """Drawdown check must return False when breach exceeds threshold."""
        from kill_switch.monitor import KillSwitchMonitor

        redis = AsyncMock()
        redis.get_peak_portfolio_value = AsyncMock(return_value=100_000.0)
        redis.set_peak_portfolio_value = AsyncMock()

        trading = MagicMock()
        trading.get_account.return_value = MagicMock(portfolio_value=90_000.0)  # 10% drawdown

        monitor = KillSwitchMonitor(redis, trading)

        with patch("kill_switch.monitor.settings") as mock_settings:
            mock_settings.max_drawdown_pct = 0.05  # 5% threshold
            result = await monitor._check_drawdown()

        assert result is False

    @pytest.mark.asyncio
    async def test_drawdown_check_safe(self):
        """Drawdown check must return True when within threshold."""
        from kill_switch.monitor import KillSwitchMonitor

        redis = AsyncMock()
        redis.get_peak_portfolio_value = AsyncMock(return_value=100_000.0)
        redis.set_peak_portfolio_value = AsyncMock()

        trading = MagicMock()
        trading.get_account.return_value = MagicMock(portfolio_value=98_000.0)  # 2% drawdown

        monitor = KillSwitchMonitor(redis, trading)

        with patch("kill_switch.monitor.settings") as mock_settings:
            mock_settings.max_drawdown_pct = 0.05  # 5% threshold
            result = await monitor._check_drawdown()

        assert result is True

    @pytest.mark.asyncio
    async def test_manual_kill_check(self):
        """Manual kill flag must halt execution."""
        from kill_switch.monitor import KillSwitchMonitor

        redis = AsyncMock()
        redis.get_manual_kill = AsyncMock(return_value=True)
        trading = MagicMock()

        monitor = KillSwitchMonitor(redis, trading)
        result = await monitor._check_manual_kill()
        assert result is False


# ── Risk Engine Tests ──────────────────────────────────────────────────

class TestRiskEngine:

    @pytest.mark.asyncio
    async def test_kill_switch_check_halts_when_disabled(self):
        """Risk engine must raise when kill switch is off."""
        from execution.risk import RiskEngine

        redis = AsyncMock()
        redis.get_execution_status = AsyncMock(return_value=False)

        risk = RiskEngine(MagicMock(), MagicMock(), redis)

        with pytest.raises(KillSwitchActivatedError):
            await risk.check_kill_switch()

    @pytest.mark.asyncio
    async def test_kill_switch_check_passes_when_enabled(self):
        """Risk engine must not raise when kill switch is on."""
        from execution.risk import RiskEngine

        redis = AsyncMock()
        redis.get_execution_status = AsyncMock(return_value=True)

        risk = RiskEngine(MagicMock(), MagicMock(), redis)
        # Should not raise
        await risk.check_kill_switch()

    @pytest.mark.asyncio
    async def test_buying_power_check_raises_when_insufficient(self):
        """Risk engine must raise when buying power is insufficient."""
        from execution.risk import RiskEngine

        trading = MagicMock()
        trading.get_account.return_value = MagicMock(buying_power=1_000.0)

        risk = RiskEngine(trading, MagicMock(), AsyncMock())

        with pytest.raises(InsufficientBuyingPowerError):
            await risk.check_buying_power(required=5_000.0)

    @pytest.mark.asyncio
    async def test_drawdown_check_raises_when_breached(self):
        """Risk engine must raise when drawdown exceeds configured limit."""
        from execution.risk import RiskEngine
        from core.state import initial_state

        risk = RiskEngine(MagicMock(), MagicMock(), AsyncMock())

        state = initial_state()
        state["drawdown_pct"] = 0.10  # 10%

        with patch("execution.risk.settings") as mock_settings:
            mock_settings.max_drawdown_pct = 0.05
            with pytest.raises(MaxDrawdownBreachedError):
                await risk.check_drawdown(state)
