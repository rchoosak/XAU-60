"""
Streamlit Trading Bot UI - Main Application.
"""
import sys
import json
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

# Page configuration
st.set_page_config(
    page_title="MT5 Trading Bot",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .stMetric {
        background-color: var(--secondary-background-color);
        padding: 10px;
        border-radius: 5px;
        border: 1px solid var(--faded-text-color);
    }
    .profit {
        color: #00c853;
    }
    .loss {
        color: #ff1744;
    }
</style>
""", unsafe_allow_html=True)


def main():
    """Main application entry point."""
    st.sidebar.title("📈 Trading Bot")

    # Navigation
    page = st.sidebar.radio(
        "Navigation",
        ["Dashboard", "Strategies", "Settings"],
        index=0
    )

    st.sidebar.markdown("---")
    state_file = Path(__file__).parent.parent / "data" / "live_state.json"
    live_status = "🔴 Offline"
    mt5_status = "Unknown"
    last_sync = "N/A"
    try:
        if state_file.exists():
            with open(state_file, "r") as f:
                state = json.load(f)
            last_updated = float(state.get("last_updated", 0) or 0)
            if last_updated > 0:
                last_sync = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_updated))
                if time.time() - last_updated < 30:
                    live_status = "🟢 Live"
            account_info = state.get("account_info", {}) if isinstance(state, dict) else {}
            mt5_status = account_info.get("server", "Connected") if live_status.startswith("🟢") else "Disconnected"
        else:
            mt5_status = "Disconnected"
    except Exception:
        live_status = "🟡 Unknown"
        mt5_status = "Unknown"

    st.sidebar.markdown(f"**Status:** {live_status}")
    st.sidebar.markdown(f"**MT5:** {mt5_status}")
    st.sidebar.caption(f"Last sync: {last_sync}")

    # Page routing
    if page == "Dashboard":
        from pages.dashboard import render_dashboard
        render_dashboard()
    elif page == "Strategies":
        from pages.strategies import render_strategies
        render_strategies()
    elif page == "Settings":
        from pages.settings import render_settings
        render_settings()


if __name__ == "__main__":
    main()
