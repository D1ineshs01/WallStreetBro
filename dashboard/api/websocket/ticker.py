"""
WebSocket handler for live tick data.
Connects to Alpaca's real-time data stream and forwards
bid/ask/price updates to connected browser clients.
"""

import asyncio
import json
from typing import Set

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = structlog.get_logger(__name__)
router = APIRouter()


class ConnectionManager:
    """Manages a set of active WebSocket connections per symbol."""

    def __init__(self):
        # symbol -> set of WebSocket connections
        self.connections: dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, symbol: str) -> None:
        await websocket.accept()
        if symbol not in self.connections:
            self.connections[symbol] = set()
        self.connections[symbol].add(websocket)
        log.debug("websocket_connected", symbol=symbol, total=len(self.connections[symbol]))

    def disconnect(self, websocket: WebSocket, symbol: str) -> None:
        if symbol in self.connections:
            self.connections[symbol].discard(websocket)
        log.debug("websocket_disconnected", symbol=symbol)

    async def broadcast(self, symbol: str, message: dict) -> None:
        """Broadcast a message to all connections watching a symbol."""
        if symbol not in self.connections:
            return
        dead: Set[WebSocket] = set()
        for ws in self.connections[symbol].copy():
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                dead.add(ws)
        # Clean up dead connections
        for ws in dead:
            self.connections[symbol].discard(ws)


manager = ConnectionManager()


@router.websocket("/ws/ticks/{symbol}")
async def tick_websocket(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for live tick data.

    Connects to Alpaca WebSocket stream for the given symbol.
    Forwards real-time quote updates (bid, ask, price, volume) to the browser.
    Implements heartbeat ping every 30s to detect stale connections.
    """
    symbol = symbol.upper()
    await manager.connect(websocket, symbol)

    heartbeat_task = asyncio.create_task(_heartbeat(websocket))

    try:
        # Try to use Alpaca stream if available
        alpaca_task = asyncio.create_task(_stream_alpaca_quotes(symbol))

        # Also listen for client messages (for graceful disconnect)
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=35.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # No message from client — connection may be stale
                try:
                    await websocket.send_text(json.dumps({"type": "keepalive"}))
                except Exception:
                    break

    except WebSocketDisconnect:
        log.info("websocket_client_disconnected", symbol=symbol)
    except Exception as exc:
        log.error("websocket_error", symbol=symbol, error=str(exc))
    finally:
        heartbeat_task.cancel()
        manager.disconnect(websocket, symbol)


async def _heartbeat(websocket: WebSocket) -> None:
    """Send a ping every 30 seconds to keep the connection alive."""
    while True:
        await asyncio.sleep(30)
        try:
            await websocket.send_text(json.dumps({"type": "ping"}))
        except Exception:
            break


async def _stream_alpaca_quotes(symbol: str) -> None:
    """
    Subscribe to Alpaca real-time quote updates for a symbol.
    Broadcasts each quote update to all connected WebSocket clients.
    Falls back to simulated data if Alpaca stream is unavailable.
    """
    from config.settings import settings

    try:
        from alpaca.data.live import StockDataStream

        stream = StockDataStream(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )

        async def quote_handler(quote):
            message = {
                "type": "quote",
                "symbol": quote.symbol,
                "bid": float(quote.bid_price),
                "ask": float(quote.ask_price),
                "bid_size": quote.bid_size,
                "ask_size": quote.ask_size,
                "timestamp": str(quote.timestamp),
            }
            await manager.broadcast(symbol, message)

        stream.subscribe_quotes(quote_handler, symbol)
        await stream._run_forever()

    except Exception as exc:
        log.warning("alpaca_stream_unavailable", error=str(exc), symbol=symbol)
        # Fallback: poll REST quote endpoint every 2 seconds
        await _poll_quote_fallback(symbol)


async def _poll_quote_fallback(symbol: str) -> None:
    """Fallback polling when Alpaca WebSocket stream is unavailable."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest
    from config.settings import settings

    client = StockHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )

    while True:
        await asyncio.sleep(2)
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = client.get_stock_latest_quote(req)
            q = quotes[symbol]
            message = {
                "type": "quote",
                "symbol": symbol,
                "bid": float(q.bid_price),
                "ask": float(q.ask_price),
                "timestamp": str(q.timestamp),
            }
            await manager.broadcast(symbol, message)
        except Exception as exc:
            log.debug("quote_poll_error", symbol=symbol, error=str(exc))
