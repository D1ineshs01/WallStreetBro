"""
Tests for trade schemas and execution agent.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import jsonschema

from execution.trade_schemas import PLACE_ORDER_SCHEMA, ALL_EXECUTION_TOOLS


class TestTradeSchemas:

    def _validate(self, schema: dict, data: dict):
        """Helper to validate data against a JSON schema."""
        jsonschema.validate(data, schema["input_schema"])

    def test_valid_market_order(self):
        """Valid market order should pass schema validation."""
        self._validate(PLACE_ORDER_SCHEMA, {
            "symbol": "AAPL",
            "qty": 10,
            "side": "buy",
            "type": "market",
            "time_in_force": "day",
            "rationale": "Supply chain event triggered bullish signal",
        })

    def test_valid_limit_order(self):
        """Valid limit order should pass schema validation."""
        self._validate(PLACE_ORDER_SCHEMA, {
            "symbol": "SPY",
            "qty": 5,
            "side": "sell",
            "type": "limit",
            "time_in_force": "gtc",
            "limit_price": 450.50,
            "rationale": "Taking profit at resistance level",
        })

    def test_invalid_side_rejected(self):
        """Invalid side value should fail schema validation."""
        with pytest.raises(jsonschema.ValidationError):
            self._validate(PLACE_ORDER_SCHEMA, {
                "symbol": "AAPL",
                "qty": 10,
                "side": "long",  # Invalid — must be buy or sell
                "type": "market",
                "time_in_force": "day",
                "rationale": "test",
            })

    def test_invalid_symbol_pattern_rejected(self):
        """Lowercase symbol should fail regex validation."""
        with pytest.raises(jsonschema.ValidationError):
            self._validate(PLACE_ORDER_SCHEMA, {
                "symbol": "aapl",  # Must be uppercase
                "qty": 10,
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "rationale": "test",
            })

    def test_additional_properties_rejected(self):
        """Extra fields should be rejected by additionalProperties: false."""
        with pytest.raises(jsonschema.ValidationError):
            self._validate(PLACE_ORDER_SCHEMA, {
                "symbol": "AAPL",
                "qty": 10,
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "rationale": "test",
                "hallucinated_field": "this should fail",  # Not in schema
            })

    def test_zero_qty_rejected(self):
        """Zero quantity should fail minimum constraint."""
        with pytest.raises(jsonschema.ValidationError):
            self._validate(PLACE_ORDER_SCHEMA, {
                "symbol": "AAPL",
                "qty": 0,  # minimum: 1
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "rationale": "test",
            })

    def test_all_tools_have_required_fields(self):
        """Every tool schema must have name, description, and input_schema."""
        for tool in ALL_EXECUTION_TOOLS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool missing 'description': {tool}"
            assert "input_schema" in tool, f"Tool missing 'input_schema': {tool}"
            assert tool["input_schema"].get("additionalProperties") is False, \
                f"Tool '{tool['name']}' must have additionalProperties: false"


class TestExecutionAgentSignalFormat:

    def test_signal_message_format(self):
        """Execution agent should format signal messages correctly."""
        from execution.execution_agent import ExecutionAgent
        from core.state import initial_state, TradeSignal

        agent = ExecutionAgent(MagicMock(), AsyncMock())

        signal: TradeSignal = {
            "signal_id": "test-123",
            "symbol": "AAPL",
            "direction": "buy",
            "rationale": "Test rationale",
            "confidence": 0.85,
            "suggested_qty": 10,
            "suggested_limit_price": None,
            "generated_by": "test",
            "timestamp": "2026-03-17T00:00:00Z",
        }

        state = initial_state()
        state["current_portfolio_value"] = 100_000.0

        message = agent._format_signal_message(signal, state)
        assert "AAPL" in message
        assert "BUY" in message
        assert "10 shares" in message
        assert "85%" in message
