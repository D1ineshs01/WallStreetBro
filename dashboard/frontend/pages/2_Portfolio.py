"""
Portfolio Page — Open positions, P&L breakdown, and account health.
"""

import requests
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import os
API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000") + "/api/v1"

st.set_page_config(page_title="Portfolio", page_icon="💼", layout="wide")
st_autorefresh(interval=15_000, key="portfolio_refresh")

st.title("💼 Portfolio")

# ── Account Summary ────────────────────────────────────────────────────
try:
    acc_resp = requests.get(f"{API_BASE}/portfolio/account", timeout=5)
    acc = acc_resp.json()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio Value", f"${float(acc.get('portfolio_value', 0)):,.2f}")
    c2.metric("Cash", f"${float(acc.get('cash', 0)):,.2f}")
    c3.metric("Buying Power", f"${float(acc.get('buying_power', 0)):,.2f}")
    c4.metric("Account Status", acc.get("status", "—"))
except Exception as exc:
    st.error(f"Cannot load account: {exc}")

st.divider()

# ── Positions ──────────────────────────────────────────────────────────
st.subheader("Open Positions")

try:
    pos_resp = requests.get(f"{API_BASE}/portfolio/positions", timeout=5)
    positions = pos_resp.json().get("positions", [])

    if positions:
        df = pd.DataFrame(positions)
        numeric_cols = ["avg_entry_price", "current_price", "unrealized_pl", "unrealized_plpc", "market_value"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # P&L bar chart
        if "unrealized_pl" in df.columns and "symbol" in df.columns:
            colors = ["#00e676" if v >= 0 else "#ff1744" for v in df["unrealized_pl"]]
            fig = px.bar(
                df, x="symbol", y="unrealized_pl",
                title="Unrealized P&L by Position",
                color="unrealized_pl",
                color_continuous_scale=["#ff1744", "white", "#00e676"],
            )
            st.plotly_chart(fig, use_container_width=True)

        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions.")
except Exception as exc:
    st.error(f"Cannot load positions: {exc}")
