from dataclasses import dataclass

from core.risk_manager import RiskManager, RiskLimits


@dataclass
class StubAccount:
    balance: float
    equity: float
    margin_level: float = 500.0
    currency: str = "USD"


class StubMT5:
    def __init__(self):
        self.account = StubAccount(balance=1000.0, equity=950.0)

    def get_account_info(self):
        return self.account

    def get_positions(self):
        return []

    def get_symbol_info(self, symbol):
        return None


def test_daily_limit_check_handles_zero_starting_balance():
    mt5 = StubMT5()
    rm = RiskManager(mt5)
    assert rm.initialize() is True
    rm._daily_stats.starting_balance = 0.0

    assert rm._is_daily_limit_reached() is False


@dataclass
class StubSymbolInfo:
    point: float = 0.01
    tick_value: float = 1.0
    tick_size: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01


class StubMT5WithSymbol(StubMT5):
    def get_symbol_info(self, symbol):
        return StubSymbolInfo()


def test_calculate_lot_size_uses_global_capital_base():
    mt5 = StubMT5WithSymbol()
    mt5.account.balance = 100000.0
    mt5.account.equity = 100000.0
    rm = RiskManager(mt5, RiskLimits(max_risk_per_trade=2.0, capital_base=10000.0))
    assert rm.initialize() is True

    lot = rm.calculate_lot_size("XAUUSD", stop_loss_pips=100.0)

    # 2% of 10,000 => risk_amount 200; pip value ~= 10; lot ~= 0.20
    assert lot == 0.2


def test_calculate_lot_size_allows_strategy_capital_override():
    mt5 = StubMT5WithSymbol()
    mt5.account.balance = 100000.0
    mt5.account.equity = 100000.0
    rm = RiskManager(mt5, RiskLimits(max_risk_per_trade=2.0, capital_base=10000.0))
    assert rm.initialize() is True

    lot = rm.calculate_lot_size(
        "XAUUSD",
        stop_loss_pips=100.0,
        risk_percent=2.0,
        capital_base=20000.0,
    )

    # 2% of 20,000 => risk_amount 400; pip value ~= 10; lot ~= 0.40
    assert lot == 0.4
