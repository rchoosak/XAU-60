# core/bot_engine.py (1-95/95)
     1 | import pandas as pd
     2 | from typing import List, Dict, Any, Optional
     3 | from loguru import logger
     4 | 
     5 | from .strategy_base import StrategyBase, TradeSignal, Signal
     6 | 
     7 | class BotEngine:
     8 |     """
     9 |     Multi-strategy orchestration engine for backtesting.
    10 |     Supports Weighted, Majority, and Priority aggregation.
    11 |     """
    12 |     
    13 |     def __init__(self, strategies: List[StrategyBase], config: Optional[Dict[str, Any]] = None):
    14 |         self.strategies = strategies
    15 |         self.config = config or {}
    16 |         
    17 |         # Aggregation Parameters
    18 |         self.aggregation_type = self.config.get("aggregation", "majority") # majority, weighted, priority
    19 |         self.weights = self.config.get("strategy_weights", {})             # { "RSI": 0.5, ... }
    20 |         self.priority = self.config.get("strategy_priority", [])           # [ "bollinger_reversion", ... ]
    21 |         
    22 |         # Basic Risk Management
    23 |         self.max_exposure_per_symbol = self.config.get("max_exposure_per_symbol", 0.5)
    24 |         
    25 |     def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
    26 |         if not self.strategies:
            logger.warning("No strategies configured for analysis")
            return None
            
        signals = []
        for strategy in self.strategies:
            try:
                sig = strategy.analyze(symbol, data)
                if sig:
                    signals.append((strategy, sig))
            except Exception as e:
                logger.error(f"Strategy {strategy.__class__.__name__} failed analysis: {e}")
                
        if not signals:
            return None
            
        if self.aggregation_type == "priority":
            return self._aggregate_priority(signals)
        elif self.aggregation_type == "weighted":
            return self._aggregate_weighted(signals)
        elif self.aggregation_type == "majority":
            return self._aggregate_majority(signals)
        else:
            logger.error(f"Unknown aggregation type: {self.aggregation_type}. Defaulting to majority.")
            return self._aggregate_majority(signals)