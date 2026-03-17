"""
Pydantic v2 event models for structured market intelligence output from Grok.
These are published to Redis and stored in PostgreSQL.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional, Union
from pydantic import BaseModel, Field


def _new_event_id() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupplyChainEvent(BaseModel):
    event_id: str = Field(default_factory=_new_event_id)
    timestamp: str = Field(default_factory=_now_iso)
    category: Literal["supply_chain"] = "supply_chain"
    channel: str = "market_events:supply_chain_disruptions"
    source: str = "grok_x_search"

    # What happened
    summary: str = Field(..., description="One-paragraph qualitative summary")
    disruption_severity: Literal["low", "medium", "high", "critical"]
    companies_affected: List[str] = Field(default_factory=list)
    commodities_affected: List[str] = Field(default_factory=list)
    regions_affected: List[str] = Field(default_factory=list)

    # Financial impact
    symbols_affected: List[str] = Field(default_factory=list, description="Ticker symbols likely affected")
    confidence: float = Field(..., ge=0.0, le=1.0)

    # Sources
    source_url: Optional[str] = None
    source_handle: Optional[str] = None
    raw_content: str = ""

    # OODA annotation
    invalidation_conditions: str = Field(
        "", description="Variables that would invalidate this analysis (OODA protocol)"
    )


class GeopoliticalEvent(BaseModel):
    event_id: str = Field(default_factory=_new_event_id)
    timestamp: str = Field(default_factory=_now_iso)
    category: Literal["geopolitical"] = "geopolitical"
    channel: str = "market_events:geopolitical"
    source: str = "grok_x_search"

    summary: str
    disruption_severity: Literal["low", "medium", "high", "critical"]
    countries_involved: List[str] = Field(default_factory=list)
    conflict_type: str = Field("", description="e.g. sanctions, military, diplomatic")
    symbols_affected: List[str] = Field(default_factory=list)
    companies_affected: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)

    source_url: Optional[str] = None
    source_handle: Optional[str] = None
    raw_content: str = ""
    invalidation_conditions: str = ""


class MacroEvent(BaseModel):
    event_id: str = Field(default_factory=_new_event_id)
    timestamp: str = Field(default_factory=_now_iso)
    category: Literal["macro"] = "macro"
    channel: str = "market_events:macro"
    source: str = "grok_web_search"

    summary: str
    disruption_severity: Literal["low", "medium", "high", "critical"]
    indicator: str = Field("", description="e.g. CPI, Fed funds rate, GDP")
    direction: str = Field("", description="e.g. beat, miss, in-line")
    symbols_affected: List[str] = Field(default_factory=list)
    companies_affected: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)

    source_url: Optional[str] = None
    source_handle: Optional[str] = None
    raw_content: str = ""
    invalidation_conditions: str = ""


class SentimentShiftEvent(BaseModel):
    event_id: str = Field(default_factory=_new_event_id)
    timestamp: str = Field(default_factory=_now_iso)
    category: Literal["sentiment"] = "sentiment"
    channel: str = "market_events:sentiment"
    source: str = "grok_x_search"

    summary: str
    disruption_severity: Literal["low", "medium", "high", "critical"]
    sentiment_direction: Literal["bullish", "bearish", "neutral"]
    symbols_affected: List[str] = Field(default_factory=list)
    companies_affected: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)

    source_url: Optional[str] = None
    source_handle: Optional[str] = None
    raw_content: str = ""
    invalidation_conditions: str = ""


# Union type for all event variants
MarketEventUnion = Union[SupplyChainEvent, GeopoliticalEvent, MacroEvent, SentimentShiftEvent]

CATEGORY_TO_MODEL = {
    "supply_chain": SupplyChainEvent,
    "geopolitical": GeopoliticalEvent,
    "macro": MacroEvent,
    "sentiment": SentimentShiftEvent,
}


def parse_event(data: dict) -> MarketEventUnion:
    """Parse a raw dict into the appropriate event model based on category."""
    category = data.get("category", "")
    model_cls = CATEGORY_TO_MODEL.get(category)
    if model_cls is None:
        raise ValueError(f"Unknown event category: {category!r}")
    return model_cls(**data)
