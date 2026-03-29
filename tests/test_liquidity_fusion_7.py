from datetime import datetime

import pandas as pd

from core.strategy_base import Position, Signal
from strategies import liquidity_fusion_7 as lf7_mod
from strategies.liquidity_fusion_7 import LiquidityFusion7


class _DummyFVG:
    def __init__(self, lower_price: float, upper_price: float, mid_price: float):
        self.lower_price = lower_price
        self.upper_price = upper_price
        self.mid_price = mid_price


class _DummyOB:
    def __init__(self, lower_price: float, upper_price: float):
        self.lower_price = lower_price
        self.upper_price = upper_price


class _DummySMC:
    def detect_bullish_choch(self, data, lookback):
        return (len(data) - 1, float(data.iloc[-1]["high"]))

    def detect_bearish_choch(self, data, lookback):
        return None

    def detect_bullish_fvg(self, data, lookback):
        return _DummyFVG(lower_price=1998.0, upper_price=2002.0, mid_price=2000.0)

    def detect_bearish_fvg(self, data, lookback):
        return None

    def detect_bullish_order_block(self, data, lookback):
        return _DummyOB(lower_price=1997.0, upper_price=2003.0)

    def detect_bearish_order_block(self, data, lookback):
        return None


def _base_config():
    return {
        "name": "Liquidity Fusion 7",
        "enabled": True,
        "symbols": ["XAUUSD"],
        "timeframe": "M1",
        "parameters": {
            "lookback": 20,
            "sweep_buffer_pips": 2.0,
            "sweep_reclaim_ratio": 0.2,
            "choch_lookback": 20,
            "fvg_lookback": 20,
            "ob_lookback": 20,
            "bos_lookback": 10,
            "atr_period": 14,
            "rsi_period": 14,
            "ema_fast_period": 9,
            "ema_slow_period": 21,
            "momentum_rsi_buy_min": 50.0,
            "momentum_rsi_sell_max": 50.0,
            "displacement_atr_min": 0.6,
            "footprint_wick_ratio_min": 0.2,
            "footprint_range_atr_min": 0.8,
            "enable_volume_footprint": True,
            "footprint_volume_multiplier": 1.0,
            "min_score": 5.0,
            "cooldown_bars": 1,
            "max_signals_per_day": 10,
            "risk_reward": 1.8,
        },
        "risk": {
            "stop_loss_atr_multiplier": 1.5,
            "stop_loss_min_pips": 80.0,
            "stop_loss_max_pips": 400.0,
            "take_profit_min_pips": 100.0,
            "trailing_stop": True,
            "trailing_pips": 80.0,
            "trailing_start_pips": 0.0,
            "trailing_pips_wide": 120.0,
            "tighten_after_profit_pips": 300.0,
            "trailing_pips_tight": 50.0,
            "lot_size": 0.01,
        },
        "session": {"use_time_filter": False},
    }


def _sample_data() -> pd.DataFrame:
    rows = []
    base = datetime(2026, 1, 1, 0, 0)
    for i in range(100):
        rows.append(
            {
                "time": base + pd.Timedelta(minutes=i),
                "open": 2000.0,
                "high": 2001.0,
                "low": 1999.0,
                "close": 2000.0,
                "volume": 100.0,
            }
        )
    # Bullish sweep: sweep below prior low, reclaim strongly
    rows[-1] = {
        "time": base + pd.Timedelta(minutes=99),
        "open": 1993.0,
        "high": 2004.0,
        "low": 1990.0,
        "close": 2002.0,
        "volume": 200.0,
    }
    return pd.DataFrame(rows)


def test_liquidity_fusion_7_generates_buy_signal(monkeypatch):
    strategy = LiquidityFusion7()
    strategy.initialize(_base_config())
    strategy.smc = _DummySMC()

    monkeypatch.setattr(
        lf7_mod,
        "calculate_atr",
        lambda data, period: pd.Series([5.0] * len(data), index=data.index),
    )
    monkeypatch.setattr(
        lf7_mod,
        "calculate_rsi",
        lambda data, period: pd.Series([55.0] * len(data), index=data.index),
    )
    monkeypatch.setattr(
        lf7_mod,
        "calculate_ema",
        lambda data, period: pd.Series([1999.0 if period == 9 else 1998.0] * len(data), index=data.index),
    )

    sig = strategy.analyze("XAUUSD", _sample_data())

    assert sig is not None
    assert sig.signal == Signal.BUY
    assert sig.comment == "LF7_BUY"
    assert sig.stop_loss < sig.entry_price < sig.take_profit


def test_liquidity_fusion_7_session_filter_blocks_signal(monkeypatch):
    strategy = LiquidityFusion7()
    cfg = _base_config()
    cfg["session"] = {
        "use_time_filter": True,
        "timezone": "UTC",
        "data_timezone": "UTC",
        "start_hour": 10,
        "end_hour": 11,
        "trade_friday": True,
    }
    strategy.initialize(cfg)
    strategy.smc = _DummySMC()

    monkeypatch.setattr(lf7_mod, "calculate_atr", lambda data, period: pd.Series([5.0] * len(data), index=data.index))
    monkeypatch.setattr(lf7_mod, "calculate_rsi", lambda data, period: pd.Series([55.0] * len(data), index=data.index))
    monkeypatch.setattr(lf7_mod, "calculate_ema", lambda data, period: pd.Series([1999.0] * len(data), index=data.index))

    data = _sample_data()
    data.loc[data.index[-1], "time"] = datetime(2026, 1, 1, 2, 0)
    sig = strategy.analyze("XAUUSD", data)
    assert sig is None


def test_liquidity_fusion_7_trailing_two_stage():
    strategy = LiquidityFusion7()
    strategy.initialize(_base_config())

    pos = Position(
        ticket=1,
        symbol="XAUUSD",
        type=Signal.BUY,
        volume=0.01,
        open_price=2000.0,
        stop_loss=1980.0,
        take_profit=2100.0,
        profit=0.0,
        magic_number=1,
        comment="t",
        open_time=datetime(2026, 1, 1),
    )

    wide_data = pd.DataFrame([{"time": datetime(2026, 1, 1, 0, 1), "close": 2010.0}])
    tight_data = pd.DataFrame([{"time": datetime(2026, 1, 1, 0, 2), "close": 2040.0}])

    # wide=120 pips => 12.0 price distance
    assert strategy.get_trailing_stop(pos, wide_data) == 1998.0
    # tight=50 pips => 5.0 price distance
    assert strategy.get_trailing_stop(pos, tight_data) == 2035.0
