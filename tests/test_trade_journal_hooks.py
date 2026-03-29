import core.trade_executor as trade_executor_module
from core.strategy_base import Signal, Position
from core.trade_executor import TradeExecutor, TradeRecord
from datetime import datetime


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
