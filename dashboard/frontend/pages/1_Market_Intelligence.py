"""
Market Intelligence Page — Deep dive into Grok event analysis.
"""

import requests
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import os
API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000") + "/api/v1"

st.set_page_config(page_title="Market Intelligence", page_icon="🌍", layout="wide")
st_autorefresh(interval=60_000, key="intel_refresh")

st.title("🌍 Market Intelligence")
st.caption("Real-time events detected by Grok AI scanner — X (Twitter) + Web")

try:
    resp = requests.get(f"{API_BASE}/insights/recent", params={"limit": 100}, timeout=10)
    events = resp.json().get("events", [])
except Exception as exc:
    st.error(f"Cannot load events: {exc}")
    events = []

if events:
    df = pd.DataFrame(events)

    # ── Summary metrics ─────────────────────────────────────────────────
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    col_m1.metric("Total Events", len(df))
    if "disruption_severity" in df.columns:
        col_m2.metric("Critical", len(df[df["disruption_severity"] == "critical"]))
        col_m3.metric("High", len(df[df["disruption_severity"] == "high"]))
    if "confidence" in df.columns:
        avg_conf = df["confidence"].mean()
        col_m4.metric("Avg Confidence", f"{avg_conf:.0%}")

    st.divider()

    # ── Summary charts ───────────────────────────────────────────────────
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

    # ── Full event detail ────────────────────────────────────────────────
    st.subheader("All Scanned Events")

    # Filters
    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        if "category" in df.columns:
            categories = ["All"] + sorted(df["category"].dropna().unique().tolist())
            selected_cat = st.selectbox("Filter by Category", categories)
            if selected_cat != "All":
                df = df[df["category"] == selected_cat]

    with filter_col2:
        if "disruption_severity" in df.columns:
            severities = ["All", "critical", "high", "medium", "low"]
            selected_sev = st.selectbox("Filter by Severity", severities)
            if selected_sev != "All":
                df = df[df["disruption_severity"] == selected_sev]

    # Show each event as an expandable card
    for _, row in df.iterrows():
        severity = row.get("disruption_severity", "low")
        color = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(severity, "⚪")
        category = row.get("category", "unknown").upper()
        summary = row.get("summary", "No summary")
        confidence = row.get("confidence", 0)
        symbols = row.get("symbols_affected", [])
        symbols_str = ", ".join(symbols) if isinstance(symbols, list) else str(symbols)

        with st.expander(f"{color} [{category}] {summary[:100]}... — Confidence: {confidence:.0%}"):
            col_a, col_b = st.columns(2)

            with col_a:
                st.markdown(f"**Severity:** {severity.upper()}")
                st.markdown(f"**Category:** {category}")
                st.markdown(f"**Confidence:** {confidence:.0%}")
                st.markdown(f"**Symbols Affected:** {symbols_str or '—'}")

                companies = row.get("companies_affected", [])
                if companies:
                    companies_str = ", ".join(companies) if isinstance(companies, list) else str(companies)
                    st.markdown(f"**Companies:** {companies_str}")

                if row.get("source_handle"):
                    st.markdown(f"**Source Handle:** @{row['source_handle']}")
                if row.get("source_url"):
                    st.markdown(f"**Source:** [Link]({row['source_url']})")
                if row.get("created_at"):
                    st.markdown(f"**Detected:** {row['created_at']}")

            with col_b:
                st.markdown("**Full Summary:**")
                st.write(row.get("summary", "—"))

                if row.get("invalidation_conditions"):
                    st.markdown("**Invalidation Conditions:**")
                    st.write(row["invalidation_conditions"])

                if row.get("raw_content"):
                    st.markdown("**Raw Content:**")
                    st.caption(str(row["raw_content"])[:500])

            # Category-specific fields
            if row.get("commodities_affected"):
                st.markdown(f"**Commodities:** {', '.join(row['commodities_affected'])}")
            if row.get("regions_affected"):
                st.markdown(f"**Regions:** {', '.join(row['regions_affected'])}")
            if row.get("countries_involved"):
                st.markdown(f"**Countries:** {', '.join(row['countries_involved'])}")
            if row.get("conflict_type"):
                st.markdown(f"**Conflict Type:** {row['conflict_type']}")
            if row.get("indicator"):
                st.markdown(f"**Macro Indicator:** {row['indicator']} — {row.get('direction', '')}")
            if row.get("sentiment_direction"):
                st.markdown(f"**Sentiment:** {row['sentiment_direction'].upper()}")

else:
    st.info("No events yet. Grok scanner runs every 15 minutes — check back shortly.")
    st.caption("The scanner searches X (Twitter) and the web for: supply chain disruptions, geopolitical events, macro data releases, and sentiment shifts.")
