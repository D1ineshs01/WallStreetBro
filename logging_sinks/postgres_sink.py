"""
PostgreSQL logging sink.
Persists market events, trade executions, and kill switch events
for audit trail and ASIC regulatory compliance.
"""

from datetime import datetime, timezone
from typing import List, Optional

import structlog
from sqlalchemy import (
    JSON, Boolean, DateTime, Float, Integer, String, Text, select
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.exceptions import DatabaseError
from core.state import MarketEvent, TradeExecution

log = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    pass


class MarketEventRecord(Base):
    __tablename__ = "market_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(64))
    disruption_severity: Mapped[str] = mapped_column(String(16))
    symbols_affected: Mapped[dict] = mapped_column(JSON, default=list)
    companies_affected: Mapped[dict] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_handle: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    raw_content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TradeExecutionRecord(Base):
    __tablename__ = "trade_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    qty: Mapped[int] = mapped_column(Integer)
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(16))
    time_in_force: Mapped[str] = mapped_column(String(8))
    limit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    filled_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KillSwitchEventRecord(Base):
    __tablename__ = "kill_switch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reason: Mapped[str] = mapped_column(Text)
    orders_cancelled: Mapped[int] = mapped_column(Integer, default=0)
    portfolio_value_at_trigger: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PostgresSink:
    """
    Async PostgreSQL sink using SQLAlchemy 2.0 + asyncpg.
    Call init_db() once on startup to create tables.
    """

    def __init__(self, database_url: str):
        self._engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init_db(self) -> None:
        """Create all tables if they don't exist."""
        async with self._engine.begin() as conn:
            await conn.run_sync(lambda conn: Base.metadata.create_all(conn, checkfirst=True))
        log.info("postgres_tables_initialized")

    async def write_market_event(self, event: MarketEvent) -> None:
        """Persist a MarketEvent dict to the market_events table."""
        try:
            async with self._session_factory() as session:
                record = MarketEventRecord(
                    event_id=event["event_id"],
                    channel=event["channel"],
                    category=event["category"],
                    source=event.get("source", "grok"),
                    disruption_severity=event.get("disruption_severity", "low"),
                    symbols_affected=event.get("symbols_affected", []),
                    companies_affected=event.get("companies_affected", []),
                    confidence=event.get("confidence", 0.0),
                    summary=event.get("summary", ""),
                    source_url=event.get("source_url"),
                    source_handle=event.get("source_handle"),
                    raw_content=event.get("raw_content", ""),
                )
                session.add(record)
                await session.commit()
        except Exception as exc:
            raise DatabaseError(f"Failed to write market event: {exc}") from exc

    async def write_trade_execution(self, execution: TradeExecution) -> None:
        """Persist a TradeExecution dict to the trade_executions table."""
        try:
            async with self._session_factory() as session:
                record = TradeExecutionRecord(
                    signal_id=execution["signal_id"],
                    order_id=execution["order_id"],
                    symbol=execution["symbol"],
                    qty=execution["qty"],
                    side=execution["side"],
                    order_type=execution["order_type"],
                    time_in_force=execution["time_in_force"],
                    limit_price=execution.get("limit_price"),
                    status=execution["status"],
                    filled_price=execution.get("filled_price"),
                )
                session.add(record)
                await session.commit()
        except Exception as exc:
            raise DatabaseError(f"Failed to write trade execution: {exc}") from exc

    async def write_kill_switch_event(
        self,
        reason: str,
        orders_cancelled: int,
        portfolio_value: Optional[float] = None,
    ) -> None:
        """Log a kill switch activation to the kill_switch_events table."""
        try:
            async with self._session_factory() as session:
                record = KillSwitchEventRecord(
                    reason=reason,
                    orders_cancelled=orders_cancelled,
                    portfolio_value_at_trigger=portfolio_value,
                )
                session.add(record)
                await session.commit()
            log.info("kill_switch_event_logged", reason=reason, orders_cancelled=orders_cancelled)
        except Exception as exc:
            log.error("kill_switch_db_log_failed", error=str(exc))

    async def get_recent_executions(self, limit: int = 100) -> List[dict]:
        """Fetch the most recent trade executions for the dashboard."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(TradeExecutionRecord)
                .order_by(TradeExecutionRecord.created_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "order_id": r.order_id,
                    "symbol": r.symbol,
                    "qty": r.qty,
                    "side": r.side,
                    "order_type": r.order_type,
                    "limit_price": r.limit_price,
                    "filled_price": r.filled_price,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    async def get_recent_events(self, limit: int = 50) -> List[dict]:
        """Fetch the most recent market events for the dashboard event feed."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(MarketEventRecord)
                .order_by(MarketEventRecord.created_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "event_id": r.event_id,
                    "category": r.category,
                    "disruption_severity": r.disruption_severity,
                    "symbols_affected": r.symbols_affected,
                    "companies_affected": r.companies_affected,
                    "summary": r.summary,
                    "confidence": r.confidence,
                    "source_url": r.source_url,
                    "source_handle": r.source_handle,
                    "raw_content": r.raw_content,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]

    async def close(self) -> None:
        await self._engine.dispose()
