from core.strategy_base import Signal, TradeSignal
from core.trade_executor import TradeExecutor


class _StubMT5:
    def __init__(self):
        self.orders = []

    class _SymbolInfo:
        point = 0.01

    def get_symbol_info(self, symbol):
        return self._SymbolInfo()

    def place_market_order(
        self,
        symbol,
        order_type,
        volume,
        stop_loss=0.0,
        take_profit=0.0,
        magic=0,
        comment="",
        slippage=10,
    ):
        self.orders.append(
            {
                "symbol": symbol,
                "order_type": order_type,
                "volume": volume,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "magic": magic,
                "comment": comment,
                "slippage": slippage,
            }
        )
        return True, 99


class _CaptureRiskManager:
    def __init__(self):
        self.calls = []

    def can_open_trade(self, symbol, signal):
        return True, "OK"

    def validate_trade_signal(self, symbol, signal, entry_price, stop_loss, take_profit):
        return True, "Valid"

    def calculate_lot_size(self, symbol, stop_loss_pips, risk_percent=None, capital_base=None):
        self.calls.append(
            {
                "symbol": symbol,
                "stop_loss_pips": stop_loss_pips,
                "risk_percent": risk_percent,
                "capital_base": capital_base,
            }
        )
        return 0.07


def test_execute_signal_passes_strategy_risk_overrides_to_lot_calc():
    mt5 = _StubMT5()
    risk_manager = _CaptureRiskManager()
    executor = TradeExecutor(mt5=mt5, risk_manager=risk_manager, default_lot_size=0.0)

    signal = TradeSignal(
        signal=Signal.BUY,
        symbol="XAUUSD",
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        lot_size=0.0,
        magic_number=1,
    )

    ticket = executor.execute_signal(
        signal,
        strategy_name="Any Strategy",
        strategy_risk={"max_risk_percent": 0.5, "capital_base": 10000},
    )

    assert ticket == 99
    assert len(risk_manager.calls) == 1
    assert risk_manager.calls[0]["risk_percent"] == 0.5
    assert risk_manager.calls[0]["capital_base"] == 10000.0
    assert mt5.orders[0]["volume"] == 0.07
