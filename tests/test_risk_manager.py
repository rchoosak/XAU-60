from dataclasses import dataclass

from core.risk_manager import RiskManager


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
