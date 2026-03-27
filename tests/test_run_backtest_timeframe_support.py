from datetime import datetime

from scripts.run_backtest import _normalize_timeframe
from scripts.run_backtest import _parse_date
from scripts.run_backtest import _resolve_special_timeframe_data_path


def test_normalize_timeframe_supports_tick_and_s1_aliases():
    assert _normalize_timeframe("tick") == "TICK"
    assert _normalize_timeframe("ticks") == "TICK"
    assert _normalize_timeframe("s1") == "S1"
    assert _normalize_timeframe("1s") == "S1"
    assert _normalize_timeframe("m15") == "M15"


def test_parse_date_supports_datetime_strings():
    d1 = _parse_date("2026-03-24")
    d2 = _parse_date("2026-03-24 12:34:56")
    d3 = _parse_date("2026-03-24T12:34:56")

    assert d1 == datetime(2026, 3, 24, 0, 0, 0)
    assert d2 == datetime(2026, 3, 24, 12, 34, 56)
    assert d3 == datetime(2026, 3, 24, 12, 34, 56)


def test_resolve_tick_from_directory(tmp_path):
    m1 = tmp_path / "xauusd-m1-bid-2026.parquet"
    tick = tmp_path / "xauusd-tick-2026.parquet"
    m1.touch()
    tick.touch()

    args = {
        "symbol": "XAUUSD",
        "timeframe": "tick",
        "data_path": str(tmp_path),
    }
    _resolve_special_timeframe_data_path(args)

    assert args["data_path"] == str(tick)


def test_resolve_tick_from_file_parent_directory(tmp_path):
    m1 = tmp_path / "xauusd-m1-bid-2026.parquet"
    tick = tmp_path / "xauusd-tick-2026.parquet"
    m1.touch()
    tick.touch()

    args = {
        "symbol": "XAUUSD",
        "timeframe": "TICK",
        "data_path": str(m1),
    }
    _resolve_special_timeframe_data_path(args)

    assert args["data_path"] == str(tick)


def test_resolve_s1_prefers_s1_over_m1(tmp_path):
    m1 = tmp_path / "xauusd-m1-bid-2026.parquet"
    s1 = tmp_path / "xauusd-s1-bid-2026.parquet"
    m1.touch()
    s1.touch()

    args = {
        "symbol": "XAUUSD",
        "timeframe": "s1",
        "data_path": str(m1),
    }
    _resolve_special_timeframe_data_path(args)

    assert args["data_path"] == str(s1)


def test_resolve_s1_falls_back_to_m1(tmp_path):
    m1 = tmp_path / "xauusd-m1-bid-2026.parquet"
    m1.touch()

    args = {
        "symbol": "XAUUSD",
        "timeframe": "s1",
        "data_path": str(tmp_path),
    }
    _resolve_special_timeframe_data_path(args)

    assert args["data_path"] == str(m1)


def test_resolve_s1_fallback_does_not_misread_m15_as_m1(tmp_path):
    m15 = tmp_path / "XAUUSD_M15.parquet"
    m1 = tmp_path / "xauusd-m1-bid-2026.parquet"
    m15.touch()
    m1.touch()

    args = {
        "symbol": "XAUUSD",
        "timeframe": "S1",
        "data_path": str(tmp_path),
    }
    _resolve_special_timeframe_data_path(args)

    assert args["data_path"] == str(m1)
