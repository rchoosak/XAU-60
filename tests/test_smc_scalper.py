from datetime import datetime

import pandas as pd

from strategies.smc_scalper import SMCScalper


def _base_config():
    return {
        "name": "SMC Scalper",
        "enabled": True,
        "symbols": ["XAUUSD"],
        "timeframe": "M5",
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
        "session": {"use_time_filter": False},
    }


def test_smc_scalper_xau_point_and_pip_scaling():
    strategy = SMCScalper()
    strategy.initialize(_base_config())

    assert strategy.smc is not None
    assert strategy.smc.point == 0.01

    data = pd.DataFrame(
        [
            {
                "time": datetime(2026, 1, 1, 0, 0),
                "open": 2000.0,
                "high": 2001.0,
                "low": 1999.0,
                "close": 2000.5,
                "volume": 1,
            }
        ]
    )
    sl = strategy._calculate_stop_loss(data, "XAUUSD", entry_price=2000.0, is_buy=True)
    assert sl == 1990.0

    tp = strategy._calculate_take_profit(
        symbol="XAUUSD",
        entry_price=2000.0,
        stop_loss=1990.0,
        order_block_level=2020.0,
        is_buy=True,
    )
    # Order block target should be 10 pips (1.0 XAUUSD price) before OB.
    assert tp == 2019.0


def test_smc_scalper_session_time_uses_timezone_conversion():
    strategy = SMCScalper()
    cfg = _base_config()
    cfg["session"] = {
        "use_time_filter": True,
        "timezone": "Asia/Bangkok",
        "data_timezone": "UTC",
        "start_hour": 8,
        "end_hour": 9,
        "trade_friday": True,
    }
    strategy.initialize(cfg)

    # Naive timestamp interpreted as UTC -> 01:15 UTC == 08:15 Asia/Bangkok.
    data = pd.DataFrame(
        [
            {
                "time": datetime(2026, 1, 1, 1, 15),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
            }
        ]
    )
    assert strategy._is_trading_time(data) is True


class _DummyFVG:
    def __init__(self):
        self.lower_price = 1999.0
        self.upper_price = 2001.0
        self.mid_price = 2000.0


class _DummyOrderBlock:
    def __init__(self):
        self.lower_price = 2010.0
        self.upper_price = 1990.0


class _CaptureLookbackSMC:
    def __init__(self):
        self.bullish_fvg_lookback = None
        self.bearish_fvg_lookback = None

    def detect_bullish_choch(self, data, lookback):
        return (len(data) - 1, float(data.iloc[-1]["high"]))

    def detect_bearish_choch(self, data, lookback):
        return (len(data) - 1, float(data.iloc[-1]["low"]))

    def detect_bullish_fvg(self, data, lookback):
        self.bullish_fvg_lookback = lookback
        return _DummyFVG()

    def detect_bearish_fvg(self, data, lookback):
        self.bearish_fvg_lookback = lookback
        return _DummyFVG()

    def detect_bearish_order_block(self, data, lookback):
        return _DummyOrderBlock()

    def detect_bullish_order_block(self, data, lookback):
        return _DummyOrderBlock()


def test_smc_scalper_uses_configured_fvg_lookback_for_bullish_and_bearish_paths():
    strategy = SMCScalper()
    cfg = _base_config()
    cfg["parameters"]["fvg_lookback"] = 33
    strategy.initialize(cfg)

    strategy.smc = _CaptureLookbackSMC()

    rows = []
    for i in range(60):
        rows.append(
            {
                "time": datetime(2026, 1, 1, 0, 0) + pd.Timedelta(minutes=i * 5),
                "open": 2000.0,
                "high": 2000.5,
                "low": 1999.5,
                "close": 2000.0,
                "volume": 1,
            }
        )
    data = pd.DataFrame(rows)

    bullish_signal = strategy._check_bullish_setup("XAUUSD", data)
    assert bullish_signal is not None
    assert strategy.smc.bullish_fvg_lookback == 33

    bearish_signal = strategy._check_bearish_setup("XAUUSD", data)
    assert bearish_signal is not None
    assert strategy.smc.bearish_fvg_lookback == 33
