from datetime import datetime

import pandas as pd

from core.strategy_base import Position, Signal
from strategies.ai_multi_timeframe_decision import AIMultiTimeframeDecision


def _sample_data(rows: int = 120) -> pd.DataFrame:
    base = datetime(2026, 4, 1, 0, 0, 0)
    data = []
    for i in range(rows):
        close = 3000.0 + i * 0.5
        data.append(
            {
                "time": base,
                "open": close - 1.0,
                "high": close + 1.0,
                "low": close - 2.0,
                "close": close,
                "volume": 100 + i,
            }
        )
    return pd.DataFrame(data)


def _base_config() -> dict:
    return {
        "name": "AI Multi Timeframe Decision",
        "enabled": True,
        "symbols": ["XAUUSD"],
        "timeframe": "M5",
        "parameters": {
            "analysis_timeframes": ["M5"],
            "min_bars_required": 60,
            "close_signal_ttl_seconds": 120,
        },
        "risk": {
            "lot_size": 0.01,
            "stop_loss_pips": 100.0,
            "take_profit_pips": 200.0,
        },
        "ai": {
            "provider": "openai_compatible",
            "base_url": "http://localhost:1234/v1",
            "model": "dummy-model",
            "api_key": "test-key",
        },
    }


def test_parse_ai_response_thai_action():
    strategy = AIMultiTimeframeDecision()
    action, reason, confidence = strategy._parse_ai_response(
        '{"action":"ซื้อเพิ่ม","reason":"trend aligns","confidence":0.81}'
    )
    assert action == strategy._ACTION_BUY_ADD
    assert reason == "trend aligns"
    assert confidence == 0.81


def test_analyze_enter_buy_returns_trade_signal(monkeypatch):
    strategy = AIMultiTimeframeDecision()
    strategy.initialize(_base_config())

    monkeypatch.setattr(strategy, "_collect_multi_timeframe_snapshot", lambda symbol, data: [{"timeframe": "M5"}])
    monkeypatch.setattr(
        strategy,
        "_collect_positions_snapshot",
        lambda symbol: {
            "symbol": symbol,
            "position_count": 0,
            "long_positions": 0,
            "long_volume": 0.0,
            "avg_entry_long": 0.0,
            "floating_profit": 0.0,
        },
    )
    monkeypatch.setattr(
        strategy,
        "_request_ai_decision",
        lambda payload: '{"action":"enter_buy","reason":"mtf uptrend","confidence":0.72}',
    )
    monkeypatch.setattr(strategy, "_symbol_point", lambda symbol: 0.01)

    signal = strategy.analyze("XAUUSD", _sample_data())
    assert signal is not None
    assert signal.signal == Signal.BUY
    assert signal.symbol == "XAUUSD"
    assert signal.stop_loss < signal.entry_price < signal.take_profit


def test_analyze_sell_out_sets_should_close(monkeypatch):
    strategy = AIMultiTimeframeDecision()
    strategy.initialize(_base_config())

    monkeypatch.setattr(strategy, "_collect_multi_timeframe_snapshot", lambda symbol, data: [{"timeframe": "M5"}])
    monkeypatch.setattr(
        strategy,
        "_collect_positions_snapshot",
        lambda symbol: {
            "symbol": symbol,
            "position_count": 1,
            "long_positions": 1,
            "long_volume": 0.01,
            "avg_entry_long": 3000.0,
            "floating_profit": 15.0,
        },
    )
    monkeypatch.setattr(
        strategy,
        "_request_ai_decision",
        lambda payload: '{"action":"sell_out","reason":"trend breakdown","confidence":0.66}',
    )

    signal = strategy.analyze("XAUUSD", _sample_data())
    assert signal is None

    position = Position(
        ticket=1,
        symbol="XAUUSD",
        type=Signal.BUY,
        volume=0.01,
        open_price=3000.0,
        stop_loss=2990.0,
        take_profit=3020.0,
        profit=10.0,
        magic_number=796000,
        comment="",
        open_time=datetime.now(),
    )
    assert strategy.should_close(position, _sample_data(20)) is True


def test_analyze_buy_add_maps_to_enter_buy_when_flat(monkeypatch):
    strategy = AIMultiTimeframeDecision()
    cfg = _base_config()
    cfg["parameters"]["map_buy_add_to_enter_when_flat"] = True
    strategy.initialize(cfg)

    monkeypatch.setattr(strategy, "_collect_multi_timeframe_snapshot", lambda symbol, data: [{"timeframe": "M5"}])
    monkeypatch.setattr(
        strategy,
        "_collect_positions_snapshot",
        lambda symbol: {
            "symbol": symbol,
            "position_count": 0,
            "long_positions": 0,
            "long_volume": 0.0,
            "avg_entry_long": 0.0,
            "floating_profit": 0.0,
        },
    )
    monkeypatch.setattr(
        strategy,
        "_request_ai_decision",
        lambda payload: '{"action":"buy_add","reason":"scale in","confidence":0.7}',
    )
    monkeypatch.setattr(strategy, "_symbol_point", lambda symbol: 0.01)

    signal = strategy.analyze("XAUUSD", _sample_data())
    assert signal is not None
    assert signal.signal == Signal.BUY


def test_payload_required_action_set_when_flat():
    strategy = AIMultiTimeframeDecision()
    payload = strategy._build_ai_payload(
        "XAUUSD",
        [{"timeframe": "M5"}],
        {
            "symbol": "XAUUSD",
            "position_count": 0,
            "long_positions": 0,
            "long_volume": 0.0,
            "avg_entry_long": 0.0,
            "floating_profit": 0.0,
        },
    )
    assert payload["required_action_set"] == ["enter_buy", "hold"]


def test_normalize_action_fallback_to_hold():
    strategy = AIMultiTimeframeDecision()
    assert strategy._normalize_action("unknown_action") == strategy._ACTION_HOLD


def test_openai_chat_urls_include_lmstudio_fallback():
    urls = AIMultiTimeframeDecision._openai_chat_urls("http://127.0.0.1:1234/api/v1")

    assert "http://127.0.0.1:1234/api/v1/chat/completions" in urls
    assert "http://127.0.0.1:1234/v1/chat/completions" in urls


def test_parse_ai_response_extracts_action_from_freeform_text():
    strategy = AIMultiTimeframeDecision()
    action, reason, confidence = strategy._parse_ai_response(
        "Based on trend alignment, final action: buy_add."
    )
    assert action == strategy._ACTION_BUY_ADD
    assert confidence == 0.5
    assert "buy_add" in reason


def test_parse_ai_response_ambiguous_action_list_falls_back_hold():
    strategy = AIMultiTimeframeDecision()
    action, _reason, _confidence = strategy._parse_ai_response(
        "Choose one action from enter_buy, hold, sell_out, buy_add."
    )
    assert action == strategy._ACTION_HOLD


def test_parse_ai_response_json_prefix_with_garbage_suffix():
    strategy = AIMultiTimeframeDecision()
    action, reason, confidence = strategy._parse_ai_response(
        '{"action":"enter_buy","reason":"trend up","confidence":0.9}\n"""\npython code...'
    )
    assert action == strategy._ACTION_ENTER_BUY
    assert reason == "trend up"
    assert confidence == 0.9


def test_parse_ai_response_meta_reads_request_id():
    strategy = AIMultiTimeframeDecision()
    action, reason, confidence, request_id = strategy._parse_ai_response_meta(
        '{"action":"hold","reason":"wait","confidence":0.5,"request_id":"abc123"}'
    )
    assert action == strategy._ACTION_HOLD
    assert reason == "wait"
    assert confidence == 0.5
    assert request_id == "abc123"


def test_openai_compatible_reads_reasoning_content_when_content_empty(monkeypatch):
    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": '{"action":"hold","reason":"fallback","confidence":0.4}',
                        }
                    }
                ]
            }

    strategy = AIMultiTimeframeDecision()
    strategy.initialize(_base_config())

    def _fake_post(url, headers=None, json=None, timeout=None):
        return _Resp()

    monkeypatch.setattr("strategies.ai_multi_timeframe_decision.requests.post", _fake_post)
    out = strategy._request_openai_compatible({"symbol": "XAUUSD"})

    assert "action" in out
    assert "hold" in out


def test_fetch_ohlcv_uses_runtime_connector():
    class RuntimeMT5:
        def get_ohlcv(self, symbol, timeframe, count):
            assert symbol == "XAUUSD"
            assert timeframe == "H1"
            assert count == 50
            return _sample_data(50)

    strategy = AIMultiTimeframeDecision()
    strategy.set_runtime_context(mt5_connector=RuntimeMT5())
    frame = strategy._fetch_ohlcv("XAUUSD", "H1", 50)

    assert frame is not None
    assert len(frame) == 50
    assert list(frame.columns) == ["time", "open", "high", "low", "close", "volume"]
