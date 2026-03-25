import pandas as pd
from typing import Dict, Any, Optional
from loguru import logger

from core.strategy_base import StrategyBase, TradeSignal, Signal
from utils.config import config as env_config

class ForceOrderTest(StrategyBase):
    """
    Test Strategy to force open a single LIVE order 
    immediately upon loading to verify MT5 order execution.
    """
    
    name = "Force Order Test"
    
    def __init__(self):
        super().__init__()
        self.fired = False
        self.lot_size = 0.01

    def initialize(self, config: Dict[str, Any]) -> None:
        self.lot_size = config.get("parameters", {}).get("lot_size", env_config.trading.default_lot_size)
        self.enabled = config.get("enabled", True)

    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        # Only fire once per lifecycle
        if not self.fired and not data.empty:
            logger.warning(f"🚀 FORCE ORDER TEST: Firing automated BUY order on {symbol} (Lot: {self.lot_size})")
            self.fired = True
            
            last_close = float(data.iloc[-1]['close'])
            
            return TradeSignal(
                symbol=symbol,
                signal=Signal.BUY,
                entry_price=last_close,
                take_profit=last_close + 5.0, 
                stop_loss=last_close - 5.0,
                lot_size=self.lot_size
            )
            
        return None

    def should_close(self, position, data: pd.DataFrame) -> bool:
        return False
