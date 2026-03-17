"""
Market Intelligence Page — Deep dive into Grok event analysis.
"""

import requests
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import os
API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000") + "/api/v1"

st.set_page_config(page_title="Market Intelligence", page_icon="🌍", layout="wide")
st_autorefresh(interval=60_000, key="intel_refresh")

st.title("🌍 Market Intelligence")
st.caption("Real-time events detected by Grok AI scanner")

try:
    resp = requests.get(f"{API_BASE}/insights/recent", params={"limit": 100}, timeout=10)
    events = resp.json().get("events", [])
except Exception as exc:
    st.error(f"Cannot load events: {exc}")
    events = []

if events:
    df = pd.DataFrame(events)

    # ── Summary charts ─────────────────────────────────────────────────
    col1, col2 = st.columns(2)

    with col1:
        if "category" in df.columns:
            cat_counts = df["category"].value_counts().reset_index()
            fig = px.pie(cat_counts, values="count", names="category", title="Events by Category")
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        if "disruption_severity" in df.columns:
            sev_counts = df["disruption_severity"].value_counts().reset_index()
            color_map = {"critical": "#ff1744", "high": "#ff9100", "medium": "#ffea00", "low": "#00e676"}
            fig2 = px.bar(
                sev_counts, x="disruption_severity", y="count",
                title="Events by Severity",
                color="disruption_severity",
                color_discrete_map=color_map,
            )
            st.plotly_chart(fig2, use_container_width=True)

    st.divider()
    st.subheader("All Events")
    display_cols = ["category", "disruption_severity", "confidence", "summary", "created_at"]
    available = [c for c in display_cols if c in df.columns]
    st.dataframe(df[available], use_container_width=True, hide_index=True)
else:
    st.info("No events yet. Grok scanner is running...")
