"""
Domain-specific exception hierarchy for Wall Street Bro.
All custom exceptions derive from WallStreetBroError so callers
can catch the entire domain with a single except clause.
"""


class WallStreetBroError(Exception):
    """Base exception for all Wall Street Bro domain errors."""


# ── Kill Switch ────────────────────────────────────────────────────────
class KillSwitchActivatedError(WallStreetBroError):
    """Raised when a trade is attempted while the kill switch is active."""


class KillSwitchTriggerError(WallStreetBroError):
    """Raised when the kill switch monitor fails to activate the switch."""


# ── Risk / Execution ───────────────────────────────────────────────────
class InsufficientBuyingPowerError(WallStreetBroError):
    """Raised when an order would exceed available buying power."""


class PositionSizeLimitError(WallStreetBroError):
    """Raised when an order would exceed the configured max position size."""


class MaxDrawdownBreachedError(WallStreetBroError):
    """Raised when portfolio drawdown exceeds the configured threshold."""


class InvalidTradeSchemaError(WallStreetBroError):
    """Raised when Claude generates a tool call that violates the trade JSON Schema."""


class AlpacaExecutionError(WallStreetBroError):
    """Raised when Alpaca returns an error on order submission."""


# ── Ingestion ──────────────────────────────────────────────────────────
class GrokRateLimitError(WallStreetBroError):
    """Raised when the Grok API rate limit is exceeded."""


class GrokParseError(WallStreetBroError):
    """Raised when Grok returns a response that cannot be parsed into a market event."""


class CollectionsAPIError(WallStreetBroError):
    """Raised when the xAI Collections (RAG) API returns an error."""


# ── Infrastructure ─────────────────────────────────────────────────────
class RedisConnectionError(WallStreetBroError):
    """Raised when the Redis client cannot connect or publish/subscribe fails."""


class DatabaseError(WallStreetBroError):
    """Raised when a PostgreSQL write or read operation fails."""


# ── Orchestration ──────────────────────────────────────────────────────
class OrchestratorError(WallStreetBroError):
    """Raised when the LangGraph supervisor produces an unroutable decision."""


class SupervisorRoutingError(OrchestratorError):
    """Raised when the supervisor node returns an invalid next_node value."""
