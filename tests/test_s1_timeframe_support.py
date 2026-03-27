from core.mt5_connector import MT5Connector
from utils import mt5_mock


def test_mt5_connector_exposes_s1_timeframe_mapping():
    assert "S1" in MT5Connector.TIMEFRAMES


def test_mt5_mock_generates_one_second_bars_for_s1():
    mt5_mock.initialize()
    try:
        rates = mt5_mock.copy_rates_from_pos("XAUUSD", mt5_mock.TIMEFRAME_S1, 0, 3)
        assert rates is not None
        assert len(rates) == 3
        assert int(rates[1]["time"] - rates[0]["time"]) == 1
        assert int(rates[2]["time"] - rates[1]["time"]) == 1
    finally:
        mt5_mock.shutdown()
