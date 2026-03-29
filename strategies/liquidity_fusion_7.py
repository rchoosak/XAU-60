"""
Liquidity Fusion 7 Strategy.

Hybrid strategy that combines:
1) Liquidity Sweep
2) Order Block
3) BOS + CHOCH
4) Fair Value Gap (FVG)
5) Session filter
6) Scalping with liquidity + momentum
7) Algorithmic footprint filter
"""
from datetime import date
from typing import Dict, Any, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from core.strategy_base import StrategyBase, TradeSignal, Signal, Position
from indicators.common import calculate_atr, calculate_rsi, calculate_ema
from indicators.smc_utils import SMCAnalyzer
from utils.config import config as env_config


class LiquidityFusion7(StrategyBase):
    """Hybrid SMC + momentum scalping strategy."""

    name = "Liquidity Fusion 7"
    version = "1.0.0"
    description = "Hybrid strategy combining sweep/OB/BOS-CHOCH/FVG/session/momentum/algo-footprint"
    author = "AlgoAct"

    def __init__(self):
        super().__init__()
        self.smc: Optional[SMCAnalyzer] = None

        # Market structure / setup
        self.lookback = 30
        self.sweep_buffer_pips = 5.0
        self.sweep_reclaim_ratio = 0.25
        self.choch_lookback = 50
        self.fvg_lookback = 30
        self.ob_lookback = 30
        self.bos_lookback = 20

        # Momentum scalping
        self.atr_period = 14
        self.rsi_period = 14
        self.ema_fast_period = 9
        self.ema_slow_period = 21
        self.momentum_rsi_buy_min = 50.0
        self.momentum_rsi_sell_max = 50.0
        self.displacement_atr_min = 0.6

        # Algo footprint filter
        self.footprint_wick_ratio_min = 0.35
        self.footprint_range_atr_min = 0.8
        self.footprint_volume_multiplier = 1.1
        self.enable_volume_footprint = True

        # Score engine
        self.min_score = 5.0
        self.weights = {
            "liquidity_sweep": 2.0,
            "order_block": 1.0,
            "bos_choch": 1.5,
            "fvg": 1.0,
            "session": 1.0,
            "momentum": 1.5,
            "algo_footprint": 1.0,
        }

        self.cooldown_bars = 1
        self.max_signals_per_day = 20

        # Risk
        self.risk_reward = 1.8
        self.stop_loss_atr_multiplier = 1.5
        self.stop_loss_min_pips = 80.0
        self.stop_loss_max_pips = 400.0
        self.take_profit_min_pips = 100.0
        self.lot_size = 0.0

        # Trailing (backward-compatible + 2-stage)
        self.use_trailing_stop = True
        self.trailing_pips = 80.0
        self.trailing_start_pips = 0.0
        self.trailing_pips_wide = 80.0
        self.tighten_after_profit_pips = 0.0
        self.trailing_pips_tight = 80.0

        # Session
        self.use_time_filter = True
        self.start_hour = 7
        self.end_hour = 20
        self.trade_friday = True
        self.session_timezone = "UTC"
        self.data_timezone = "UTC"

        # Runtime state
        self.magic_number = 789888
        self._bar_counter: Dict[str, int] = {}
        self._last_signal_bar: Dict[str, int] = {}
        self._daily_signal_date: Dict[str, date] = {}
        self._daily_signal_count: Dict[str, int] = {}

    def initialize(self, config: Dict[str, Any]) -> None:
        self.config = config

        params = config.get("parameters", {})
        self.lookback = int(params.get("lookback", 30))
        self.sweep_buffer_pips = float(params.get("sweep_buffer_pips", 5.0))
        self.sweep_reclaim_ratio = float(params.get("sweep_reclaim_ratio", 0.25))
        self.choch_lookback = int(params.get("choch_lookback", 50))
        self.fvg_lookback = int(params.get("fvg_lookback", 30))
        self.ob_lookback = int(params.get("ob_lookback", 30))
        self.bos_lookback = int(params.get("bos_lookback", 20))

        self.atr_period = int(params.get("atr_period", 14))
        self.rsi_period = int(params.get("rsi_period", 14))
        self.ema_fast_period = int(params.get("ema_fast_period", 9))
        self.ema_slow_period = int(params.get("ema_slow_period", 21))
        self.momentum_rsi_buy_min = float(params.get("momentum_rsi_buy_min", 50.0))
        self.momentum_rsi_sell_max = float(params.get("momentum_rsi_sell_max", 50.0))
        self.displacement_atr_min = float(params.get("displacement_atr_min", 0.6))

        self.footprint_wick_ratio_min = float(params.get("footprint_wick_ratio_min", 0.35))
        self.footprint_range_atr_min = float(params.get("footprint_range_atr_min", 0.8))
        self.footprint_volume_multiplier = float(params.get("footprint_volume_multiplier", 1.1))
        self.enable_volume_footprint = bool(params.get("enable_volume_footprint", True))

        self.min_score = float(params.get("min_score", 5.0))
        self.cooldown_bars = max(1, int(params.get("cooldown_bars", 1)))
        self.max_signals_per_day = max(1, int(params.get("max_signals_per_day", 20)))

        weight_cfg = params.get("weights", {})
        if isinstance(weight_cfg, dict):
            for key in self.weights:
                if key in weight_cfg:
                    self.weights[key] = float(weight_cfg[key])

        self.risk_reward = float(params.get("risk_reward", 1.8))

        risk = config.get("risk", {})
        self.stop_loss_atr_multiplier = float(risk.get("stop_loss_atr_multiplier", 1.5))
        self.stop_loss_min_pips = float(risk.get("stop_loss_min_pips", 80.0))
        self.stop_loss_max_pips = float(risk.get("stop_loss_max_pips", 400.0))
        self.take_profit_min_pips = float(risk.get("take_profit_min_pips", 100.0))
        self.lot_size = float(risk.get("lot_size", env_config.trading.default_lot_size))

        self.use_trailing_stop = bool(risk.get("trailing_stop", True))
        self.trailing_pips = float(risk.get("trailing_pips", 80.0))
        self.trailing_start_pips = float(risk.get("trailing_start_pips", 0.0))
        self.trailing_pips_wide = float(risk.get("trailing_pips_wide", self.trailing_pips))
        self.tighten_after_profit_pips = float(risk.get("tighten_after_profit_pips", 0.0))
        self.trailing_pips_tight = float(risk.get("trailing_pips_tight", self.trailing_pips))

        session = config.get("session", {})
        self.use_time_filter = bool(session.get("use_time_filter", True))
        self.start_hour = int(session.get("start_hour", 7))
        self.end_hour = int(session.get("end_hour", 20))
        self.trade_friday = bool(session.get("trade_friday", True))
        self.session_timezone = session.get("timezone", "UTC")
        self.data_timezone = session.get("data_timezone", "UTC")

        self.symbols = config.get("symbols", ["XAUUSD"])
        self.timeframe = config.get("timeframe", "M1")
        self.enabled = config.get("enabled", True)
        self.magic_number = int(config.get("magic_number", 789888))

        point = self._point_for_symbol(self.symbols[0] if self.symbols else "XAUUSD")
        self.smc = SMCAnalyzer(
            swing_lookback=5,
            fvg_min_pips=max(1.0, self.sweep_buffer_pips),
            ob_displacement_factor=1.8,
            point=point,
        )

        self._bar_counter = {}
        self._last_signal_bar = {}
        self._daily_signal_date = {}
        self._daily_signal_count = {}

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        required = max(
            self.lookback + 2,
            self.choch_lookback + 2,
            self.atr_period + 2,
            self.ema_slow_period + 2,
            self.rsi_period + 2,
        )
        if len(data) < required:
            return None
        if self.smc is None:
            return None

        self._bar_counter[symbol] = self._bar_counter.get(symbol, 0) + 1
        cur_bar_idx = self._bar_counter[symbol]
        if cur_bar_idx - self._last_signal_bar.get(symbol, -10_000) < self.cooldown_bars:
            return None

        session_ok, session_time = self._is_trading_time(data)
        if not session_ok:
            return None
        self._reset_daily_counter(symbol, session_time.date())
        if self._daily_signal_count.get(symbol, 0) >= self.max_signals_per_day:
            return None

        try:
            atr = float(calculate_atr(data, self.atr_period).iloc[-1])
            rsi = float(calculate_rsi(data, self.rsi_period).iloc[-1])
            ema_fast = float(calculate_ema(data, self.ema_fast_period).iloc[-1])
            ema_slow = float(calculate_ema(data, self.ema_slow_period).iloc[-1])
        except Exception:
            return None
        if pd.isna(atr) or pd.isna(rsi) or pd.isna(ema_fast) or pd.isna(ema_slow):
            return None

        sweep_signal, sweep_level = self._detect_liquidity_sweep(data)
        if sweep_signal == Signal.HOLD:
            return None

        bos_choch_ok = self._confirm_bos_choch(data, sweep_signal)
        fvg_ok, fvg_mid = self._confirm_fvg(data, sweep_signal)
        ob_ok, ob_target = self._confirm_order_block(data, sweep_signal)
        momentum_ok = self._confirm_momentum(data, sweep_signal, rsi, ema_fast, ema_slow, atr)
        algo_ok = self._confirm_algo_footprint(data, sweep_signal, atr)

        score = 0.0
        score += self.weights["liquidity_sweep"]
        score += self.weights["session"]
        if bos_choch_ok:
            score += self.weights["bos_choch"]
        if fvg_ok:
            score += self.weights["fvg"]
        if ob_ok:
            score += self.weights["order_block"]
        if momentum_ok:
            score += self.weights["momentum"]
        if algo_ok:
            score += self.weights["algo_footprint"]

        if score < self.min_score:
            return None

        signal = self._build_signal(
            symbol=symbol,
            direction=sweep_signal,
            data=data,
            atr=atr,
            sweep_level=sweep_level,
            fvg_mid=fvg_mid,
            ob_target=ob_target,
        )
        if signal is None:
            return None

        self._last_signal_bar[symbol] = cur_bar_idx
        self._daily_signal_count[symbol] = self._daily_signal_count.get(symbol, 0) + 1
        return signal

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

    def _detect_liquidity_sweep(self, data: pd.DataFrame) -> Tuple[Signal, float]:
        recent = data.tail(self.lookback + 1)
        current = recent.iloc[-1]
        previous = recent.iloc[:-1]
        if len(previous) < 3:
            return Signal.HOLD, 0.0

        prior_high = float(previous["high"].max())
        prior_low = float(previous["low"].min())
        full_range = max(1e-9, float(current["high"]) - float(current["low"]))
        reclaim_up = (float(current["close"]) - float(current["low"])) / full_range
        reclaim_down = (float(current["high"]) - float(current["close"])) / full_range
        buffer_price = self._pips_to_price(self.symbols[0] if self.symbols else "XAUUSD", self.sweep_buffer_pips)

        bullish_sweep = (
            float(current["low"]) < prior_low - buffer_price
            and reclaim_up >= self.sweep_reclaim_ratio
        )
        bearish_sweep = (
            float(current["high"]) > prior_high + buffer_price
            and reclaim_down >= self.sweep_reclaim_ratio
        )

        if bullish_sweep and not bearish_sweep:
            return Signal.BUY, float(current["low"])
        if bearish_sweep and not bullish_sweep:
            return Signal.SELL, float(current["high"])
        return Signal.HOLD, 0.0

    def _confirm_bos_choch(self, data: pd.DataFrame, direction: Signal) -> bool:
        if direction == Signal.BUY:
            choch = self.smc.detect_bullish_choch(data, self.choch_lookback)
            prev_high = float(data.iloc[-(self.bos_lookback + 1):-1]["high"].max())
            bos = float(data.iloc[-1]["close"]) > prev_high
            return choch is not None or bos
        if direction == Signal.SELL:
            choch = self.smc.detect_bearish_choch(data, self.choch_lookback)
            prev_low = float(data.iloc[-(self.bos_lookback + 1):-1]["low"].min())
            bos = float(data.iloc[-1]["close"]) < prev_low
            return choch is not None or bos
        return False

    def _confirm_fvg(self, data: pd.DataFrame, direction: Signal) -> Tuple[bool, Optional[float]]:
        current_price = float(data.iloc[-1]["close"])
        near_dist = self._pips_to_price(self.symbols[0] if self.symbols else "XAUUSD", 30.0)
        if direction == Signal.BUY:
            fvg = self.smc.detect_bullish_fvg(data, self.fvg_lookback)
            if fvg is None:
                return False, None
            in_zone = fvg.lower_price <= current_price <= fvg.upper_price
            near_zone = abs(current_price - fvg.mid_price) <= near_dist
            return in_zone or near_zone, fvg.mid_price
        if direction == Signal.SELL:
            fvg = self.smc.detect_bearish_fvg(data, self.fvg_lookback)
            if fvg is None:
                return False, None
            in_zone = fvg.lower_price <= current_price <= fvg.upper_price
            near_zone = abs(current_price - fvg.mid_price) <= near_dist
            return in_zone or near_zone, fvg.mid_price
        return False, None

    def _confirm_order_block(self, data: pd.DataFrame, direction: Signal) -> Tuple[bool, Optional[float]]:
        current_price = float(data.iloc[-1]["close"])
        near_dist = self._pips_to_price(self.symbols[0] if self.symbols else "XAUUSD", 40.0)

        if direction == Signal.BUY:
            ob = self.smc.detect_bullish_order_block(data, self.ob_lookback)
            if ob is None:
                return False, None
            in_zone = ob.lower_price <= current_price <= ob.upper_price
            near_zone = min(abs(current_price - ob.lower_price), abs(current_price - ob.upper_price)) <= near_dist
            return in_zone or near_zone, ob.upper_price

        if direction == Signal.SELL:
            ob = self.smc.detect_bearish_order_block(data, self.ob_lookback)
            if ob is None:
                return False, None
            in_zone = ob.lower_price <= current_price <= ob.upper_price
            near_zone = min(abs(current_price - ob.lower_price), abs(current_price - ob.upper_price)) <= near_dist
            return in_zone or near_zone, ob.lower_price

        return False, None

    def _confirm_momentum(
        self,
        data: pd.DataFrame,
        direction: Signal,
        rsi: float,
        ema_fast: float,
        ema_slow: float,
        atr: float,
    ) -> bool:
        bar = data.iloc[-1]
        body = abs(float(bar["close"]) - float(bar["open"]))
        displacement_ok = body >= atr * self.displacement_atr_min

        if direction == Signal.BUY:
            return rsi >= self.momentum_rsi_buy_min and ema_fast >= ema_slow and displacement_ok
        if direction == Signal.SELL:
            return rsi <= self.momentum_rsi_sell_max and ema_fast <= ema_slow and displacement_ok
        return False

    def _confirm_algo_footprint(self, data: pd.DataFrame, direction: Signal, atr: float) -> bool:
        bar = data.iloc[-1]
        high = float(bar["high"])
        low = float(bar["low"])
        open_price = float(bar["open"])
        close = float(bar["close"])

        candle_range = max(1e-9, high - low)
        upper_wick = high - max(open_price, close)
        lower_wick = min(open_price, close) - low
        wick_ratio = (lower_wick / candle_range) if direction == Signal.BUY else (upper_wick / candle_range)
        range_ok = candle_range >= atr * self.footprint_range_atr_min
        wick_ok = wick_ratio >= self.footprint_wick_ratio_min

        if not self.enable_volume_footprint or "volume" not in data.columns:
            return wick_ok and range_ok

        recent_vol = data.tail(21)["volume"]
        vol_median = float(recent_vol.iloc[:-1].median()) if len(recent_vol) > 1 else 0.0
        current_vol = float(recent_vol.iloc[-1])
        vol_ok = vol_median <= 0 or current_vol >= vol_median * self.footprint_volume_multiplier
        return wick_ok and range_ok and vol_ok

    def _build_signal(
        self,
        symbol: str,
        direction: Signal,
        data: pd.DataFrame,
        atr: float,
        sweep_level: float,
        fvg_mid: Optional[float],
        ob_target: Optional[float],
    ) -> Optional[TradeSignal]:
        entry = float(data.iloc[-1]["close"])
        min_sl = self._pips_to_price(symbol, self.stop_loss_min_pips)
        max_sl = self._pips_to_price(symbol, self.stop_loss_max_pips)
        tp_min = self._pips_to_price(symbol, self.take_profit_min_pips)

        if direction == Signal.BUY:
            raw_sl_distance = max(min_sl, entry - sweep_level, atr * self.stop_loss_atr_multiplier)
            if max_sl > 0:
                raw_sl_distance = min(raw_sl_distance, max_sl)
            stop_loss = entry - raw_sl_distance

            rr_tp = entry + raw_sl_distance * self.risk_reward
            model_tp = max(rr_tp, entry + tp_min)
            if fvg_mid and fvg_mid > entry:
                model_tp = max(model_tp, fvg_mid)
            if ob_target and ob_target > entry:
                model_tp = max(model_tp, ob_target)
            take_profit = model_tp
            comment = "LF7_BUY"

        elif direction == Signal.SELL:
            raw_sl_distance = max(min_sl, sweep_level - entry, atr * self.stop_loss_atr_multiplier)
            if max_sl > 0:
                raw_sl_distance = min(raw_sl_distance, max_sl)
            stop_loss = entry + raw_sl_distance

            rr_tp = entry - raw_sl_distance * self.risk_reward
            model_tp = min(rr_tp, entry - tp_min)
            if fvg_mid and fvg_mid < entry:
                model_tp = min(model_tp, fvg_mid)
            if ob_target and ob_target < entry:
                model_tp = min(model_tp, ob_target)
            take_profit = model_tp
            comment = "LF7_SELL"

        else:
            return None

        if direction == Signal.BUY and take_profit <= entry:
            return None
        if direction == Signal.SELL and take_profit >= entry:
            return None

        return TradeSignal(
            signal=direction,
            symbol=symbol,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=self.lot_size,
            comment=comment,
            magic_number=self.magic_number,
        )

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

    def _is_trading_time(self, data: pd.DataFrame) -> Tuple[bool, pd.Timestamp]:
        ts = self._to_session_time(data.iloc[-1]["time"])
        if not self.use_time_filter:
            return True, ts

        hour = ts.hour
        weekday = ts.weekday()
        if not self.trade_friday and weekday == 4:
            return False, ts

        if self.start_hour < self.end_hour:
            in_window = self.start_hour <= hour < self.end_hour
        else:
            in_window = hour >= self.start_hour or hour < self.end_hour
        return in_window, ts

    def _reset_daily_counter(self, symbol: str, current_day: date) -> None:
        if self._daily_signal_date.get(symbol) != current_day:
            self._daily_signal_date[symbol] = current_day
            self._daily_signal_count[symbol] = 0
