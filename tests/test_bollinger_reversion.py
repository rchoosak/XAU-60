from datetime import datetime

import pandas as pd

from core.strategy_base import Signal
from strategies import bollinger_reversion as br_mod
from strategies.bollinger_reversion import BollingerReversion


def test_xau_pip_scaling_for_stop_loss_and_take_profit(monkeypatch):
    strategy = BollingerReversion()
    strategy.initialize(
        {
            "name": "Bollinger Reversion",
            "enabled": True,
            "symbols": ["XAUUSD"],
            "timeframe": "M15",
            "parameters": {
                "bb_period": 20,
                "bb_std_dev": 2.0,
                "rsi_period": 14,
                "rsi_oversold": 30.0,
                "rsi_overbought": 70.0,
            },
            "risk": {
                "stop_loss_pips": 100.0,
                "take_profit_pips": 300.0,
                "use_dynamic_tp": False,
                "lot_size": 0.01,
            },
            "session": {"use_time_filter": False},
        }
    )

    def fake_bb(data, period=20, std_dev=2.0):
        idx = data.index
        return (
            pd.Series([2010.0] * len(idx), index=idx),
            pd.Series([2001.0] * len(idx), index=idx),
            pd.Series([1999.0] * len(idx), index=idx),
        )

    def fake_rsi(data, period=14):
        return pd.Series([20.0] * len(data), index=data.index)

    monkeypatch.setattr(br_mod, "calculate_bollinger_bands", fake_bb)
    monkeypatch.setattr(br_mod, "calculate_rsi", fake_rsi)

    data = pd.DataFrame(
        [
            {
                "time": datetime(2026, 1, 1, 8, 0),
                "open": 2000.5,
                "high": 2001.0,
                "low": 1998.9,
                "close": 2000.8,
                "volume": 100,
            }
        ]
        * 30
    )

    sig = strategy.analyze("XAUUSD", data)
    assert sig is not None
    assert sig.signal == Signal.BUY
    # 100 pips on XAU (pip=0.1) => 10.0 price units
    assert round(sig.entry_price - sig.stop_loss, 6) == 10.0
    # 300 pips => 30.0 price units
    assert round(sig.take_profit - sig.entry_price, 6) == 30.0


def test_trading_time_uses_data_timezone_and_session_timezone():
    strategy = BollingerReversion()
    strategy.initialize(
        {
            "name": "Bollinger Reversion",
            "enabled": True,
            "symbols": ["XAUUSD"],
            "timeframe": "M15",
            "parameters": {},
            "risk": {},
            "session": {
                "use_time_filter": True,
                "timezone": "Asia/Bangkok",
                "data_timezone": "UTC",
                "start_hour": 8,
                "end_hour": 9,
                "trade_friday": True,
            },
        }
    )

    # Naive timestamp interpreted as UTC -> 01:15 UTC == 08:15 Asia/Bangkok
    data = pd.DataFrame(
        [{"time": datetime(2026, 1, 1, 1, 15), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    )
    assert strategy._is_trading_time(data) is True
