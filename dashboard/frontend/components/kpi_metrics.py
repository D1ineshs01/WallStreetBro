"""
KPI metric cards for the top of the dashboard.
Fetches live data from the FastAPI backend.
"""

import requests
import streamlit as st

API_BASE = "http://localhost:8000/api/v1"


def render_portfolio_value():
    """Portfolio value KPI card."""
    try:
        resp = requests.get(f"{API_BASE}/portfolio/account", timeout=3)
        data = resp.json()
        value = float(data.get("portfolio_value", 0))
        equity = float(data.get("equity", value))
        delta = equity - value
        st.metric(
            label="Portfolio Value",
            value=f"${value:,.2f}",
            delta=f"${delta:+,.2f}",
        )
    except Exception:
        st.metric(label="Portfolio Value", value="—", delta=None)


def render_buying_power():
    """Buying power KPI card."""
    try:
        resp = requests.get(f"{API_BASE}/portfolio/account", timeout=3)
        data = resp.json()
        bp = float(data.get("buying_power", 0))
        st.metric(label="Buying Power", value=f"${bp:,.2f}")
    except Exception:
        st.metric(label="Buying Power", value="—")


def render_drawdown():
    """Drawdown KPI card — red when > 2%, orange when > 1%."""
    try:
        resp = requests.get(f"{API_BASE}/portfolio/account", timeout=3)
        data = resp.json()
        portfolio_value = float(data.get("portfolio_value", 0))

        # Try to get peak from session state
        peak = st.session_state.get("peak_portfolio_value", portfolio_value)
        if portfolio_value > peak:
            st.session_state["peak_portfolio_value"] = portfolio_value
            peak = portfolio_value

        drawdown = (peak - portfolio_value) / peak if peak > 0 else 0
        color = "normal"
        if drawdown > 0.02:
            color = "inverse"
        elif drawdown > 0.01:
            color = "off"

        st.metric(
            label="Drawdown",
            value=f"{drawdown:.2%}",
            delta=f"-{drawdown:.2%}" if drawdown > 0 else "0%",
            delta_color="inverse",
        )
    except Exception:
        st.metric(label="Drawdown", value="—")


def render_open_positions():
    """Count of open positions."""
    try:
        resp = requests.get(f"{API_BASE}/portfolio/positions", timeout=3)
        data = resp.json()
        count = len(data.get("positions", []))
        st.metric(label="Open Positions", value=count)
    except Exception:
        st.metric(label="Open Positions", value="—")
