"""
MACD Scalper Strategy.

Short-term momentum strategy for XAUUSD:
- Entry on fresh MACD line/signal crossover
- Histogram acceleration confirmation
- Optional EMA trend filter
- ATR-based SL/TP with optional trailing stop
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional, Set
from zoneinfo import ZoneInfo

import pandas as pd
from loguru import logger

from core.strategy_base import Position, Signal, StrategyBase, TradeSignal
from indicators.common import calculate_adx, calculate_atr, calculate_ema, calculate_macd
from utils.config import config as env_config


class MACDScalper(StrategyBase):
    name = "MACD Scalper"
    version = "1.1.0"
    description = "Balanced MACD scalper with sideway regime filter and safety gate"
    author = "AlgoAct"

    def __init__(self) -> None:
        super().__init__()

        # Core indicator params
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_signal = 9
        self.min_hist_abs = 0.04
        self.require_hist_acceleration = True
        self.require_cross_over_zero = False

        # Filters
        self.use_ema_filter = True
        self.ema_filter_period = 50
        self.allow_long = True
        self.allow_short = True
        self.max_spread_points = 40.0
        self.max_signals_per_day = 80

        # Sideway regime filter (balanced mode)
        self.sideway_mode_enabled = True
        self.sideway_adx_period = 14
        self.sideway_adx_max = 22.0
        self.sideway_ema_slope_lookback = 8
        self.sideway_ema_slope_max_pips = 18.0
        self.sideway_range_lookback = 36
        self.sideway_min_range_pips = 40.0
        self.sideway_max_range_pips = 280.0
        self.sideway_min_atr_pips = 6.0
        self.sideway_max_atr_pips = 55.0

        # Safety gate: halt new entries when regime invalid / after loss streak
        self.safety_halt_on_regime_break = True
        self.safety_resume_confirm_bars = 4
        self.safety_halt_after_consecutive_losses = 3
        self.safety_loss_halt_bars = 25
        self.pre_sl_exit_enabled = True
        self.pre_sl_min_bars_in_trade = 1
        self.pre_sl_loss_ratio = 0.55
        self.pre_sl_fast_loss_ratio = 0.35
        self.pre_sl_hard_loss_ratio = 0.70
        self.pre_sl_hist_flip_abs = 0.05
        self.pre_sl_hist_flat_abs = 0.02
        self.pre_sl_price_buffer_pips = 6.0

        # Risk / exit
        self.atr_period = 14
        self.atr_stop_mult = 1.0
        self.atr_target_mult = 1.3
        self.min_stop_pips = 30.0
        self.max_stop_pips = 180.0
        self.risk_reward = 1.4
        self.max_bars_in_trade = 20
        self.cooldown_bars = 2
        self.use_trailing_stop = False
        self.trailing_start_pips = 0.0
        self.trailing_pips = 30.0
        self.lot_size = 0.0

        # Session / execution window
        self.use_time_filter = True
        self.start_hour = 6
        self.end_hour = 23
        self.trade_friday = True
        self.session_timezone = "UTC"
        self.data_timezone = "UTC"
        self.blocked_hours: Set[int] = set()
        self.blocked_weekdays: Set[int] = set()

        # Runtime state
        self.magic_number = 792100
        self._bar_counter: Dict[str, int] = {}
        self._last_signal_bar: Dict[str, int] = {}
        self._open_bar_by_ticket: Dict[int, int] = {}
        self._daily_signal_date: Dict[str, date] = {}
        self._daily_signal_count: Dict[str, int] = {}
        self._regime_ok_streak: Dict[str, int] = {}
        self._regime_halted: Dict[str, bool] = {}
        self._loss_halt_until_bar: Dict[str, int] = {}
        self._consecutive_losses: Dict[str, int] = {}
        self.log_diagnostics = False

    def initialize(self, config: Dict[str, Any]) -> None:
        self.config = config
        params = config.get("parameters", {})
        risk = config.get("risk", {})
        session = config.get("session", {})
        exec_filters = config.get("execution_filters", {})
        diagnostics = config.get("diagnostics", {})

        self.macd_fast = int(params.get("macd_fast", 12))
        self.macd_slow = int(params.get("macd_slow", 26))
        self.macd_signal = int(params.get("macd_signal", 9))
        self.min_hist_abs = float(params.get("min_hist_abs", 0.04))
        self.require_hist_acceleration = bool(params.get("require_hist_acceleration", True))
        self.require_cross_over_zero = bool(params.get("require_cross_over_zero", False))
        self.use_ema_filter = bool(params.get("use_ema_filter", True))
        self.ema_filter_period = int(params.get("ema_filter_period", 50))
        self.max_spread_points = float(params.get("max_spread_points", 40.0))
        self.atr_period = int(params.get("atr_period", 14))
        self.max_signals_per_day = max(1, int(params.get("max_signals_per_day", 80)))
        self.sideway_mode_enabled = bool(params.get("sideway_mode_enabled", True))
        self.sideway_adx_period = int(params.get("sideway_adx_period", 14))
        self.sideway_adx_max = float(params.get("sideway_adx_max", 22.0))
        self.sideway_ema_slope_lookback = max(1, int(params.get("sideway_ema_slope_lookback", 8)))
        self.sideway_ema_slope_max_pips = float(params.get("sideway_ema_slope_max_pips", 18.0))
        self.sideway_range_lookback = max(8, int(params.get("sideway_range_lookback", 36)))
        self.sideway_min_range_pips = float(params.get("sideway_min_range_pips", 40.0))
        self.sideway_max_range_pips = float(params.get("sideway_max_range_pips", 280.0))
        self.sideway_min_atr_pips = float(params.get("sideway_min_atr_pips", 6.0))
        self.sideway_max_atr_pips = float(params.get("sideway_max_atr_pips", 55.0))
        self.safety_halt_on_regime_break = bool(params.get("safety_halt_on_regime_break", True))
        self.safety_resume_confirm_bars = max(1, int(params.get("safety_resume_confirm_bars", 4)))
        self.safety_halt_after_consecutive_losses = max(0, int(params.get("safety_halt_after_consecutive_losses", 3)))
        self.safety_loss_halt_bars = max(1, int(params.get("safety_loss_halt_bars", 25)))
        self.pre_sl_exit_enabled = bool(params.get("pre_sl_exit_enabled", True))
        self.pre_sl_min_bars_in_trade = max(0, int(params.get("pre_sl_min_bars_in_trade", 1)))
        self.pre_sl_loss_ratio = float(params.get("pre_sl_loss_ratio", 0.55))
        self.pre_sl_fast_loss_ratio = float(params.get("pre_sl_fast_loss_ratio", 0.35))
        self.pre_sl_hard_loss_ratio = float(params.get("pre_sl_hard_loss_ratio", 0.70))
        self.pre_sl_hist_flip_abs = float(params.get("pre_sl_hist_flip_abs", 0.05))
        self.pre_sl_hist_flat_abs = float(params.get("pre_sl_hist_flat_abs", 0.02))
        self.pre_sl_price_buffer_pips = float(params.get("pre_sl_price_buffer_pips", 6.0))

        self.atr_stop_mult = float(risk.get("atr_stop_mult", 1.0))
        self.atr_target_mult = float(risk.get("atr_target_mult", 1.3))
        self.min_stop_pips = float(risk.get("min_stop_pips", 30.0))
        self.max_stop_pips = float(risk.get("max_stop_pips", 180.0))
        self.risk_reward = float(risk.get("risk_reward", 1.4))
        self.max_bars_in_trade = int(risk.get("max_bars_in_trade", 20))
        self.cooldown_bars = max(0, int(risk.get("cooldown_bars", 2)))
        self.use_trailing_stop = bool(risk.get("trailing_stop", False))
        self.trailing_start_pips = float(risk.get("trailing_start_pips", 0.0))
        self.trailing_pips = float(risk.get("trailing_pips", 30.0))
        self.lot_size = float(risk.get("lot_size", env_config.trading.default_lot_size))

        self.use_time_filter = bool(session.get("use_time_filter", True))
        self.start_hour = int(session.get("start_hour", 6))
        self.end_hour = int(session.get("end_hour", 23))
        self.trade_friday = bool(session.get("trade_friday", True))
        self.session_timezone = str(session.get("timezone", "UTC"))
        self.data_timezone = str(session.get("data_timezone", "UTC"))

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

        self.log_diagnostics = bool(diagnostics.get("enabled", False))

        self.symbols = config.get("symbols", ["XAUUSD"])
        self.timeframe = str(config.get("timeframe", "M1"))
        self.enabled = bool(config.get("enabled", True))
        self.magic_number = int(config.get("magic_number", 792100))

        self._bar_counter = {}
        self._last_signal_bar = {}
        self._open_bar_by_ticket = {}
        self._daily_signal_date = {}
        self._daily_signal_count = {}
        self._regime_ok_streak = {}
        self._regime_halted = {}
        self._loss_halt_until_bar = {}
        self._consecutive_losses = {}

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        need = max(
            self.macd_slow + self.macd_signal + 3,
            self.ema_filter_period + self.sideway_ema_slope_lookback + 3,
            self.atr_period + 3,
            self.sideway_adx_period + 3,
            self.sideway_range_lookback + 3,
        )
        if len(data) < need:
            return None

        self._bar_counter[symbol] = self._bar_counter.get(symbol, 0) + 1
        current_bar = self._bar_counter[symbol]
        last_signal_bar = self._last_signal_bar.get(symbol, -10_000)
        if current_bar - last_signal_bar <= self.cooldown_bars:
            return None

        if self.use_time_filter:
            is_open, session_time = self._is_trading_time(data)
            if not is_open:
                return None
            if self._is_blocked_window(session_time):
                return None
            self._reset_daily_counter(symbol, session_time.date())
            if self._daily_signal_count.get(symbol, 0) >= self.max_signals_per_day:
                return None

        spread_points = self._extract_spread_points(symbol, data)
        if spread_points is not None and spread_points > self.max_spread_points:
            return None

        if current_bar <= self._loss_halt_until_bar.get(symbol, -1):
            return None

        try:
            macd_line, signal_line, hist = calculate_macd(
                data,
                fast=self.macd_fast,
                slow=self.macd_slow,
                signal=self.macd_signal,
            )
            ema = calculate_ema(data, period=self.ema_filter_period)
            atr = calculate_atr(data, period=self.atr_period)
            adx = calculate_adx(data, period=self.sideway_adx_period)
        except Exception:
            return None

        values = [
            macd_line.iloc[-1], macd_line.iloc[-2],
            signal_line.iloc[-1], signal_line.iloc[-2],
            hist.iloc[-1], hist.iloc[-2],
            ema.iloc[-1], atr.iloc[-1], adx.iloc[-1],
        ]
        if any(pd.isna(v) for v in values):
            return None

        macd_now = float(macd_line.iloc[-1])
        macd_prev = float(macd_line.iloc[-2])
        signal_now = float(signal_line.iloc[-1])
        signal_prev = float(signal_line.iloc[-2])
        hist_now = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2])
        ema_now = float(ema.iloc[-1])
        ema_prev = float(ema.iloc[-(self.sideway_ema_slope_lookback + 1)])
        atr_now = float(atr.iloc[-1])
        adx_now = float(adx.iloc[-1])
        close_now = float(data.iloc[-1]["close"])
        pip_price = self._pips_to_price(symbol, 1.0)
        if pip_price <= 0:
            return None

        regime_ok, regime_reason = self._is_sideway_regime(
            symbol=symbol,
            data=data,
            adx_now=adx_now,
            atr_now=atr_now,
            ema_now=ema_now,
            ema_prev=ema_prev,
            pip_price=pip_price,
        )

        if self.safety_halt_on_regime_break:
            if not regime_ok:
                self._regime_halted[symbol] = True
                self._regime_ok_streak[symbol] = 0
                if self.log_diagnostics:
                    logger.debug(f"[MACD] {symbol} entry halted: {regime_reason}")
                return None
            if self._regime_halted.get(symbol, False):
                streak = self._regime_ok_streak.get(symbol, 0) + 1
                self._regime_ok_streak[symbol] = streak
                if streak < self.safety_resume_confirm_bars:
                    return None
                self._regime_halted[symbol] = False
                self._regime_ok_streak[symbol] = 0
                if self.log_diagnostics:
                    logger.info(f"[MACD] {symbol} entry resumed after regime recovery")
        elif not regime_ok:
            return None

        bullish_cross = macd_prev <= signal_prev and macd_now > signal_now
        bearish_cross = macd_prev >= signal_prev and macd_now < signal_now

        bullish_hist_ok = hist_now >= self.min_hist_abs
        bearish_hist_ok = hist_now <= -self.min_hist_abs
        if self.require_hist_acceleration:
            bullish_hist_ok = bullish_hist_ok and (hist_now > hist_prev)
            bearish_hist_ok = bearish_hist_ok and (hist_now < hist_prev)

        long_ok = self.allow_long and bullish_cross and bullish_hist_ok
        short_ok = self.allow_short and bearish_cross and bearish_hist_ok

        if self.require_cross_over_zero:
            long_ok = long_ok and (macd_now > 0.0)
            short_ok = short_ok and (macd_now < 0.0)

        if self.use_ema_filter:
            long_ok = long_ok and (close_now >= ema_now)
            short_ok = short_ok and (close_now <= ema_now)

        if not long_ok and not short_ok:
            return None

        if long_ok and short_ok:
            # Defensive tie-breaker: select stronger histogram impulse.
            if abs(hist_now - hist_prev) < 1e-12:
                return None
            long_ok = (hist_now - hist_prev) > 0
            short_ok = not long_ok

        direction = Signal.BUY if long_ok else Signal.SELL
        signal = self._build_signal(symbol, direction, close_now, atr_now)
        if signal is None:
            return None

        self._last_signal_bar[symbol] = current_bar
        if self.use_time_filter:
            self._daily_signal_count[symbol] = self._daily_signal_count.get(symbol, 0) + 1

        if self.log_diagnostics:
            logger.info(
                f"[MACD] {symbol} {direction.name} | close={close_now:.2f} "
                f"macd={macd_now:.4f} signal={signal_now:.4f} hist={hist_now:.4f} "
                f"adx={adx_now:.2f} regime={regime_reason}"
            )
        return signal

    def _build_signal(
        self,
        symbol: str,
        direction: Signal,
        entry_price: float,
        atr_value: float,
    ) -> Optional[TradeSignal]:
        atr_stop = max(atr_value * self.atr_stop_mult, 0.0)
        stop_distance = max(self._pips_to_price(symbol, self.min_stop_pips), atr_stop)
        max_stop_distance = self._pips_to_price(symbol, self.max_stop_pips)
        if stop_distance <= 0:
            return None
        stop_distance = min(stop_distance, max_stop_distance)

        atr_tp = max(atr_value * self.atr_target_mult, 0.0)
        rr_tp = stop_distance * self.risk_reward
        target_distance = max(rr_tp, atr_tp)

        if direction == Signal.BUY:
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + target_distance
            comment = "MACD_BUY"
        else:
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - target_distance
            comment = "MACD_SELL"

        return TradeSignal(
            signal=direction,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=self.lot_size,
            comment=comment,
            magic_number=self.magic_number,
        )

    def should_close(self, position: Position, data: pd.DataFrame) -> bool:
        if len(data) < self.macd_slow + self.macd_signal + 2:
            return False
        try:
            macd_line, signal_line, hist = calculate_macd(
                data,
                fast=self.macd_fast,
                slow=self.macd_slow,
                signal=self.macd_signal,
            )
        except Exception:
            return False

        if pd.isna(macd_line.iloc[-1]) or pd.isna(signal_line.iloc[-1]):
            return False

        macd_now = float(macd_line.iloc[-1])
        signal_now = float(signal_line.iloc[-1])
        hist_now = float(hist.iloc[-1]) if not pd.isna(hist.iloc[-1]) else 0.0
        if position.type == Signal.BUY and macd_now < signal_now:
            return True
        if position.type == Signal.SELL and macd_now > signal_now:
            return True

        open_bar = self._open_bar_by_ticket.get(int(position.ticket))
        bar_idx = self._bar_counter.get(position.symbol, 0)
        bars_in_trade = (bar_idx - open_bar) if open_bar is not None else self.pre_sl_min_bars_in_trade

        if self.pre_sl_exit_enabled and bars_in_trade >= self.pre_sl_min_bars_in_trade:
            current_price = float(data.iloc[-1]["close"])
            prev_close = float(data.iloc[-2]["close"]) if len(data) >= 2 else current_price
            bar_low = float(data.iloc[-1]["low"])
            bar_high = float(data.iloc[-1]["high"])
            risk_distance = abs(float(position.open_price) - float(position.stop_loss))
            if risk_distance > 0:
                buffer_price = self._pips_to_price(position.symbol, self.pre_sl_price_buffer_pips)
                if position.type == Signal.BUY:
                    adverse = max(0.0, float(position.open_price) - current_price)
                    near_sl_intrabar = bar_low <= (float(position.stop_loss) + buffer_price)
                    macd_against = macd_now < signal_now
                    hist_against = hist_now <= -abs(self.pre_sl_hist_flip_abs)
                    weak_hist = hist_now <= abs(self.pre_sl_hist_flat_abs)
                    adverse_candle = current_price < prev_close
                else:
                    adverse = max(0.0, current_price - float(position.open_price))
                    near_sl_intrabar = bar_high >= (float(position.stop_loss) - buffer_price)
                    macd_against = macd_now > signal_now
                    hist_against = hist_now >= abs(self.pre_sl_hist_flip_abs)
                    weak_hist = hist_now >= -abs(self.pre_sl_hist_flat_abs)
                    adverse_candle = current_price > prev_close

                loss_ratio = adverse / risk_distance
                fast_trigger = macd_against and hist_against and (loss_ratio >= self.pre_sl_fast_loss_ratio)
                near_sl_trigger = (
                    near_sl_intrabar
                    and (loss_ratio >= self.pre_sl_loss_ratio)
                    and (macd_against or hist_against or weak_hist)
                )
                hard_trigger = adverse_candle and (loss_ratio >= self.pre_sl_hard_loss_ratio)
                if fast_trigger or near_sl_trigger or hard_trigger:
                    return True

        if open_bar is not None and (bar_idx - open_bar) >= self.max_bars_in_trade:
            return True
        return False

    def get_trailing_stop(self, position: Position, data: pd.DataFrame) -> Optional[float]:
        if not self.use_trailing_stop or len(data) == 0:
            return None

        current_price = float(data.iloc[-1]["close"])
        pip_price = self._pips_to_price(position.symbol, 1.0)
        if pip_price <= 0:
            return None

        if position.type == Signal.BUY:
            profit_price = current_price - float(position.open_price)
        else:
            profit_price = float(position.open_price) - current_price
        if profit_price <= 0:
            return None

        profit_pips = profit_price / pip_price
        if profit_pips < self.trailing_start_pips:
            return None

        trail_distance = self._pips_to_price(position.symbol, self.trailing_pips)
        return self._apply_trailing_distance(position, current_price, trail_distance)

    def on_trade_opened(self, position: Position) -> None:
        self._open_bar_by_ticket[int(position.ticket)] = self._bar_counter.get(position.symbol, 0)

    def on_trade_closed(self, position: Position, profit: float) -> None:
        self._open_bar_by_ticket.pop(int(position.ticket), None)
        symbol = position.symbol
        if profit < 0:
            losses = self._consecutive_losses.get(symbol, 0) + 1
            self._consecutive_losses[symbol] = losses
            if (
                self.safety_halt_after_consecutive_losses > 0
                and losses >= self.safety_halt_after_consecutive_losses
            ):
                bar_now = self._bar_counter.get(symbol, 0)
                self._loss_halt_until_bar[symbol] = bar_now + self.safety_loss_halt_bars
                self._consecutive_losses[symbol] = 0
                logger.warning(
                    f"[MACD] {symbol} safety halt after losses | "
                    f"resume_after_bar={self._loss_halt_until_bar[symbol]}"
                )
        else:
            self._consecutive_losses[symbol] = 0

    @staticmethod
    def _point_for_symbol(symbol: str) -> float:
        s = symbol.upper()
        if "XAU" in s or "GOLD" in s:
            return 0.01
        if s.endswith("JPY"):
            return 0.001
        return 0.0001

    def _pips_to_price(self, symbol: str, pips: float) -> float:
        return float(pips) * self._point_for_symbol(symbol) * 10.0

    def _extract_spread_points(self, symbol: str, data: pd.DataFrame) -> Optional[float]:
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

    def _is_trading_time(self, data: pd.DataFrame) -> tuple[bool, pd.Timestamp]:
        ts = pd.Timestamp(data.iloc[-1]["time"])
        data_tz = ZoneInfo(self.data_timezone)
        session_tz = ZoneInfo(self.session_timezone)
        if ts.tzinfo is None:
            ts = ts.tz_localize(data_tz)
        session_time = ts.tz_convert(session_tz)

        if not self.trade_friday and session_time.weekday() == 4:
            return False, session_time

        hour = session_time.hour
        if self.start_hour <= self.end_hour:
            is_open = self.start_hour <= hour < self.end_hour
        else:
            is_open = hour >= self.start_hour or hour < self.end_hour
        return is_open, session_time

    def _is_blocked_window(self, session_time: pd.Timestamp) -> bool:
        if session_time.hour in self.blocked_hours:
            return True
        if session_time.weekday() in self.blocked_weekdays:
            return True
        return False

    def _reset_daily_counter(self, symbol: str, day: date) -> None:
        current_day = self._daily_signal_date.get(symbol)
        if current_day != day:
            self._daily_signal_date[symbol] = day
            self._daily_signal_count[symbol] = 0

    def _is_sideway_regime(
        self,
        symbol: str,
        data: pd.DataFrame,
        adx_now: float,
        atr_now: float,
        ema_now: float,
        ema_prev: float,
        pip_price: float,
    ) -> tuple[bool, str]:
        if not self.sideway_mode_enabled:
            return True, "sideway_mode_disabled"

        if adx_now > self.sideway_adx_max:
            return False, f"adx_high({adx_now:.2f}>{self.sideway_adx_max:.2f})"

        slope_pips = abs(ema_now - ema_prev) / pip_price
        if slope_pips > self.sideway_ema_slope_max_pips:
            return False, f"ema_slope_high({slope_pips:.1f}p)"

        atr_pips = atr_now / pip_price
        if atr_pips < self.sideway_min_atr_pips:
            return False, f"atr_too_low({atr_pips:.1f}p)"
        if atr_pips > self.sideway_max_atr_pips:
            return False, f"atr_too_high({atr_pips:.1f}p)"

        window = data.tail(self.sideway_range_lookback)
        range_pips = (float(window["high"].max()) - float(window["low"].min())) / pip_price
        if range_pips < self.sideway_min_range_pips:
            return False, f"range_too_narrow({range_pips:.1f}p)"
        if range_pips > self.sideway_max_range_pips:
            return False, f"range_too_wide({range_pips:.1f}p)"

        return True, "sideway_ok"

    @staticmethod
    def _parse_blocked_hours(raw: Any) -> Set[int]:
        blocked: Set[int] = set()
        if not isinstance(raw, list):
            return blocked
        for item in raw:
            try:
                blocked.add(int(item) % 24)
            except Exception:
                continue
        return blocked

    @staticmethod
    def _parse_blocked_weekdays(raw: Any) -> Set[int]:
        blocked: Set[int] = set()
        if not isinstance(raw, list):
            return blocked
        for item in raw:
            try:
                blocked.add(int(item) % 7)
            except Exception:
                continue
        return blocked
