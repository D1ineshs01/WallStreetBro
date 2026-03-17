"""
Tests for the Grok ingestion agent.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ingestion.schemas import SupplyChainEvent, parse_event


class TestIngestionSchemas:

    def test_supply_chain_event_valid(self):
        """SupplyChainEvent should parse valid data."""
        event = SupplyChainEvent(
            summary="Port of Rotterdam closed due to worker strike.",
            disruption_severity="high",
            symbols_affected=["XOM", "BP"],
            companies_affected=["ExxonMobil", "BP"],
            confidence=0.85,
        )
        assert event.category == "supply_chain"
        assert event.confidence == 0.85
        assert len(event.event_id) == 36  # UUID

    def test_confidence_bounds(self):
        """Confidence must be between 0 and 1."""
        with pytest.raises(Exception):
            SupplyChainEvent(
                summary="test",
                disruption_severity="low",
                confidence=1.5,  # Invalid
            )

    def test_parse_event_supply_chain(self):
        """parse_event should dispatch to correct model."""
        data = {
            "category": "supply_chain",
            "summary": "Test event",
            "disruption_severity": "medium",
            "confidence": 0.7,
        }
        event = parse_event(data)
        assert isinstance(event, SupplyChainEvent)

    def test_parse_event_unknown_category(self):
        """parse_event should raise on unknown category."""
        with pytest.raises(ValueError, match="Unknown event category"):
            parse_event({"category": "unknown_type"})


class TestGrokAgent:

    @pytest.mark.asyncio
    async def test_parse_response_valid_json(self):
        """Agent should parse clean JSON array responses."""
        from ingestion.grok_agent import GrokIngestionAgent

        redis = AsyncMock()
        agent = GrokIngestionAgent(redis)

        raw = '[{"category": "supply_chain", "summary": "test", "disruption_severity": "low", "confidence": 0.8}]'
        result = agent._parse_response(raw)
        assert isinstance(result, list)
        assert result[0]["category"] == "supply_chain"

    @pytest.mark.asyncio
    async def test_parse_response_strips_markdown_fences(self):
        """Agent should strip ```json markdown fences from response."""
        from ingestion.grok_agent import GrokIngestionAgent

        redis = AsyncMock()
        agent = GrokIngestionAgent(redis)

        raw = "```json\n[]\n```"
        result = agent._parse_response(raw)
        assert result == []

    @pytest.mark.asyncio
    async def test_parse_response_invalid_json_raises(self):
        """Agent should raise GrokParseError on invalid JSON."""
        from ingestion.grok_agent import GrokIngestionAgent
        from core.exceptions import GrokParseError

        redis = AsyncMock()
        agent = GrokIngestionAgent(redis)

        with pytest.raises(GrokParseError):
            agent._parse_response("not valid json at all")

    @pytest.mark.asyncio
    async def test_empty_response_returns_no_events(self):
        """Agent should handle empty array response gracefully."""
        from ingestion.grok_agent import GrokIngestionAgent

        redis = AsyncMock()
        agent = GrokIngestionAgent(redis)

        result = agent._parse_response("[]")
        assert result == []
