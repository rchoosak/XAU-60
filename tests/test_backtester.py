from datetime import datetime

from core.backtester import Backtester
from core.strategy_base import Signal


def test_close_position_uses_backtest_bar_time_for_exit():
    bt = Backtester({"commission": 0.0})
    entry_time = datetime(2026, 3, 1, 10, 0, 0)
    exit_time = datetime(2026, 3, 1, 10, 15, 0)
    pos = {
        "signal": Signal.BUY,
        "entry_price": 2000.0,
        "stop_loss": 1990.0,
        "take_profit": 2010.0,
        "entry_time": entry_time,
        "lot_size": 0.1,
    }

    trade = bt._close_position(
        pos=pos,
        exit_price=2001.0,
        reason="TP",
        symbol="XAUUSD",
        point=0.01,
        exit_time=exit_time,
    )

    assert trade["exit_time"] == exit_time
