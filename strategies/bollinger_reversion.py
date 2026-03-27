import pandas as pd
from typing import Dict, Any, Optional
from loguru import logger
from datetime import datetime
from zoneinfo import ZoneInfo

from core.strategy_base import StrategyBase, TradeSignal, Signal, Position
from indicators.common import calculate_bollinger_bands, calculate_rsi
from utils.config import config as env_config

class BollingerReversion(StrategyBase):
    """
    Bollinger Bands + RSI Mean Reversion Strategy for Sideways/Ranging Markets.
    
    Entry Logic:
    - BUY: Price touches/drops below Lower Bollinger Band AND RSI <= Oversold level (Default 30)
    - SELL: Price touches/rises above Upper Bollinger Band AND RSI >= Overbought level (Default 70)
    
    Exit Logic:
    - Take Profit: Middle Bollinger Band (SMA 20) by default, or fixed fallback pips.
    - Stop Loss: Fixed pips based.
    """
    
    name = "Bollinger Reversion"
    version = "1.0.0"
    description = "Mean reversion strategy for sideways markets using Bollinger Bands and RSI"
    author = "AlgoAct"
    
    def __init__(self):
        super().__init__()
        
        # Bollinger Bands Parameters
        self.bb_period = 20
        self.bb_std_dev = 2.0
        
        # RSI Parameters
        self.rsi_period = 14
        self.rsi_overbought = 70.0
        self.rsi_oversold = 30.0
        
        # Risk Parameters
        self.stop_loss_pips = 100.0
        self.take_profit_pips = 300.0  
        self.use_dynamic_tp = True     
        self.use_trailing_stop = False
        self.trailing_pips = 40.0
        
        # Session filters 
        self.use_time_filter = True
        self.start_hour = 2
        self.end_hour = 10
        self.trade_friday = True
        self.session_timezone = "UTC"
        self.data_timezone = "UTC"
        
        self.magic_number = 789555
        
    def initialize(self, config: Dict[str, Any]) -> None:
        self.config = config
        
        params = config.get("parameters", {})
        self.bb_period = params.get("bb_period", 20)
        self.bb_std_dev = params.get("bb_std_dev", 2.0)
        self.rsi_period = params.get("rsi_period", 14)
        self.rsi_overbought = params.get("rsi_overbought", 70.0)
        self.rsi_oversold = params.get("rsi_oversold", 30.0)
        
        risk = config.get("risk", {})
        self.stop_loss_pips = risk.get("stop_loss_pips", 100.0)
        self.take_profit_pips = risk.get("take_profit_pips", 300.0)
        self.use_dynamic_tp = risk.get("use_dynamic_tp", True)
        self.use_trailing_stop = risk.get("trailing_stop", False)
        self.trailing_pips = risk.get("trailing_pips", 40.0)
        self.lot_size = risk.get("lot_size", env_config.trading.default_lot_size)
        
        session = config.get("session", {})
        self.use_time_filter = session.get("use_time_filter", True)
        self.start_hour = session.get("start_hour", 2)
        self.end_hour = session.get("end_hour", 10)
        self.trade_friday = session.get("trade_friday", True)
        self.session_timezone = session.get("timezone", "UTC")
        self.data_timezone = session.get("data_timezone", "UTC")
        
        self.symbols = config.get("symbols", ["XAUUSD"])
        self.timeframe = config.get("timeframe", "M15")
        self.enabled = config.get("enabled", True)
        self.magic_number = config.get("magic_number", 789555)
        
    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        if len(data) < max(self.bb_period, self.rsi_period):
            return None
            
        if self.use_time_filter and not self._is_trading_time(data):
            return None
            
        # Add a safeguard against empty subsets when calculating
        try:
            upper_band, mid_band, lower_band = calculate_bollinger_bands(
                data, period=self.bb_period, std_dev=self.bb_std_dev
            )
            rsi = calculate_rsi(data, period=self.rsi_period)
        except Exception as e:
            return None
            
        current_bar = data.iloc[-1]
        
        current_close = current_bar["close"]
        current_open = current_bar["open"]
        current_low = current_bar["low"]
        current_high = current_bar["high"]
        
        curr_upper = float(upper_band.iloc[-1])
        curr_lower = float(lower_band.iloc[-1])
        curr_mid = float(mid_band.iloc[-1])
        curr_rsi = float(rsi.iloc[-1])
        
        if pd.isna(curr_upper) or pd.isna(curr_rsi):
            return None
            
        # BUY LOGIC: Price poked lower band and RSI is oversold
        if current_low <= curr_lower and curr_rsi <= self.rsi_oversold:
            # Confirm bounce: Price is closing bullish (close > open)
            if current_close > current_open:
                return self._create_signal(
                    Signal.BUY, symbol, current_close, curr_mid
                )
                
        # SELL LOGIC: Price poked upper band and RSI is overbought
        if current_high >= curr_upper and curr_rsi >= self.rsi_overbought:
            # Confirm rejection: Price is closing bearish (close < open)
            if current_close < current_open:
                return self._create_signal(
                    Signal.SELL, symbol, current_close, curr_mid
                )
                
        return None
        
    def _create_signal(self, direction: Signal, symbol: str, entry_price: float, mid_band: float) -> TradeSignal:
        point = 0.01 if "XAU" in symbol else 0.0001
        
        if direction == Signal.BUY:
            stop_loss = entry_price - (self.stop_loss_pips * point * 10)
            if self.use_dynamic_tp:
                take_profit = mid_band
                # If mid band is too close, fallback to fixed pips
                if (take_profit - entry_price) < (30 * point * 10):
                    take_profit = entry_price + (self.take_profit_pips * point * 10)
            else:
                take_profit = entry_price + (self.take_profit_pips * point * 10)
            comment = "BB_Reversion_BUY"
        else:
            stop_loss = entry_price + (self.stop_loss_pips * point * 10)
            if self.use_dynamic_tp:
                take_profit = mid_band
                if (entry_price - take_profit) < (30 * point * 10):
                    take_profit = entry_price - (self.take_profit_pips * point * 10)
            else:
                take_profit = entry_price - (self.take_profit_pips * point * 10)
            comment = "BB_Reversion_SELL"
            
        return TradeSignal(
            signal=direction,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            lot_size=self.lot_size,
            comment=comment,
            magic_number=self.magic_number
        )

    def should_close(self, position: Position, data: pd.DataFrame) -> bool:
        return False
        
    def get_trailing_stop(self, position: Position, data: pd.DataFrame) -> Optional[float]:
        if not self.use_trailing_stop or position.profit <= 0:
            return None
            
        current_price = data.iloc[-1]["close"]
        point = 0.1 if "XAU" in position.symbol else 0.0001
        trail_distance = self.trailing_pips * point * 10
        
        if position.type == Signal.BUY:
            new_sl = current_price - trail_distance
            if new_sl > position.stop_loss and new_sl < current_price:
                return new_sl
        else:
            new_sl = current_price + trail_distance
            if (position.stop_loss == 0 or new_sl < position.stop_loss) and new_sl > current_price:
                return new_sl
        return None
        
    def _is_trading_time(self, data: pd.DataFrame) -> bool:
        current_time = self._to_session_time(data.iloc[-1]["time"])
        hour = current_time.hour if hasattr(current_time, 'hour') else 0
        weekday = current_time.weekday() if hasattr(current_time, 'weekday') else 0
        
        if not self.trade_friday and weekday == 4:
            return False
            
        if self.start_hour < self.end_hour:
            return self.start_hour <= hour < self.end_hour
        else: 
            return hour >= self.start_hour or hour < self.end_hour

    def _to_session_time(self, timestamp):
        ts = pd.Timestamp(timestamp)
        session_tz = ZoneInfo(self.session_timezone)
        data_tz = ZoneInfo(self.data_timezone)
        if ts.tzinfo is None:
            ts = ts.tz_localize(data_tz)
        return ts.tz_convert(session_tz)
