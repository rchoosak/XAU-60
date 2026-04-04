"""
Adaptive Volatility Grid Strategy.

Mean-reversion grid entries with ATR-adaptive spacing, trend filter, and
optional two-stage trailing stop.
"""
from datetime import date
from typing import Dict, Any, Optional, Set
from zoneinfo import ZoneInfo

import pandas as pd

from core.strategy_base import StrategyBase, TradeSignal, Signal, Position
from indicators.common import calculate_atr, calculate_adx, calculate_rsi, calculate_ema
from utils.config import config as env_config


class AdaptiveVolatilityGrid(StrategyBase):
    """
    ATR-adaptive grid strategy for volatile ranging markets.

    Entry Logic:
    - Compute dynamic spacing from ATR.
    - BUY when price is below anchor (EMA) by spacing and RSI is oversold.
    - SELL when price is above anchor (EMA) by spacing and RSI is overbought.
    - Skip entries when ADX indicates strong trend.

    Exit Logic:
    - Initial SL/TP is derived from ATR + risk/reward.
    - Optional trailing stop with wide->tight profile.
    """

    name = "Adaptive Volatility Grid"
    version = "1.0.0"
    description = "ATR-adaptive mean-reversion grid with ADX trend filter"
    author = "AlgoAct"

    def __init__(self):
        super().__init__()

        # Signal generation
        self.atr_period = 14
        self.grid_atr_multiplier = 1.0
        self.min_grid_spacing_pips = 80.0
        self.max_grid_spacing_pips = 300.0
        self.anchor_ema_period = 34

        self.trend_adx_period = 14
        self.trend_adx_max = 28.0

        self.rsi_period = 14
        self.rsi_oversold = 35.0
        self.rsi_overbought = 65.0

        # Side-specific signal tuning
        self.buy_rsi_oversold = 35.0
        self.sell_rsi_overbought = 65.0
        self.buy_grid_spacing_multiplier = 1.0
        self.sell_grid_spacing_multiplier = 1.0
        self.sell_trend_adx_max = 28.0
        self.sell_require_rsi_rollover = False
        self.sell_require_bearish_candle = False
        self.sell_anchor_slope_max_pips = 0.0

        self.risk_reward = 1.6
        self.take_profit_spacing_multiplier = 1.0

        self.cooldown_bars = 1
        self.reentry_spacing_factor = 0.8
        self.max_signals_per_day = 30

        # Risk management
        self.stop_loss_atr_multiplier = 1.8
        self.stop_loss_min_pips = 120.0
        self.stop_loss_max_pips = 500.0
        self.sell_stop_loss_atr_multiplier = 1.8
        self.sell_stop_loss_min_pips = 120.0
        self.sell_stop_loss_max_pips = 500.0
        self.sell_risk_reward = 1.6
        self.use_trailing_stop = True
        self.trailing_pips = 120.0
        self.trailing_start_pips = 0.0
        self.trailing_pips_wide = 120.0
        self.tighten_after_profit_pips = 0.0
        self.trailing_pips_tight = 120.0
        self.lot_size = 0.0
        self.reversal_exit_enabled = True
        self.reversal_exit_min_bars_in_trade = 1
        self.reversal_exit_loss_ratio = 0.45
        self.reversal_exit_near_sl_ratio = 0.70
        self.reversal_exit_anchor_flip_pips = 8.0
        self.reversal_exit_require_trend_strength = False
        self.reversal_exit_trend_adx_min = 22.0

        # Session filter
        self.use_time_filter = True
        self.start_hour = 0
        self.end_hour = 23
        self.trade_friday = True
        self.session_timezone = "UTC"
        self.data_timezone = "UTC"

        # Optional execution filters (config-driven)
        self.allow_long = True
        self.allow_short = True
        self.blocked_hours: Set[int] = set()
        self.blocked_weekdays: Set[int] = set()

        # Runtime state
        self.magic_number = 789777
        self._bar_counter: Dict[str, int] = {}
        self._last_signal_bar: Dict[str, int] = {}
        self._last_entry_price_by_side: Dict[str, Dict[Signal, float]] = {}
        self._daily_signal_date: Dict[str, date] = {}
        self._daily_signal_count: Dict[str, int] = {}
        self._open_bar_by_ticket: Dict[int, int] = {}

    def initialize(self, config: Dict[str, Any]) -> None:
        self.config = config

        params = config.get("parameters", {})
        self.atr_period = int(params.get("atr_period", 14))
        self.grid_atr_multiplier = float(params.get("grid_atr_multiplier", 1.0))
        self.min_grid_spacing_pips = float(params.get("min_grid_spacing_pips", 80.0))
        self.max_grid_spacing_pips = float(params.get("max_grid_spacing_pips", 300.0))
        self.anchor_ema_period = int(params.get("anchor_ema_period", 34))

        self.trend_adx_period = int(params.get("trend_adx_period", 14))
        self.trend_adx_max = float(params.get("trend_adx_max", 28.0))

        self.rsi_period = int(params.get("rsi_period", 14))
        self.rsi_oversold = float(params.get("rsi_oversold", 35.0))
        self.rsi_overbought = float(params.get("rsi_overbought", 65.0))
        self.buy_rsi_oversold = float(params.get("buy_rsi_oversold", self.rsi_oversold))
        self.sell_rsi_overbought = float(params.get("sell_rsi_overbought", self.rsi_overbought))
        self.buy_grid_spacing_multiplier = max(0.5, float(params.get("buy_grid_spacing_multiplier", 1.0)))
        self.sell_grid_spacing_multiplier = max(0.5, float(params.get("sell_grid_spacing_multiplier", 1.0)))
        self.sell_trend_adx_max = float(params.get("sell_trend_adx_max", self.trend_adx_max))
        self.sell_require_rsi_rollover = bool(params.get("sell_require_rsi_rollover", False))
        self.sell_require_bearish_candle = bool(params.get("sell_require_bearish_candle", False))
        self.sell_anchor_slope_max_pips = float(params.get("sell_anchor_slope_max_pips", 0.0))

        self.risk_reward = float(params.get("risk_reward", 1.6))
        self.take_profit_spacing_multiplier = float(
            params.get("take_profit_spacing_multiplier", 1.0)
        )
        self.cooldown_bars = max(1, int(params.get("cooldown_bars", 1)))
        self.reentry_spacing_factor = max(0.0, float(params.get("reentry_spacing_factor", 0.8)))
        self.max_signals_per_day = max(1, int(params.get("max_signals_per_day", 30)))

        risk = config.get("risk", {})
        self.stop_loss_atr_multiplier = float(risk.get("stop_loss_atr_multiplier", 1.8))
        self.stop_loss_min_pips = float(risk.get("stop_loss_min_pips", 120.0))
        self.stop_loss_max_pips = float(risk.get("stop_loss_max_pips", 500.0))
        self.sell_stop_loss_atr_multiplier = float(
            risk.get("sell_stop_loss_atr_multiplier", self.stop_loss_atr_multiplier)
        )
        self.sell_stop_loss_min_pips = float(
            risk.get("sell_stop_loss_min_pips", self.stop_loss_min_pips)
        )
        self.sell_stop_loss_max_pips = float(
            risk.get("sell_stop_loss_max_pips", self.stop_loss_max_pips)
        )
        self.sell_risk_reward = float(risk.get("sell_risk_reward", self.risk_reward))
        self.use_trailing_stop = bool(risk.get("trailing_stop", True))
        self.trailing_pips = float(risk.get("trailing_pips", 120.0))
        self.trailing_start_pips = float(risk.get("trailing_start_pips", 0.0))
        self.trailing_pips_wide = float(risk.get("trailing_pips_wide", self.trailing_pips))
        self.tighten_after_profit_pips = float(risk.get("tighten_after_profit_pips", 0.0))
        self.trailing_pips_tight = float(risk.get("trailing_pips_tight", self.trailing_pips))
        self.lot_size = float(risk.get("lot_size", env_config.trading.default_lot_size))
        self.reversal_exit_enabled = bool(risk.get("reversal_exit_enabled", True))
        self.reversal_exit_min_bars_in_trade = max(0, int(risk.get("reversal_exit_min_bars_in_trade", 1)))
        self.reversal_exit_loss_ratio = float(risk.get("reversal_exit_loss_ratio", 0.45))
        self.reversal_exit_near_sl_ratio = float(risk.get("reversal_exit_near_sl_ratio", 0.70))
        self.reversal_exit_anchor_flip_pips = float(risk.get("reversal_exit_anchor_flip_pips", 8.0))
        self.reversal_exit_require_trend_strength = bool(
            risk.get("reversal_exit_require_trend_strength", False)
        )
        self.reversal_exit_trend_adx_min = float(risk.get("reversal_exit_trend_adx_min", 22.0))

        session = config.get("session", {})
        self.use_time_filter = bool(session.get("use_time_filter", True))
        self.start_hour = int(session.get("start_hour", 0))
        self.end_hour = int(session.get("end_hour", 23))
        self.trade_friday = bool(session.get("trade_friday", True))
        self.session_timezone = session.get("timezone", "UTC")
        self.data_timezone = session.get("data_timezone", "UTC")

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
        self.enabled = config.get("enabled", True)
        self.magic_number = int(config.get("magic_number", 789777))

        self._bar_counter = {}
        self._last_signal_bar = {}
        self._last_entry_price_by_side = {}
        self._daily_signal_date = {}
        self._daily_signal_count = {}
        self._open_bar_by_ticket = {}

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        required = max(self.atr_period, self.anchor_ema_period, self.trend_adx_period, self.rsi_period) + 2
        if len(data) < required:
            return None

        self._bar_counter[symbol] = self._bar_counter.get(symbol, 0) + 1
        current_bar_index = self._bar_counter[symbol]
        last_signal_bar = self._last_signal_bar.get(symbol, -10_000)
        if current_bar_index - last_signal_bar < self.cooldown_bars:
            return None

        is_open, session_time = self._is_trading_time(data)
        if not is_open:
            return None

        if self._is_blocked_window(session_time):
            return None

        self._reset_daily_counter(symbol, session_time.date())
        if self._daily_signal_count.get(symbol, 0) >= self.max_signals_per_day:
            return None

        try:
            atr_series = calculate_atr(data, self.atr_period)
            adx_series = calculate_adx(data, self.trend_adx_period)
            rsi_series = calculate_rsi(data, self.rsi_period)
            anchor_series = calculate_ema(data, self.anchor_ema_period)

            atr = float(atr_series.iloc[-1])
            adx = float(adx_series.iloc[-1])
            rsi = float(rsi_series.iloc[-1])
            prev_rsi = float(rsi_series.iloc[-2])
            anchor = float(anchor_series.iloc[-1])
            prev_anchor = float(anchor_series.iloc[-2])
        except Exception:
            return None

        if (
            pd.isna(atr)
            or pd.isna(adx)
            or pd.isna(rsi)
            or pd.isna(prev_rsi)
            or pd.isna(anchor)
            or pd.isna(prev_anchor)
        ):
            return None

        current_price = float(data.iloc[-1]["close"])
        current_open = float(data.iloc[-1]["open"])
        spacing = self._resolve_grid_spacing(symbol, atr)
        if spacing <= 0:
            return None

        buy_spacing = spacing * self.buy_grid_spacing_multiplier
        sell_spacing = spacing * self.sell_grid_spacing_multiplier
        anchor_slope = anchor - prev_anchor
        sell_slope_limit = self._pips_to_price(symbol, self.sell_anchor_slope_max_pips)

        long_setup = (
            adx <= self.trend_adx_max
            and current_price <= anchor - buy_spacing
            and rsi <= self.buy_rsi_oversold
        )
        short_setup = (
            adx <= self.sell_trend_adx_max
            and current_price >= anchor + sell_spacing
            and rsi >= self.sell_rsi_overbought
        )
        if short_setup and self.sell_require_rsi_rollover:
            short_setup = rsi < prev_rsi
        if short_setup and self.sell_require_bearish_candle:
            short_setup = current_price < current_open
        if short_setup and self.sell_anchor_slope_max_pips > 0:
            short_setup = anchor_slope <= sell_slope_limit

        signal = Signal.HOLD
        if long_setup:
            signal = Signal.BUY
        elif short_setup:
            signal = Signal.SELL
        else:
            return None

        if signal == Signal.BUY and not self.allow_long:
            return None
        if signal == Signal.SELL and not self.allow_short:
            return None

        if not self._passes_reentry_filter(symbol, signal, current_price, spacing):
            return None

        trade_signal = self._build_trade_signal(
            symbol=symbol,
            signal=signal,
            entry_price=current_price,
            atr=atr,
            spacing=spacing,
        )
        if trade_signal is None:
            return None

        self._last_signal_bar[symbol] = current_bar_index
        self._daily_signal_count[symbol] = self._daily_signal_count.get(symbol, 0) + 1
        side_state = self._last_entry_price_by_side.setdefault(symbol, {})
        side_state[signal] = current_price
        return trade_signal

    def should_close(self, position: Position, data: pd.DataFrame) -> bool:
        if not self.reversal_exit_enabled:
            return False

        required = max(self.anchor_ema_period, self.trend_adx_period, self.rsi_period) + 2
        if len(data) < required:
            return False

        open_bar = self._open_bar_by_ticket.get(int(position.ticket))
        bar_idx = self._bar_counter.get(position.symbol, 0)
        bars_in_trade = (
            bar_idx - open_bar
            if open_bar is not None
            else self.reversal_exit_min_bars_in_trade
        )
        if bars_in_trade < self.reversal_exit_min_bars_in_trade:
            return False

        risk_distance = abs(float(position.open_price) - float(position.stop_loss))
        if risk_distance <= 0:
            return False

        try:
            anchor_series = calculate_ema(data, self.anchor_ema_period)
            adx_series = calculate_adx(data, self.trend_adx_period)
            rsi_series = calculate_rsi(data, self.rsi_period)
        except Exception:
            return False

        anchor_now = float(anchor_series.iloc[-1])
        anchor_prev = float(anchor_series.iloc[-2])
        adx_now = float(adx_series.iloc[-1])
        rsi_now = float(rsi_series.iloc[-1])
        if (
            pd.isna(anchor_now)
            or pd.isna(anchor_prev)
            or pd.isna(adx_now)
            or pd.isna(rsi_now)
        ):
            return False

        current_price = float(data.iloc[-1]["close"])
        flip_buffer = self._pips_to_price(position.symbol, self.reversal_exit_anchor_flip_pips)

        if position.type == Signal.BUY:
            adverse = max(0.0, float(position.open_price) - current_price)
            near_sl = current_price <= (float(position.stop_loss) + flip_buffer)
            trend_flip = (
                current_price < (anchor_now - flip_buffer)
                and (anchor_now < anchor_prev or rsi_now < 50.0)
            )
        else:
            adverse = max(0.0, current_price - float(position.open_price))
            near_sl = current_price >= (float(position.stop_loss) - flip_buffer)
            trend_flip = (
                current_price > (anchor_now + flip_buffer)
                and (anchor_now > anchor_prev or rsi_now > 50.0)
            )

        if self.reversal_exit_require_trend_strength and adx_now < self.reversal_exit_trend_adx_min:
            trend_flip = False

        loss_ratio = adverse / risk_distance
        if trend_flip and loss_ratio >= self.reversal_exit_loss_ratio:
            return True
        if near_sl and loss_ratio >= self.reversal_exit_near_sl_ratio:
            return True
        return False

    def on_trade_opened(self, position: Position) -> None:
        self._open_bar_by_ticket[int(position.ticket)] = self._bar_counter.get(position.symbol, 0)

    def on_trade_closed(self, position: Position, profit: float) -> None:
        self._open_bar_by_ticket.pop(int(position.ticket), None)

    def get_trailing_stop(self, position: Position, data: pd.DataFrame) -> Optional[float]:
        if not self.use_trailing_stop:
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

        trail_pips = self.trailing_pips_wide
        if self.tighten_after_profit_pips > 0 and profit_pips >= self.tighten_after_profit_pips:
            trail_pips = self.trailing_pips_tight

        trail_distance = self._pips_to_price(position.symbol, trail_pips)
        return self._apply_trailing_distance(position, current_price, trail_distance)

    def _build_trade_signal(
        self,
        symbol: str,
        signal: Signal,
        entry_price: float,
        atr: float,
        spacing: float,
    ) -> Optional[TradeSignal]:
        if signal == Signal.BUY:
            min_sl_distance = self._pips_to_price(symbol, self.stop_loss_min_pips)
            max_sl_distance = self._pips_to_price(symbol, self.stop_loss_max_pips)
            stop_distance = max(min_sl_distance, atr * self.stop_loss_atr_multiplier)
            if max_sl_distance > 0:
                stop_distance = min(stop_distance, max_sl_distance)
            if stop_distance <= 0:
                return None

            tp_from_spacing = spacing * self.take_profit_spacing_multiplier
            tp_from_rr = stop_distance * self.risk_reward
            take_profit_distance = max(tp_from_spacing, tp_from_rr)
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + take_profit_distance
            comment = "AVG_GRID_BUY"
        elif signal == Signal.SELL:
            min_sl_distance = self._pips_to_price(symbol, self.sell_stop_loss_min_pips)
            max_sl_distance = self._pips_to_price(symbol, self.sell_stop_loss_max_pips)
            stop_distance = max(min_sl_distance, atr * self.sell_stop_loss_atr_multiplier)
            if max_sl_distance > 0:
                stop_distance = min(stop_distance, max_sl_distance)
            if stop_distance <= 0:
                return None

            tp_from_spacing = spacing * self.take_profit_spacing_multiplier
            tp_from_rr = stop_distance * self.sell_risk_reward
            take_profit_distance = max(tp_from_spacing, tp_from_rr)
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - take_profit_distance
            comment = "AVG_GRID_SELL"
        else:
            return None

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

    def _resolve_grid_spacing(self, symbol: str, atr_value: float) -> float:
        min_spacing = self._pips_to_price(symbol, self.min_grid_spacing_pips)
        max_spacing = self._pips_to_price(symbol, self.max_grid_spacing_pips)
        dynamic_spacing = atr_value * self.grid_atr_multiplier

        spacing = max(min_spacing, dynamic_spacing)
        if max_spacing > 0:
            spacing = min(spacing, max_spacing)
        return spacing

    def _passes_reentry_filter(
        self,
        symbol: str,
        signal: Signal,
        entry_price: float,
        spacing: float,
    ) -> bool:
        by_side = self._last_entry_price_by_side.get(symbol, {})
        previous = by_side.get(signal)
        if previous is None:
            return True

        required_move = spacing * self.reentry_spacing_factor
        return abs(entry_price - previous) >= required_move

    def _point_for_symbol(self, symbol: str) -> float:
        if "XAU" in symbol or "GOLD" in symbol:
            return 0.01
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

    def _is_trading_time(self, data: pd.DataFrame) -> tuple[bool, pd.Timestamp]:
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
