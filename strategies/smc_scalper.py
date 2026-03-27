"""
SMC Scalper Strategy - Smart Money Concepts based scalping.
Converted from XAUUSD_SMC_Scalper.mq5
"""
import pandas as pd
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

from core.strategy_base import StrategyBase, Signal, TradeSignal, Position
from indicators.smc_utils import SMCAnalyzer
from indicators.common import calculate_atr


class SMCScalper(StrategyBase):
    """
    Smart Money Concepts Scalping Strategy.

    Entry Logic:
    - BUY: Bullish CHoCH + Bullish FVG + Bearish Order Block (target)
    - SELL: Bearish CHoCH + Bearish FVG + Bullish Order Block (target)

    Entry at FVG midline, exit at Order Block or R:R target.
    """

    name = "SMC Scalper"
    version = "2.0.0"
    description = "Smart Money Concepts scalping with CHoCH, FVG, and Order Blocks"
    author = "AlgoAct"

    def __init__(self):
        super().__init__()
        self.smc: Optional[SMCAnalyzer] = None

        # Default parameters
        self.choch_lookback = 50
        self.fvg_min_pips = 5.0
        self.fvg_lookback = 20
        self.ob_lookback = 20
        self.risk_reward = 2.0
        self.use_trailing_stop = True
        self.trailing_pips = 50.0
        self.atr_period = 14
        self.atr_multiplier = 2.0
        self.use_atr_sl = True
        self.stop_loss_pips = 100.0

        # Session filter
        self.use_time_filter = True
        self.start_hour = 8
        self.end_hour = 18
        self.trade_friday = False
        self.session_timezone = "UTC"
        self.data_timezone = "UTC"

        self.magic_number = 789123

    def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize strategy with configuration."""
        self.config = config

        # Load parameters
        params = config.get("parameters", {})
        self.choch_lookback = params.get("choch_lookback", 50)
        self.fvg_min_pips = params.get("fvg_min_pips", 5.0)
        self.fvg_lookback = int(params.get("fvg_lookback", 20))
        self.ob_lookback = params.get("ob_lookback", 20)
        self.risk_reward = params.get("risk_reward", 2.0)
        self.use_trailing_stop = params.get("trailing_stop", True)
        self.trailing_pips = params.get("trailing_pips", 50.0)
        self.atr_period = params.get("atr_period", 14)
        self.atr_multiplier = params.get("atr_multiplier", 2.0)
        self.use_atr_sl = params.get("use_atr_sl", True)
        risk = config.get("risk", {})
        self.stop_loss_pips = risk.get("stop_loss_pips", params.get("stop_loss_pips", 100.0))

        # Session settings
        session = config.get("session", {})
        self.use_time_filter = session.get("use_time_filter", True)
        self.start_hour = session.get("start_hour", 8)
        self.end_hour = session.get("end_hour", 18)
        self.trade_friday = session.get("trade_friday", False)
        self.session_timezone = session.get("timezone", "UTC")
        self.data_timezone = session.get("data_timezone", "UTC")

        # Strategy settings
        self.symbols = config.get("symbols", ["XAUUSD"])
        self.timeframe = config.get("timeframe", "M5")
        self.enabled = config.get("enabled", True)
        self.magic_number = config.get("magic_number", 789123)

        # Risk settings
        self.lot_size = float(risk.get("lot_size", 0.0))

        # Initialize SMC analyzer
        symbol_for_point = self.symbols[0] if self.symbols else "XAUUSD"
        point = self._point_for_symbol(symbol_for_point)
        self.smc = SMCAnalyzer(
            swing_lookback=5,
            fvg_min_pips=self.fvg_min_pips,
            ob_displacement_factor=self.atr_multiplier,
            point=point
        )

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        """Analyze market and generate trade signal."""
        if self.smc is None:
            return None
        if len(data) < self.choch_lookback:
            return None

        if not self._is_trading_time(data):
            return None

        # Check for bullish setup
        signal = self._check_bullish_setup(symbol, data)
        if signal:
            return signal

        # Check for bearish setup
        signal = self._check_bearish_setup(symbol, data)
        if signal:
            return signal

        return None

    def _check_bullish_setup(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        """Check for bullish SMC setup."""
        # Detect Bullish CHoCH
        choch = self.smc.detect_bullish_choch(data, self.choch_lookback)
        if not choch:
            return None

        # Detect Bullish FVG
        fvg = self.smc.detect_bullish_fvg(data, self.fvg_lookback)
        if not fvg:
            return None

        # Detect Bearish Order Block (for take profit target)
        ob = self.smc.detect_bearish_order_block(data, self.ob_lookback)
        if not ob:
            return None

        current_price = data.iloc[-1]["close"]

        # Check if price is in FVG zone
        if not (fvg.lower_price <= current_price <= fvg.upper_price):
            # Check if price is close to FVG (within 20 pips)
            max_distance = self._pips_to_price(symbol, 20)
            if abs(current_price - fvg.mid_price) > max_distance:
                return None

        # Entry at FVG midline
        entry_price = fvg.mid_price

        # Calculate stop loss
        stop_loss = self._calculate_stop_loss(data, symbol, entry_price, is_buy=True)

        # Calculate take profit
        take_profit = self._calculate_take_profit(
            symbol, entry_price, stop_loss, ob.lower_price, is_buy=True
        )

        return TradeSignal(
            signal=Signal.BUY,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=self.lot_size,
            comment=f"SMC_BUY_CHoCH_FVG",
            magic_number=self.magic_number
        )

    def _check_bearish_setup(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        """Check for bearish SMC setup."""
        # Detect Bearish CHoCH
        choch = self.smc.detect_bearish_choch(data, self.choch_lookback)
        if not choch:
            return None

        # Detect Bearish FVG
        fvg = self.smc.detect_bearish_fvg(data, self.fvg_lookback)
        if not fvg:
            return None

        # Detect Bullish Order Block (for take profit target)
        ob = self.smc.detect_bullish_order_block(data, self.ob_lookback)
        if not ob:
            return None

        current_price = data.iloc[-1]["close"]

        # Check if price is in FVG zone
        if not (fvg.lower_price <= current_price <= fvg.upper_price):
            max_distance = self._pips_to_price(symbol, 20)
            if abs(current_price - fvg.mid_price) > max_distance:
                return None

        # Entry at FVG midline
        entry_price = fvg.mid_price

        # Calculate stop loss
        stop_loss = self._calculate_stop_loss(data, symbol, entry_price, is_buy=False)

        # Calculate take profit
        take_profit = self._calculate_take_profit(
            symbol, entry_price, stop_loss, ob.upper_price, is_buy=False
        )

        return TradeSignal(
            signal=Signal.SELL,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=self.lot_size,
            comment=f"SMC_SELL_CHoCH_FVG",
            magic_number=self.magic_number
        )

    def _calculate_stop_loss(
        self,
        data: pd.DataFrame,
        symbol: str,
        entry_price: float,
        is_buy: bool
    ) -> float:
        """Calculate stop loss based on ATR or fixed pips."""
        if self.use_atr_sl and len(data) >= self.atr_period:
            atr = calculate_atr(data, self.atr_period)
            atr_value = atr.iloc[-1] * self.atr_multiplier

            if is_buy:
                return entry_price - atr_value
            else:
                return entry_price + atr_value
        else:
            sl_distance = self._pips_to_price(symbol, self.stop_loss_pips)

            if is_buy:
                return entry_price - sl_distance
            else:
                return entry_price + sl_distance

    def _calculate_take_profit(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        order_block_level: float,
        is_buy: bool
    ) -> float:
        """Calculate take profit based on order block or R:R ratio."""
        stop_distance = abs(entry_price - stop_loss)

        # Try to use order block level
        if order_block_level > 0:
            if is_buy and order_block_level > entry_price:
                distance_to_ob = order_block_level - entry_price
                # Check if OB provides at least 1.5:1 RR
                if distance_to_ob >= stop_distance * 1.5:
                    # Target 10 pips before order block
                    return order_block_level - self._pips_to_price(symbol, 10)

            elif not is_buy and order_block_level < entry_price:
                distance_to_ob = entry_price - order_block_level
                if distance_to_ob >= stop_distance * 1.5:
                    return order_block_level + self._pips_to_price(symbol, 10)

        # Fall back to fixed R:R ratio
        if is_buy:
            return entry_price + (stop_distance * self.risk_reward)
        else:
            return entry_price - (stop_distance * self.risk_reward)

    def should_close(self, position: Position, data: pd.DataFrame) -> bool:
        """Check if position should be closed."""
        # This strategy primarily uses SL/TP for exits
        # Additional exit logic can be added here
        return False

    def get_trailing_stop(self, position: Position, data: pd.DataFrame) -> Optional[float]:
        """Calculate trailing stop."""
        if not self.use_trailing_stop:
            return None

        if position.profit <= 0:
            return None

        current_price = data.iloc[-1]["close"]
        trail_distance = self._pips_to_price(position.symbol, self.trailing_pips)

        if position.type == Signal.BUY:
            new_sl = current_price - trail_distance
            if new_sl > position.stop_loss and new_sl < current_price:
                return new_sl

        else:  # SELL
            new_sl = current_price + trail_distance
            if (position.stop_loss == 0 or new_sl < position.stop_loss) and new_sl > current_price:
                return new_sl

        return None

    def _is_trading_time(self, data: pd.DataFrame) -> bool:
        """Check if current time is within trading session."""
        if not self.use_time_filter:
            return True

        current_time = self._to_session_time(data.iloc[-1]["time"])

        if isinstance(current_time, pd.Timestamp):
            hour = current_time.hour
            weekday = current_time.weekday()
        else:
            hour = current_time.hour
            weekday = current_time.weekday() if hasattr(current_time, "weekday") else 0

        # Friday check
        if not self.trade_friday and weekday == 4:
            return False

        # Hour check (supports overnight windows)
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour

    def _point_for_symbol(self, symbol: str) -> float:
        if "XAU" in symbol or "GOLD" in symbol:
            return 0.01
        return 0.0001

    def _pips_to_price(self, symbol: str, pips: float) -> float:
        return pips * self._point_for_symbol(symbol) * 10

    def _to_session_time(self, timestamp):
        ts = pd.Timestamp(timestamp)
        session_tz = ZoneInfo(self.session_timezone)
        data_tz = ZoneInfo(self.data_timezone)
        if ts.tzinfo is None:
            ts = ts.tz_localize(data_tz)
        return ts.tz_convert(session_tz)
