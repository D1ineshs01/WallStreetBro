"""
Execution Log Page — Full history of all trade executions.
"""

import requests
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import os
API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000") + "/api/v1"

st.set_page_config(page_title="Execution Log", page_icon="📋", layout="wide")
st_autorefresh(interval=30_000, key="exec_log_refresh")

st.title("📋 Execution Log")
st.caption("Complete audit trail of all AI-generated trades")

try:
    resp = requests.get(f"{API_BASE}/portfolio/executions", params={"limit": 500}, timeout=10)
    executions = resp.json().get("executions", [])

    if executions:
        df = pd.DataFrame(executions)

        # ── Summary metrics ────────────────────────────────────────────
        total_trades = len(df)
        buys = len(df[df.get("side", pd.Series()).str.lower().str.contains("buy", na=False)])
        sells = total_trades - buys

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Executions", total_trades)
        c2.metric("Buy Orders", buys)
        c3.metric("Sell Orders", sells)

        st.divider()

        # ── Filters ────────────────────────────────────────────────────
        if "symbol" in df.columns:
            symbols = ["All"] + sorted(df["symbol"].unique().tolist())
            selected_symbol = st.selectbox("Filter by Symbol", symbols)
            if selected_symbol != "All":
                df = df[df["symbol"] == selected_symbol]

        # ── Full table ─────────────────────────────────────────────────
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "order_id": st.column_config.TextColumn("Order ID", width="small"),
                "symbol": st.column_config.TextColumn("Symbol"),
                "side": st.column_config.TextColumn("Side"),
                "qty": st.column_config.NumberColumn("Qty"),
                "order_type": st.column_config.TextColumn("Type"),
                "limit_price": st.column_config.NumberColumn("Limit", format="$%.2f"),
                "filled_price": st.column_config.NumberColumn("Filled", format="$%.2f"),
                "status": st.column_config.TextColumn("Status"),
                "created_at": st.column_config.TextColumn("Timestamp"),
            },
        )
    else:
        st.info("No executions recorded yet.")
except Exception as exc:
    st.error(f"Cannot load execution log: {exc}")
