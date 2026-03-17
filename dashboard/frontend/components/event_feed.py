"""
Grok intelligence event feed panel.
Shows real-time market events published by the Grok scanner.
"""

import requests
import streamlit as st

API_BASE = "http://localhost:8000/api/v1"

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


def render() -> None:
    """Render the Grok event feed panel."""
    st.subheader("Market Intelligence Feed")

    try:
        resp = requests.get(f"{API_BASE}/insights/recent", params={"limit": 20}, timeout=5)
        events = resp.json().get("events", [])
    except Exception:
        st.warning("Cannot connect to intelligence feed.")
        return

    if not events:
        st.info("No market events detected yet. Scanner is running...")
        return

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
            expanded=(severity in ("critical", "high")),
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
