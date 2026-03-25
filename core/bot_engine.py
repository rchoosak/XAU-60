import pandas as pd
from typing import List, Dict, Any, Optional
from loguru import logger
from .strategy_base import StrategyBase, Signal, TradeSignal, Position

class BotEngine:
    """
    Multi-strategy Bot Engine.
    Handles loading multiple strategies, aggregating signals, and risk management.
    """

    def __init__(self, strategies: List[StrategyBase], aggregation: str = "majority"):
        """
        Initialize Bot Engine.
        
        Args:
            strategies: List of initialized strategies
            aggregation: Signal aggregation logic ('majority', 'weighted', 'priority')
        """
        self.strategies = strategies
        self.aggregation = aggregation
        self.max_positions = 3
        self.risk_per_trade = 0.01 # 1% of balance
        
    def analyze(self, symbol: str, data: pd.DataFrame) -> Optional[TradeSignal]:
        """
        Call analyze() on each strategy and aggregate signals.
        """
        signals = []
        for strategy in self.strategies:
            try:
                sig = strategy.analyze(symbol, data)
                if sig and sig.signal != Signal.HOLD:
                    # Mocking confidence if not present in existing TradeSignal
                    # In a real scenario, TradeSignal might need an update or a wrapper
                    signals.append({
                        "strategy": strategy.name,
                        "signal": sig.signal,
                        "confidence": 1.0, # Default for now
                        "sl": sig.stop_loss,
                        "tp": sig.take_profit,
                        "lot_size": sig.lot_size
                    })
            except Exception as e:
                logger.error(f"Error in strategy {strategy.name}: {e}")

        if not signals:
            return None

        return self._aggregate(signals, symbol)

    def _aggregate(self, signals: List[Dict], symbol: str) -> Optional[TradeSignal]:
        """Aggregate signals based on chosen logic."""
        if self.aggregation == "majority":
            return self._aggregate_majority(signals, symbol)
        elif self.aggregation == "weighted":
            return self._aggregate_weighted(signals, symbol)
        elif self.aggregation == "priority":
            return self._aggregate_priority(signals, symbol)
        else:
            logger.error(f"Unknown aggregation: {self.aggregation}")
            return None

    def _aggregate_majority(self, signals: List[Dict], symbol: str) -> Optional[TradeSignal]:
        buy_count = sum(1 for s in signals if s["signal"] == Signal.BUY)
        sell_count = sum(1 for s in signals if s["signal"] == Signal.SELL)
        
        if buy_count > sell_count:
            # Take the first BUY signal's SL/TP as a simple heuristic
            first_buy = next(s for s in signals if s["signal"] == Signal.BUY)
            return TradeSignal(
                signal=Signal.BUY,
                symbol=symbol,
                entry_price=0.0, # Will be set by backtester
                stop_loss=first_buy["sl"],
                take_profit=first_buy["tp"],
                lot_size=first_buy["lot_size"],
                comment=f"Bot Aggregated (Majority Buy: {buy_count}/{len(signals)})"
            )
        elif sell_count > buy_count:
            first_sell = next(s for s in signals if s["signal"] == Signal.SELL)
            return TradeSignal(
                signal=Signal.SELL,
                symbol=symbol,
                entry_price=0.0,
                stop_loss=first_sell["sl"],
                take_profit=first_sell["tp"],
                lot_size=first_sell["lot_size"],
                comment=f"Bot Aggregated (Majority Sell: {sell_count}/{len(signals)})"
            )
        
        return None

    def _aggregate_weighted(self, signals: List[Dict], symbol: str) -> Optional[TradeSignal]:
        # Simple weighted (confidence * signal_value)
        total_weight = 0
        for s in signals:
            val = 1 if s["signal"] == Signal.BUY else -1
            total_weight += val * s["confidence"]
            
        if abs(total_weight) > 0.5: # Threshold
            direction = Signal.BUY if total_weight > 0 else Signal.SELL
            # Find closest signal to the winning direction
            best_s = next(s for s in signals if s["signal"] == direction)
            return TradeSignal(
                signal=direction,
                symbol=symbol,
                entry_price=0.0,
                stop_loss=best_s["sl"],
                take_profit=best_s["tp"],
                lot_size=best_s["lot_size"],
                comment=f"Bot Aggregated (Weighted: {total_weight:.2f})"
            )
        return None

    def _aggregate_priority(self, signals: List[Dict], symbol: str) -> Optional[TradeSignal]:
        # Simply take the first strategy's signal as priority
        s = signals[0]
        return TradeSignal(
            signal=s["signal"],
            symbol=symbol,
            entry_price=0.0,
            stop_loss=s["sl"],
            take_profit=s["tp"],
            lot_size=s["lot_size"],
            comment=f"Bot Aggregated (Priority: {s['strategy']})"
        )
