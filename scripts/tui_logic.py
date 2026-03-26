import random
from datetime import datetime, timedelta
import pandas as pd
from typing import List, Dict, Any, Optional

class DataSimulator:
    """Simulates real-time OHLC data."""
    def __init__(self, start_price: float = 2000.0, volatility: float = 0.5):
        self.current_price = start_price
        self.volatility = volatility
        self.history = []
        self.current_sim_time = datetime(2024, 3, 26, 9, 0) # Start of trading day
        # Pre-populate some history
        for _ in range(50):
            self.tick()

    def tick(self) -> Dict[str, Any]:
        """Generate one new candle."""
        open_p = self.current_price
        high_p = open_p + random.uniform(0, self.volatility * 2)
        low_p = open_p - random.uniform(0, self.volatility * 2)
        close_p = random.uniform(low_p, high_p)
        
        candle = {
            "time": self.current_sim_time,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": random.randint(100, 1000)
        }
        self.current_sim_time += timedelta(minutes=1)
        self.current_price = close_p
        self.history.append(candle)
        if len(self.history) > 100:
            self.history.pop(0)
        return candle

class PositionManager:
    """Tracks open positions and calculates PnL."""
    def __init__(self, initial_balance: float = 1000.0):
        self.balance = initial_balance
        self.equity = initial_balance
        self.open_positions = []
        self.closed_trades = []

    def open_position(self, type: str, price: float, lot: float = 0.1):
        pos = {
            "id": random.randint(1000, 9999),
            "type": type,
            "entry_price": price,
            "current_price": price,
            "lot": lot,
            "pnl_pct": 0.0,
            "pnl_usd": 0.0
        }
        self.open_positions.append(pos)

    def update(self, current_price: float):
        total_pnl = 0.0
        for pos in self.open_positions:
            pos["current_price"] = current_price
            # Gold approximation: 1 lot, 1usd move = 100usd profit
            multiplier = 100
            if pos["type"] == "BUY":
                pnl = (current_price - pos["entry_price"]) * pos["lot"] * multiplier
            else:
                pnl = (pos["entry_price"] - current_price) * pos["lot"] * multiplier
                
            pos["pnl_usd"] = round(pnl, 2)
            pos["pnl_pct"] = round((pnl / (self.balance * 0.1)) * 100, 2) # Arbitrary risk basis
            total_pnl += pnl
        
        self.equity = round(self.balance + total_pnl, 2)

    def close_all(self):
        for pos in self.open_positions:
            self.balance += pos["pnl_usd"]
            self.closed_trades.append(pos)
        self.open_positions = []
        self.equity = self.balance

class SignalManager:
    """Manages signal history."""
    def __init__(self, max_history: int = 100):
        self.history = []
        self.max_history = max_history

    def add_signal(self, type: str, price: float):
        signal = {
            "time": datetime.now(),
            "type": type,
            "price": price,
            "result": random.choice([f"+{random.uniform(0.1, 2.5):.2f}%", f"-{random.uniform(0.1, 1.5):.2f}%"])
        }
        self.history.insert(0, signal)
        if len(self.history) > self.max_history:
            self.history.pop()
