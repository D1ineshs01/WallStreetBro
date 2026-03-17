"""
Tests for the LangGraph orchestration layer.
"""

import pytest
from unittest.mock import MagicMock, patch

from core.state import AgentState, initial_state


class TestAgentState:

    def test_initial_state_fail_closed(self):
        """Initial state must have execution_enabled=False (fail-closed)."""
        state = initial_state()
        assert state["execution_enabled"] is False
        assert state["kill_switch_active"] is False
        assert state["next_node"] == "ingestion"

    def test_initial_state_empty_pipelines(self):
        """Initial state must have empty event/signal/execution lists."""
        state = initial_state()
        assert state["market_events"] == []
        assert state["trade_signals"] == []
        assert state["trade_executions"] == []


class TestRouting:

    def test_route_valid_nodes(self):
        """Valid node names should route correctly."""
        from orchestration.nodes import route_from_supervisor

        for node in ["ingestion", "execution", "visualization", "kill_switch", "end"]:
            state = initial_state()
            state["next_node"] = node
            result = route_from_supervisor(state)
            assert result == node

    def test_route_invalid_node_falls_back_to_end(self):
        """Invalid node name should fall back to 'end' without raising."""
        from orchestration.nodes import route_from_supervisor

        state = initial_state()
        state["next_node"] = "nonexistent_node"
        result = route_from_supervisor(state)
        assert result == "end"

    def test_route_missing_next_node_defaults_to_end(self):
        """Missing next_node key should default to 'end'."""
        from orchestration.nodes import route_from_supervisor

        state = initial_state()
        del state["next_node"]
        result = route_from_supervisor(state)
        assert result == "end"


class TestSignalGeneration:

    def test_low_confidence_events_ignored(self):
        """Events with confidence < 0.7 should not generate signals."""
        from orchestration.nodes import _generate_signals_from_events
        from ingestion.schemas import SupplyChainEvent

        event = SupplyChainEvent(
            summary="Minor port delay",
            disruption_severity="high",
            symbols_affected=["XOM"],
            confidence=0.5,  # Below threshold
        )

        signals = _generate_signals_from_events([event])
        assert len(signals) == 0

    def test_high_confidence_critical_events_generate_signals(self):
        """CRITICAL events with high confidence should generate sell signals."""
        from orchestration.nodes import _generate_signals_from_events
        from ingestion.schemas import SupplyChainEvent

        event = SupplyChainEvent(
            summary="Major port closure — global shipping disrupted",
            disruption_severity="critical",
            symbols_affected=["ZIM", "MATX"],
            confidence=0.92,
        )

        signals = _generate_signals_from_events([event])
        assert len(signals) > 0
        assert all(s["direction"] == "sell" for s in signals)
        assert all(s["confidence"] >= 0.7 for s in signals)
