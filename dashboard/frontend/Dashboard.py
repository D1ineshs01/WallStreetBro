"""
Wall Street Bro — Streamlit Dashboard

Main entry point for the real-time trading dashboard.
Run with: streamlit run dashboard/frontend/app.py

Architecture note: Streamlit runs in its own process separate from
the FastAPI backend and agent loop, communicating via REST + SSE.
"""

import os
import sys

import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# Fix import path for Streamlit Cloud
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from components import candlestick, event_feed, kpi_metrics

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000") + "/api/v1"

# ── Page config ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Wall Street Bro",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Auto-refresh every 30 seconds ─────────────────────────────────────
count = st_autorefresh(interval=30_000, key="dashboard_refresh")

# ── Sidebar ────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("📈 Wall Street Bro")
    st.caption("Autonomous Trading Intelligence")
    st.divider()

    # Symbol selector
    symbol = st.selectbox(
        "Focus Symbol",
        ["SPY", "QQQ", "AAPL", "TSLA", "NVDA", "GLD", "USO", "TLT"],
        index=0,
    )

    # Timeframe
    timeframe = st.selectbox(
        "Chart Timeframe",
        ["1Min", "5Min", "15Min", "1Hour", "1Day"],
        index=4,
    )

    # Chart bar count
    bar_limit = st.slider("Bars", min_value=20, max_value=500, value=100, step=10)

    st.divider()

    # ── Kill Switch ────────────────────────────────────────────────────
    st.subheader("Risk Controls")

    # Read current kill switch state
    kill_active = False
    try:
        resp = requests.get(f"{API_BASE}/portfolio/account", timeout=2)
        # Status inferred from API availability (kill switch state is in Redis)
    except Exception:
        pass

    kill_col1, kill_col2 = st.columns(2)
    with kill_col1:
        if st.button("🛑 HALT TRADING", type="primary", use_container_width=True):
            try:
                r = requests.post(f"{API_BASE}/portfolio/kill-switch/activate", timeout=5)
                if r.status_code == 200:
                    st.success("Kill switch activated!")
                    st.session_state["kill_switch_active"] = True
            except Exception as exc:
                st.error(f"Failed: {exc}")

    with kill_col2:
        if st.button("▶ CLEAR KILL", use_container_width=True):
            try:
                r = requests.post(f"{API_BASE}/portfolio/kill-switch/deactivate", timeout=5)
                if r.status_code == 200:
                    st.success("Manual kill cleared.")
                    st.session_state["kill_switch_active"] = False
            except Exception as exc:
                st.error(f"Failed: {exc}")

    if st.session_state.get("kill_switch_active"):
        st.warning("⚠️ KILL SWITCH ACTIVE — Trading halted")

    st.divider()
    st.caption(f"Auto-refresh #{count} | {symbol} / {timeframe}")


# ── Kill switch banner ─────────────────────────────────────────────────
if st.session_state.get("kill_switch_active"):
    st.error("🛑 KILL SWITCH ACTIVE — All trading has been halted")

# ── Header ─────────────────────────────────────────────────────────────
st.title("Wall Street Bro — Trading Intelligence Dashboard")

# ── KPI Row ────────────────────────────────────────────────────────────
kpi1, kpi2, kpi3, kpi4 = st.columns(4)

with kpi1:
    kpi_metrics.render_portfolio_value()
with kpi2:
    kpi_metrics.render_buying_power()
with kpi3:
    kpi_metrics.render_drawdown()
with kpi4:
    kpi_metrics.render_open_positions()

st.divider()

# ── Main Content: Chart + Event Feed ──────────────────────────────────
chart_col, feed_col = st.columns([3, 1])

with chart_col:
    candlestick.render(symbol=symbol, timeframe=timeframe, limit=bar_limit)

with feed_col:
    event_feed.render()

# ── Execution Log ──────────────────────────────────────────────────────
st.divider()
st.subheader("Recent Trade Executions")

try:
    exec_resp = requests.get(f"{API_BASE}/portfolio/executions", params={"limit": 50}, timeout=5)
    executions = exec_resp.json().get("executions", [])

    if executions:
        import pandas as pd
        df = pd.DataFrame(executions)
        # Clean up columns for display
        display_cols = ["symbol", "side", "qty", "order_type", "limit_price", "filled_price", "status", "created_at"]
        available = [c for c in display_cols if c in df.columns]
        st.dataframe(
            df[available].rename(columns={
                "order_type": "type",
                "created_at": "timestamp",
                "limit_price": "limit",
                "filled_price": "filled",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No executions recorded yet.")
except Exception:
    st.info("Execution history unavailable — check API connection.")

# ── Open Positions ─────────────────────────────────────────────────────
st.subheader("Open Positions")
try:
    pos_resp = requests.get(f"{API_BASE}/portfolio/positions", timeout=5)
    positions = pos_resp.json().get("positions", [])

    if positions:
        import pandas as pd
        df_pos = pd.DataFrame(positions)
        st.dataframe(df_pos, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions.")
except Exception:
    st.info("Positions unavailable — check API connection.")
