"""
Edge Rejection Micro Scalping Strategy.

Production-oriented implementation for XAU60 framework with:
- sideway regime gating
- range and edge classification
- liquidity sweep + rejection confirmation
- micro-structure reversal confirmation
- sweep-extreme stop placement
- conservative micro-scalping exits
- runtime safety / cooldown / diagnostics
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
from loguru import logger

from core.strategy_base import Position, Signal, StrategyBase, TradeSignal
from indicators.common import calculate_adx, calculate_atr, calculate_ema, calculate_rsi
from utils.config import config as env_config


@dataclass
class StrategyConfig:
    # Core identity
    symbols: List[str] = field(default_factory=lambda: ["XAUUSD"])
    timeframe: str = "M1"
    enabled: bool = True
    magic_number: int = 791111
    lot_size: float = 0.0

    # Regime / volatility
    atr_period: int = 14
    atr_baseline_window: int = 80
    atr_compression_threshold: float = 0.90
    adx_period: int = 14
    adx_max_sideway: float = 20.0
    slope_ema_period: int = 55
    slope_lookback: int = 12
    slope_max_atr_multiple: float = 0.28
    overlap_lookback: int = 18
    overlap_min_ratio: float = 0.52
    expansion_bar_multiple: float = 1.8
    expansion_ratio_max: float = 0.30
    regime_score_min: float = 65.0
    regime_filter_enabled: bool = True

    # Range model
    range_lookback: int = 90
    min_range_width_pips: float = 90.0
    max_range_width_pips: float = 420.0
    touch_tolerance_pips: float = 14.0
    min_touches_per_side: int = 2
    max_mid_drift_ratio: float = 0.22
    range_filter_enabled: bool = True

    # Edge zoning
    edge_zone_percent: float = 0.18
    center_exclusion_percent: float = 0.24
    min_distance_from_mid_pips: float = 35.0

    # Sweep / rejection
    sweep_threshold_pips: float = 12.0
    sweep_threshold_pips_buy: float = 12.0
    sweep_threshold_pips_sell: float = 12.0
    close_inside_buffer_pips: float = 4.0
    close_inside_buffer_pips_buy: float = 4.0
    close_inside_buffer_pips_sell: float = 4.0
    rejection_wick_ratio_min: float = 1.15
    rejection_wick_ratio_min_buy: float = 1.15
    rejection_wick_ratio_min_sell: float = 1.15
    require_close_back_inside: bool = True
    rejection_score_min: float = 1.0
    rejection_score_min_buy: float = 1.0
    rejection_score_min_sell: float = 1.0
    next_bar_failure_required: bool = False
    momentum_rsi_period: int = 14
    momentum_fail_buy_max_rsi: float = 52.0
    momentum_fail_sell_min_rsi: float = 48.0

    # Micro structure
    micro_structure_lookback: int = 6
    micro_break_buffer_pips: float = 2.0
    allow_engulfing_confirmation: bool = True
    require_structure_break: bool = True

    # Stops / targets
    stop_buffer_atr_mult: float = 0.18
    stop_buffer_min_pips: float = 6.0
    min_stop_pips: float = 45.0
    max_stop_pips: float = 180.0
    midpoint_tp_enabled: bool = True
    midpoint_min_reward_r: float = 0.45
    fixed_rr_target: float = 1.20
    opposite_edge_front_run_pips: float = 12.0
    partial_tp_ratio: float = 0.55
    move_to_breakeven_after_partial: bool = True
    breakeven_buffer_pips: float = 3.0
    trailing_stop_enabled: bool = True
    trailing_start_pips: float = 85.0
    trailing_distance_pips: float = 70.0

    # Time stop / invalidation
    max_bars_in_trade: int = 26
    no_progress_bar_count: int = 8
    no_progress_min_mfe_pips: float = 18.0
    invalidate_on_regime_break: bool = True
    flatten_on_regime_invalidation: bool = True
    invalidate_atr_jump_multiple: float = 1.35
    invalidate_adx_threshold: float = 28.0

    # Safety controls
    one_active_position_only: bool = True
    max_trades_per_range: int = 2
    cooldown_bars_after_stop: int = 8
    cooldown_bars_after_force_exit: int = 3
    duplicate_sweep_block_bars: int = 30
    spread_threshold_points: float = 40.0
    max_daily_signals: int = 20

    # Session / execution filters
    use_time_filter: bool = True
    session_timezone: str = "UTC"
    data_timezone: str = "UTC"
    start_hour: int = 0
    end_hour: int = 23
    long_allowed_hours: Set[int] = field(default_factory=set)
    short_allowed_hours: Set[int] = field(default_factory=set)
    trade_friday: bool = True
    allow_long: bool = True
    allow_short: bool = True
    blocked_hours: Set[int] = field(default_factory=set)
    blocked_weekdays: Set[int] = field(default_factory=set)

    # Diagnostics
    log_diagnostics: bool = True
    diagnostic_level: str = "DEBUG"
    state_stale_prune_bars: int = 4


@dataclass
class RegimeState:
    valid: bool
    atr_current: float
    atr_baseline: float
    slope: float
    volatility_state: str
    reason: str
    adx: float
    overlap_ratio: float
    expansion_ratio: float
    score: float


@dataclass
class RangeState:
    valid: bool
    range_high: float
    range_low: float
    range_mid: float
    range_width: float
    range_width_pips: float
    upper_touches: int
    lower_touches: int
    drift_ratio: float
    reason: str
    signature: str


@dataclass
class EdgeContext:
    zone: str  # upper | lower | center | none
    in_center_exclusion: bool
    distance_to_mid: float
    distance_to_mid_pips: float


@dataclass
class SweepEvent:
    direction: Signal
    detected: bool
    sweep_depth: float
    sweep_depth_pips: float
    wick_ratio: float
    close_back_inside: bool
    rejection_score: float
    extreme_price: float
    signature: str
    reason: str


@dataclass
class EntrySignal:
    signal: Signal
    model: str
    reason: str
    sweep_signature: str
    range_signature: str


@dataclass
class PositionPlan:
    entry_price: float
    stop_loss: float
    tp_partial: float
    tp_final: float
    effective_take_profit: float
    stop_distance_pips: float
    reward_to_mid_r: float
    reward_to_final_r: float


@dataclass
class TradeState:
    ticket: int
    symbol: str
    direction: Signal
    range_signature: str
    sweep_signature: str
    opened_bar: int
    entry_price: float
    stop_loss: float
    tp_partial: float
    tp_final: float
    bars_in_trade: int = 0
    partial_hit: bool = False
    moved_to_breakeven: bool = False
    max_favorable_pips: float = 0.0
    last_progress_bar: int = 0
    last_seen_bar: int = 0


class XAU60Adapter:
    """
    Framework adapter.

    Assumptions:
    - Strategy receives OHLCV DataFrame in `analyze`.
    - Optional spread can be inferred only when bid/ask columns exist in data.
    - Exit callbacks from framework are not guaranteed for SL/TP broker exits.
    """

    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg

    @staticmethod
    def _point_for_symbol(symbol: str) -> float:
        s = symbol.upper()
        if "XAU" in s or "GOLD" in s:
            return 0.01
        if s.endswith("JPY"):
            return 0.001
        return 0.0001

    def pips_to_price(self, symbol: str, pips: float) -> float:
        return float(pips) * self._point_for_symbol(symbol) * 10.0

    def price_to_pips(self, symbol: str, price_distance: float) -> float:
        pip_price = self.pips_to_price(symbol, 1.0)
        if pip_price <= 0:
            return 0.0
        return float(price_distance) / pip_price

    def extract_spread_points(self, symbol: str, data: pd.DataFrame) -> Optional[float]:
        if "bid" not in data.columns or "ask" not in data.columns:
            return None
        bid = float(data.iloc[-1]["bid"])
        ask = float(data.iloc[-1]["ask"])
        if ask < bid:
            return None
        point = self._point_for_symbol(symbol)
        if point <= 0:
            return None
        return (ask - bid) / point

    @staticmethod
    def overlap_ratio(window: pd.DataFrame) -> float:
        if len(window) < 2:
            return 0.0
        overlap_sum = 0.0
        union_sum = 0.0
        prev_high = float(window.iloc[0]["high"])
        prev_low = float(window.iloc[0]["low"])
        for i in range(1, len(window)):
            high = float(window.iloc[i]["high"])
            low = float(window.iloc[i]["low"])
            overlap = max(0.0, min(prev_high, high) - max(prev_low, low))
            union = max(prev_high, high) - min(prev_low, low)
            overlap_sum += overlap
            union_sum += max(union, 1e-9)
            prev_high = high
            prev_low = low
        return overlap_sum / max(union_sum, 1e-9)

    @staticmethod
    def expansion_ratio(window: pd.DataFrame, atr: float, expansion_mult: float) -> float:
        if len(window) == 0 or atr <= 0:
            return 1.0
        bar_ranges = (window["high"] - window["low"]).astype(float)
        expanded = (bar_ranges > (atr * expansion_mult)).sum()
        return float(expanded) / float(len(window))

    @staticmethod
    def rejection_wick_ratio(open_: float, high: float, low: float, close: float, side: str) -> float:
        body = max(abs(close - open_), 1e-9)
        upper = max(0.0, high - max(open_, close))
        lower = max(0.0, min(open_, close) - low)
        if side == "upper":
            return upper / body
        return lower / body

    @staticmethod
    def clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def log(self, symbol: str, event: str, payload: Dict[str, Any], level: str = "DEBUG") -> None:
        record = {"strategy": "Edge Rejection Micro Scalping", "symbol": symbol, "event": event, **payload}
        log_level = str(level or "DEBUG").upper()
        if log_level == "INFO":
            logger.info(record)
        elif log_level == "WARNING":
            logger.warning(record)
        elif log_level == "ERROR":
            logger.error(record)
        else:
            logger.debug(record)


class EdgeRejectionMicroScalpingStrategy(StrategyBase):
    name = "Edge Rejection Micro Scalping"
    version = "1.0.0"
    description = "Sideway range edge sweep-rejection micro scalping for XAUUSD"
    author = "AlgoAct"

    def __init__(self):
        super().__init__()
        self.cfg = StrategyConfig()
        self.adapter = XAU60Adapter(self.cfg)

        self._bar_index: Dict[str, int] = {}
        self._cooldown_until: Dict[str, int] = {}
        self._last_signal_day: Dict[str, date] = {}
        self._daily_signal_count: Dict[str, int] = {}
        self._sweep_last_bar: Dict[str, int] = {}
        self._trades_per_range: Dict[str, int] = {}
        self._active_trades: Dict[int, TradeState] = {}
        self._last_plan_by_symbol_side: Dict[Tuple[str, Signal], Tuple[PositionPlan, EntrySignal, int]] = {}
        self._pending_force_exit_cooldown: Dict[str, int] = {}

    def initialize(self, config: Dict[str, Any]) -> None:
        self.config = config
        params = config.get("parameters", {}) or {}
        risk = config.get("risk", {}) or {}
        session = config.get("session", {}) or {}
        filters = config.get("execution_filters", {}) or {}
        diag = config.get("diagnostics", {}) or {}

        # Identity
        self.cfg.symbols = list(config.get("symbols", self.cfg.symbols))
        self.cfg.timeframe = str(config.get("timeframe", self.cfg.timeframe))
        self.cfg.enabled = bool(config.get("enabled", self.cfg.enabled))
        self.cfg.magic_number = int(config.get("magic_number", self.cfg.magic_number))
        self.cfg.lot_size = float(risk.get("lot_size", env_config.trading.default_lot_size))

        # Parameters
        self.cfg.atr_period = int(params.get("atr_period", self.cfg.atr_period))
        self.cfg.atr_baseline_window = int(params.get("atr_baseline_window", self.cfg.atr_baseline_window))
        self.cfg.atr_compression_threshold = float(
            params.get("atr_compression_threshold", self.cfg.atr_compression_threshold)
        )
        self.cfg.adx_period = int(params.get("adx_period", self.cfg.adx_period))
        self.cfg.adx_max_sideway = float(params.get("adx_max_sideway", self.cfg.adx_max_sideway))
        self.cfg.slope_ema_period = int(params.get("slope_ema_period", self.cfg.slope_ema_period))
        self.cfg.slope_lookback = int(params.get("slope_lookback", self.cfg.slope_lookback))
        self.cfg.slope_max_atr_multiple = float(
            params.get("slope_max_atr_multiple", self.cfg.slope_max_atr_multiple)
        )
        self.cfg.overlap_lookback = int(params.get("overlap_lookback", self.cfg.overlap_lookback))
        self.cfg.overlap_min_ratio = float(params.get("overlap_min_ratio", self.cfg.overlap_min_ratio))
        self.cfg.expansion_bar_multiple = float(
            params.get("expansion_bar_multiple", self.cfg.expansion_bar_multiple)
        )
        self.cfg.expansion_ratio_max = float(params.get("expansion_ratio_max", self.cfg.expansion_ratio_max))
        self.cfg.regime_score_min = float(params.get("regime_score_min", self.cfg.regime_score_min))
        self.cfg.regime_filter_enabled = bool(params.get("regime_filter_enabled", self.cfg.regime_filter_enabled))

        self.cfg.range_lookback = int(params.get("range_lookback", self.cfg.range_lookback))
        self.cfg.min_range_width_pips = float(params.get("min_range_width_pips", self.cfg.min_range_width_pips))
        self.cfg.max_range_width_pips = float(params.get("max_range_width_pips", self.cfg.max_range_width_pips))
        self.cfg.touch_tolerance_pips = float(params.get("touch_tolerance_pips", self.cfg.touch_tolerance_pips))
        self.cfg.min_touches_per_side = int(params.get("min_touches_per_side", self.cfg.min_touches_per_side))
        self.cfg.max_mid_drift_ratio = float(params.get("max_mid_drift_ratio", self.cfg.max_mid_drift_ratio))
        self.cfg.range_filter_enabled = bool(params.get("range_filter_enabled", self.cfg.range_filter_enabled))

        self.cfg.edge_zone_percent = float(params.get("edge_zone_percent", self.cfg.edge_zone_percent))
        self.cfg.center_exclusion_percent = float(
            params.get("center_exclusion_percent", self.cfg.center_exclusion_percent)
        )
        self.cfg.min_distance_from_mid_pips = float(
            params.get("min_distance_from_mid_pips", self.cfg.min_distance_from_mid_pips)
        )

        self.cfg.sweep_threshold_pips = float(params.get("sweep_threshold_pips", self.cfg.sweep_threshold_pips))
        self.cfg.sweep_threshold_pips_buy = float(
            params.get("sweep_threshold_pips_buy", self.cfg.sweep_threshold_pips)
        )
        self.cfg.sweep_threshold_pips_sell = float(
            params.get("sweep_threshold_pips_sell", self.cfg.sweep_threshold_pips)
        )
        self.cfg.close_inside_buffer_pips = float(
            params.get("close_inside_buffer_pips", self.cfg.close_inside_buffer_pips)
        )
        self.cfg.close_inside_buffer_pips_buy = float(
            params.get("close_inside_buffer_pips_buy", self.cfg.close_inside_buffer_pips)
        )
        self.cfg.close_inside_buffer_pips_sell = float(
            params.get("close_inside_buffer_pips_sell", self.cfg.close_inside_buffer_pips)
        )
        self.cfg.rejection_wick_ratio_min = float(
            params.get("rejection_wick_ratio_min", self.cfg.rejection_wick_ratio_min)
        )
        self.cfg.rejection_wick_ratio_min_buy = float(
            params.get("rejection_wick_ratio_min_buy", self.cfg.rejection_wick_ratio_min)
        )
        self.cfg.rejection_wick_ratio_min_sell = float(
            params.get("rejection_wick_ratio_min_sell", self.cfg.rejection_wick_ratio_min)
        )
        self.cfg.require_close_back_inside = bool(
            params.get("require_close_back_inside", self.cfg.require_close_back_inside)
        )
        self.cfg.rejection_score_min = float(params.get("rejection_score_min", self.cfg.rejection_score_min))
        self.cfg.rejection_score_min_buy = float(
            params.get("rejection_score_min_buy", self.cfg.rejection_score_min)
        )
        self.cfg.rejection_score_min_sell = float(
            params.get("rejection_score_min_sell", self.cfg.rejection_score_min)
        )
        self.cfg.next_bar_failure_required = bool(
            params.get("next_bar_failure_required", self.cfg.next_bar_failure_required)
        )
        self.cfg.momentum_rsi_period = int(params.get("momentum_rsi_period", self.cfg.momentum_rsi_period))
        self.cfg.momentum_fail_buy_max_rsi = float(
            params.get("momentum_fail_buy_max_rsi", self.cfg.momentum_fail_buy_max_rsi)
        )
        self.cfg.momentum_fail_sell_min_rsi = float(
            params.get("momentum_fail_sell_min_rsi", self.cfg.momentum_fail_sell_min_rsi)
        )

        self.cfg.micro_structure_lookback = int(
            params.get("micro_structure_lookback", self.cfg.micro_structure_lookback)
        )
        self.cfg.micro_break_buffer_pips = float(params.get("micro_break_buffer_pips", self.cfg.micro_break_buffer_pips))
        self.cfg.allow_engulfing_confirmation = bool(
            params.get("allow_engulfing_confirmation", self.cfg.allow_engulfing_confirmation)
        )
        self.cfg.require_structure_break = bool(
            params.get("require_structure_break", self.cfg.require_structure_break)
        )

        # Risk / exits
        self.cfg.stop_buffer_atr_mult = float(risk.get("stop_buffer_atr_mult", self.cfg.stop_buffer_atr_mult))
        self.cfg.stop_buffer_min_pips = float(risk.get("stop_buffer_min_pips", self.cfg.stop_buffer_min_pips))
        self.cfg.min_stop_pips = float(risk.get("min_stop_pips", self.cfg.min_stop_pips))
        self.cfg.max_stop_pips = float(risk.get("max_stop_pips", self.cfg.max_stop_pips))
        self.cfg.midpoint_tp_enabled = bool(risk.get("midpoint_tp_enabled", self.cfg.midpoint_tp_enabled))
        self.cfg.midpoint_min_reward_r = float(risk.get("midpoint_min_reward_r", self.cfg.midpoint_min_reward_r))
        self.cfg.fixed_rr_target = float(risk.get("fixed_rr_target", self.cfg.fixed_rr_target))
        self.cfg.opposite_edge_front_run_pips = float(
            risk.get("opposite_edge_front_run_pips", self.cfg.opposite_edge_front_run_pips)
        )
        self.cfg.partial_tp_ratio = float(risk.get("partial_tp_ratio", self.cfg.partial_tp_ratio))
        self.cfg.move_to_breakeven_after_partial = bool(
            risk.get("move_to_breakeven_after_partial", self.cfg.move_to_breakeven_after_partial)
        )
        self.cfg.breakeven_buffer_pips = float(risk.get("breakeven_buffer_pips", self.cfg.breakeven_buffer_pips))
        self.cfg.trailing_stop_enabled = bool(risk.get("trailing_stop", self.cfg.trailing_stop_enabled))
        self.cfg.trailing_start_pips = float(risk.get("trailing_start_pips", self.cfg.trailing_start_pips))
        self.cfg.trailing_distance_pips = float(risk.get("trailing_pips", self.cfg.trailing_distance_pips))

        self.cfg.max_bars_in_trade = int(risk.get("max_bars_in_trade", self.cfg.max_bars_in_trade))
        self.cfg.no_progress_bar_count = int(risk.get("no_progress_bar_count", self.cfg.no_progress_bar_count))
        self.cfg.no_progress_min_mfe_pips = float(
            risk.get("no_progress_min_mfe_pips", self.cfg.no_progress_min_mfe_pips)
        )
        self.cfg.invalidate_on_regime_break = bool(
            risk.get("invalidate_on_regime_break", self.cfg.invalidate_on_regime_break)
        )
        self.cfg.flatten_on_regime_invalidation = bool(
            risk.get("flatten_on_regime_invalidation", self.cfg.flatten_on_regime_invalidation)
        )
        self.cfg.invalidate_atr_jump_multiple = float(
            risk.get("invalidate_atr_jump_multiple", self.cfg.invalidate_atr_jump_multiple)
        )
        self.cfg.invalidate_adx_threshold = float(
            risk.get("invalidate_adx_threshold", self.cfg.invalidate_adx_threshold)
        )

        self.cfg.one_active_position_only = bool(
            risk.get("one_active_position_only", self.cfg.one_active_position_only)
        )
        self.cfg.max_trades_per_range = int(risk.get("max_trades_per_range", self.cfg.max_trades_per_range))
        self.cfg.cooldown_bars_after_stop = int(
            risk.get("cooldown_bars_after_stop", self.cfg.cooldown_bars_after_stop)
        )
        self.cfg.cooldown_bars_after_force_exit = int(
            risk.get("cooldown_bars_after_force_exit", self.cfg.cooldown_bars_after_force_exit)
        )
        self.cfg.duplicate_sweep_block_bars = int(
            risk.get("duplicate_sweep_block_bars", self.cfg.duplicate_sweep_block_bars)
        )
        self.cfg.spread_threshold_points = float(
            risk.get("spread_threshold_points", self.cfg.spread_threshold_points)
        )
        self.cfg.max_daily_signals = int(risk.get("max_daily_signals", self.cfg.max_daily_signals))

        # Session / filters
        self.cfg.use_time_filter = bool(session.get("use_time_filter", self.cfg.use_time_filter))
        self.cfg.session_timezone = str(session.get("timezone", self.cfg.session_timezone))
        self.cfg.data_timezone = str(session.get("data_timezone", self.cfg.data_timezone))
        self.cfg.start_hour = int(session.get("start_hour", self.cfg.start_hour))
        self.cfg.end_hour = int(session.get("end_hour", self.cfg.end_hour))
        self.cfg.long_allowed_hours = self._parse_hour_set(session.get("long_allowed_hours", []))
        self.cfg.short_allowed_hours = self._parse_hour_set(session.get("short_allowed_hours", []))
        self.cfg.trade_friday = bool(session.get("trade_friday", self.cfg.trade_friday))

        if bool(filters.get("enabled", False)):
            self.cfg.allow_long = bool(filters.get("allow_long", self.cfg.allow_long))
            self.cfg.allow_short = bool(filters.get("allow_short", self.cfg.allow_short))
            self.cfg.blocked_hours = self._parse_blocked_hours(filters.get("blocked_hours", []))
            self.cfg.blocked_weekdays = self._parse_blocked_weekdays(filters.get("blocked_weekdays", []))
        else:
            self.cfg.allow_long = True
            self.cfg.allow_short = True
            self.cfg.blocked_hours = set()
            self.cfg.blocked_weekdays = set()

        self.cfg.log_diagnostics = bool(diag.get("enabled", self.cfg.log_diagnostics))
        self.cfg.diagnostic_level = str(diag.get("level", self.cfg.diagnostic_level))
        self.cfg.state_stale_prune_bars = int(diag.get("state_stale_prune_bars", self.cfg.state_stale_prune_bars))

        self.symbols = self.cfg.symbols
        self.timeframe = self.cfg.timeframe
        self.enabled = self.cfg.enabled
        self.adapter = XAU60Adapter(self.cfg)

        self._bar_index.clear()
        self._cooldown_until.clear()
        self._last_signal_day.clear()
        self._daily_signal_count.clear()
        self._sweep_last_bar.clear()
        self._trades_per_range.clear()
        self._active_trades.clear()
        self._last_plan_by_symbol_side.clear()
        self._pending_force_exit_cooldown.clear()

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        signal = self.on_bar(symbol, data)
        if signal is None:
            return None

        comment = f"ERMS_{signal.model[:4].upper()}_{signal.sweep_signature[:8]}"
        comment = comment[:31]
        key = (symbol, signal.signal)
        plan_tuple = self._last_plan_by_symbol_side.get(key)
        if plan_tuple is None:
            return None
        plan = plan_tuple[0]

        return TradeSignal(
            signal=signal.signal,
            symbol=symbol,
            entry_price=plan.entry_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.effective_take_profit,
            lot_size=self.cfg.lot_size,
            comment=comment,
            magic_number=self.cfg.magic_number,
        )

    def should_close(self, position: Position, data: pd.DataFrame) -> bool:
        return self.manage_open_position(position, data)

    def get_trailing_stop(self, position: Position, data: pd.DataFrame) -> Optional[float]:
        state = self._active_trades.get(position.ticket)
        if state is None:
            return None

        symbol = position.symbol
        current_price = float(data.iloc[-1]["close"])
        pip_price = self.adapter.pips_to_price(symbol, 1.0)
        if pip_price <= 0:
            return None

        if position.type == Signal.BUY:
            profit_pips = (current_price - position.open_price) / pip_price
        else:
            profit_pips = (position.open_price - current_price) / pip_price

        # Breakeven promotion after partial milestone.
        if (
            self.cfg.move_to_breakeven_after_partial
            and state.partial_hit
            and not state.moved_to_breakeven
        ):
            be_buffer = self.adapter.pips_to_price(symbol, self.cfg.breakeven_buffer_pips)
            if position.type == Signal.BUY:
                be_sl = position.open_price + be_buffer
                if be_sl > position.stop_loss:
                    state.moved_to_breakeven = True
                    return be_sl
            else:
                be_sl = position.open_price - be_buffer
                if position.stop_loss == 0 or be_sl < position.stop_loss:
                    state.moved_to_breakeven = True
                    return be_sl

        if not self.cfg.trailing_stop_enabled:
            return None
        if profit_pips < self.cfg.trailing_start_pips:
            return None
        trail_distance = self.adapter.pips_to_price(symbol, self.cfg.trailing_distance_pips)
        return self._apply_trailing_distance(position, current_price, trail_distance)

    def on_trade_opened(self, position: Position) -> None:
        symbol = position.symbol
        current_bar = self._bar_index.get(symbol, 0)
        key = (symbol, position.type)
        plan_tuple = self._last_plan_by_symbol_side.get(key)

        if plan_tuple is not None:
            plan, entry_signal, planned_bar = plan_tuple
            if abs(plan.entry_price - position.open_price) <= self.adapter.pips_to_price(symbol, 20):
                tp_partial = plan.tp_partial
                tp_final = plan.tp_final
                range_signature = entry_signal.range_signature
                sweep_signature = entry_signal.sweep_signature
            else:
                tp_partial = self._derive_partial_target(position)
                tp_final = position.take_profit
                range_signature = "runtime_unknown"
                sweep_signature = "runtime_unknown"
        else:
            tp_partial = self._derive_partial_target(position)
            tp_final = position.take_profit
            range_signature = "runtime_unknown"
            sweep_signature = "runtime_unknown"

        self._active_trades[position.ticket] = TradeState(
            ticket=position.ticket,
            symbol=symbol,
            direction=position.type,
            range_signature=range_signature,
            sweep_signature=sweep_signature,
            opened_bar=current_bar,
            entry_price=position.open_price,
            stop_loss=position.stop_loss,
            tp_partial=tp_partial,
            tp_final=tp_final,
            bars_in_trade=0,
            partial_hit=False,
            moved_to_breakeven=False,
            max_favorable_pips=0.0,
            last_progress_bar=current_bar,
            last_seen_bar=current_bar,
        )

        if sweep_signature and sweep_signature != "runtime_unknown":
            self._sweep_last_bar[sweep_signature] = current_bar
        if range_signature and range_signature != "runtime_unknown":
            self._trades_per_range[range_signature] = self._trades_per_range.get(range_signature, 0) + 1

    def on_trade_closed(self, position: Position, profit: float) -> None:
        # Framework may not always invoke this callback; keep defensive logic.
        state = self._active_trades.pop(position.ticket, None)
        symbol = position.symbol
        current_bar = self._bar_index.get(symbol, 0)
        if state and profit < 0:
            self._cooldown_until[symbol] = max(
                self._cooldown_until.get(symbol, current_bar),
                current_bar + self.cfg.cooldown_bars_after_stop,
            )

    # ---------------------------------------------------------------------
    # Core orchestration
    # ---------------------------------------------------------------------
    def on_bar(self, symbol: str, data: pd.DataFrame) -> Optional[EntrySignal]:
        required = max(
            self.cfg.range_lookback + 2,
            self.cfg.atr_baseline_window + 2,
            self.cfg.slope_lookback + 3,
            self.cfg.micro_structure_lookback + 3,
            self.cfg.overlap_lookback + 2,
            self.cfg.momentum_rsi_period + 3,
        )
        if len(data) < required:
            return None

        bar = self._bar_index.get(symbol, 0) + 1
        self._bar_index[symbol] = bar
        self._prune_stale_trade_states(symbol, bar)

        session_ok, session_time = self._is_session_allowed(data)
        if not session_ok:
            self._diag(symbol, "session_block", {"bar": bar, "session_time": str(session_time)})
            return None

        if not self._passes_daily_limit(symbol, session_time.date()):
            self._diag(symbol, "daily_limit_block", {"bar": bar, "date": str(session_time.date())})
            return None

        if self._is_in_cooldown(symbol, bar):
            self._diag(symbol, "cooldown_block", {"bar": bar, "cooldown_until": self._cooldown_until.get(symbol, 0)})
            return None

        regime = self.detect_sideway_regime(symbol, data)
        range_state = self.detect_range(symbol, data)
        close = float(data.iloc[-1]["close"])
        edge = self.classify_edge_context(symbol, close, range_state)

        if self.should_invalidate_regime(symbol, data, regime, range_state):
            self._diag(symbol, "regime_invalidation", {"bar": bar, "reason": "regime_or_range_invalid"})
            return None

        if self.cfg.one_active_position_only and self._has_active_trade(symbol, bar):
            self._diag(symbol, "active_trade_block", {"bar": bar})
            return None

        if edge.zone in {"center", "none"}:
            self._diag(symbol, "edge_block", {"bar": bar, "zone": edge.zone, "distance_pips": edge.distance_to_mid_pips})
            return None

        direction_hint = self._edge_zone_to_direction(edge.zone)
        if direction_hint != Signal.HOLD and not self._is_direction_session_allowed(direction_hint, session_time):
            self._diag(
                symbol,
                "direction_session_block",
                {
                    "bar": bar,
                    "direction": direction_hint.name,
                    "hour": session_time.hour,
                },
            )
            return None

        if edge.distance_to_mid_pips < self.cfg.min_distance_from_mid_pips:
            self._diag(
                symbol,
                "mid_distance_block",
                {
                    "bar": bar,
                    "distance_pips": round(edge.distance_to_mid_pips, 2),
                    "required": self.cfg.min_distance_from_mid_pips,
                },
            )
            return None

        spread_points = self.adapter.extract_spread_points(symbol, data)
        if spread_points is not None and spread_points > self.cfg.spread_threshold_points:
            self._diag(
                symbol,
                "spread_block",
                {"bar": bar, "spread_points": round(spread_points, 2), "threshold": self.cfg.spread_threshold_points},
            )
            return None

        if self._trades_per_range.get(range_state.signature, 0) >= self.cfg.max_trades_per_range:
            self._diag(
                symbol,
                "range_trade_limit_block",
                {"bar": bar, "range_signature": range_state.signature, "count": self._trades_per_range.get(range_state.signature, 0)},
            )
            return None

        sweep = self.detect_liquidity_sweep(symbol, data, range_state, edge, bar)
        if not sweep.detected:
            return None

        if self._is_duplicate_sweep(sweep.signature, bar):
            self._diag(symbol, "duplicate_sweep_block", {"bar": bar, "sweep_signature": sweep.signature})
            return None

        rejection_ok, rejection_reason = self.confirm_rejection(symbol, data, sweep)
        if not rejection_ok:
            self._diag(symbol, "rejection_block", {"bar": bar, "reason": rejection_reason})
            return None

        micro_ok, micro_reason = self.detect_micro_structure_shift(symbol, data, sweep.direction)
        if not micro_ok:
            self._diag(symbol, "micro_structure_block", {"bar": bar, "reason": micro_reason})
            return None

        entry = self.generate_entry_signal(symbol, data, regime, range_state, edge, sweep, bar)
        if entry is None:
            return None

        self._last_signal_day[symbol] = session_time.date()
        self._daily_signal_count[symbol] = self._daily_signal_count.get(symbol, 0) + 1
        self._diag(
            symbol,
            "entry_approved",
            {
                "bar": bar,
                "direction": entry.signal.name,
                "model": entry.model,
                "sweep_signature": entry.sweep_signature,
                "range_signature": entry.range_signature,
            },
            level="INFO",
        )
        return entry

    # ---------------------------------------------------------------------
    # Core detection logic
    # ---------------------------------------------------------------------
    def detect_sideway_regime(self, symbol: str, data: pd.DataFrame) -> RegimeState:
        atr_period = max(2, min(self.cfg.atr_period, len(data) - 2))
        baseline_window = max(5, min(self.cfg.atr_baseline_window, len(data) - 2))
        adx_period = max(2, min(self.cfg.adx_period, len(data) - 2))
        slope_ema = max(2, min(self.cfg.slope_ema_period, len(data) - 2))
        slope_lookback = max(1, min(self.cfg.slope_lookback, len(data) - 2))
        overlap_lookback = max(3, min(self.cfg.overlap_lookback, len(data)))

        atr_series = calculate_atr(data, atr_period)
        adx_series = calculate_adx(data, adx_period)
        ema_series = calculate_ema(data, slope_ema)

        atr_current = float(atr_series.iloc[-1])
        atr_baseline = float(atr_series.tail(baseline_window).mean())
        adx_now = float(adx_series.iloc[-1])
        ema_now = float(ema_series.iloc[-1])
        ema_prev = float(ema_series.iloc[-1 - slope_lookback])
        slope = ema_now - ema_prev

        overlap = self.adapter.overlap_ratio(data.iloc[-overlap_lookback:])
        expansion = self.adapter.expansion_ratio(data.iloc[-overlap_lookback:], atr_current, self.cfg.expansion_bar_multiple)

        atr_ratio = atr_current / max(atr_baseline, 1e-9)
        slope_norm = abs(slope) / max(atr_current, 1e-9)

        score = 0.0
        score += 25.0 if atr_ratio <= self.cfg.atr_compression_threshold else 0.0
        score += 25.0 if adx_now <= self.cfg.adx_max_sideway else 0.0
        score += 20.0 if slope_norm <= self.cfg.slope_max_atr_multiple else 0.0
        score += 20.0 if overlap >= self.cfg.overlap_min_ratio else 0.0
        score += 10.0 if expansion <= self.cfg.expansion_ratio_max else 0.0

        if atr_ratio <= self.cfg.atr_compression_threshold * 0.85:
            vol_state = "compressed"
        elif atr_ratio <= self.cfg.atr_compression_threshold * 1.15:
            vol_state = "balanced"
        else:
            vol_state = "expanded"

        valid = score >= self.cfg.regime_score_min
        if not self.cfg.regime_filter_enabled:
            valid = True

        reason = (
            f"score={score:.1f} atr_ratio={atr_ratio:.3f} "
            f"adx={adx_now:.2f} slope_norm={slope_norm:.3f} "
            f"overlap={overlap:.3f} expansion={expansion:.3f}"
        )
        state = RegimeState(
            valid=valid,
            atr_current=atr_current,
            atr_baseline=atr_baseline,
            slope=slope,
            volatility_state=vol_state,
            reason=reason,
            adx=adx_now,
            overlap_ratio=overlap,
            expansion_ratio=expansion,
            score=score,
        )
        self._diag(
            symbol,
            "regime",
            {
                "valid": state.valid,
                "score": round(state.score, 2),
                "atr_current": round(state.atr_current, 4),
                "atr_baseline": round(state.atr_baseline, 4),
                "adx": round(state.adx, 3),
                "volatility_state": state.volatility_state,
                "reason": state.reason,
            },
        )
        return state

    def detect_range(self, symbol: str, data: pd.DataFrame) -> RangeState:
        # Build the structural range from completed bars only.
        # If we include the current bar, sweep depth cannot exceed the range edge.
        usable = max(0, len(data) - 1)
        lookback = max(10, min(self.cfg.range_lookback, usable))
        if usable <= 0:
            state = RangeState(
                valid=False,
                range_high=0.0,
                range_low=0.0,
                range_mid=0.0,
                range_width=0.0,
                range_width_pips=0.0,
                upper_touches=0,
                lower_touches=0,
                drift_ratio=1.0,
                reason="insufficient_completed_bars",
                signature="na",
            )
            return state

        window = data.iloc[-(lookback + 1):-1]
        high = float(window["high"].max())
        low = float(window["low"].min())
        mid = (high + low) / 2.0
        width = high - low
        width_pips = self.adapter.price_to_pips(symbol, width)

        min_width_price = self.adapter.pips_to_price(symbol, self.cfg.min_range_width_pips)
        max_width_price = self.adapter.pips_to_price(symbol, self.cfg.max_range_width_pips)
        tolerance = self.adapter.pips_to_price(symbol, self.cfg.touch_tolerance_pips)

        upper_touches = int((window["high"] >= (high - tolerance)).sum())
        lower_touches = int((window["low"] <= (low + tolerance)).sum())

        half = max(2, lookback // 2)
        w1 = window.iloc[:half]
        w2 = window.iloc[-half:]
        m1 = (float(w1["high"].max()) + float(w1["low"].min())) / 2.0
        m2 = (float(w2["high"].max()) + float(w2["low"].min())) / 2.0
        drift_ratio = abs(m2 - m1) / max(width, 1e-9)

        valid = (
            width > 0
            and width >= min_width_price
            and width <= max_width_price
            and upper_touches >= self.cfg.min_touches_per_side
            and lower_touches >= self.cfg.min_touches_per_side
            and drift_ratio <= self.cfg.max_mid_drift_ratio
        )
        if not self.cfg.range_filter_enabled:
            valid = True

        reason = (
            f"width_pips={width_pips:.1f} touches=({upper_touches},{lower_touches}) "
            f"drift_ratio={drift_ratio:.3f}"
        )
        signature = f"{round(high,2)}|{round(low,2)}|{lookback}"
        state = RangeState(
            valid=valid,
            range_high=high,
            range_low=low,
            range_mid=mid,
            range_width=width,
            range_width_pips=width_pips,
            upper_touches=upper_touches,
            lower_touches=lower_touches,
            drift_ratio=drift_ratio,
            reason=reason,
            signature=signature,
        )
        self._diag(
            symbol,
            "range",
            {
                "valid": state.valid,
                "high": round(state.range_high, 3),
                "low": round(state.range_low, 3),
                "mid": round(state.range_mid, 3),
                "width_pips": round(state.range_width_pips, 2),
                "reason": state.reason,
            },
        )
        return state

    def classify_edge_context(self, symbol: str, close: float, range_state: RangeState) -> EdgeContext:
        width = max(range_state.range_width, 1e-9)
        edge_zone = width * self.adapter.clamp(self.cfg.edge_zone_percent, 0.05, 0.45)
        center_band = width * self.adapter.clamp(self.cfg.center_exclusion_percent, 0.05, 0.90)
        center_half = center_band / 2.0

        dist_mid = abs(close - range_state.range_mid)
        in_center = dist_mid <= center_half

        if close >= range_state.range_high - edge_zone:
            zone = "upper"
        elif close <= range_state.range_low + edge_zone:
            zone = "lower"
        elif in_center:
            zone = "center"
        else:
            zone = "none"

        ctx = EdgeContext(
            zone=zone,
            in_center_exclusion=in_center,
            distance_to_mid=dist_mid,
            distance_to_mid_pips=self.adapter.price_to_pips(symbol, dist_mid),
        )
        self._diag(
            symbol,
            "edge_context",
            {
                "zone": ctx.zone,
                "in_center": ctx.in_center_exclusion,
                "dist_mid_pips": round(ctx.distance_to_mid_pips, 2),
            },
        )
        return ctx

    def detect_liquidity_sweep(
        self,
        symbol: str,
        data: pd.DataFrame,
        range_state: RangeState,
        edge_ctx: EdgeContext,
        bar_index: int,
    ) -> SweepEvent:
        row = data.iloc[-1]
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        detected = False
        direction = Signal.HOLD
        depth = 0.0
        wick_ratio = 0.0
        close_back_inside = False
        extreme = close
        reason = "no_sweep"
        required_wick_ratio = self.cfg.rejection_wick_ratio_min

        if edge_ctx.zone == "upper":
            direction = Signal.SELL
            sweep_threshold = self.adapter.pips_to_price(
                symbol, self._sweep_threshold_pips_for_direction(direction)
            )
            inside_buffer = self.adapter.pips_to_price(
                symbol, self._close_inside_buffer_pips_for_direction(direction)
            )
            required_wick_ratio = self._rejection_wick_ratio_min_for_direction(direction)
            depth = max(0.0, high - range_state.range_high)
            wick_ratio = self.adapter.rejection_wick_ratio(open_, high, low, close, "upper")
            close_back_inside = close <= (range_state.range_high - inside_buffer)
            detected = depth >= sweep_threshold and (
                close_back_inside
                or wick_ratio >= required_wick_ratio
            )
            extreme = high
            reason = "upper_sweep" if detected else "upper_edge_no_sweep"
        elif edge_ctx.zone == "lower":
            direction = Signal.BUY
            sweep_threshold = self.adapter.pips_to_price(
                symbol, self._sweep_threshold_pips_for_direction(direction)
            )
            inside_buffer = self.adapter.pips_to_price(
                symbol, self._close_inside_buffer_pips_for_direction(direction)
            )
            required_wick_ratio = self._rejection_wick_ratio_min_for_direction(direction)
            depth = max(0.0, range_state.range_low - low)
            wick_ratio = self.adapter.rejection_wick_ratio(open_, high, low, close, "lower")
            close_back_inside = close >= (range_state.range_low + inside_buffer)
            detected = depth >= sweep_threshold and (
                close_back_inside
                or wick_ratio >= required_wick_ratio
            )
            extreme = low
            reason = "lower_sweep" if detected else "lower_edge_no_sweep"

        if not detected:
            direction = Signal.HOLD

        depth_pips = self.adapter.price_to_pips(symbol, depth)
        rejection_score = 0.0
        rejection_score += 1.0 if close_back_inside else 0.0
        rejection_score += min(2.0, wick_ratio / max(required_wick_ratio, 1e-9))

        signature = f"{symbol}:{bar_index}:{direction.name}:{round(extreme,2)}"
        event = SweepEvent(
            direction=direction,
            detected=detected,
            sweep_depth=depth,
            sweep_depth_pips=depth_pips,
            wick_ratio=wick_ratio,
            close_back_inside=close_back_inside,
            rejection_score=rejection_score,
            extreme_price=extreme,
            signature=signature,
            reason=reason,
        )
        self._diag(
            symbol,
            "sweep",
            {
                "detected": event.detected,
                "direction": event.direction.name,
                "depth_pips": round(event.sweep_depth_pips, 2),
                "wick_ratio": round(event.wick_ratio, 3),
                "close_back_inside": event.close_back_inside,
                "rejection_score": round(event.rejection_score, 3),
                "wick_required": round(required_wick_ratio, 3),
                "signature": event.signature,
                "reason": event.reason,
            },
        )
        return event

    def confirm_rejection(self, symbol: str, data: pd.DataFrame, sweep: SweepEvent) -> Tuple[bool, str]:
        if not sweep.detected:
            return False, "sweep_not_detected"

        wick_min = self._rejection_wick_ratio_min_for_direction(sweep.direction)
        score_min = self._rejection_score_min_for_direction(sweep.direction)

        if sweep.wick_ratio < wick_min:
            return False, f"wick_ratio_too_low_{sweep.wick_ratio:.2f}"

        if self.cfg.require_close_back_inside and not sweep.close_back_inside:
            return False, "close_not_back_inside"

        if sweep.rejection_score < score_min:
            return False, f"rejection_score_too_low_{sweep.rejection_score:.2f}"

        rsi_period = max(2, min(self.cfg.momentum_rsi_period, len(data) - 2))
        rsi_series = calculate_rsi(data, rsi_period)
        rsi_now = float(rsi_series.iloc[-1])
        if sweep.direction == Signal.BUY and rsi_now > self.cfg.momentum_fail_buy_max_rsi:
            return False, f"buy_rejection_momentum_not_failed_rsi_{rsi_now:.2f}"
        if sweep.direction == Signal.SELL and rsi_now < self.cfg.momentum_fail_sell_min_rsi:
            return False, f"sell_rejection_momentum_not_failed_rsi_{rsi_now:.2f}"

        # Optional confirmation on next-bar failure is disabled by default in this framework
        # because we typically evaluate one bar at a time for immediate execution.
        return True, "ok"

    def detect_micro_structure_shift(self, symbol: str, data: pd.DataFrame, direction: Signal) -> Tuple[bool, str]:
        lb = max(3, min(self.cfg.micro_structure_lookback, len(data) - 2))
        recent = data.iloc[-(lb + 1):]
        row = recent.iloc[-1]
        close = float(row["close"])
        open_ = float(row["open"])

        buffer_price = self.adapter.pips_to_price(symbol, self.cfg.micro_break_buffer_pips)
        prev_high = float(recent.iloc[:-1]["high"].max())
        prev_low = float(recent.iloc[:-1]["low"].min())

        structure_ok = True
        if self.cfg.require_structure_break:
            if direction == Signal.BUY:
                structure_ok = close >= prev_high + buffer_price
            else:
                structure_ok = close <= prev_low - buffer_price

        if not structure_ok:
            return False, "no_structure_break"

        if not self.cfg.allow_engulfing_confirmation:
            return True, "structure_only"

        prev = recent.iloc[-2]
        prev_open = float(prev["open"])
        prev_close = float(prev["close"])

        bullish_engulf = close > open_ and prev_close < prev_open and close >= prev_open and open_ <= prev_close
        bearish_engulf = close < open_ and prev_close > prev_open and close <= prev_open and open_ >= prev_close

        if direction == Signal.BUY and not bullish_engulf and close <= open_:
            return False, "no_bullish_reversal_candle"
        if direction == Signal.SELL and not bearish_engulf and close >= open_:
            return False, "no_bearish_reversal_candle"
        return True, "ok"

    def generate_entry_signal(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: RegimeState,
        range_state: RangeState,
        edge_ctx: EdgeContext,
        sweep: SweepEvent,
        bar_index: int,
    ) -> Optional[EntrySignal]:
        if self.cfg.regime_filter_enabled and not regime.valid:
            return None
        if self.cfg.range_filter_enabled and not range_state.valid:
            return None
        if sweep.direction == Signal.HOLD:
            return None
        if sweep.direction == Signal.BUY and not self.cfg.allow_long:
            return None
        if sweep.direction == Signal.SELL and not self.cfg.allow_short:
            return None

        if sweep.direction == Signal.BUY and edge_ctx.zone != "lower":
            return None
        if sweep.direction == Signal.SELL and edge_ctx.zone != "upper":
            return None

        entry_price = float(data.iloc[-1]["close"])
        plan = self._build_position_plan(symbol, data, sweep, range_state, entry_price)
        if plan is None:
            return None

        if plan.reward_to_mid_r < self.cfg.midpoint_min_reward_r:
            self._diag(
                symbol,
                "reward_block",
                {
                    "reward_to_mid_r": round(plan.reward_to_mid_r, 3),
                    "required": self.cfg.midpoint_min_reward_r,
                },
            )
            return None

        model = "edge_rejection_micro"
        signal = EntrySignal(
            signal=sweep.direction,
            model=model,
            reason="all_conditions_passed",
            sweep_signature=sweep.signature,
            range_signature=range_state.signature,
        )

        key = (symbol, signal.signal)
        self._last_plan_by_symbol_side[key] = (plan, signal, bar_index)
        return signal

    def calculate_stop_loss(
        self,
        symbol: str,
        direction: Signal,
        entry_price: float,
        sweep_extreme: float,
        atr_value: float,
    ) -> Tuple[Optional[float], float, str]:
        min_stop_price = self.adapter.pips_to_price(symbol, self.cfg.min_stop_pips)
        max_stop_price = self.adapter.pips_to_price(symbol, self.cfg.max_stop_pips)
        atr_buffer = max(
            self.adapter.pips_to_price(symbol, self.cfg.stop_buffer_min_pips),
            atr_value * self.cfg.stop_buffer_atr_mult,
        )

        if direction == Signal.BUY:
            stop = sweep_extreme - atr_buffer
            stop_distance = entry_price - stop
        else:
            stop = sweep_extreme + atr_buffer
            stop_distance = stop - entry_price

        if stop_distance <= 0:
            return None, 0.0, "non_positive_stop_distance"
        if stop_distance < min_stop_price:
            return None, self.adapter.price_to_pips(symbol, stop_distance), "stop_too_tight"
        if stop_distance > max_stop_price:
            return None, self.adapter.price_to_pips(symbol, stop_distance), "stop_too_wide"
        return stop, self.adapter.price_to_pips(symbol, stop_distance), "ok"

    def calculate_take_profit(
        self,
        symbol: str,
        direction: Signal,
        entry_price: float,
        stop_loss: float,
        range_state: RangeState,
    ) -> PositionPlan:
        risk = abs(entry_price - stop_loss)
        rr_target = risk * self.cfg.fixed_rr_target
        front_run = self.adapter.pips_to_price(symbol, self.cfg.opposite_edge_front_run_pips)

        midpoint = range_state.range_mid
        if direction == Signal.BUY:
            opposite = range_state.range_high - front_run
            rr_price = entry_price + rr_target
            conservative_final = min(rr_price, opposite)
        else:
            opposite = range_state.range_low + front_run
            rr_price = entry_price - rr_target
            conservative_final = max(rr_price, opposite)

        if self.cfg.midpoint_tp_enabled:
            tp_partial = midpoint
        else:
            tp_partial = conservative_final

        if direction == Signal.BUY:
            if tp_partial <= entry_price:
                tp_partial = entry_price + (risk * max(0.2, self.cfg.partial_tp_ratio))
            if conservative_final <= entry_price:
                conservative_final = entry_price + risk
            tp_final = max(conservative_final, tp_partial)
            effective_tp = tp_partial if self.cfg.midpoint_tp_enabled else tp_final
        else:
            if tp_partial >= entry_price:
                tp_partial = entry_price - (risk * max(0.2, self.cfg.partial_tp_ratio))
            if conservative_final >= entry_price:
                conservative_final = entry_price - risk
            tp_final = min(conservative_final, tp_partial)
            effective_tp = tp_partial if self.cfg.midpoint_tp_enabled else tp_final

        reward_mid = abs(tp_partial - entry_price)
        reward_final = abs(tp_final - entry_price)
        reward_mid_r = reward_mid / max(risk, 1e-9)
        reward_final_r = reward_final / max(risk, 1e-9)

        return PositionPlan(
            entry_price=entry_price,
            stop_loss=stop_loss,
            tp_partial=tp_partial,
            tp_final=tp_final,
            effective_take_profit=effective_tp,
            stop_distance_pips=self.adapter.price_to_pips(symbol, risk),
            reward_to_mid_r=reward_mid_r,
            reward_to_final_r=reward_final_r,
        )

    def should_force_exit(self, symbol: str, current_bar: int, state: TradeState) -> Tuple[bool, str]:
        if state.bars_in_trade >= self.cfg.max_bars_in_trade:
            return True, "time_stop_max_bars"

        bars_since_progress = current_bar - state.last_progress_bar
        if (
            state.bars_in_trade >= self.cfg.no_progress_bar_count
            and bars_since_progress >= self.cfg.no_progress_bar_count
            and state.max_favorable_pips < self.cfg.no_progress_min_mfe_pips
        ):
            return True, "time_stop_no_progress"
        return False, "hold"

    def should_invalidate_regime(
        self,
        symbol: str,
        data: pd.DataFrame,
        regime: RegimeState,
        range_state: RangeState,
    ) -> bool:
        if not self.cfg.invalidate_on_regime_break:
            return False
        if not regime.valid or not range_state.valid:
            return True

        if regime.adx >= self.cfg.invalidate_adx_threshold:
            return True

        atr_jump = regime.atr_current / max(regime.atr_baseline, 1e-9)
        if atr_jump >= self.cfg.invalidate_atr_jump_multiple:
            return True
        return False

    # ---------------------------------------------------------------------
    # Position management
    # ---------------------------------------------------------------------
    def manage_open_position(self, position: Position, data: pd.DataFrame) -> bool:
        symbol = position.symbol
        bar = self._bar_index.get(symbol, 0)
        state = self._active_trades.get(position.ticket)
        if state is None:
            state = TradeState(
                ticket=position.ticket,
                symbol=symbol,
                direction=position.type,
                range_signature="runtime_unknown",
                sweep_signature="runtime_unknown",
                opened_bar=bar,
                entry_price=position.open_price,
                stop_loss=position.stop_loss,
                tp_partial=self._derive_partial_target(position),
                tp_final=position.take_profit,
                bars_in_trade=0,
                partial_hit=False,
                moved_to_breakeven=False,
                max_favorable_pips=0.0,
                last_progress_bar=bar,
                last_seen_bar=bar,
            )
            self._active_trades[position.ticket] = state

        state.last_seen_bar = bar
        state.bars_in_trade += 1

        current_price = float(data.iloc[-1]["close"])
        pip_price = self.adapter.pips_to_price(symbol, 1.0)
        if pip_price > 0:
            if position.type == Signal.BUY:
                favorable = max(0.0, current_price - position.open_price)
                partial_hit_now = current_price >= state.tp_partial
            else:
                favorable = max(0.0, position.open_price - current_price)
                partial_hit_now = current_price <= state.tp_partial
            favorable_pips = favorable / pip_price
            if favorable_pips > state.max_favorable_pips:
                state.max_favorable_pips = favorable_pips
                state.last_progress_bar = bar
            if partial_hit_now:
                state.partial_hit = True

        force_exit, reason = self.should_force_exit(symbol, bar, state)
        if force_exit:
            self._cooldown_until[symbol] = max(
                self._cooldown_until.get(symbol, bar),
                bar + self.cfg.cooldown_bars_after_force_exit,
            )
            self._diag(symbol, "force_exit", {"ticket": position.ticket, "reason": reason}, level="INFO")
            self._active_trades.pop(position.ticket, None)
            return True

        if self.cfg.flatten_on_regime_invalidation:
            regime = self.detect_sideway_regime(symbol, data)
            range_state = self.detect_range(symbol, data)
            if self.should_invalidate_regime(symbol, data, regime, range_state):
                self._diag(symbol, "regime_exit", {"ticket": position.ticket}, level="INFO")
                self._active_trades.pop(position.ticket, None)
                return True
        return False

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------
    def _build_position_plan(
        self,
        symbol: str,
        data: pd.DataFrame,
        sweep: SweepEvent,
        range_state: RangeState,
        entry_price: float,
    ) -> Optional[PositionPlan]:
        atr_period = max(2, min(self.cfg.atr_period, len(data) - 2))
        atr_series = calculate_atr(data, atr_period)
        atr_now = float(atr_series.iloc[-1])

        stop_loss, stop_distance_pips, stop_reason = self.calculate_stop_loss(
            symbol=symbol,
            direction=sweep.direction,
            entry_price=entry_price,
            sweep_extreme=sweep.extreme_price,
            atr_value=atr_now,
        )
        if stop_loss is None:
            self._diag(
                symbol,
                "stop_block",
                {"reason": stop_reason, "stop_distance_pips": round(stop_distance_pips, 2)},
            )
            return None

        plan = self.calculate_take_profit(
            symbol=symbol,
            direction=sweep.direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            range_state=range_state,
        )
        self._diag(
            symbol,
            "position_plan",
            {
                "entry": round(plan.entry_price, 3),
                "stop": round(plan.stop_loss, 3),
                "tp_partial": round(plan.tp_partial, 3),
                "tp_final": round(plan.tp_final, 3),
                "effective_tp": round(plan.effective_take_profit, 3),
                "stop_pips": round(plan.stop_distance_pips, 2),
                "reward_mid_r": round(plan.reward_to_mid_r, 3),
                "reward_final_r": round(plan.reward_to_final_r, 3),
            },
        )
        return plan

    def _derive_partial_target(self, position: Position) -> float:
        if position.type == Signal.BUY:
            return position.open_price + ((position.take_profit - position.open_price) * self.cfg.partial_tp_ratio)
        return position.open_price - ((position.open_price - position.take_profit) * self.cfg.partial_tp_ratio)

    def _diag(self, symbol: str, event: str, payload: Dict[str, Any], level: str = "DEBUG") -> None:
        if not self.cfg.log_diagnostics:
            return
        self.adapter.log(symbol=symbol, event=event, payload=payload, level=level or self.cfg.diagnostic_level)

    def _is_duplicate_sweep(self, signature: str, bar_index: int) -> bool:
        last = self._sweep_last_bar.get(signature)
        if last is None:
            return False
        return (bar_index - last) <= self.cfg.duplicate_sweep_block_bars

    def _passes_daily_limit(self, symbol: str, trading_day: date) -> bool:
        last_day = self._last_signal_day.get(symbol)
        if last_day != trading_day:
            self._last_signal_day[symbol] = trading_day
            self._daily_signal_count[symbol] = 0
        return self._daily_signal_count.get(symbol, 0) < self.cfg.max_daily_signals

    def _is_in_cooldown(self, symbol: str, bar_index: int) -> bool:
        return bar_index < self._cooldown_until.get(symbol, -1)

    def _has_active_trade(self, symbol: str, bar_index: int) -> bool:
        for st in self._active_trades.values():
            if st.symbol != symbol:
                continue
            if (bar_index - st.last_seen_bar) <= self.cfg.state_stale_prune_bars:
                return True
        return False

    def _prune_stale_trade_states(self, symbol: str, bar_index: int) -> None:
        stale: List[int] = []
        for ticket, state in self._active_trades.items():
            if state.symbol != symbol:
                continue
            if (bar_index - state.last_seen_bar) > self.cfg.state_stale_prune_bars:
                stale.append(ticket)
        for ticket in stale:
            self._active_trades.pop(ticket, None)

    def _is_session_allowed(self, data: pd.DataFrame) -> Tuple[bool, pd.Timestamp]:
        current = self._to_session_time(data.iloc[-1]["time"])
        if not self.cfg.use_time_filter:
            return True, current
        weekday = current.weekday()
        if not self.cfg.trade_friday and weekday == 4:
            return False, current

        hour = current.hour
        if hour in self.cfg.blocked_hours:
            return False, current
        if weekday in self.cfg.blocked_weekdays:
            return False, current

        if self.cfg.start_hour < self.cfg.end_hour:
            return self.cfg.start_hour <= hour < self.cfg.end_hour, current
        return hour >= self.cfg.start_hour or hour < self.cfg.end_hour, current

    def _is_direction_session_allowed(self, direction: Signal, current: pd.Timestamp) -> bool:
        hour = current.hour
        if direction == Signal.BUY and self.cfg.long_allowed_hours:
            return hour in self.cfg.long_allowed_hours
        if direction == Signal.SELL and self.cfg.short_allowed_hours:
            return hour in self.cfg.short_allowed_hours
        return True

    @staticmethod
    def _edge_zone_to_direction(zone: str) -> Signal:
        if zone == "lower":
            return Signal.BUY
        if zone == "upper":
            return Signal.SELL
        return Signal.HOLD

    def _sweep_threshold_pips_for_direction(self, direction: Signal) -> float:
        if direction == Signal.BUY:
            return self.cfg.sweep_threshold_pips_buy
        if direction == Signal.SELL:
            return self.cfg.sweep_threshold_pips_sell
        return self.cfg.sweep_threshold_pips

    def _close_inside_buffer_pips_for_direction(self, direction: Signal) -> float:
        if direction == Signal.BUY:
            return self.cfg.close_inside_buffer_pips_buy
        if direction == Signal.SELL:
            return self.cfg.close_inside_buffer_pips_sell
        return self.cfg.close_inside_buffer_pips

    def _rejection_wick_ratio_min_for_direction(self, direction: Signal) -> float:
        if direction == Signal.BUY:
            return self.cfg.rejection_wick_ratio_min_buy
        if direction == Signal.SELL:
            return self.cfg.rejection_wick_ratio_min_sell
        return self.cfg.rejection_wick_ratio_min

    def _rejection_score_min_for_direction(self, direction: Signal) -> float:
        if direction == Signal.BUY:
            return self.cfg.rejection_score_min_buy
        if direction == Signal.SELL:
            return self.cfg.rejection_score_min_sell
        return self.cfg.rejection_score_min

    def _to_session_time(self, timestamp: Any) -> pd.Timestamp:
        ts = pd.Timestamp(timestamp)
        session_tz = ZoneInfo(self.cfg.session_timezone)
        data_tz = ZoneInfo(self.cfg.data_timezone)
        if ts.tzinfo is None:
            ts = ts.tz_localize(data_tz)
        return ts.tz_convert(session_tz)

    def _parse_hour_set(self, raw: Any) -> Set[int]:
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

    def _parse_blocked_hours(self, raw: Any) -> Set[int]:
        return self._parse_hour_set(raw)

    def _parse_blocked_weekdays(self, raw: Any) -> Set[int]:
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
                d = int(text)
            except ValueError:
                continue
            if 0 <= d <= 6:
                out.add(d)
        return out
