"""
Live Orders & Intelligence — always-on page.
Shows open orders, order history, market quotes, and full event database.
Available 24/7 regardless of whether the trading agent is sleeping.
"""

import os
import requests
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from datetime import datetime

API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000") + "/api/v1"

st.set_page_config(page_title="Live Orders", page_icon="📋", layout="wide")
st_autorefresh(interval=30_000, key="orders_refresh")

st.title("📋 Live Orders & Intelligence")
st.caption("Reads directly from Alpaca + database — available 24/7 even while the agent is sleeping.")

# ── Open Orders ────────────────────────────────────────────────────────
st.subheader("Open Orders")

try:
    resp = requests.get(f"{API_BASE}/portfolio/orders", timeout=5)
    orders = resp.json().get("orders", [])

    if orders:
        df = pd.DataFrame(orders)

        # Colour-code side
        def side_badge(side):
            s = str(side).upper()
            if "BUY" in s:
                return "🟢 BUY"
            elif "SELL" in s:
                return "🔴 SELL"
            return s

        df["side"] = df["side"].apply(side_badge)

        display_cols = ["symbol", "side", "qty", "filled_qty", "order_type",
                        "limit_price", "stop_price", "status", "time_in_force", "created_at"]
        available = [c for c in display_cols if c in df.columns]
        st.dataframe(
            df[available].rename(columns={
                "order_type": "type",
                "limit_price": "limit",
                "stop_price": "stop",
                "time_in_force": "tif",
                "created_at": "placed at",
                "filled_qty": "filled",
            }),
            use_container_width=True,
            hide_index=True,
        )

        # Quick stats
        c1, c2, c3 = st.columns(3)
        buys = sum(1 for o in orders if "BUY" in str(o.get("side", "")).upper())
        sells = len(orders) - buys
        symbols = list({o["symbol"] for o in orders})
        c1.metric("Total Open Orders", len(orders))
        c2.metric("Buys / Sells", f"{buys} / {sells}")
        c3.metric("Symbols", ", ".join(symbols) or "—")

    else:
        st.info("No open orders at the moment.")

except Exception as exc:
    st.error(f"Cannot load orders: {exc}")

st.divider()

# ── Market Quotes for watched symbols ──────────────────────────────────
st.subheader("Market Quotes")

# Symbols: from open orders + user additions
open_symbols = []
try:
    resp = requests.get(f"{API_BASE}/portfolio/orders", timeout=3)
    open_symbols = list({o["symbol"] for o in resp.json().get("orders", [])})
except Exception:
    pass

default_symbols = list(set(open_symbols + ["SPY", "QQQ", "GLD"]))
watch_input = st.text_input(
    "Symbols to watch (comma-separated)",
    value=", ".join(sorted(default_symbols)),
)
watch_symbols = [s.strip().upper() for s in watch_input.split(",") if s.strip()]

if watch_symbols:
    quote_cols = st.columns(min(len(watch_symbols), 4))
    for i, symbol in enumerate(watch_symbols[:8]):
        with quote_cols[i % 4]:
            try:
                q_resp = requests.get(
                    f"{API_BASE}/market/quote/{symbol}", timeout=3
                )
                q = q_resp.json()
                bid = float(q.get("bid", 0))
                ask = float(q.get("ask", 0))
                mid = (bid + ask) / 2 if bid and ask else 0
                spread = ask - bid
                st.metric(
                    label=symbol,
                    value=f"${mid:.2f}",
                    delta=f"spread ${spread:.3f}",
                )
            except Exception:
                st.metric(label=symbol, value="—")

st.divider()

# ── Order History (from DB) ────────────────────────────────────────────
st.subheader("Order History (All Time)")

try:
    hist_resp = requests.get(f"{API_BASE}/portfolio/orders/history", params={"limit": 200}, timeout=5)
    hist_orders = hist_resp.json().get("orders", [])

    if hist_orders:
        df_hist = pd.DataFrame(hist_orders)

        # Summary metrics
        h1, h2, h3, h4 = st.columns(4)
        total = len(df_hist)
        filled = len(df_hist[df_hist.get("status", pd.Series()).str.lower() == "filled"]) if "status" in df_hist.columns else "—"
        unique_syms = df_hist["symbol"].nunique() if "symbol" in df_hist.columns else "—"
        sides = df_hist["side"].value_counts().to_dict() if "side" in df_hist.columns else {}
        h1.metric("Total Orders", total)
        h2.metric("Filled", filled)
        h3.metric("Symbols Traded", unique_syms)
        h4.metric("Buy / Sell", f"{sides.get('buy', 0)} / {sides.get('sell', 0)}")

        # Filter by symbol
        if "symbol" in df_hist.columns:
            sym_filter = st.multiselect(
                "Filter by symbol",
                options=sorted(df_hist["symbol"].unique()),
                default=[],
            )
            if sym_filter:
                df_hist = df_hist[df_hist["symbol"].isin(sym_filter)]

        display = ["symbol", "side", "qty", "order_type", "limit_price",
                   "filled_price", "status", "created_at"]
        avail = [c for c in display if c in df_hist.columns]
        st.dataframe(
            df_hist[avail].rename(columns={
                "order_type": "type",
                "limit_price": "limit",
                "filled_price": "filled @",
                "created_at": "timestamp",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No order history in database yet.")

except Exception as exc:
    st.error(f"Cannot load order history: {exc}")

st.divider()

# ── Full Event Database ────────────────────────────────────────────────
st.subheader("Event Intelligence Database")

SEVERITY_COLORS = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
CATEGORY_ICONS = {
    "supply_chain": "🚢", "geopolitical": "🌍", "macro": "📊", "sentiment": "💬"
}

try:
    ev_resp = requests.get(f"{API_BASE}/insights/recent", params={"limit": 200}, timeout=5)
    events = ev_resp.json().get("events", [])

    if events:
        # Filter controls
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            sev_filter = st.multiselect(
                "Severity",
                ["critical", "high", "medium", "low"],
                default=["critical", "high", "medium", "low"],
            )
        with fc2:
            cat_filter = st.multiselect(
                "Category",
                ["supply_chain", "geopolitical", "macro", "sentiment"],
                default=["supply_chain", "geopolitical", "macro", "sentiment"],
            )
        with fc3:
            sym_search = st.text_input("Symbol search", placeholder="e.g. SPY")

        filtered = [
            e for e in events
            if e.get("disruption_severity", "low") in sev_filter
            and e.get("category", "") in cat_filter
            and (
                not sym_search
                or any(sym_search.upper() in s for s in e.get("symbols_affected", []))
            )
        ]

        st.caption(f"Showing {len(filtered)} of {len(events)} events")

        for event in filtered:
            category = event.get("category", "unknown")
            severity = event.get("disruption_severity", "low")
            symbols = event.get("symbols_affected", [])
            summary = event.get("summary", "")
            confidence = event.get("confidence", 0)
            ts = event.get("created_at", "")[:19].replace("T", " ") if event.get("created_at") else ""

            icon = CATEGORY_ICONS.get(category, "📌")
            sev_icon = SEVERITY_COLORS.get(severity, "⚪")

            with st.expander(
                f"{sev_icon} {icon} {category.replace('_', ' ').title()} — "
                f"{', '.join(symbols[:3]) or 'N/A'} | {ts}",
                expanded=False,
            ):
                col_a, col_b, col_c = st.columns([3, 1, 1])
                with col_a:
                    st.caption(f"⏰ {ts}")
                with col_b:
                    st.caption(f"Confidence: {confidence:.0%}")
                with col_c:
                    badge = {
                        "critical": ":red[CRITICAL]",
                        "high": ":orange[HIGH]",
                        "medium": ":yellow[MEDIUM]",
                        "low": ":green[LOW]",
                    }.get(severity, severity.upper())
                    st.markdown(badge)

                st.write(summary)

                if symbols:
                    st.caption("Affected: " + " · ".join(f"`{s}`" for s in symbols))
    else:
        st.info("No events in database yet. Events are recorded when the agent scans during market hours.")

except Exception as exc:
    st.error(f"Cannot load events: {exc}")
