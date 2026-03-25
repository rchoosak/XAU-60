import pandas as pd
from typing import List, Dict, Any, Optional
from loguru import logger

from .strategy_base import StrategyBase, TradeSignal, Signal

class BotEngine:
    """
    Multi-strategy orchestration engine for backtesting.
    Supports Weighted, Majority, and Priority aggregation.
    """
    
    def __init__(self, strategies: List[StrategyBase], config: Optional[Dict[str, Any]] = None):
        self.strategies = strategies
        self.config = config or {}
        
        # Aggregation Parameters
        self.aggregation_type = self.config.get("aggregation", "majority") # majority, weighted, priority
        self.weights = self.config.get("strategy_weights", {})             # { "RSI": 0.5, ... }
        self.priority = self.config.get("strategy_priority", [])           # [ "bollinger_reversion", ... ]
        
        # Basic Risk Management
        self.max_exposure_per_symbol = self.config.get("max_exposure_per_symbol", 0.5)
        
    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        signals = []
        for strategy in self.strategies:
            sig = strategy.analyze(symbol, data)
            if sig:
                signals.append((strategy, sig))
                
        if not signals:
            return None
            
        if self.aggregation_type == "priority":
            return self._aggregate_priority(signals)
        elif self.aggregation_type == "weighted":
            return self._aggregate_weighted(signals)
        else:
            return self._aggregate_majority(signals)

    def _aggregate_majority(self, signals: List[tuple]) -> Optional[TradeSignal]:
        buys = [s for f, s in signals if s.signal == Signal.BUY]
        sells = [s for f, s in signals if s.signal == Signal.SELL]
        
        if len(buys) > len(signals) / 2:
            return buys[0]
        if len(sells) > len(signals) / 2:
            return sells[0]
            
        return None

    def _aggregate_weighted(self, signals: List[tuple]) -> Optional[TradeSignal]:
        buy_weight = 0.0
        sell_weight = 0.0
        
        for strat, sig in signals:
            # Use formal name or class name for weight lookup
            name = getattr(strat, "name", strat.__class__.__name__)
            weight = self.weights.get(name, 1.0)
            
            if sig.signal == Signal.BUY:
                buy_weight += weight
            elif sig.signal == Signal.SELL:
                sell_weight += weight
                
        if buy_weight > sell_weight and buy_weight >= 1.0:
            # Find the strongest signal in the direction
            return [s for f, s in signals if s.signal == Signal.BUY][0]
        if sell_weight > buy_weight and sell_weight >= 1.0:
            return [s for f, s in signals if s.signal == Signal.SELL][0]
            
        return None

    def _aggregate_priority(self, signals: List[tuple]) -> Optional[TradeSignal]:
        # Sort by priority list
        # priority: ["bollinger_reversion", "smc_scalper"]
        
        for p_name in self.priority:
            for strat, sig in signals:
                # Check formal name and module name
                name = getattr(strat, "name", "").lower().replace(" ", "_")
                if name == p_name or strat.__class__.__name__.lower() == p_name:
                    return sig
                    
        # Fallback to the first signal found if no priority matches
        return signals[0][1]

    def should_close(self, position: Any, data: pd.DataFrame) -> bool:
        """Close if majority of strategies think we should close."""
        close_votes = 0
        for strategy in self.strategies:
            if hasattr(strategy, "should_close") and strategy.should_close(position, data):
                close_votes += 1
        return close_votes > len(self.strategies) / 2
