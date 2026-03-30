from datetime import datetime, timedelta

import pandas as pd

from main import TradingBot
from core.strategy_base import Signal, TradeSignal


class _StubSymbolInfo:
    point = 0.01


class _StubMT5:
    def __init__(self, up: bool = True):
        self.up = up

    def get_symbol_info(self, symbol):
        return _StubSymbolInfo()

    def get_ohlcv(self, symbol, timeframe, count):
        now = datetime(2026, 1, 1, 12, 0, 0)
        rows = []
        base = 2000.0
        step = 0.25 if self.up else -0.25
        for i in range(max(220, count)):
            close = base + (step * i)
            rows.append(
                {
                    "time": now - timedelta(minutes=(max(220, count) - i)),
                    "open": close - 0.1,
                    "high": close + 0.2,
                    "low": close - 0.2,
                    "close": close,
                    "volume": 100,
                }
            )
        return pd.DataFrame(rows).tail(count).reset_index(drop=True)


class _StubStrategy:
    timeframe = "M1"
    config = {
        "execution_filters": {"enabled": False},
        "trend_bias_filter": {
            "enabled": True,
            "timeframes_count": 4,
            "timeframe_ladder": ["M1", "M5", "M15", "M30", "H1", "H2", "H4"],
            "lookback_bars": 220,
            "ema_fast_period": 50,
            "ema_slow_period": 200,
            "slope_lookback": 5,
            "min_slope_pips": 0.0,
            "up_score_threshold": 0.5,
            "down_score_threshold": 0.5,
        },
    }


def _signal(side: Signal) -> TradeSignal:
    return TradeSignal(
        signal=side,
        symbol="XAUUSD",
        entry_price=2000.0,
        stop_loss=1990.0 if side == Signal.BUY else 2010.0,
        take_profit=2010.0 if side == Signal.BUY else 1990.0,
        lot_size=0.01,
    )


def test_mtf_bias_blocks_sell_when_consensus_is_uptrend():
    bot = TradingBot()
    bot.mt5 = _StubMT5(up=True)
    bot.config = {"trend_bias_filter": {"enabled": False}}
    strategy = _StubStrategy()

    allowed, reason = bot._passes_execution_filters(
        strategy_name="stub",
        strategy=strategy,
        symbol="XAUUSD",
        signal=_signal(Signal.SELL),
        bar_time=datetime(2026, 1, 1, 12, 0, 0),
        decision_context={},
    )

    assert allowed is False
    assert reason.startswith("mtf_bias_up_blocks_sell")


def test_mtf_bias_blocks_buy_when_consensus_is_downtrend():
    bot = TradingBot()
    bot.mt5 = _StubMT5(up=False)
    bot.config = {"trend_bias_filter": {"enabled": False}}
    strategy = _StubStrategy()

    allowed, reason = bot._passes_execution_filters(
        strategy_name="stub",
        strategy=strategy,
        symbol="XAUUSD",
        signal=_signal(Signal.BUY),
        bar_time=datetime(2026, 1, 1, 12, 0, 0),
        decision_context={},
    )

    assert allowed is False
    assert reason.startswith("mtf_bias_down_blocks_buy")


def test_mtf_bias_timeframes_follow_strategy_timeframe():
    bot = TradingBot()
    cfg = {
        "timeframes_count": 4,
        "timeframe_ladder": ["M1", "M5", "M15", "M30", "H1", "H2", "H4"],
    }

    assert bot._bias_timeframes_from_strategy_tf("M1", cfg) == ["M1", "M5", "M15", "M30"]
    assert bot._bias_timeframes_from_strategy_tf("M5", cfg) == ["M5", "M15", "M30", "H1"]

