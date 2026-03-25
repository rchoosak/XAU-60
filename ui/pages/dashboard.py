"""
Dashboard Page - Live trading overview.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import json
import time
from pathlib import Path


def render_dashboard():
    """Render the dashboard page."""
    st.title("📊 Trading Dashboard")

    state_file = Path("data/live_state.json")
    state = {}
    is_live = False

    try:
        if state_file.exists():
            with open(state_file, "r") as f:
                state = json.load(f)
            
            # Check if the file was updated recently (e.g., last 30 seconds)
            last_updated = state.get("last_updated", 0)
            if time.time() - last_updated < 30:
                is_live = True
    except Exception as e:
        st.error(f"Error reading state file: {e}")

    # Top metrics row
    col1, col2, col3, col4, col5 = st.columns(5)
    
    acc = state.get("account_info", {})
    pos = state.get("positions", [])
    trades = state.get("recent_trades", [])

    if not is_live:
        st.warning("⚠️ **Bot is currently OFFLINE or State is stale.** (Start `python main.py` to see live metrics)")
        balance = 0.0
        equity = 0.0
        open_pnl = 0.0
        margin_level = 0.0
    else:
        st.success("🟢 **Bot is LIVE and syncing data.**")
        balance = acc.get("balance", 0.0)
        equity = acc.get("equity", 0.0)
        open_pnl = sum(p.get("profit", 0.0) for p in pos)
        margin_level = acc.get("margin_level", 0.0)

    # Calculate today's P&L approximately from recent trades
    today_pnl = 0.0
    # In a full implementation, you'd filter history_deals_get by today. 

    with col1:
        st.metric("Balance", f"${balance:,.2f}")

    with col2:
        eq_diff = equity - balance
        st.metric("Equity", f"${equity:,.2f}", f"{eq_diff:+,.2f}" if eq_diff != 0 else None)

    with col3:
        st.metric("Open P&L", f"${open_pnl:+,.2f}", f"{(open_pnl/balance*100):+.2f}%" if balance else "0.00%")

    with col4:
        st.metric("Margin Level", f"{margin_level:,.2f}%" if margin_level > 0 else "N/A")

    with col5:
        st.metric("Open Positions", str(len(pos)))

    st.markdown("---")

    col_left, col_right = st.columns([2, 1])

    with col_left:
        # Open positions
        st.subheader(f"Open Positions ({len(pos)})")

        if is_live and pos:
            pos_data = []
            for p in pos:
                pos_data.append({
                    "Ticket": p.get("ticket"),
                    "Symbol": p.get("symbol"),
                    "Type": "BUY" if p.get("type") == 0 else "SELL",
                    "Volume": p.get("volume"),
                    "Entry": p.get("price_open"),
                    "Current": p.get("price_current"),
                    "P&L": f"${p.get('profit', 0.0):+,.2f}",
                })
            st.dataframe(pd.DataFrame(pos_data), use_container_width=True, hide_index=True)
        else:
            st.info("No open positions")

    with col_right:
        # Risk status
        st.subheader("Account Info")

        if is_live:
            st.write(f"**Broker:** {acc.get('company', 'N/A')}")
            st.write(f"**Server:** {acc.get('server', 'N/A')}")
            st.write(f"**Currency:** {acc.get('currency', 'USD')}")
            st.write(f"**Leverage:** 1:{acc.get('leverage', 1)}")
            st.write(f"**Free Margin:** ${acc.get('margin_free', 0.0):,.2f}")

        st.markdown("---")

        # Recent trades log
        st.subheader("Recent Signals Executed")
        
        if is_live and trades:
            for t in reversed(trades[-5:]): # show last 5
                st.markdown(
                    f"**{t.get('open_time')}** - {t.get('symbol')} {t.get('signal')} "
                    f"`{t.get('strategy')}` (Lot: {t.get('lot_size')})"
                )
        else:
            st.info("No recent trades")

    # Add a manual refresh button
    if st.button("Refresh Dashboard"):
        st.rerun()
