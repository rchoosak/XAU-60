from datetime import datetime

import pandas as pd

from core.strategy_base import Position, Signal
from strategies.bollinger_reversion import BollingerReversion
from strategies.trend_break_trauma import TrendBreakTrauma


def test_bollinger_reversion_two_stage_trailing_profile():
    strategy = BollingerReversion()
    strategy.initialize(
        {
            "enabled": True,
            "symbols": ["XAUUSD"],
            "timeframe": "M15",
            "parameters": {},
            "risk": {
                "trailing_stop": True,
                "trailing_pips": 30.0,
                "trailing_start_pips": 5.0,
                "trailing_pips_wide": 20.0,
                "tighten_after_profit_pips": 50.0,
                "trailing_pips_tight": 10.0,
                "lot_size": 0.01,
            },
            "session": {"use_time_filter": False},
        }
    )

    pos = Position(
        ticket=11,
        symbol="XAUUSD",
        type=Signal.BUY,
        volume=0.01,
        open_price=2000.0,
        stop_loss=1990.0,
        take_profit=2100.0,
        profit=0.0,
        magic_number=1,
        comment="t",
        open_time=datetime(2026, 1, 1),
    )
    wide_data = pd.DataFrame([{"time": datetime(2026, 1, 1, 0, 1), "close": 2030.0}])
    tight_data = pd.DataFrame([{"time": datetime(2026, 1, 1, 0, 2), "close": 2060.0}])

    assert strategy.get_trailing_stop(pos, wide_data) == 2010.0
    assert strategy.get_trailing_stop(pos, tight_data) == 2050.0


def test_trend_break_trauma_two_stage_trailing_profile_for_sell():
    strategy = TrendBreakTrauma()
    strategy.initialize(
        {
            "enabled": True,
            "symbols": ["XAUUSD"],
            "timeframe": "M1",
            "parameters": {},
            "risk": {
                "trailing_stop": True,
                "trailing_pips": 40.0,
                "trailing_start_pips": 5.0,
                "trailing_pips_wide": 30.0,
                "tighten_after_profit_pips": 60.0,
                "trailing_pips_tight": 10.0,
                "lot_size": 0.01,
            },
            "session": {"use_time_filter": False},
        }
    )

    pos = Position(
        ticket=22,
        symbol="XAUUSD",
        type=Signal.SELL,
        volume=0.01,
        open_price=2000.0,
        stop_loss=2010.0,
        take_profit=1900.0,
        profit=0.0,
        magic_number=2,
        comment="t",
        open_time=datetime(2026, 1, 1),
    )
    wide_data = pd.DataFrame([{"time": datetime(2026, 1, 1, 0, 1), "close": 1970.0}])
    tight_data = pd.DataFrame([{"time": datetime(2026, 1, 1, 0, 2), "close": 1930.0}])

    # For SELL: SL moves down from 2010 -> 2000 (wide), then 1940 (tight)
    assert strategy.get_trailing_stop(pos, wide_data) == 2000.0
    assert strategy.get_trailing_stop(pos, tight_data) == 1940.0
