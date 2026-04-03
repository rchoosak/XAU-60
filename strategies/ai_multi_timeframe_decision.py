"""
AI Multi Timeframe Decision Strategy.

This strategy collects multi-timeframe market context, sends it to an AI model,
then maps the response to one of four actions:
- enter_buy (เข้าซื้อ)
- hold (ถือต่อ)
- sell_out (ขายออก)
- buy_add (ซื้อเพิ่ม)
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import requests
from loguru import logger

from core.mt5_connector import MT5Connector, mt5
from core.strategy_base import Position, Signal, StrategyBase, TradeSignal


class AIMultiTimeframeDecision(StrategyBase):
    """AI-driven strategy using multi-timeframe context."""

    name = "AI Multi Timeframe Decision"
    version = "1.0.0"
    description = "Uses AI to classify market action into buy/hold/sell-out/buy-add"
    author = "XAU60"

    _ACTION_ENTER_BUY = "ENTER_BUY"
    _ACTION_HOLD = "HOLD"
    _ACTION_SELL_OUT = "SELL_OUT"
    _ACTION_BUY_ADD = "BUY_ADD"
    _VALID_ACTIONS = {_ACTION_ENTER_BUY, _ACTION_HOLD, _ACTION_SELL_OUT, _ACTION_BUY_ADD}

    _TIMEFRAME_ALIASES = {
        "1M": "M1",
        "5M": "M5",
        "15M": "M15",
        "30M": "M30",
        "60M": "H1",
        "120M": "H2",
        "240M": "H4",
        "1H": "H1",
        "2H": "H2",
        "4H": "H4",
    }

    def __init__(self):
        super().__init__()
        self.magic_number = 796000
        self.lot_size = 0.0

        self.analysis_timeframes: List[str] = ["M1", "M5", "M15", "H1", "H4"]
        self.bars_per_timeframe = 120
        self.prompt_recent_bars = 20
        self.stop_loss_pips = 120.0
        self.take_profit_pips = 240.0
        self.close_signal_ttl_seconds = 120
        self.allow_buy_when_already_long = False
        self.allow_add_without_position = False
        self.map_buy_add_to_enter_when_flat = True
        self.min_bars_required = 80
        self.min_request_interval_seconds = 0.0
        self.max_response_latency_seconds = 8.0
        self.error_backoff_seconds = 3.0

        self.provider = "openai_compatible"
        self.base_url = "https://api.openai.com/v1"
        self.model = "gpt-4o-mini"
        self.api_key = ""
        self.timeout_seconds = 15
        self.temperature = 0.1
        self.max_tokens = 180

        self._close_signal_until: Dict[str, float] = {}
        self._runtime_mt5: Optional[Any] = None
        self._next_request_after: Dict[str, float] = {}

    def set_runtime_context(
        self,
        mt5_connector: Optional[Any] = None,
        trade_executor: Optional[Any] = None,
        risk_manager: Optional[Any] = None,
    ) -> None:
        """
        Optional runtime hook from TradingBot to provide active connectors.
        """
        self._runtime_mt5 = mt5_connector

    def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize strategy with configuration."""
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.symbols = list(self.config.get("symbols", ["XAUUSD"]))
        self.timeframe = self._normalize_timeframe(self.config.get("timeframe", "M5"))
        self.magic_number = int(self.config.get("magic_number", 796000))

        params = self.config.get("parameters", {}) or {}
        risk = self.config.get("risk", {}) or {}

        raw_tfs = params.get("analysis_timeframes", ["M1", "M5", "M15", "H1", "H4"])
        self.analysis_timeframes = self._normalize_timeframes(raw_tfs)
        if self.timeframe not in self.analysis_timeframes:
            self.analysis_timeframes.insert(0, self.timeframe)

        self.bars_per_timeframe = max(30, int(params.get("bars_per_timeframe", 120)))
        self.prompt_recent_bars = max(8, int(params.get("prompt_recent_bars", 20)))
        self.stop_loss_pips = float(risk.get("stop_loss_pips", params.get("stop_loss_pips", 120.0)))
        self.take_profit_pips = float(risk.get("take_profit_pips", params.get("take_profit_pips", 240.0)))
        self.lot_size = float(risk.get("lot_size", params.get("lot_size", 0.0)))
        self.close_signal_ttl_seconds = max(10, int(params.get("close_signal_ttl_seconds", 120)))
        self.allow_buy_when_already_long = bool(params.get("allow_buy_when_already_long", False))
        self.allow_add_without_position = bool(params.get("allow_add_without_position", False))
        self.map_buy_add_to_enter_when_flat = bool(params.get("map_buy_add_to_enter_when_flat", True))
        self.min_bars_required = max(30, int(params.get("min_bars_required", 80)))
        self.min_request_interval_seconds = max(0.0, float(params.get("min_request_interval_seconds", 0.0)))
        self.max_response_latency_seconds = max(1.0, float(params.get("max_response_latency_seconds", 8.0)))
        self.error_backoff_seconds = max(0.0, float(params.get("error_backoff_seconds", 3.0)))

        ai = self.config.get("ai", {}) or {}
        self.provider = str(ai.get("provider", "openai_compatible")).strip().lower()
        self.base_url = str(ai.get("base_url", "https://api.openai.com/v1")).strip()
        self.model = str(ai.get("model", "gpt-4o-mini")).strip()
        self.timeout_seconds = max(5, int(ai.get("timeout_seconds", 15)))
        self.temperature = float(ai.get("temperature", 0.1))
        self.max_tokens = max(80, int(ai.get("max_tokens", 180)))

        api_key = str(ai.get("api_key", "")).strip()
        api_key_env = str(ai.get("api_key_env", "OPENAI_API_KEY")).strip()
        self.api_key = api_key or os.getenv(api_key_env, "")

        self._close_signal_until = {}
        self._next_request_after = {}
        logger.info(
            "Initialized AI strategy | provider={} model={} tfs={}",
            self.provider,
            self.model,
            ",".join(self.analysis_timeframes),
        )

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        """Analyze market and request action decision from AI."""
        if data is None or data.empty or len(data) < self.min_bars_required:
            return None

        now = time.time()
        next_allowed = float(self._next_request_after.get(symbol, 0.0) or 0.0)
        if now < next_allowed:
            return None

        mtf = self._collect_multi_timeframe_snapshot(symbol, data)
        if not mtf:
            logger.warning("AI strategy skipped: no multi-timeframe data for {}", symbol)
            return None

        positions = self._collect_positions_snapshot(symbol)
        request_id = uuid.uuid4().hex[:12]
        payload = self._build_ai_payload(symbol, mtf, positions, request_id=request_id)

        request_started = time.time()
        ai_raw = self._request_ai_decision(payload)
        elapsed = time.time() - request_started
        if not ai_raw:
            self._next_request_after[symbol] = time.time() + self.error_backoff_seconds
            logger.warning("AI strategy received empty decision, fallback HOLD for {}", symbol)
            return None

        if elapsed > self.max_response_latency_seconds:
            self._next_request_after[symbol] = time.time() + self.error_backoff_seconds
            logger.warning(
                "AI response discarded for {} due to latency {:.2f}s > {:.2f}s",
                symbol,
                elapsed,
                self.max_response_latency_seconds,
            )
            return None

        action, reason, confidence, response_request_id = self._parse_ai_response_meta(ai_raw)
        if response_request_id and response_request_id != request_id:
            self._next_request_after[symbol] = time.time() + self.error_backoff_seconds
            logger.warning(
                "AI response request_id mismatch for {} (expected={}, got={}), discarded",
                symbol,
                request_id,
                response_request_id,
            )
            return None
        if action not in self._VALID_ACTIONS:
            logger.warning("AI strategy returned unknown action '{}', fallback HOLD", action)
            return None

        if action == self._ACTION_SELL_OUT:
            if positions["long_positions"] > 0:
                self._close_signal_until[symbol] = time.time() + self.close_signal_ttl_seconds
                logger.info(
                    "AI decision SELL_OUT for {} | confidence={} reason={}",
                    symbol,
                    confidence,
                    reason,
                )
            return None

        if action == self._ACTION_HOLD:
            return None

        if action == self._ACTION_ENTER_BUY and positions["long_positions"] > 0 and not self.allow_buy_when_already_long:
            logger.info("AI decision ENTER_BUY ignored for {} (long already open)", symbol)
            return None

        if action == self._ACTION_BUY_ADD and positions["long_positions"] <= 0 and not self.allow_add_without_position:
            if self.map_buy_add_to_enter_when_flat:
                logger.info("AI decision BUY_ADD mapped to ENTER_BUY for {} (no long position)", symbol)
                action = self._ACTION_ENTER_BUY
            else:
                logger.info("AI decision BUY_ADD ignored for {} (no long position to add)", symbol)
                return None

        signal = self._build_buy_signal(symbol, data, action, confidence, reason)
        self._next_request_after[symbol] = time.time() + self.min_request_interval_seconds
        logger.info(
            "AI decision {} for {} | confidence={} reason={}",
            action,
            symbol,
            confidence,
            reason,
        )
        return signal

    def should_close(self, position: Position, data: pd.DataFrame) -> bool:
        """Close when recent AI decision requested SELL_OUT for this symbol."""
        expiry = self._close_signal_until.get(position.symbol)
        if not expiry:
            return False
        if time.time() > expiry:
            self._close_signal_until.pop(position.symbol, None)
            return False
        return True

    def _build_buy_signal(
        self,
        symbol: str,
        data: pd.DataFrame,
        action: str,
        confidence: float,
        reason: str,
    ) -> TradeSignal:
        entry = float(data.iloc[-1]["close"])
        point = self._symbol_point(symbol)
        distance_sl = max(point * 10.0, self.stop_loss_pips * point * 10.0)
        distance_tp = max(point * 10.0, self.take_profit_pips * point * 10.0)

        stop_loss = entry - distance_sl
        take_profit = entry + distance_tp
        short_reason = (reason or "ai_decision").replace("\n", " ").strip()
        if len(short_reason) > 40:
            short_reason = short_reason[:40]
        comment = f"AI:{action}:{confidence:.2f}:{short_reason}"

        return TradeSignal(
            signal=Signal.BUY,
            symbol=symbol,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=self.lot_size,
            comment=comment,
            magic_number=self.magic_number,
        )

    def _symbol_point(self, symbol: str) -> float:
        point = 0.01
        runtime = self._runtime_mt5
        if runtime is not None and hasattr(runtime, "get_symbol_info"):
            info = runtime.get_symbol_info(symbol)
            if info is not None:
                point = getattr(info, "point", point)
        else:
            info = mt5.symbol_info(symbol)
            if info is not None:
                point = getattr(info, "point", point)
        try:
            p = float(point)
            return p if p > 0 else 0.01
        except (TypeError, ValueError):
            return 0.01

    def _collect_multi_timeframe_snapshot(self, symbol: str, current_data: pd.DataFrame) -> List[Dict[str, Any]]:
        snapshots: List[Dict[str, Any]] = []
        for tf in self.analysis_timeframes:
            frame_data: Optional[pd.DataFrame]
            if tf == self.timeframe:
                frame_data = current_data.tail(self.bars_per_timeframe).copy()
            else:
                frame_data = self._fetch_ohlcv(symbol, tf, self.bars_per_timeframe)
            if frame_data is None or frame_data.empty:
                continue
            snapshots.append(self._summarize_frame(tf, frame_data))
        return snapshots

    def _fetch_ohlcv(self, symbol: str, timeframe: str, bars: int) -> Optional[pd.DataFrame]:
        tf_key = self._normalize_timeframe(timeframe)

        runtime = self._runtime_mt5
        if runtime is not None and hasattr(runtime, "get_ohlcv"):
            try:
                frame = runtime.get_ohlcv(symbol, tf_key, bars)
                if frame is not None and not frame.empty:
                    return frame[["time", "open", "high", "low", "close", "volume"]].copy()
            except Exception as exc:
                logger.warning("AI strategy runtime get_ohlcv failed {} {}: {}", symbol, tf_key, exc)

        tf_code = MT5Connector.TIMEFRAMES.get(tf_key)
        if tf_code is None:
            logger.warning("AI strategy unknown timeframe '{}'", timeframe)
            return None

        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, tf_code, 0, bars)
        if rates is None or len(rates) == 0:
            return None

        df = pd.DataFrame(rates)
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], unit="s")
        if "tick_volume" in df.columns and "volume" not in df.columns:
            df["volume"] = df["tick_volume"]
        for col in ("open", "high", "low", "close", "volume"):
            if col not in df.columns:
                df[col] = 0.0
        return df[["time", "open", "high", "low", "close", "volume"]]

    def _summarize_frame(self, timeframe: str, df: pd.DataFrame) -> Dict[str, Any]:
        data = df.tail(max(self.prompt_recent_bars, 12)).copy()
        close = data["close"].astype(float)
        high = data["high"].astype(float)
        low = data["low"].astype(float)

        ema_fast = close.ewm(span=9, adjust=False).mean()
        ema_slow = close.ewm(span=21, adjust=False).mean()
        trend_state = "sideway"
        if len(ema_fast) >= 1 and len(ema_slow) >= 1:
            if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
                trend_state = "up"
            elif ema_fast.iloc[-1] < ema_slow.iloc[-1]:
                trend_state = "down"

        close_first = float(close.iloc[0])
        close_last = float(close.iloc[-1])
        pct_change = ((close_last - close_first) / close_first * 100.0) if close_first != 0 else 0.0

        return {
            "timeframe": timeframe,
            "bars": int(len(data)),
            "last_time": str(data.iloc[-1]["time"]),
            "last_close": round(close_last, 5),
            "pct_change_window": round(pct_change, 4),
            "window_high": round(float(high.max()), 5),
            "window_low": round(float(low.min()), 5),
            "avg_range": round(float((high - low).mean()), 5),
            "ema_fast_9": round(float(ema_fast.iloc[-1]), 5),
            "ema_slow_21": round(float(ema_slow.iloc[-1]), 5),
            "trend": trend_state,
            "recent_closes": [round(float(v), 5) for v in close.tail(self.prompt_recent_bars).tolist()],
        }

    def _collect_positions_snapshot(self, symbol: str) -> Dict[str, Any]:
        raw_positions: List[Any] = []
        runtime = self._runtime_mt5
        if runtime is not None and hasattr(runtime, "get_positions"):
            try:
                raw_positions = list(runtime.get_positions(symbol=symbol) or [])
            except TypeError:
                all_positions = list(runtime.get_positions() or [])
                raw_positions = [p for p in all_positions if str(getattr(p, "symbol", "")) == symbol]
        else:
            raw_positions = list(mt5.positions_get(symbol=symbol) or [])

        long_positions = 0
        long_volume = 0.0
        avg_entry_long = 0.0
        total_profit = 0.0

        buy_type = getattr(mt5, "ORDER_TYPE_BUY", 0)
        for pos in raw_positions:
            profit = float(getattr(pos, "profit", 0.0) or 0.0)
            total_profit += profit
            pos_type = getattr(pos, "type", None)
            volume = float(getattr(pos, "volume", 0.0) or 0.0)
            price_open = float(
                getattr(
                    pos,
                    "price_open",
                    getattr(pos, "open_price", 0.0),
                )
                or 0.0
            )

            is_buy = pos_type == buy_type or pos_type == Signal.BUY
            if is_buy:
                long_positions += 1
                long_volume += volume
                avg_entry_long += volume * price_open

        if long_volume > 0:
            avg_entry_long = avg_entry_long / long_volume
        else:
            avg_entry_long = 0.0

        return {
            "symbol": symbol,
            "position_count": int(len(raw_positions)),
            "long_positions": int(long_positions),
            "long_volume": round(float(long_volume), 4),
            "avg_entry_long": round(float(avg_entry_long), 5),
            "floating_profit": round(float(total_profit), 2),
        }

    def _build_ai_payload(
        self,
        symbol: str,
        mtf_data: List[Dict[str, Any]],
        positions: Dict[str, Any],
        request_id: str = "",
    ) -> Dict[str, Any]:
        long_positions = int(positions.get("long_positions", 0) or 0)
        if long_positions > 0:
            required_action_set = ["enter_buy", "hold", "sell_out", "buy_add"]
        else:
            required_action_set = ["enter_buy", "hold"]

        return {
            "request_id": request_id,
            "symbol": symbol,
            "strategy": self.name,
            "required_action_set": required_action_set,
            "timeframes": mtf_data,
            "position_state": positions,
            "rules": {
                "prefer_hold_when_uncertain": True,
                "do_not_short": True,
                "consider_sell_out_when_trend_turns_down_and_position_is_risky": True,
                "consider_buy_add_only_when_existing_long_has_supporting_trend": True,
                "if_no_long_position_never_use_buy_add_or_sell_out": long_positions <= 0,
            },
        }

    def _request_ai_decision(self, payload: Dict[str, Any]) -> str:
        try:
            if self.provider == "ollama":
                return self._request_ollama(payload)
            return self._request_openai_compatible(payload)
        except Exception as exc:
            logger.error("AI decision request failed: {}", exc)
            return ""

    def _request_openai_compatible(self, payload: Dict[str, Any]) -> str:
        system_prompt = (
            "You are a trading decision assistant. "
            "Return ONLY JSON with keys: action, reason, confidence, request_id. "
            "Allowed action values: enter_buy, hold, sell_out, buy_add."
        )
        user_prompt = (
            "Analyze this market context and choose one action from allowed set.\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        last_error: Optional[Exception] = None
        for url in self._openai_chat_urls(self.base_url):
            try:
                response = requests.post(url, headers=headers, json=body, timeout=self.timeout_seconds)
                if response.status_code in {404, 405}:
                    logger.warning(
                        "AI endpoint not supported at {} (status={}), trying fallback",
                        url,
                        response.status_code,
                    )
                    continue
                response.raise_for_status()
                parsed = response.json()
                choices = parsed.get("choices", [])
                if not choices:
                    return ""
                message = choices[0].get("message", {}) or {}
                content = str(message.get("content", "") or "").strip()
                if content:
                    return content
                # Some reasoning-capable local models return empty content and
                # put text in reasoning_content when max_tokens is tight.
                reasoning = str(message.get("reasoning_content", "") or "").strip()
                return reasoning
            except requests.RequestException as exc:
                last_error = exc
                continue

        if last_error:
            raise last_error
        raise RuntimeError("No compatible OpenAI chat endpoint found")

    def _request_ollama(self, payload: Dict[str, Any]) -> str:
        system_prompt = (
            "You are a trading decision assistant. "
            "Return ONLY JSON with keys: action, reason, confidence, request_id. "
            "Allowed action values: enter_buy, hold, sell_out, buy_add."
        )
        user_prompt = (
            "Analyze this market context and choose one action from allowed set.\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

        base = self.base_url.rstrip("/")
        if base.endswith("/api/chat"):
            url = base
        else:
            url = f"{base}/api/chat"

        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {
                "temperature": self.temperature,
            },
        }

        response = requests.post(url, json=body, timeout=self.timeout_seconds)
        response.raise_for_status()
        parsed = response.json()
        message = parsed.get("message", {}) or {}
        return str(message.get("content", "") or "").strip()

    @staticmethod
    def _openai_chat_urls(base_url: str) -> List[str]:
        """
        Build candidate OpenAI-compatible chat endpoints.
        Handles common local variants such as /v1 and /api/v1.
        """
        base = str(base_url or "").strip().rstrip("/")
        if not base:
            base = "https://api.openai.com/v1"

        candidates: List[str] = []

        def add(url: str) -> None:
            u = str(url or "").strip()
            if not u:
                return
            if u not in candidates:
                candidates.append(u)

        if "/api/v1" in base:
            alt = base.replace("/api/v1", "/v1")
            if alt.endswith("/chat/completions"):
                add(alt)
            else:
                add(f"{alt}/chat/completions")

        if base.endswith("/chat/completions"):
            add(base)
        else:
            add(f"{base}/chat/completions")

        split = urlsplit(base)
        if split.scheme and split.netloc:
            root = urlunsplit((split.scheme, split.netloc, "", "", ""))
            add(f"{root}/v1/chat/completions")
            add(f"{root}/api/v1/chat/completions")

        return candidates

    def _parse_ai_response(self, text: str) -> Tuple[str, str, float]:
        action, reason, confidence, _request_id = self._parse_ai_response_meta(text)
        return action, reason, confidence

    def _parse_ai_response_meta(self, text: str) -> Tuple[str, str, float, str]:
        raw = (text or "").strip()
        if not raw:
            return self._ACTION_HOLD, "empty_response", 0.0, ""

        data: Dict[str, Any] = {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = self._extract_first_json_object(raw)

        if data:
            action_raw = str(data.get("action", data.get("decision", ""))).strip()
            reason = str(data.get("reason", data.get("explanation", ""))).strip()
            confidence = self._coerce_confidence(data.get("confidence", 0.0))
            request_id = str(data.get("request_id", "") or "").strip()
            return self._normalize_action(action_raw), reason or "ai_json", confidence, request_id

        extracted = self._extract_action_from_text(raw)
        return extracted, raw[:120], 0.5, ""

    @staticmethod
    def _extract_first_json_object(raw: str) -> Dict[str, Any]:
        """
        Parse the first valid JSON object from mixed text.
        Useful for model outputs like:
          {"action":"hold",...}\nextra text...
        """
        text = str(raw or "")
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                obj, _end = decoder.raw_decode(text[idx:])
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        return {}

    @staticmethod
    def _coerce_confidence(value: Any) -> float:
        try:
            c = float(value)
            if c < 0:
                return 0.0
            if c > 1:
                return 1.0
            return c
        except (TypeError, ValueError):
            return 0.0

    def _normalize_action(self, raw_action: str) -> str:
        key = str(raw_action or "").strip().lower()
        key = key.replace("-", "_").replace(" ", "_")

        mapping = {
            "enter_buy": self._ACTION_ENTER_BUY,
            "buy": self._ACTION_ENTER_BUY,
            "open_buy": self._ACTION_ENTER_BUY,
            "long": self._ACTION_ENTER_BUY,
            "เข้าซื้อ": self._ACTION_ENTER_BUY,
            "buy_entry": self._ACTION_ENTER_BUY,

            "hold": self._ACTION_HOLD,
            "wait": self._ACTION_HOLD,
            "no_action": self._ACTION_HOLD,
            "ถือต่อ": self._ACTION_HOLD,
            "ถือ": self._ACTION_HOLD,

            "sell_out": self._ACTION_SELL_OUT,
            "exit": self._ACTION_SELL_OUT,
            "close": self._ACTION_SELL_OUT,
            "close_position": self._ACTION_SELL_OUT,
            "ขายออก": self._ACTION_SELL_OUT,
            "sell": self._ACTION_SELL_OUT,

            "buy_add": self._ACTION_BUY_ADD,
            "add_buy": self._ACTION_BUY_ADD,
            "add": self._ACTION_BUY_ADD,
            "scale_in": self._ACTION_BUY_ADD,
            "ซื้อเพิ่ม": self._ACTION_BUY_ADD,
        }
        return mapping.get(key, self._ACTION_HOLD)

    def _extract_action_from_text(self, text: str) -> str:
        """Extract action keyword from free-form model output."""
        body = str(text or "").lower()
        # 1) Prefer explicit "action: <label>" patterns.
        explicit_patterns = [
            r"\baction\s*[:=]\s*['\"]?([a-z_ -]+)['\"]?",
            r"\bfinal\s+action\s*[:=]\s*['\"]?([a-z_ -]+)['\"]?",
            r"\brecommended\s+action\s*[:=]\s*['\"]?([a-z_ -]+)['\"]?",
            r"\bdecision\s*[:=]\s*['\"]?([a-z_ -]+)['\"]?",
            r"\b(เข้าซื้อ|ถือต่อ|ขายออก|ซื้อเพิ่ม)\b",
        ]
        for pattern in explicit_patterns:
            match = re.search(pattern, body)
            if not match:
                continue
            candidate = match.group(1) if match.groups() else match.group(0)
            action = self._normalize_action(candidate.strip())
            if action in self._VALID_ACTIONS:
                return action

        # 2) If multiple action labels appear (typical "allowed actions are ..."),
        # treat as ambiguous and stay safe.
        canonical_hits = set()
        for label in ("enter_buy", "hold", "sell_out", "buy_add"):
            if re.search(rf"\b{label}\b", body):
                canonical_hits.add(label)
        if len(canonical_hits) >= 2:
            return self._ACTION_HOLD

        # 3) If exactly one canonical label appears, accept it.
        if len(canonical_hits) == 1:
            return self._normalize_action(next(iter(canonical_hits)))

        # 4) Conservative fallback.
        return self._ACTION_HOLD

    def _normalize_timeframes(self, raw: Any) -> List[str]:
        if isinstance(raw, str):
            candidates = [part.strip() for part in raw.split(",") if part.strip()]
        elif isinstance(raw, list):
            candidates = [str(part).strip() for part in raw if str(part).strip()]
        else:
            candidates = []

        normalized: List[str] = []
        seen = set()
        for tf in candidates:
            val = self._normalize_timeframe(tf)
            if not val or val in seen:
                continue
            if val not in MT5Connector.TIMEFRAMES:
                continue
            normalized.append(val)
            seen.add(val)

        return normalized or [self.timeframe]

    def _normalize_timeframe(self, value: Any) -> str:
        text = str(value or "").strip().upper()
        if not text:
            return "M1"
        return self._TIMEFRAME_ALIASES.get(text, text)
