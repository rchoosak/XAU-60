"""
Hybrid Sideway Liquidity Reversion Strategy.

Integrated low-timeframe range framework:
- Sideway/dormant regime filter
- Range edge mean reversion
- Liquidity sweep reversal
- Failed breakout fade
- Internal micro scalp
- Session-aware execution
"""
from datetime import date
from typing import Dict, Any, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from core.strategy_base import StrategyBase, TradeSignal, Signal, Position
from indicators.common import calculate_atr, calculate_adx, calculate_ema, calculate_rsi
from utils.config import config as env_config


class HybridSidewayLiquidityReversion(StrategyBase):
    name = "Hybrid Sideway Liquidity Reversion"
    version = "1.0.0"
    description = "Sideway liquidity trap + mean reversion hybrid for S1/M1/M5"
    author = "AlgoAct"

    def __init__(self):
        super().__init__()

        # Regime and structure
        self.regime_atr_fast = 14
        self.regime_atr_slow = 100
        self.regime_adx_max = 18.0
        self.regime_ema_period = 50
        self.regime_slope_lookback = 12
        self.regime_slope_max_atr = 0.20
        self.regime_overlap_lookback = 20
        self.regime_overlap_min = 0.55
        self.regime_expansion_atr_multiple = 1.8
        self.regime_expansion_ratio_max = 0.28
        self.regime_score_min = 65.0
        self.regime_filter_enabled = True

        self.range_lookback = 96
        self.range_min_width_pips = 80.0
        self.range_max_width_pips = 320.0
        self.range_touch_tolerance_pips = 18.0
        self.range_min_touches_per_side = 2
        self.range_filter_enabled = True
        self.edge_buffer_pips = 12.0
        self.reclaim_buffer_pips = 8.0

        # Liquidity and breakout behavior
        self.sweep_depth_pips = 16.0
        self.wick_ratio_min = 1.2
        self.breakout_accept_buffer_pips = 10.0
        self.breakout_accept_bars = 3
        self.breakout_state_expire_bars = 6
        self.failed_breakout_reentry_bars = 3
        self.breakout_body_atr_min = 0.65

        # Internal scalp
        self.allow_internal_scalp = True
        self.internal_scalp_trigger_fraction = 0.28
        self.internal_scalp_min_distance_pips = 14.0
        self.edge_require_rejection = True

        # Additional signal gates
        self.momentum_rsi_period = 14
        self.edge_model_adx_max = 22.0
        self.edge_rsi_buy_max = 45.0
        self.edge_rsi_sell_min = 55.0
        self.scalp_rsi_buy_max = 45.0
        self.scalp_rsi_sell_min = 55.0
        self.cooldown_bars = 2
        self.reentry_spacing_factor = 0.7
        self.max_signals_per_day = 24

        # Risk
        self.edge_stop_atr_mult = 1.0
        self.sweep_stop_atr_mult = 1.15
        self.fade_stop_atr_mult = 1.1
        self.scalp_stop_atr_mult = 0.8

        self.edge_min_rr = 1.0
        self.sweep_min_rr = 1.2
        self.fade_min_rr = 1.1
        self.scalp_min_rr = 0.8

        self.stop_loss_min_pips = 40.0
        self.stop_loss_max_pips = 400.0
        self.trailing_stop = True
        self.trailing_start_pips = 80.0
        self.trailing_pips = 90.0
        self.lot_size = 0.0

        # Session and execution
        self.use_time_filter = True
        self.start_hour = 0
        self.end_hour = 23
        self.trade_friday = True
        self.session_timezone = "UTC"
        self.data_timezone = "UTC"

        self.allow_long = True
        self.allow_short = True
        self.blocked_hours: Set[int] = set()
        self.blocked_weekdays: Set[int] = set()

        # Runtime state
        self.magic_number = 790901
        self._bar_counter: Dict[str, int] = {}
        self._last_signal_bar: Dict[str, int] = {}
        self._last_entry_price_by_side: Dict[str, Dict[Signal, float]] = {}
        self._daily_signal_date: Dict[str, date] = {}
        self._daily_signal_count: Dict[str, int] = {}
        self._breakout_state: Dict[str, Dict[str, Any]] = {}

    def initialize(self, config: Dict[str, Any]) -> None:
        self.config = config

        params = config.get("parameters", {})
        self.regime_atr_fast = int(params.get("regime_atr_fast", self.regime_atr_fast))
        self.regime_atr_slow = int(params.get("regime_atr_slow", self.regime_atr_slow))
        self.regime_adx_max = float(params.get("regime_adx_max", self.regime_adx_max))
        self.regime_ema_period = int(params.get("regime_ema_period", self.regime_ema_period))
        self.regime_slope_lookback = int(params.get("regime_slope_lookback", self.regime_slope_lookback))
        self.regime_slope_max_atr = float(params.get("regime_slope_max_atr", self.regime_slope_max_atr))
        self.regime_overlap_lookback = int(params.get("regime_overlap_lookback", self.regime_overlap_lookback))
        self.regime_overlap_min = float(params.get("regime_overlap_min", self.regime_overlap_min))
        self.regime_expansion_atr_multiple = float(
            params.get("regime_expansion_atr_multiple", self.regime_expansion_atr_multiple)
        )
        self.regime_expansion_ratio_max = float(
            params.get("regime_expansion_ratio_max", self.regime_expansion_ratio_max)
        )
        self.regime_score_min = float(params.get("regime_score_min", self.regime_score_min))
        self.regime_filter_enabled = bool(params.get("regime_filter_enabled", self.regime_filter_enabled))

        self.range_lookback = int(params.get("range_lookback", self.range_lookback))
        self.range_min_width_pips = float(params.get("range_min_width_pips", self.range_min_width_pips))
        self.range_max_width_pips = float(params.get("range_max_width_pips", self.range_max_width_pips))
        self.range_touch_tolerance_pips = float(
            params.get("range_touch_tolerance_pips", self.range_touch_tolerance_pips)
        )
        self.range_min_touches_per_side = int(
            params.get("range_min_touches_per_side", self.range_min_touches_per_side)
        )
        self.range_filter_enabled = bool(params.get("range_filter_enabled", self.range_filter_enabled))
        self.edge_buffer_pips = float(params.get("edge_buffer_pips", self.edge_buffer_pips))
        self.reclaim_buffer_pips = float(params.get("reclaim_buffer_pips", self.reclaim_buffer_pips))

        self.sweep_depth_pips = float(params.get("sweep_depth_pips", self.sweep_depth_pips))
        self.wick_ratio_min = float(params.get("wick_ratio_min", self.wick_ratio_min))
        self.breakout_accept_buffer_pips = float(
            params.get("breakout_accept_buffer_pips", self.breakout_accept_buffer_pips)
        )
        self.breakout_accept_bars = int(params.get("breakout_accept_bars", self.breakout_accept_bars))
        self.breakout_state_expire_bars = int(
            params.get("breakout_state_expire_bars", self.breakout_state_expire_bars)
        )
        self.failed_breakout_reentry_bars = int(
            params.get("failed_breakout_reentry_bars", self.failed_breakout_reentry_bars)
        )
        self.breakout_body_atr_min = float(params.get("breakout_body_atr_min", self.breakout_body_atr_min))

        self.allow_internal_scalp = bool(params.get("allow_internal_scalp", self.allow_internal_scalp))
        self.internal_scalp_trigger_fraction = float(
            params.get("internal_scalp_trigger_fraction", self.internal_scalp_trigger_fraction)
        )
        self.internal_scalp_min_distance_pips = float(
            params.get("internal_scalp_min_distance_pips", self.internal_scalp_min_distance_pips)
        )
        self.edge_require_rejection = bool(params.get("edge_require_rejection", self.edge_require_rejection))

        self.momentum_rsi_period = int(params.get("momentum_rsi_period", self.momentum_rsi_period))
        self.edge_model_adx_max = float(params.get("edge_model_adx_max", self.edge_model_adx_max))
        self.edge_rsi_buy_max = float(params.get("edge_rsi_buy_max", self.edge_rsi_buy_max))
        self.edge_rsi_sell_min = float(params.get("edge_rsi_sell_min", self.edge_rsi_sell_min))
        self.scalp_rsi_buy_max = float(params.get("scalp_rsi_buy_max", self.scalp_rsi_buy_max))
        self.scalp_rsi_sell_min = float(params.get("scalp_rsi_sell_min", self.scalp_rsi_sell_min))
        self.cooldown_bars = max(1, int(params.get("cooldown_bars", self.cooldown_bars)))
        self.reentry_spacing_factor = max(0.0, float(params.get("reentry_spacing_factor", self.reentry_spacing_factor)))
        self.max_signals_per_day = max(1, int(params.get("max_signals_per_day", self.max_signals_per_day)))

        risk = config.get("risk", {})
        self.edge_stop_atr_mult = float(risk.get("edge_stop_atr_mult", self.edge_stop_atr_mult))
        self.sweep_stop_atr_mult = float(risk.get("sweep_stop_atr_mult", self.sweep_stop_atr_mult))
        self.fade_stop_atr_mult = float(risk.get("fade_stop_atr_mult", self.fade_stop_atr_mult))
        self.scalp_stop_atr_mult = float(risk.get("scalp_stop_atr_mult", self.scalp_stop_atr_mult))

        self.edge_min_rr = float(risk.get("edge_min_rr", self.edge_min_rr))
        self.sweep_min_rr = float(risk.get("sweep_min_rr", self.sweep_min_rr))
        self.fade_min_rr = float(risk.get("fade_min_rr", self.fade_min_rr))
        self.scalp_min_rr = float(risk.get("scalp_min_rr", self.scalp_min_rr))

        self.stop_loss_min_pips = float(risk.get("stop_loss_min_pips", self.stop_loss_min_pips))
        self.stop_loss_max_pips = float(risk.get("stop_loss_max_pips", self.stop_loss_max_pips))
        self.trailing_stop = bool(risk.get("trailing_stop", self.trailing_stop))
        self.trailing_start_pips = float(risk.get("trailing_start_pips", self.trailing_start_pips))
        self.trailing_pips = float(risk.get("trailing_pips", self.trailing_pips))
        self.lot_size = float(risk.get("lot_size", env_config.trading.default_lot_size))

        session = config.get("session", {})
        self.use_time_filter = bool(session.get("use_time_filter", self.use_time_filter))
        self.start_hour = int(session.get("start_hour", self.start_hour))
        self.end_hour = int(session.get("end_hour", self.end_hour))
        self.trade_friday = bool(session.get("trade_friday", self.trade_friday))
        self.session_timezone = str(session.get("timezone", self.session_timezone))
        self.data_timezone = str(session.get("data_timezone", self.data_timezone))

        exec_filters = config.get("execution_filters", {})
        if bool(exec_filters.get("enabled", False)):
            self.allow_long = bool(exec_filters.get("allow_long", True))
            self.allow_short = bool(exec_filters.get("allow_short", True))
            self.blocked_hours = self._parse_blocked_hours(exec_filters.get("blocked_hours", []))
            self.blocked_weekdays = self._parse_blocked_weekdays(exec_filters.get("blocked_weekdays", []))
        else:
            self.allow_long = True
            self.allow_short = True
            self.blocked_hours = set()
            self.blocked_weekdays = set()

        self.symbols = config.get("symbols", ["XAUUSD"])
        self.timeframe = config.get("timeframe", "M1")
        self.enabled = bool(config.get("enabled", True))
        self.magic_number = int(config.get("magic_number", self.magic_number))

        self._bar_counter = {}
        self._last_signal_bar = {}
        self._last_entry_price_by_side = {}
        self._daily_signal_date = {}
        self._daily_signal_count = {}
        self._breakout_state = {}

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        required = max(
            30,
            self.momentum_rsi_period + 2,
            self.range_min_touches_per_side + 5,
        )
        if len(data) < required:
            return None

        data_len = len(data)
        atr_fast_period = max(2, min(self.regime_atr_fast, data_len - 2))
        atr_slow_period = max(2, min(self.regime_atr_slow, data_len - 2))
        ema_period = max(2, min(self.regime_ema_period, data_len - 2))
        rsi_period = max(2, min(self.momentum_rsi_period, data_len - 2))

        self._bar_counter[symbol] = self._bar_counter.get(symbol, 0) + 1
        current_bar_index = self._bar_counter[symbol]
        last_signal_bar = self._last_signal_bar.get(symbol, -10_000)
        if current_bar_index - last_signal_bar < self.cooldown_bars:
            return None

        is_open, session_time = self._is_trading_time(data)
        if not is_open or self._is_blocked_window(session_time):
            return None

        self._reset_daily_counter(symbol, session_time.date())
        if self._daily_signal_count.get(symbol, 0) >= self.max_signals_per_day:
            return None

        try:
            atr_fast_series = calculate_atr(data, atr_fast_period)
            atr_slow_series = calculate_atr(data, atr_slow_period)
            adx_series = calculate_adx(data, 14)
            ema_series = calculate_ema(data, ema_period)
            rsi_series = calculate_rsi(data, rsi_period)

            atr_fast = float(atr_fast_series.iloc[-1])
            atr_slow = float(atr_slow_series.iloc[-1])
            adx = float(adx_series.iloc[-1])
            rsi = float(rsi_series.iloc[-1])
            close = float(data.iloc[-1]["close"])
        except Exception:
            return None

        if pd.isna(atr_fast) or pd.isna(atr_slow) or pd.isna(adx) or pd.isna(rsi):
            return None

        regime_ok, _ = self._detect_sideway_regime(data, atr_fast, atr_slow, adx, ema_series)
        if self.regime_filter_enabled and not regime_ok:
            self._breakout_state.pop(symbol, None)
            return None

        range_ctx = self._detect_range(symbol, data)
        if self.range_filter_enabled and not range_ctx["valid"]:
            self._breakout_state.pop(symbol, None)
            return None

        self._update_breakout_state(symbol, current_bar_index, close, range_ctx)
        event_ctx = self._classify_events(symbol, current_bar_index, data, range_ctx, atr_fast)

        signal, model = self._select_entry_model(symbol, data, range_ctx, event_ctx, adx, rsi)
        if signal is None or model is None:
            return None

        if signal == Signal.BUY and not self.allow_long:
            return None
        if signal == Signal.SELL and not self.allow_short:
            return None

        if not self._passes_reentry_filter(symbol, signal, close, range_ctx["width_price"]):
            return None

        trade_signal = self._build_trade_signal(
            symbol=symbol,
            signal=signal,
            model=model,
            entry_price=close,
            atr=atr_fast,
            range_ctx=range_ctx,
        )
        if trade_signal is None:
            return None

        self._last_signal_bar[symbol] = current_bar_index
        self._daily_signal_count[symbol] = self._daily_signal_count.get(symbol, 0) + 1
        side_state = self._last_entry_price_by_side.setdefault(symbol, {})
        side_state[signal] = close
        return trade_signal

    def should_close(self, position: Position, data: pd.DataFrame) -> bool:
        return False

    def get_trailing_stop(self, position: Position, data: pd.DataFrame) -> Optional[float]:
        if not self.trailing_stop or len(data) < 2:
            return None

        current_price = float(data.iloc[-1]["close"])
        pip_price = self._pips_to_price(position.symbol, 1.0)
        if pip_price <= 0:
            return None

        if position.type == Signal.BUY:
            profit_price = current_price - position.open_price
        else:
            profit_price = position.open_price - current_price
        if profit_price <= 0:
            return None

        profit_pips = profit_price / pip_price
        if profit_pips < self.trailing_start_pips:
            return None

        trail_distance = self._pips_to_price(position.symbol, self.trailing_pips)
        return self._apply_trailing_distance(position, current_price, trail_distance)

    def _detect_sideway_regime(
        self,
        data: pd.DataFrame,
        atr_fast: float,
        atr_slow: float,
        adx: float,
        ema_series: pd.Series,
    ) -> Tuple[bool, float]:
        atr_ratio = atr_fast / max(atr_slow, 1e-9)

        ema_now = float(ema_series.iloc[-1])
        ema_prev = float(ema_series.iloc[-1 - self.regime_slope_lookback])
        slope_atr = abs(ema_now - ema_prev) / max(atr_fast, 1e-9)

        overlap = self._candle_overlap_ratio(data.iloc[-self.regime_overlap_lookback:])
        expansion_ratio = self._expansion_ratio(data.iloc[-self.regime_overlap_lookback:], atr_fast)

        score = 0.0
        score += 25.0 if atr_ratio <= 0.95 else 0.0
        score += 25.0 if adx <= self.regime_adx_max else 0.0
        score += 20.0 if slope_atr <= self.regime_slope_max_atr else 0.0
        score += 20.0 if overlap >= self.regime_overlap_min else 0.0
        score += 10.0 if expansion_ratio <= self.regime_expansion_ratio_max else 0.0
        return score >= self.regime_score_min, score

    def _detect_range(self, symbol: str, data: pd.DataFrame) -> Dict[str, Any]:
        window = data.iloc[-self.range_lookback:]
        high = float(window["high"].max())
        low = float(window["low"].min())
        midpoint = (high + low) / 2.0
        width_price = high - low

        min_width = self._pips_to_price(symbol, self.range_min_width_pips)
        max_width = self._pips_to_price(symbol, self.range_max_width_pips)
        tolerance = self._pips_to_price(symbol, self.range_touch_tolerance_pips)

        upper_touches = int((window["high"] >= (high - tolerance)).sum())
        lower_touches = int((window["low"] <= (low + tolerance)).sum())

        valid = (
            width_price > 0
            and width_price >= min_width
            and width_price <= max_width
            and upper_touches >= self.range_min_touches_per_side
            and lower_touches >= self.range_min_touches_per_side
        )

        return {
            "valid": valid,
            "high": high,
            "low": low,
            "midpoint": midpoint,
            "width_price": width_price,
        }

    def _update_breakout_state(
        self,
        symbol: str,
        current_bar_index: int,
        close: float,
        range_ctx: Dict[str, Any],
    ) -> None:
        accept_buffer = self._pips_to_price(symbol, self.breakout_accept_buffer_pips)
        state = self._breakout_state.get(symbol)

        if close > range_ctx["high"] + accept_buffer:
            self._breakout_state[symbol] = {"direction": "up", "bar_index": current_bar_index}
            return
        if close < range_ctx["low"] - accept_buffer:
            self._breakout_state[symbol] = {"direction": "down", "bar_index": current_bar_index}
            return

        if state is None:
            return
        if current_bar_index - int(state.get("bar_index", -10_000)) > self.breakout_state_expire_bars:
            self._breakout_state.pop(symbol, None)

    def _classify_events(
        self,
        symbol: str,
        current_bar_index: int,
        data: pd.DataFrame,
        range_ctx: Dict[str, Any],
        atr: float,
    ) -> Dict[str, Any]:
        row = data.iloc[-1]
        close = float(row["close"])
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])

        edge_buffer = self._pips_to_price(symbol, self.edge_buffer_pips)
        sweep_depth = self._pips_to_price(symbol, self.sweep_depth_pips)
        reclaim_buffer = self._pips_to_price(symbol, self.reclaim_buffer_pips)
        breakout_buffer = self._pips_to_price(symbol, self.breakout_accept_buffer_pips)

        at_upper_edge = close >= range_ctx["high"] - edge_buffer
        at_lower_edge = close <= range_ctx["low"] + edge_buffer

        sweep_up = (
            high > range_ctx["high"] + sweep_depth
            and close <= range_ctx["high"] - reclaim_buffer
        )
        sweep_down = (
            low < range_ctx["low"] - sweep_depth
            and close >= range_ctx["low"] + reclaim_buffer
        )

        rejection_upper = self._is_rejection_wick(open_, high, low, close, side="upper")
        rejection_lower = self._is_rejection_wick(open_, high, low, close, side="lower")

        body = abs(close - open_)
        true_breakout_up = close > range_ctx["high"] + breakout_buffer and body >= atr * self.breakout_body_atr_min
        true_breakout_down = close < range_ctx["low"] - breakout_buffer and body >= atr * self.breakout_body_atr_min

        failed_up_break = False
        failed_down_break = False
        breakout_accepted_up = False
        breakout_accepted_down = False

        state = self._breakout_state.get(symbol)
        if state:
            age = current_bar_index - int(state.get("bar_index", current_bar_index))
            direction = str(state.get("direction", ""))
            if direction == "up":
                if age >= self.breakout_accept_bars and close > range_ctx["high"]:
                    breakout_accepted_up = True
                if age <= self.failed_breakout_reentry_bars and close < range_ctx["high"] - reclaim_buffer:
                    failed_up_break = True
                    self._breakout_state.pop(symbol, None)
            elif direction == "down":
                if age >= self.breakout_accept_bars and close < range_ctx["low"]:
                    breakout_accepted_down = True
                if age <= self.failed_breakout_reentry_bars and close > range_ctx["low"] + reclaim_buffer:
                    failed_down_break = True
                    self._breakout_state.pop(symbol, None)

        return {
            "close": close,
            "at_upper_edge": at_upper_edge,
            "at_lower_edge": at_lower_edge,
            "sweep_up": sweep_up,
            "sweep_down": sweep_down,
            "rejection_upper": rejection_upper,
            "rejection_lower": rejection_lower,
            "true_breakout_up": true_breakout_up,
            "true_breakout_down": true_breakout_down,
            "failed_up_break": failed_up_break,
            "failed_down_break": failed_down_break,
            "breakout_accepted_up": breakout_accepted_up,
            "breakout_accepted_down": breakout_accepted_down,
        }

    def _select_entry_model(
        self,
        symbol: str,
        data: pd.DataFrame,
        range_ctx: Dict[str, Any],
        event_ctx: Dict[str, Any],
        adx: float,
        rsi: float,
    ) -> Tuple[Optional[Signal], Optional[str]]:
        bull_shift = self._detect_micro_structure_shift(data, direction=Signal.BUY)
        bear_shift = self._detect_micro_structure_shift(data, direction=Signal.SELL)

        # 1) Liquidity sweep reversal (highest priority)
        if event_ctx["sweep_down"] and bull_shift and not event_ctx["breakout_accepted_down"]:
            return Signal.BUY, "sweep_reversal"
        if event_ctx["sweep_up"] and bear_shift and not event_ctx["breakout_accepted_up"]:
            return Signal.SELL, "sweep_reversal"

        # 2) Failed breakout fade
        if event_ctx["failed_down_break"] and bull_shift:
            return Signal.BUY, "failed_breakout_fade"
        if event_ctx["failed_up_break"] and bear_shift:
            return Signal.SELL, "failed_breakout_fade"

        # 3) Range edge mean reversion
        if (
            event_ctx["at_lower_edge"]
            and (event_ctx["rejection_lower"] or not self.edge_require_rejection)
            and not event_ctx["true_breakout_down"]
            and adx <= self.edge_model_adx_max
            and rsi <= self.edge_rsi_buy_max
        ):
            return Signal.BUY, "edge_reversion"
        if (
            event_ctx["at_upper_edge"]
            and (event_ctx["rejection_upper"] or not self.edge_require_rejection)
            and not event_ctx["true_breakout_up"]
            and adx <= self.edge_model_adx_max
            and rsi >= self.edge_rsi_sell_min
        ):
            return Signal.SELL, "edge_reversion"

        # 4) Internal micro scalp (lowest priority)
        if self.allow_internal_scalp:
            width = max(range_ctx["width_price"], 1e-9)
            dev = (event_ctx["close"] - range_ctx["midpoint"]) / width
            min_scalp_dist = self._pips_to_price(symbol, self.internal_scalp_min_distance_pips)
            dist_to_mid = abs(event_ctx["close"] - range_ctx["midpoint"])
            if dist_to_mid >= min_scalp_dist:
                if (
                    dev <= -self.internal_scalp_trigger_fraction
                    and bull_shift
                    and rsi <= self.scalp_rsi_buy_max
                ):
                    return Signal.BUY, "internal_scalp"
                if (
                    dev >= self.internal_scalp_trigger_fraction
                    and bear_shift
                    and rsi >= self.scalp_rsi_sell_min
                ):
                    return Signal.SELL, "internal_scalp"

        return None, None

    def _build_trade_signal(
        self,
        symbol: str,
        signal: Signal,
        model: str,
        entry_price: float,
        atr: float,
        range_ctx: Dict[str, Any],
    ) -> Optional[TradeSignal]:
        atr_mult_map = {
            "edge_reversion": self.edge_stop_atr_mult,
            "sweep_reversal": self.sweep_stop_atr_mult,
            "failed_breakout_fade": self.fade_stop_atr_mult,
            "internal_scalp": self.scalp_stop_atr_mult,
        }
        min_rr_map = {
            "edge_reversion": self.edge_min_rr,
            "sweep_reversal": self.sweep_min_rr,
            "failed_breakout_fade": self.fade_min_rr,
            "internal_scalp": self.scalp_min_rr,
        }

        atr_mult = atr_mult_map.get(model, self.edge_stop_atr_mult)
        min_rr = min_rr_map.get(model, self.edge_min_rr)

        min_stop = self._pips_to_price(symbol, self.stop_loss_min_pips)
        max_stop = self._pips_to_price(symbol, self.stop_loss_max_pips)
        stop_distance = max(min_stop, atr * atr_mult)
        stop_distance = min(stop_distance, max_stop)
        if stop_distance <= 0:
            return None

        if signal == Signal.BUY:
            stop_loss = entry_price - stop_distance
            take_profit = self._resolve_take_profit(
                signal=signal,
                model=model,
                entry_price=entry_price,
                stop_distance=stop_distance,
                range_ctx=range_ctx,
                min_rr=min_rr,
            )
        else:
            stop_loss = entry_price + stop_distance
            take_profit = self._resolve_take_profit(
                signal=signal,
                model=model,
                entry_price=entry_price,
                stop_distance=stop_distance,
                range_ctx=range_ctx,
                min_rr=min_rr,
            )

        if take_profit is None:
            return None

        comment = f"HSLR_{model}".upper()[:31]
        return TradeSignal(
            signal=signal,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=self.lot_size,
            comment=comment,
            magic_number=self.magic_number,
        )

    def _resolve_take_profit(
        self,
        signal: Signal,
        model: str,
        entry_price: float,
        stop_distance: float,
        range_ctx: Dict[str, Any],
        min_rr: float,
    ) -> Optional[float]:
        direction = 1.0 if signal == Signal.BUY else -1.0
        midpoint = float(range_ctx["midpoint"])
        opposite_edge = float(range_ctx["high"] if signal == Signal.BUY else range_ctx["low"])

        if model == "internal_scalp":
            target = midpoint
        elif model == "edge_reversion":
            target = midpoint
        else:
            target = opposite_edge

        reward = abs(target - entry_price)
        min_reward = stop_distance * max(min_rr, 0.1)
        if reward < min_reward:
            target = entry_price + direction * min_reward
        if target == entry_price:
            return None
        return target

    def _detect_micro_structure_shift(self, data: pd.DataFrame, direction: Signal) -> bool:
        n = 5
        if len(data) < n + 2:
            return False

        window = data.iloc[-(n + 1):]
        close = float(window.iloc[-1]["close"])
        open_ = float(window.iloc[-1]["open"])
        prev_high = float(window.iloc[:-1]["high"].max())
        prev_low = float(window.iloc[:-1]["low"].min())

        if direction == Signal.BUY:
            return close > prev_high and close > open_
        return close < prev_low and close < open_

    def _passes_reentry_filter(
        self,
        symbol: str,
        signal: Signal,
        entry_price: float,
        width_price: float,
    ) -> bool:
        by_side = self._last_entry_price_by_side.get(symbol, {})
        previous = by_side.get(signal)
        if previous is None:
            return True
        required_move = width_price * self.reentry_spacing_factor
        return abs(entry_price - previous) >= required_move

    def _candle_overlap_ratio(self, data: pd.DataFrame) -> float:
        if len(data) < 2:
            return 0.0
        overlap_sum = 0.0
        total_sum = 0.0
        prev_high = float(data.iloc[0]["high"])
        prev_low = float(data.iloc[0]["low"])
        for i in range(1, len(data)):
            high = float(data.iloc[i]["high"])
            low = float(data.iloc[i]["low"])
            inter = max(0.0, min(prev_high, high) - max(prev_low, low))
            union = max(prev_high, high) - min(prev_low, low)
            overlap_sum += inter
            total_sum += max(union, 1e-9)
            prev_high = high
            prev_low = low
        return overlap_sum / max(total_sum, 1e-9)

    def _expansion_ratio(self, data: pd.DataFrame, atr_value: float) -> float:
        if len(data) == 0:
            return 1.0
        ranges = (data["high"] - data["low"]).astype(float)
        expanded = (ranges > (atr_value * self.regime_expansion_atr_multiple)).sum()
        return float(expanded) / float(len(data))

    def _is_rejection_wick(self, open_: float, high: float, low: float, close: float, side: str) -> bool:
        body = max(abs(close - open_), 1e-9)
        upper_wick = high - max(open_, close)
        lower_wick = min(open_, close) - low
        if side == "upper":
            return upper_wick / body >= self.wick_ratio_min and close <= open_
        return lower_wick / body >= self.wick_ratio_min and close >= open_

    def _point_for_symbol(self, symbol: str) -> float:
        sym = symbol.upper()
        if "XAU" in sym or "GOLD" in sym:
            return 0.01
        if sym.endswith("JPY"):
            return 0.001
        return 0.0001

    def _pips_to_price(self, symbol: str, pips: float) -> float:
        return pips * self._point_for_symbol(symbol) * 10

    def _to_session_time(self, timestamp) -> pd.Timestamp:
        ts = pd.Timestamp(timestamp)
        session_tz = ZoneInfo(self.session_timezone)
        data_tz = ZoneInfo(self.data_timezone)
        if ts.tzinfo is None:
            ts = ts.tz_localize(data_tz)
        return ts.tz_convert(session_tz)

    def _is_trading_time(self, data: pd.DataFrame) -> Tuple[bool, pd.Timestamp]:
        current_time = self._to_session_time(data.iloc[-1]["time"])
        if not self.use_time_filter:
            return True, current_time

        hour = current_time.hour
        weekday = current_time.weekday()
        if not self.trade_friday and weekday == 4:
            return False, current_time

        if self.start_hour < self.end_hour:
            in_window = self.start_hour <= hour < self.end_hour
        else:
            in_window = hour >= self.start_hour or hour < self.end_hour
        return in_window, current_time

    def _reset_daily_counter(self, symbol: str, current_day: date) -> None:
        last_day = self._daily_signal_date.get(symbol)
        if last_day != current_day:
            self._daily_signal_date[symbol] = current_day
            self._daily_signal_count[symbol] = 0

    def _is_blocked_window(self, session_time: pd.Timestamp) -> bool:
        if self.blocked_hours and session_time.hour in self.blocked_hours:
            return True
        if self.blocked_weekdays and session_time.weekday() in self.blocked_weekdays:
            return True
        return False

    def _parse_blocked_hours(self, raw) -> Set[int]:
        out: Set[int] = set()
        if raw is None:
            return out
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            if "-" in text:
                parts = [p.strip() for p in text.split("-", 1)]
                if len(parts) != 2:
                    continue
                try:
                    start = int(parts[0])
                    end = int(parts[1])
                except ValueError:
                    continue
                if 0 <= start <= 23 and 0 <= end <= 23:
                    if start <= end:
                        out.update(range(start, end + 1))
                    else:
                        out.update(range(start, 24))
                        out.update(range(0, end + 1))
                continue
            try:
                hour = int(text)
            except ValueError:
                continue
            if 0 <= hour <= 23:
                out.add(hour)
        return out

    def _parse_blocked_weekdays(self, raw) -> Set[int]:
        mapping = {
            "mon": 0,
            "monday": 0,
            "tue": 1,
            "tues": 1,
            "tuesday": 1,
            "wed": 2,
            "wednesday": 2,
            "thu": 3,
            "thur": 3,
            "thurs": 3,
            "thursday": 3,
            "fri": 4,
            "friday": 4,
            "sat": 5,
            "saturday": 5,
            "sun": 6,
            "sunday": 6,
        }
        out: Set[int] = set()
        if raw is None:
            return out
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if value is None:
                continue
            text = str(value).strip().lower()
            if not text:
                continue
            if text in mapping:
                out.add(mapping[text])
                continue
            try:
                day = int(text)
            except ValueError:
                continue
            if 0 <= day <= 6:
                out.add(day)
        return out
