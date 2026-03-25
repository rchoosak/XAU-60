"""
Backtest Page - Strategy backtesting interface.
Uses the real BacktestEngine with historical data from MT5.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.mt5_connector import MT5Connector
from core.backtest_engine import BacktestEngine, BacktestResult
from core.strategy_loader import StrategyLoader


def render_backtest():
    """Render the backtesting page."""
    st.title("📈 Strategy Backtesting")

    # Load available strategies from config
    strategy_loader = StrategyLoader()
    strategy_loader.discover_strategies()

    config_dir = Path(__file__).parent.parent.parent / "config" / "strategies"
    strategy_files = list(config_dir.glob("*.yaml")) if config_dir.exists() else []

    strategies_info = {}
    for f in strategy_files:
        try:
            with open(f, "r") as file:
                config = yaml.safe_load(file)
                name = config.get("name", f.stem)
                strategies_info[name] = {
                    "file_stem": f.stem,
                    "config": config,
                    "timeframe": config.get("timeframe", "M15"),
                    "symbols": config.get("symbols", ["XAUUSD"]),
                }
        except Exception:
            pass

    # Sidebar configuration
    with st.sidebar:
        st.subheader("Backtest Settings")

        strategy_names = list(strategies_info.keys()) if strategies_info else ["No strategies found"]
        selected_strategy = st.selectbox("Strategy", options=strategy_names)

        # Auto-fill symbol and timeframe from strategy config
        strategy_info = strategies_info.get(selected_strategy, {})
        default_symbols = strategy_info.get("symbols", ["XAUUSD"])
        default_tf = strategy_info.get("timeframe", "M15")

        all_symbols = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]
        symbol = st.selectbox(
            "Symbol",
            options=all_symbols,
            index=all_symbols.index(default_symbols[0])
            if default_symbols and default_symbols[0] in all_symbols
            else 0,
        )

        tf_options = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
        timeframe = st.selectbox(
            "Timeframe",
            options=tf_options,
            index=tf_options.index(default_tf) if default_tf in tf_options else 2,
        )

        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input(
                "Start Date", value=datetime.now() - timedelta(days=90)
            )
        with col2:
            end_date = st.date_input("End Date", value=datetime.now())

        initial_balance = st.number_input(
            "Initial Balance ($)", value=10000.0, step=1000.0
        )

        lot_size = st.number_input(
            "Lot Size", value=0.01, step=0.01, format="%.2f"
        )

        spread_pips = st.number_input(
            "Spread (pips)", value=2.0, step=0.5, format="%.1f"
        )

        run_backtest = st.button(
            "Run Backtest", type="primary", use_container_width=True
        )

    # Main content
    if run_backtest:
        if selected_strategy not in strategies_info:
            st.error("No strategy selected.")
            return

        _run_real_backtest(
            strategy_name=selected_strategy,
            strategy_loader=strategy_loader,
            symbol=symbol,
            timeframe=timeframe,
            start_date=datetime.combine(start_date, datetime.min.time()),
            end_date=datetime.combine(end_date, datetime.max.time()),
            initial_balance=initial_balance,
            lot_size=lot_size,
            spread_pips=spread_pips,
        )
    else:
        st.info(
            "Configure backtest parameters in the sidebar and click "
            "'Run Backtest' to start."
        )


# ------------------------------------------------------------------
# Backtest runner
# ------------------------------------------------------------------

def _run_real_backtest(
    strategy_name: str,
    strategy_loader: StrategyLoader,
    symbol: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime,
    initial_balance: float,
    lot_size: float,
    spread_pips: float,
):
    """Run a real backtest using BacktestEngine and display results."""

    # 1. Connect to MT5
    with st.spinner("Connecting to MT5..."):
        mt5 = MT5Connector()
        if not mt5.connect():
            st.error(
                "Failed to connect to MT5. "
                "Check your credentials in `.env` or bridge configuration."
            )
            return

    # 2. Load strategy
    with st.spinner(f"Loading strategy: {strategy_name}..."):
        strategy = strategy_loader.load_strategy(strategy_name)
        if strategy is None:
            st.error(f"Failed to load strategy: {strategy_name}")
            mt5.disconnect()
            return

    # 3. Run backtest
    with st.spinner(
        f"Running backtest: **{strategy_name}** on {symbol} {timeframe} "
        f"({start_date.date()} → {end_date.date()})..."
    ):
        engine = BacktestEngine(mt5)
        result = engine.run_backtest(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            initial_balance=initial_balance,
            lot_size=lot_size,
            spread_pips=spread_pips,
        )

    mt5.disconnect()

    # 4. Display results
    if result is None:
        st.error(
            "Backtest failed — no historical data available for the selected period."
        )
        return

    if result.total_trades == 0:
        st.warning(
            "Backtest completed but **no trades** were generated. "
            "Try adjusting the date range or strategy parameters."
        )

    _display_results(result)


# ------------------------------------------------------------------
# Display helpers
# ------------------------------------------------------------------

def _display_results(result: BacktestResult):
    """Display BacktestResult in the UI."""

    # ---- Key metrics row ----
    st.subheader("Performance Summary")

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        pct = (result.total_profit / result.initial_balance) * 100 if result.initial_balance else 0
        st.metric("Total P&L", f"${result.total_profit:,.2f}", f"{pct:+.2f}%")

    with col2:
        st.metric("Win Rate", f"{result.win_rate:.1f}%")

    with col3:
        st.metric("Profit Factor", f"{result.profit_factor:.2f}")

    with col4:
        st.metric("Max Drawdown", f"{result.max_drawdown_percent:.2f}%")

    with col5:
        st.metric("Sharpe Ratio", f"{result.sharpe_ratio:.2f}")

    st.markdown("---")

    # ---- Equity curve ----
    st.subheader("Equity Curve")

    if result.equity_curve:
        dates = pd.date_range(
            start=result.start_date,
            end=result.end_date,
            periods=len(result.equity_curve),
        )

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=result.equity_curve,
                mode="lines",
                fill="tozeroy",
                line=dict(color="#00c853", width=2),
                fillcolor="rgba(0, 200, 83, 0.1)",
                name="Equity",
            )
        )

        fig.add_hline(
            y=result.initial_balance,
            line_dash="dash",
            line_color="gray",
            annotation_text="Initial Balance",
        )

        fig.update_layout(
            height=400,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title="Date",
            yaxis_title="Equity ($)",
        )
        st.plotly_chart(fig, use_container_width=True)

    # ---- Stats + Distribution ----
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("Detailed Statistics")

        stats_df = pd.DataFrame(
            {
                "Metric": [
                    "Strategy",
                    "Symbol",
                    "Timeframe",
                    "Period",
                    "Initial Balance",
                    "Final Balance",
                    "Total Profit",
                    "Total Pips",
                    "Total Trades",
                    "Winning Trades",
                    "Losing Trades",
                    "Win Rate",
                    "Average Win",
                    "Average Loss",
                    "Largest Win",
                    "Largest Loss",
                    "Profit Factor",
                    "Max Drawdown",
                    "Sharpe Ratio",
                    "Consecutive Wins",
                    "Consecutive Losses",
                ],
                "Value": [
                    str(result.strategy_name),
                    str(result.symbol),
                    str(result.timeframe),
                    f"{result.start_date.date()} → {result.end_date.date()}",
                    f"${result.initial_balance:,.2f}",
                    f"${result.final_balance:,.2f}",
                    f"${result.total_profit:,.2f}",
                    f"{result.total_profit_pips:.1f}",
                    str(result.total_trades),
                    str(result.winning_trades),
                    str(result.losing_trades),
                    f"{result.win_rate:.1f}%",
                    f"${result.average_winner:,.2f}",
                    f"${result.average_loser:,.2f}",
                    f"${result.largest_winner:,.2f}",
                    f"${result.largest_loser:,.2f}",
                    f"{result.profit_factor:.2f}",
                    f"{result.max_drawdown_percent:.2f}%",
                    f"{result.sharpe_ratio:.2f}",
                    str(result.max_consecutive_wins),
                    str(result.max_consecutive_losses),
                ],
            }
        )

        st.dataframe(stats_df, use_container_width=True, hide_index=True)

    with col_right:
        st.subheader("Trade Distribution")

        if result.trades:
            pnls = [t.profit for t in result.trades]

            fig_dist = go.Figure()
            fig_dist.add_trace(
                go.Histogram(x=pnls, nbinsx=20, marker_color="#2196f3")
            )
            fig_dist.update_layout(
                height=300,
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title="P&L ($)",
                yaxis_title="Count",
            )
            st.plotly_chart(fig_dist, use_container_width=True)
        else:
            st.info("No trades to display.")

    # ---- Trade log ----
    st.markdown("---")
    st.subheader("Trade Log")

    if result.trades:
        trades_data = []
        for t in result.trades:
            entry_str = (
                t.entry_time.strftime("%Y-%m-%d %H:%M")
                if hasattr(t.entry_time, "strftime")
                else str(t.entry_time)
            )
            exit_str = (
                t.exit_time.strftime("%Y-%m-%d %H:%M")
                if hasattr(t.exit_time, "strftime")
                else str(t.exit_time)
            )
            trades_data.append(
                {
                    "Entry Time": entry_str,
                    "Exit Time": exit_str,
                    "Type": t.signal.name,
                    "Entry": f"{t.entry_price:.2f}",
                    "Exit": f"{t.exit_price:.2f}",
                    "SL": f"{t.stop_loss:.2f}",
                    "TP": f"{t.take_profit:.2f}",
                    "Lot": t.lot_size,
                    "Pips": f"{t.profit_pips:.1f}",
                    "P&L ($)": f"{t.profit:.2f}",
                    "Exit Reason": t.exit_reason,
                }
            )

        trades_df = pd.DataFrame(trades_data)
        st.dataframe(trades_df, use_container_width=True, hide_index=True)

        # Export
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            csv = trades_df.to_csv(index=False)
            st.download_button(
                "Download Trades CSV",
                csv,
                "backtest_trades.csv",
                "text/csv",
                use_container_width=True,
            )
    else:
        st.info("No trades were generated during the backtest period.")
