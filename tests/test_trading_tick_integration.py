from datetime import datetime

import pandas as pd

from main import TradingBot
from core.strategy_base import Position, Signal, TradeSignal


class StubStrategy:
    symbols = ["XAUUSD"]
    timeframe = "M15"
    enabled = True

    def __init__(self):
        self.opened_positions = []

    def analyze(self, symbol, data):
        return TradeSignal(
            signal=Signal.BUY,
            symbol=symbol,
            entry_price=float(data.iloc[-1]["close"]),
            stop_loss=float(data.iloc[-1]["close"]) - 1.0,
            take_profit=float(data.iloc[-1]["close"]) + 1.0,
            lot_size=0.01,
        )

    def on_trade_opened(self, position):
        self.opened_positions.append(position)

    def should_close(self, position, data):
        return False


class StubLoader:
    def __init__(self, strategy):
        self.strategy = strategy

    def get_enabled_strategies(self):
        return {"stub": self.strategy}


class StubMT5:
    def __init__(self, bar_time):
        self.bar_time = bar_time

    def get_ohlcv(self, symbol, timeframe, count):
        return pd.DataFrame(
            [
                {
                    "time": self.bar_time,
                    "open": 2000.0,
                    "high": 2001.0,
                    "low": 1999.0,
                    "close": 2000.5,
                    "volume": 100,
                }
            ]
        )

    def get_positions(self):
        return [
            Position(
                ticket=123,
                symbol="XAUUSD",
                type=Signal.BUY,
                volume=0.01,
                open_price=2000.5,
                stop_loss=1999.5,
                take_profit=2001.5,
                profit=0.0,
                magic_number=1,
                comment="",
                open_time=self.bar_time,
            )
        ]


class StubExecutor:
    def __init__(self):
        self.calls = 0

    def execute_signal(self, signal, strategy_name, strategy_risk=None):
        self.calls += 1
        return 123

    def manage_positions(self, strategies):
        return None


def test_tick_processes_each_bar_once_and_sends_real_position():
    bar_time = datetime(2026, 3, 27, 10, 0, 0)
    strategy = StubStrategy()
    bot = TradingBot()
    bot.strategy_loader = StubLoader(strategy)
    bot.mt5 = StubMT5(bar_time)
    bot.trade_executor = StubExecutor()

    bot._tick()
    bot._tick()

    assert bot.trade_executor.calls == 1
    assert len(strategy.opened_positions) == 1
    assert strategy.opened_positions[0].ticket == 123
