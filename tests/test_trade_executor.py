from datetime import datetime

from core.strategy_base import Position, Signal
from core.trade_executor import TradeExecutor, TradeRecord


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
                profit=15.0,
                magic_number=111,
                comment="",
                open_time=datetime.now(),
            )
        ]

    def close_position(self, ticket):
        return True

    def get_tick(self, symbol):
        return {"bid": 2005.0, "ask": 2005.5}


class StubRiskManager:
    def __init__(self):
        self.results = []

    def record_trade_result(self, profit):
        self.results.append(profit)


def test_close_trade_uses_market_side_close_price():
    mt5 = StubMT5()
    risk_manager = StubRiskManager()
    executor = TradeExecutor(mt5, risk_manager)
    executor._active_trades[1] = TradeRecord(
        ticket=1,
        symbol="XAUUSD",
        signal=Signal.BUY,
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        lot_size=0.01,
        strategy="test",
        magic_number=111,
        open_time=datetime.now(),
    )

    ok = executor.close_trade(1, "test-close")

    assert ok is True
    assert len(executor.get_trade_history()) == 1
    assert executor.get_trade_history()[0].close_price == 2005.0
    assert risk_manager.results == [15.0]
