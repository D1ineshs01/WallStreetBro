"""
REST endpoints for historical market data (OHLCV bars, quotes).
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


@router.get("/bars/{symbol}")
async def get_bars(
    symbol: str,
    request: Request,
    timeframe: str = Query("1Day", pattern="^(1Min|5Min|15Min|1Hour|1Day)$"),
    limit: int = Query(100, ge=1, le=1000),
):
    """
    Fetch OHLCV bars from Alpaca for a given symbol.
    Used by the Streamlit candlestick chart component.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed

    from config.settings import settings

    timeframe_map = {
        "1Min": TimeFrame.Minute,
        "5Min": TimeFrame.Minute,
        "15Min": TimeFrame.Minute,
        "1Hour": TimeFrame.Hour,
        "1Day": TimeFrame.Day,
    }

    client = StockHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )

    try:
        tf = timeframe_map.get(timeframe, TimeFrame.Day)
        req = StockBarsRequest(symbol_or_symbols=symbol.upper(), timeframe=tf, limit=limit, feed=DataFeed.IEX)
        bars = client.get_stock_bars(req)
        bar_list = bars[symbol.upper()] if symbol.upper() in bars else []

        if bar_list:
            return {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "bars": [
                    {
                        "timestamp": str(b.timestamp),
                        "open": float(b.open),
                        "high": float(b.high),
                        "low": float(b.low),
                        "close": float(b.close),
                        "volume": int(b.volume),
                    }
                    for b in bar_list
                ],
            }

        # Alpaca returned no data — fall back to yfinance (free, no API key needed)
        import yfinance as yf

        period_map = {
            "1Min": ("7d", "1m"),
            "5Min": ("60d", "5m"),
            "15Min": ("60d", "15m"),
            "1Hour": ("730d", "1h"),
            "1Day": ("max", "1d"),
        }
        safe_limit = min(limit, 100)  # Cap at 100 bars to prevent memory issues
        yf_period, yf_interval = period_map.get(timeframe, ("max", "1d"))
        ticker = yf.Ticker(symbol.upper())
        df = ticker.history(period=yf_period, interval=yf_interval)
        df = df.tail(safe_limit)

        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "source": "yfinance",
            "bars": [
                {
                    "timestamp": str(ts),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                }
                for ts, row in df.iterrows()
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/quote/{symbol}")
async def get_quote(symbol: str):
    """Get the latest bid/ask quote for a symbol."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest

    from config.settings import settings

    client = StockHistoricalDataClient(
        api_key=settings.alpaca_api_key,
        secret_key=settings.alpaca_secret_key,
    )

    try:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol.upper())
        quotes = client.get_stock_latest_quote(req)
        q = quotes[symbol.upper()]
        return {
            "symbol": symbol.upper(),
            "bid": float(q.bid_price),
            "ask": float(q.ask_price),
            "bid_size": q.bid_size,
            "ask_size": q.ask_size,
            "timestamp": str(q.timestamp),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
