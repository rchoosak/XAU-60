from datetime import datetime, timedelta, timezone

import pandas as pd

from main import TradingBot
from core.strategy_base import Position, Signal
from core.trade_executor import TradeExecutor
from strategies.smc_scalper import SMCScalper


class _DummyFVG:
    def __init__(self, lower_price: float, upper_price: float):
        self.lower_price = lower_price
        self.upper_price = upper_price
        self.mid_price = (lower_price + upper_price) / 2.0


class _DummyOrderBlock:
    def __init__(self, lower_price: float, upper_price: float):
        self.lower_price = lower_price
        self.upper_price = upper_price


class _AlwaysBullishSMC:
    """Force bullish setup so integration can verify execution pipeline."""

    def detect_bullish_choch(self, data, lookback):
        return (len(data) - 1, float(data.iloc[-1]["high"]))

    def detect_bullish_fvg(self, data, lookback):
        c = float(data.iloc[-1]["close"])
        return _DummyFVG(lower_price=c - 0.4, upper_price=c + 0.4)

    def detect_bearish_order_block(self, data, lookback):
        c = float(data.iloc[-1]["close"])
        return _DummyOrderBlock(lower_price=c + 3.0, upper_price=c + 5.0)

    def detect_bearish_choch(self, data, lookback):
        return None

    def detect_bearish_fvg(self, data, lookback):
        return None

    def detect_bullish_order_block(self, data, lookback):
        return None


class _StubRiskManager:
    def can_open_trade(self, symbol, signal):
        return True, "OK"

    def validate_trade_signal(self, symbol, signal, entry_price, stop_loss, take_profit):
        return True, "Valid"

    def calculate_lot_size(self, symbol, stop_loss_pips):
        return 0.01

    def record_trade_result(self, profit):
        return None


class _EAFeedMT5Stub:
    """
    Simulates MT5 data received from EA bridge:
    - time values originate as epoch seconds
    - connector converts them to pandas datetime
    """

    def __init__(self, bars: pd.DataFrame):
        self._bars = bars
        self.orders = []
        self.positions = []
        self._ticket = 1000

    def get_ohlcv(self, symbol, timeframe, count):
        return self._bars.tail(count).reset_index(drop=True)

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
        close_price = float(self._bars.iloc[-1]["close"])

        self.orders.append(
            {
                "ticket": ticket,
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
        self.positions.append(
            Position(
                ticket=ticket,
                symbol=symbol,
                type=order_type,
                volume=volume,
                open_price=close_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                profit=0.0,
                magic_number=magic,
                comment=comment,
                open_time=self._bars.iloc[-1]["time"],
            )
        )
        return True, ticket

    def get_positions(self, symbol=None):
        if symbol:
            return [p for p in self.positions if p.symbol == symbol]
        return list(self.positions)


class _SingleStrategyLoader:
    def __init__(self, strategy):
        self._strategy = strategy

    def get_enabled_strategies(self):
        return {"SMC Scalper": self._strategy}


def _build_ea_like_bars(last_dt_utc: datetime, periods: int = 70) -> pd.DataFrame:
    """
    Build OHLC bars like EA bridge payload:
    - EA/MT5 provides epoch seconds
    - connector transforms with pd.to_datetime(..., unit='s')
    """
    start = last_dt_utc - timedelta(seconds=periods - 1)
    rows = []
    for i in range(periods):
        t = start + timedelta(seconds=i)
        rows.append(
            {
                "time": int(t.timestamp()),
                "open": 2000.0 + i * 0.01,
                "high": 2000.6 + i * 0.01,
                "low": 1999.4 + i * 0.01,
                "close": 2000.2 + i * 0.01,
                "tick_volume": 100 + i,
            }
        )

    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.rename(columns={"tick_volume": "volume"})
    return df[["time", "open", "high", "low", "close", "volume"]]


def _make_smc_config(start_hour: int, end_hour: int):
    return {
        "name": "SMC Scalper",
        "enabled": True,
        "symbols": ["XAUUSD"],
        "timeframe": "S1",
        "magic_number": 789123,
        "parameters": {
            "choch_lookback": 50,
            "fvg_min_pips": 5.0,
            "fvg_lookback": 20,
            "ob_lookback": 20,
            "risk_reward": 2.0,
            "trailing_stop": False,
            "trailing_pips": 20.0,
            "use_atr_sl": False,
            "stop_loss_pips": 100.0,
        },
        "risk": {"lot_size": 0.01},
        "session": {
            "use_time_filter": True,
            "timezone": "UTC",
            "data_timezone": "UTC",
            "start_hour": start_hour,
            "end_hour": end_hour,
            "trade_friday": True,
        },
    }


def test_ea_epoch_data_triggers_order_when_smc_conditions_match():
    # Friday 2026-03-27 12:30:00 UTC is inside [12, 13) session window.
    last_dt_utc = datetime(2026, 3, 27, 12, 30, 0, tzinfo=timezone.utc)
    bars = _build_ea_like_bars(last_dt_utc=last_dt_utc, periods=80)

    strategy = SMCScalper()
    strategy.initialize(_make_smc_config(start_hour=12, end_hour=13))
    strategy.smc = _AlwaysBullishSMC()

    mt5 = _EAFeedMT5Stub(bars)
    executor = TradeExecutor(
        mt5=mt5,
        risk_manager=_StubRiskManager(),
        default_magic=123456,
        default_lot_size=0.01,
        slippage=10,
    )

    bot = TradingBot()
    bot.mt5 = mt5
    bot.trade_executor = executor
    bot.strategy_loader = _SingleStrategyLoader(strategy)

    bot._tick()

    assert len(mt5.orders) == 1
    assert mt5.orders[0]["symbol"] == "XAUUSD"
    assert mt5.orders[0]["order_type"] == Signal.BUY
    assert mt5.orders[0]["volume"] == 0.01


def test_ea_epoch_data_is_blocked_when_session_window_does_not_match():
    # Same Friday data but outside [7, 8) UTC -> no trade.
    last_dt_utc = datetime(2026, 3, 27, 12, 30, 0, tzinfo=timezone.utc)
    bars = _build_ea_like_bars(last_dt_utc=last_dt_utc, periods=80)

    strategy = SMCScalper()
    strategy.initialize(_make_smc_config(start_hour=7, end_hour=8))
    strategy.smc = _AlwaysBullishSMC()

    mt5 = _EAFeedMT5Stub(bars)
    executor = TradeExecutor(
        mt5=mt5,
        risk_manager=_StubRiskManager(),
        default_magic=123456,
        default_lot_size=0.01,
        slippage=10,
    )

    bot = TradingBot()
    bot.mt5 = mt5
    bot.trade_executor = executor
    bot.strategy_loader = _SingleStrategyLoader(strategy)

    bot._tick()

    assert len(mt5.orders) == 0
