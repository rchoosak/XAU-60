from datetime import datetime
from types import SimpleNamespace

import pandas as pd

import core.mt5_connector as mt5_connector_module
import main as main_module
from core.strategy_base import Position, Signal, TradeSignal
from core.trade_executor import TradeExecutor


class _FakeMT5API:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240
    TIMEFRAME_D1 = 1440
    TIMEFRAME_W1 = 10080
    TIMEFRAME_MN1 = 43200
    TIMEFRAME_S1 = 1001
    TIMEFRAME_H2 = 120

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 10
    ORDER_TIME_GTC = 20
    ORDER_FILLING_IOC = 30
    TRADE_RETCODE_DONE = 10009

    def __init__(self):
        self.init_calls = []
        self.login_calls = []
        self.shutdown_calls = 0
        self.symbol_select_calls = []
        self.order_requests = []
        self._ticket = 5000

    def initialize(self, **kwargs):
        self.init_calls.append(kwargs)
        return True

    def login(self, login, password=None, server=None):
        self.login_calls.append({"login": login, "password": password, "server": server})
        return True

    def shutdown(self):
        self.shutdown_calls += 1

    def last_error(self):
        return 0, "ok"

    def account_info(self):
        return SimpleNamespace(
            login=123456,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            margin_free=10000.0,
            margin_level=0.0,
            profit=0.0,
            currency="USD",
            leverage=100,
            server="Demo-Server",
            company="Broker",
        )

    def terminal_info(self):
        return SimpleNamespace(
            name="terminal",
            trade_allowed=True,
            tradeapi_disabled=False,
        )

    def symbol_select(self, symbol, enabled):
        self.symbol_select_calls.append((symbol, enabled))
        return True

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        now = int(datetime(2026, 3, 31, 0, 0, 0).timestamp())
        rows = []
        for i in range(count):
            rows.append(
                {
                    "time": now + i * 60,
                    "open": 2000.0 + i * 0.1,
                    "high": 2000.5 + i * 0.1,
                    "low": 1999.5 + i * 0.1,
                    "close": 2000.2 + i * 0.1,
                    "tick_volume": 100 + i,
                }
            )
        return rows

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(
            bid=2000.0,
            ask=2000.2,
            last=2000.1,
            volume=100,
            time=int(datetime(2026, 3, 31, 0, 0, 0).timestamp()),
        )

    def order_send(self, request):
        self._ticket += 1
        self.order_requests.append(request)
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=self._ticket,
            comment="ok",
        )


def test_mt5_connector_connect_integration(monkeypatch):
    fake = _FakeMT5API()
    monkeypatch.setattr(mt5_connector_module, "mt5", fake)

    connector = mt5_connector_module.MT5Connector()
    ok = connector.connect(login=123456, password="pw", server="Demo-Server", timeout=45000)

    assert ok is True
    assert fake.init_calls and fake.init_calls[0]["timeout"] == 45000
    assert fake.login_calls and fake.login_calls[0]["login"] == 123456
    assert connector.is_connected() is True

    connector.disconnect()
    assert fake.shutdown_calls == 1


def test_mt5_connector_request_data_integration(monkeypatch):
    fake = _FakeMT5API()
    monkeypatch.setattr(mt5_connector_module, "mt5", fake)

    connector = mt5_connector_module.MT5Connector()
    df = connector.get_ohlcv("XAUUSD", "M1", 3)

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert fake.symbol_select_calls == [("XAUUSD", True)]


def test_mt5_connector_create_order_integration(monkeypatch):
    fake = _FakeMT5API()
    monkeypatch.setattr(mt5_connector_module, "mt5", fake)

    connector = mt5_connector_module.MT5Connector()
    ok, ticket = connector.place_market_order(
        symbol="XAUUSD",
        order_type=Signal.BUY,
        volume=0.01,
        stop_loss=1999.0,
        take_profit=2002.0,
        magic=1234,
        comment="integration-test",
        slippage=10,
    )

    assert ok is True
    assert ticket > 0
    assert len(fake.order_requests) == 1
    assert fake.order_requests[0]["symbol"] == "XAUUSD"
    assert fake.order_requests[0]["type"] == fake.ORDER_TYPE_BUY


def test_mt5_connector_blocks_order_when_autotrading_disabled(monkeypatch):
    fake = _FakeMT5API()
    fake.terminal_info = lambda: SimpleNamespace(
        name="terminal",
        trade_allowed=False,
        tradeapi_disabled=False,
    )
    monkeypatch.setattr(mt5_connector_module, "mt5", fake)

    connector = mt5_connector_module.MT5Connector()
    ok, ticket = connector.place_market_order(
        symbol="XAUUSD",
        order_type=Signal.BUY,
        volume=0.01,
        stop_loss=1999.0,
        take_profit=2002.0,
        magic=1234,
        comment="autotrading-disabled",
        slippage=10,
    )

    assert ok is False
    assert ticket == 0
    assert fake.order_requests == []


class _BotMT5Connector:
    def __init__(self):
        self.backend_mode = "mock"
        self.connect_calls = []
        self.orders = []
        self.positions = []
        self._ticket = 9000

    def connect(self, login=None, password=None, server=None, path=None, timeout=60000):
        self.connect_calls.append(
            {
                "login": login,
                "password": password,
                "server": server,
                "path": path,
                "timeout": timeout,
            }
        )
        return True

    def get_ohlcv(self, symbol, timeframe, count):
        return pd.DataFrame(
            [
                {
                    "time": datetime(2026, 3, 31, 9, 0, 0),
                    "open": 2000.0,
                    "high": 2001.0,
                    "low": 1999.0,
                    "close": 2000.4,
                    "volume": 100,
                }
            ]
        )

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
        self._ticket += 1
        ticket = self._ticket
        self.orders.append(
            {
                "ticket": ticket,
                "symbol": symbol,
                "order_type": order_type,
                "volume": volume,
                "magic": magic,
            }
        )
        self.positions.append(
            Position(
                ticket=ticket,
                symbol=symbol,
                type=order_type,
                volume=volume,
                open_price=2000.4,
                stop_loss=stop_loss,
                take_profit=take_profit,
                profit=0.0,
                magic_number=magic,
                comment=comment,
                open_time=datetime(2026, 3, 31, 9, 0, 0),
            )
        )
        return True, ticket

    def get_positions(self, symbol=None):
        if symbol:
            return [p for p in self.positions if p.symbol == symbol]
        return list(self.positions)

    def get_symbol_info(self, symbol):
        return SimpleNamespace(point=0.01)

    def get_tick(self, symbol):
        return {"bid": 2000.0, "ask": 2000.2}

    def close_position(self, ticket):
        return True

    def modify_position(self, ticket, stop_loss=None, take_profit=None):
        return True

    def get_account_info(self):
        return SimpleNamespace(
            login=123456,
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            free_margin=10000.0,
            margin_level=0.0,
            profit=0.0,
            currency="USD",
            leverage=100,
            server="Demo-Server",
            company="Broker",
        )

    def disconnect(self):
        return None


class _RiskManagerStub:
    def __init__(self, mt5, limits):
        self.mt5 = mt5
        self.limits = limits

    def initialize(self):
        return True

    def can_open_trade(self, symbol, signal):
        return True, "OK"

    def validate_trade_signal(self, symbol, signal, entry_price, stop_loss, take_profit):
        return True, "Valid"

    def calculate_lot_size(self, symbol, stop_loss_pips, risk_percent=None, capital_base=None):
        return 0.01

    def record_trade_result(self, profit):
        return None


class _PatternStrategy:
    symbols = ["XAUUSD"]
    timeframe = "M15"
    enabled = True
    magic_number = 654321

    def __init__(self):
        self.opened = []
        self.config = {"risk": {}}

    def analyze(self, symbol, data):
        return TradeSignal(
            signal=Signal.BUY,
            symbol=symbol,
            entry_price=float(data.iloc[-1]["close"]),
            stop_loss=float(data.iloc[-1]["close"]) - 1.0,
            take_profit=float(data.iloc[-1]["close"]) + 2.0,
            lot_size=0.01,
            magic_number=self.magic_number,
            comment="pattern",
        )

    def on_trade_opened(self, position):
        self.opened.append(position)

    def should_close(self, position, data):
        return False

    def get_trailing_stop(self, position, data):
        return None


class _StrategyLoaderStub:
    def __init__(self):
        self.strategy = _PatternStrategy()

    def load_all_strategies(self):
        return None

    def get_enabled_strategies(self):
        return {"pattern_strategy": self.strategy}


def test_full_loop_connect_request_data_create_order_for_pattern(monkeypatch):
    def _load_config_stub(self):
        self.config = {
            "logging": {"level": "INFO"},
            "mt5": {
                "login": 123456,
                "password": "pw",
                "server": "Demo-Server",
                "timeout": 60000,
            },
            "risk": {},
            "trading": {
                "default_magic_number": 654321,
                "default_lot_size": 0.01,
                "slippage": 10,
            },
        }
        return True

    monkeypatch.setattr(main_module.TradingBot, "load_config", _load_config_stub)
    monkeypatch.setattr(main_module, "setup_logger", lambda **kwargs: None)
    monkeypatch.setattr(main_module, "MT5Connector", _BotMT5Connector)
    monkeypatch.setattr(main_module, "RiskManager", _RiskManagerStub)
    monkeypatch.setattr(main_module, "StrategyLoader", _StrategyLoaderStub)
    monkeypatch.setattr(main_module, "TradeExecutor", TradeExecutor)

    bot = main_module.TradingBot()
    assert bot.initialize() is True
    assert isinstance(bot.mt5, _BotMT5Connector)
    assert len(bot.mt5.connect_calls) == 1

    bot._tick()

    assert len(bot.mt5.orders) == 1
    assert bot.mt5.orders[0]["symbol"] == "XAUUSD"
    assert bot.mt5.orders[0]["order_type"] == Signal.BUY
    assert len(bot.strategy_loader.strategy.opened) == 1
