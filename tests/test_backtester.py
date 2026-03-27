from datetime import datetime

from core.backtester import Backtester
from core.strategy_base import Signal
from core.strategy_base import TradeSignal


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


def test_calculate_lot_size_returns_zero_when_below_min_lot():
    bt = Backtester(
        {
            "min_lot": 0.01,
            "lot_step": 0.01,
            "risk_per_trade": 0.000001,
            "leverage": 100.0,
            "lot_size": 0,
        }
    )
    sig = TradeSignal(
        signal=Signal.BUY,
        symbol="XAUUSD",
        entry_price=2000.0,
        stop_loss=1900.0,
        take_profit=2100.0,
        lot_size=0,
    )
    lot = bt._calculate_lot_size(
        balance=1000.0,
        sig=sig,
        point=0.01,
        current_price=2000.0,
        positions=[],
        symbol="XAUUSD",
    )
    assert lot == 0.0


def test_calculate_lot_size_rounds_to_lot_step():
    bt = Backtester(
        {
            "min_lot": 0.01,
            "lot_step": 0.01,
            "risk_per_trade": 0.005,
            "leverage": 100.0,
            "lot_size": 0,
        }
    )
    sig = TradeSignal(
        signal=Signal.BUY,
        symbol="XAUUSD",
        entry_price=2000.0,
        stop_loss=1999.25,
        take_profit=2001.5,
        lot_size=0,
    )
    lot = bt._calculate_lot_size(
        balance=10000.0,
        sig=sig,
        point=0.01,
        current_price=2000.0,
        positions=[],
        symbol="XAUUSD",
    )
    assert lot >= 0.01
    assert abs((lot / 0.01) - round(lot / 0.01)) < 1e-9


def test_calculate_lot_size_xau_uses_contract_size_correctly():
    bt = Backtester(
        {
            "risk_per_trade": 0.01,
            "leverage": 100.0,
            "lot_size": 0,
            "min_lot": 0.01,
            "lot_step": 0.01,
        }
    )
    sig = TradeSignal(
        signal=Signal.BUY,
        symbol="XAUUSD",
        entry_price=2000.0,
        stop_loss=1990.0,  # $10 distance
        take_profit=2010.0,
        lot_size=0,
    )
    lot = bt._calculate_lot_size(
        balance=10000.0,
        sig=sig,
        point=0.01,
        current_price=2000.0,
        positions=[],
        symbol="XAUUSD",
    )
    # 1% of $10,000 = $100 risk with $10/0.01-lot SL => 0.10 lots.
    assert lot == 0.1


def test_backtester_respects_zero_risk_and_zero_default_lot():
    bt = Backtester(
        {
            "risk_per_trade": 0.0,
            "lot_size": 0.0,
            "min_lot": 0.01,
            "lot_step": 0.01,
        }
    )
    sig = TradeSignal(
        signal=Signal.BUY,
        symbol="XAUUSD",
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2010.0,
        lot_size=0,
    )
    lot = bt._calculate_lot_size(
        balance=10000.0,
        sig=sig,
        point=0.01,
        current_price=2000.0,
        positions=[],
        symbol="XAUUSD",
    )
    assert lot == 0.0
