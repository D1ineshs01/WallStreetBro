"""
Grok intelligence event feed panel.
Shows real-time market events published by the Grok scanner.
"""

import requests
import streamlit as st

import os
API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000") + "/api/v1"

SEVERITY_COLORS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}

CATEGORY_ICONS = {
    "supply_chain": "🚢",
    "geopolitical": "🌍",
    "macro": "📊",
    "sentiment": "💬",
}


SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def render() -> None:
    """Render the top-4 worst Grok events on the main dashboard."""
    st.subheader("Top Threats")

    try:
        resp = requests.get(f"{API_BASE}/insights/recent", params={"limit": 50}, timeout=5)
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception as exc:
        st.warning(f"Intelligence feed unavailable: {exc}")
        return

    if not events:
        st.info("No events in database yet — events populate when the agent scans during market hours (9:15 AM – 4:00 PM ET, Mon–Fri).")
        return

    # Sort by severity (critical first) and keep worst 4
    events = sorted(events, key=lambda e: SEVERITY_RANK.get(e.get("disruption_severity", "low"), 3))
    events = events[:4]

    for event in events:
        category = event.get("category", "unknown")
        severity = event.get("disruption_severity", "low")
        symbols = event.get("symbols_affected", [])
        summary = event.get("summary", "")
        confidence = event.get("confidence", 0)
        timestamp = event.get("created_at", "")[:19].replace("T", " ")

        icon = CATEGORY_ICONS.get(category, "📌")
        sev_icon = SEVERITY_COLORS.get(severity, "⚪")

        with st.expander(
            f"{sev_icon} {icon} {category.replace('_', ' ').title()} — {', '.join(symbols[:3]) or 'N/A'}",
            expanded=False,
        ):
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.caption(f"⏰ {timestamp} | Confidence: {confidence:.0%}")
            with col_b:
                severity_badge = {
                    "critical": ":red[CRITICAL]",
                    "high": ":orange[HIGH]",
                    "medium": ":yellow[MEDIUM]",
                    "low": ":green[LOW]",
                }.get(severity, severity.upper())
                st.markdown(severity_badge)

            st.write(summary[:500] + ("..." if len(summary) > 500 else ""))

            if symbols:
                st.caption("Affected: " + " · ".join(f"`{s}`" for s in symbols))
