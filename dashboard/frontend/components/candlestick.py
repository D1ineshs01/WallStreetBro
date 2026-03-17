"""
Interactive candlestick chart with trade execution overlays.
Uses Plotly go.Candlestick + scatter markers for buy/sell trades.
"""

import requests
import pandas as pd
import plotly.graph_objs as go
import streamlit as st

API_BASE = "http://localhost:8000/api/v1"


def render(symbol: str, timeframe: str = "1Day", limit: int = 100) -> None:
    """
    Render an interactive OHLCV candlestick chart for the given symbol.
    Overlays executed trades as green (buy) or red (sell) triangle markers.
    """
    # ── Fetch OHLCV bars ───────────────────────────────────────────────
    try:
        resp = requests.get(
            f"{API_BASE}/market/bars/{symbol}",
            params={"timeframe": timeframe, "limit": limit},
            timeout=10,
        )
        bars_data = resp.json().get("bars", [])
    except Exception as exc:
        st.error(f"Failed to load chart data: {exc}")
        return

    if not bars_data:
        st.info(f"No bar data available for {symbol}")
        return

    df = pd.DataFrame(bars_data)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # ── Fetch trade executions ─────────────────────────────────────────
    try:
        exec_resp = requests.get(f"{API_BASE}/portfolio/executions", params={"limit": 200}, timeout=5)
        executions = exec_resp.json().get("executions", [])
    except Exception:
        executions = []

    # Filter executions for this symbol
    symbol_execs = [e for e in executions if e.get("symbol") == symbol]

    # ── Build Plotly figure ────────────────────────────────────────────
    fig = go.Figure()

    # Candlestick trace
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=symbol,
            increasing_line_color="#26a69a",   # Teal for up candles
            decreasing_line_color="#ef5350",   # Red for down candles
        )
    )

    # Volume bars (secondary y-axis)
    if "volume" in df.columns:
        fig.add_trace(
            go.Bar(
                x=df["timestamp"],
                y=df["volume"],
                name="Volume",
                marker_color="rgba(100, 100, 255, 0.3)",
                yaxis="y2",
            )
        )

    # Trade overlays
    buys = [e for e in symbol_execs if e.get("side", "").lower() in ("buy", "OrderSide.BUY")]
    sells = [e for e in symbol_execs if e.get("side", "").lower() in ("sell", "OrderSide.SELL")]

    if buys:
        buy_prices = [e.get("filled_price") or e.get("limit_price") for e in buys]
        buy_times = [e.get("created_at") for e in buys]
        fig.add_trace(
            go.Scatter(
                x=buy_times,
                y=buy_prices,
                mode="markers",
                name="Buy",
                marker=dict(
                    symbol="triangle-up",
                    size=14,
                    color="#00e676",
                    line=dict(color="white", width=1),
                ),
            )
        )

    if sells:
        sell_prices = [e.get("filled_price") or e.get("limit_price") for e in sells]
        sell_times = [e.get("created_at") for e in sells]
        fig.add_trace(
            go.Scatter(
                x=sell_times,
                y=sell_prices,
                mode="markers",
                name="Sell",
                marker=dict(
                    symbol="triangle-down",
                    size=14,
                    color="#ff1744",
                    line=dict(color="white", width=1),
                ),
            )
        )

    # ── Layout ─────────────────────────────────────────────────────────
    fig.update_layout(
        title=dict(text=f"{symbol} — {timeframe}", font=dict(size=16)),
        xaxis_title="Time",
        yaxis_title="Price (USD)",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        height=500,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis2=dict(
            title="Volume",
            overlaying="y",
            side="right",
            showgrid=False,
            range=[0, df["volume"].max() * 5] if "volume" in df.columns else None,
        ),
        margin=dict(l=40, r=40, t=60, b=40),
    )

    st.plotly_chart(fig, use_container_width=True)
