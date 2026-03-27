"""
Adaptive Volatility Grid Strategy.

Mean-reversion grid entries with ATR-adaptive spacing, trend filter, and
optional two-stage trailing stop.
"""
from datetime import date
from typing import Dict, Any, Optional
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

        self.risk_reward = 1.6
        self.take_profit_spacing_multiplier = 1.0

        self.cooldown_bars = 1
        self.reentry_spacing_factor = 0.8
        self.max_signals_per_day = 30

        # Risk management
        self.stop_loss_atr_multiplier = 1.8
        self.stop_loss_min_pips = 120.0
        self.stop_loss_max_pips = 500.0
        self.use_trailing_stop = True
        self.trailing_pips = 120.0
        self.trailing_start_pips = 0.0
        self.trailing_pips_wide = 120.0
        self.tighten_after_profit_pips = 0.0
        self.trailing_pips_tight = 120.0
        self.lot_size = 0.0

        # Session filter
        self.use_time_filter = True
        self.start_hour = 0
        self.end_hour = 23
        self.trade_friday = True
        self.session_timezone = "UTC"
        self.data_timezone = "UTC"

        # Runtime state
        self.magic_number = 789777
        self._bar_counter: Dict[str, int] = {}
        self._last_signal_bar: Dict[str, int] = {}
        self._last_entry_price_by_side: Dict[str, Dict[Signal, float]] = {}
        self._daily_signal_date: Dict[str, date] = {}
        self._daily_signal_count: Dict[str, int] = {}

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
        self.use_trailing_stop = bool(risk.get("trailing_stop", True))
        self.trailing_pips = float(risk.get("trailing_pips", 120.0))
        self.trailing_start_pips = float(risk.get("trailing_start_pips", 0.0))
        self.trailing_pips_wide = float(risk.get("trailing_pips_wide", self.trailing_pips))
        self.tighten_after_profit_pips = float(risk.get("tighten_after_profit_pips", 0.0))
        self.trailing_pips_tight = float(risk.get("trailing_pips_tight", self.trailing_pips))
        self.lot_size = float(risk.get("lot_size", env_config.trading.default_lot_size))

        session = config.get("session", {})
        self.use_time_filter = bool(session.get("use_time_filter", True))
        self.start_hour = int(session.get("start_hour", 0))
        self.end_hour = int(session.get("end_hour", 23))
        self.trade_friday = bool(session.get("trade_friday", True))
        self.session_timezone = session.get("timezone", "UTC")
        self.data_timezone = session.get("data_timezone", "UTC")

        self.symbols = config.get("symbols", ["XAUUSD"])
        self.timeframe = config.get("timeframe", "M1")
        self.enabled = config.get("enabled", True)
        self.magic_number = int(config.get("magic_number", 789777))

        self._bar_counter = {}
        self._last_signal_bar = {}
        self._last_entry_price_by_side = {}
        self._daily_signal_date = {}
        self._daily_signal_count = {}

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

        self._reset_daily_counter(symbol, session_time.date())
        if self._daily_signal_count.get(symbol, 0) >= self.max_signals_per_day:
            return None

        try:
            atr = float(calculate_atr(data, self.atr_period).iloc[-1])
            adx = float(calculate_adx(data, self.trend_adx_period).iloc[-1])
            rsi = float(calculate_rsi(data, self.rsi_period).iloc[-1])
            anchor = float(calculate_ema(data, self.anchor_ema_period).iloc[-1])
        except Exception:
            return None

        if pd.isna(atr) or pd.isna(adx) or pd.isna(rsi) or pd.isna(anchor):
            return None

        if adx > self.trend_adx_max:
            return None

        current_price = float(data.iloc[-1]["close"])
        spacing = self._resolve_grid_spacing(symbol, atr)
        if spacing <= 0:
            return None

        signal = Signal.HOLD
        if current_price <= anchor - spacing and rsi <= self.rsi_oversold:
            signal = Signal.BUY
        elif current_price >= anchor + spacing and rsi >= self.rsi_overbought:
            signal = Signal.SELL
        else:
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
        return False

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

        if signal == Signal.BUY:
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + take_profit_distance
            comment = "AVG_GRID_BUY"
        elif signal == Signal.SELL:
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
