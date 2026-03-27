from datetime import datetime

import pandas as pd

import core.backtester as backtester_module
import core.trade_executor as trade_executor_module
from core.backtester import Backtester
from core.strategy_base import Signal, TradeSignal, Position, StrategyBase
from core.trade_executor import TradeExecutor, TradeRecord


class OneShotStrategy(StrategyBase):
    name = "OneShot"

    def __init__(self):
        super().__init__()
        self.fired = False

    def initialize(self, config):
        pass

    def analyze(self, symbol, data):
        if self.fired:
            return None
        self.fired = True
        entry = float(data.iloc[-1]["close"])
        return TradeSignal(
            signal=Signal.BUY,
            symbol=symbol,
            entry_price=entry,
            stop_loss=entry - 1.0,
            take_profit=entry + 0.01,
            lot_size=0.01,
            comment="ONESHOT_BUY",
        )

    def should_close(self, position, data):
        return False


def test_backtester_emits_trade_journal_events(monkeypatch):
    events = []
    monkeypatch.setattr(backtester_module, "append_trade_event", lambda e: events.append(e))

    bt = Backtester(
        {
            "warmup": 0,
            "lookback": 10,
            "save_trades": False,
            "log_signals": False,
            "use_trailing_stop": False,
            "spread_pips": 0.0,
            "commission": 0.0,
        }
    )
    bt.data = pd.DataFrame(
        [
            {"time": datetime(2026, 1, 1, 0, 0), "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1},
            {"time": datetime(2026, 1, 1, 0, 15), "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.05, "volume": 1},
            {"time": datetime(2026, 1, 1, 0, 30), "open": 100.05, "high": 100.2, "low": 100.0, "close": 100.1, "volume": 1},
        ]
    )
    bt.symbol = "XAUUSD"
    bt.timeframe = "M15"
    bt.executor = OneShotStrategy()
    bt.current_index = 0
    bt.balance = bt.initial_balance
    bt.equity = bt.initial_balance
    bt.positions = []
    bt.trades = []

    while bt.step() is not None:
        pass

    kinds = [e["event"] for e in events]
    assert "OPEN" in kinds
    assert "CLOSE" in kinds
    assert all(e["mode"] == "backtest" for e in events)


class StubMT5:
    def get_positions(self):
        return [
            Position(
                ticket=1,
                symbol="XAUUSD",
                type=Signal.BUY,
                volume=0.01,
                open_price=2000.0,
                stop_loss=1990.0,
                take_profit=2020.0,
                profit=10.0,
                magic_number=111,
                comment="",
                open_time=datetime.now(),
            )
        ]

    def close_position(self, ticket):
        return True

    def get_tick(self, symbol):
        return {"bid": 2001.0, "ask": 2001.5}


class StubRiskManager:
    def can_open_trade(self, symbol, signal):
        return True, "OK"

    def validate_trade_signal(self, symbol, signal, entry, sl, tp):
        return True, "Valid"

    def record_trade_result(self, profit):
        return None

    def calculate_lot_size(self, symbol, sl_pips):
        return 0.01


def test_trade_executor_emits_live_close_event(monkeypatch):
    events = []
    monkeypatch.setattr(trade_executor_module, "append_trade_event", lambda e: events.append(e))

    ex = TradeExecutor(StubMT5(), StubRiskManager())
    ex._active_trades[1] = TradeRecord(
        ticket=1,
        symbol="XAUUSD",
        signal=Signal.BUY,
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        lot_size=0.01,
        strategy="TestStrategy",
        magic_number=111,
        open_time=datetime.now(),
    )

    assert ex.close_trade(1, "test") is True
    assert any(e.get("event") == "CLOSE" and e.get("mode") == "live" for e in events)
