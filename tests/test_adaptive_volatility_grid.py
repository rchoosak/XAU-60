from datetime import datetime

import pandas as pd

from core.strategy_base import Position, Signal
from strategies import adaptive_volatility_grid as avg_mod
from strategies.adaptive_volatility_grid import AdaptiveVolatilityGrid


def _base_config():
    return {
        "name": "Adaptive Volatility Grid",
        "enabled": True,
        "symbols": ["XAUUSD"],
        "timeframe": "M1",
        "parameters": {
            "atr_period": 14,
            "grid_atr_multiplier": 1.5,
            "min_grid_spacing_pips": 50.0,
            "max_grid_spacing_pips": 300.0,
            "anchor_ema_period": 34,
            "trend_adx_period": 14,
            "trend_adx_max": 28.0,
            "rsi_period": 14,
            "rsi_oversold": 35.0,
            "rsi_overbought": 65.0,
            "risk_reward": 1.4,
            "take_profit_spacing_multiplier": 1.0,
            "cooldown_bars": 2,
            "reentry_spacing_factor": 0.8,
            "max_signals_per_day": 30,
        },
        "risk": {
            "stop_loss_atr_multiplier": 1.5,
            "stop_loss_min_pips": 60.0,
            "stop_loss_max_pips": 500.0,
            "trailing_stop": True,
            "trailing_pips": 120.0,
            "trailing_start_pips": 0.0,
            "trailing_pips_wide": 100.0,
            "tighten_after_profit_pips": 300.0,
            "trailing_pips_tight": 40.0,
            "lot_size": 0.01,
        },
        "session": {"use_time_filter": False},
    }


def _sample_data(last_close: float, last_time: datetime) -> pd.DataFrame:
    rows = []
    base = datetime(2026, 1, 1, 0, 0)
    for i in range(80):
        close = 2000.0 + (i * 0.02)
        rows.append(
            {
                "time": base + pd.Timedelta(minutes=i),
                "open": close - 0.05,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 100,
            }
        )
    rows[-1]["time"] = last_time
    rows[-1]["close"] = last_close
    rows[-1]["open"] = last_close + 0.05
    rows[-1]["high"] = last_close + 0.1
    rows[-1]["low"] = last_close - 0.1
    return pd.DataFrame(rows)


def test_adaptive_volatility_grid_buy_signal_from_oversold_deviation(monkeypatch):
    strategy = AdaptiveVolatilityGrid()
    strategy.initialize(_base_config())

    def fake_atr(data, period):
        return pd.Series([8.0] * len(data), index=data.index)

    def fake_adx(data, period):
        return pd.Series([20.0] * len(data), index=data.index)

    def fake_rsi(data, period):
        return pd.Series([25.0] * len(data), index=data.index)

    def fake_ema(data, period, column="close"):
        return pd.Series([2000.0] * len(data), index=data.index)

    monkeypatch.setattr(avg_mod, "calculate_atr", fake_atr)
    monkeypatch.setattr(avg_mod, "calculate_adx", fake_adx)
    monkeypatch.setattr(avg_mod, "calculate_rsi", fake_rsi)
    monkeypatch.setattr(avg_mod, "calculate_ema", fake_ema)

    data = _sample_data(last_close=1980.0, last_time=datetime(2026, 1, 1, 2, 0))
    sig = strategy.analyze("XAUUSD", data)

    assert sig is not None
    assert sig.signal == Signal.BUY
    assert sig.entry_price == 1980.0
    assert round(sig.entry_price - sig.stop_loss, 4) == 12.0
    assert round(sig.take_profit - sig.entry_price, 4) == 16.8


def test_adaptive_volatility_grid_blocks_entries_when_trending(monkeypatch):
    strategy = AdaptiveVolatilityGrid()
    strategy.initialize(_base_config())

    monkeypatch.setattr(avg_mod, "calculate_atr", lambda data, period: pd.Series([8.0] * len(data), index=data.index))
    monkeypatch.setattr(avg_mod, "calculate_adx", lambda data, period: pd.Series([45.0] * len(data), index=data.index))
    monkeypatch.setattr(avg_mod, "calculate_rsi", lambda data, period: pd.Series([25.0] * len(data), index=data.index))
    monkeypatch.setattr(avg_mod, "calculate_ema", lambda data, period, column="close": pd.Series([2000.0] * len(data), index=data.index))

    data = _sample_data(last_close=1980.0, last_time=datetime(2026, 1, 1, 2, 0))
    sig = strategy.analyze("XAUUSD", data)
    assert sig is None


def test_adaptive_volatility_grid_respects_cooldown_bars(monkeypatch):
    strategy = AdaptiveVolatilityGrid()
    strategy.initialize(_base_config())

    monkeypatch.setattr(avg_mod, "calculate_atr", lambda data, period: pd.Series([8.0] * len(data), index=data.index))
    monkeypatch.setattr(avg_mod, "calculate_adx", lambda data, period: pd.Series([20.0] * len(data), index=data.index))
    monkeypatch.setattr(avg_mod, "calculate_rsi", lambda data, period: pd.Series([25.0] * len(data), index=data.index))
    monkeypatch.setattr(avg_mod, "calculate_ema", lambda data, period, column="close": pd.Series([2000.0] * len(data), index=data.index))

    data_1 = _sample_data(last_close=1980.0, last_time=datetime(2026, 1, 1, 2, 0))
    data_2 = _sample_data(last_close=1979.0, last_time=datetime(2026, 1, 1, 2, 1))

    sig_1 = strategy.analyze("XAUUSD", data_1)
    sig_2 = strategy.analyze("XAUUSD", data_2)

    assert sig_1 is not None
    assert sig_2 is None


def test_adaptive_volatility_grid_trailing_two_stage():
    strategy = AdaptiveVolatilityGrid()
    strategy.initialize(_base_config())

    pos = Position(
        ticket=1,
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

    wide_data = pd.DataFrame([{"time": datetime(2026, 1, 1, 2, 0), "close": 2010.0}])
    tight_data = pd.DataFrame([{"time": datetime(2026, 1, 1, 2, 1), "close": 2040.0}])

    assert strategy.get_trailing_stop(pos, wide_data) == 2000.0
    assert strategy.get_trailing_stop(pos, tight_data) == 2036.0


def test_adaptive_volatility_grid_should_close_on_reversal_before_sl(monkeypatch):
    strategy = AdaptiveVolatilityGrid()
    cfg = _base_config()
    cfg["risk"]["reversal_exit_enabled"] = True
    cfg["risk"]["reversal_exit_loss_ratio"] = 0.4
    cfg["risk"]["reversal_exit_near_sl_ratio"] = 0.7
    cfg["risk"]["reversal_exit_anchor_flip_pips"] = 5.0
    strategy.initialize(cfg)

    monkeypatch.setattr(
        avg_mod,
        "calculate_ema",
        lambda data, period, column="close": pd.Series([2000.0] * (len(data) - 1) + [1998.0], index=data.index),
    )
    monkeypatch.setattr(avg_mod, "calculate_adx", lambda data, period: pd.Series([25.0] * len(data), index=data.index))
    monkeypatch.setattr(avg_mod, "calculate_rsi", lambda data, period: pd.Series([45.0] * len(data), index=data.index))

    strategy._bar_counter["XAUUSD"] = 100
    strategy._open_bar_by_ticket[777] = 90

    pos = Position(
        ticket=777,
        symbol="XAUUSD",
        type=Signal.BUY,
        volume=0.01,
        open_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        profit=-6.0,
        magic_number=789777,
        comment="",
        open_time=datetime(2026, 1, 1),
    )
    data = _sample_data(last_close=1994.0, last_time=datetime(2026, 1, 1, 3, 0))

    assert strategy.should_close(pos, data) is True
