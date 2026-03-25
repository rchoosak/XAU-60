import pandas as pd
import numpy as np
import os
from typing import List, Dict, Any, Optional, Tuple, Union
from loguru import logger
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor

from .strategy_base import StrategyBase, Signal, TradeSignal, Position
from .bot_engine import BotEngine

class Backtester:
    """
    Local Parquet-based Backtesting Engine.
    Works fully offline using data pre-synced to parquet files.
    """

    def __init__(
        self,
        initial_balance: float = 10000.0,
        spread_pips: float = 2.0,
        commission_per_lot: float = 0.0,
        data_dir: str = "data/backtest-db"
    ):
        self.initial_balance = initial_balance
        self.spread_pips = spread_pips
        self.commission_per_lot = commission_per_lot
        self.data_dir = data_dir

    def load_data(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Load data from parquet and filter by date."""
        path = os.path.join(self.data_dir, f"{symbol}_{timeframe}.parquet")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Data file not found: {path}. Run sync_data.py first.")
        
        df = pd.read_parquet(path)
        df["time"] = pd.to_datetime(df["time"])
        mask = (df["time"] >= start) & (df["time"] <= end)
        return df.loc[mask].sort_values("time")

    def run(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        executor: Union[StrategyBase, BotEngine],
        mode: str = "real"
    ) -> Dict[str, Any]:
        """
        Run the backtest loop.
        
        Args:
            symbol: Trading symbol
            timeframe: Timeframe
            start: Start date
            end: End date
            executor: A strategy or a BotEngine
            mode: 'real' (parquet) or 'random'
        """
        if mode == "real":
            data = self.load_data(symbol, timeframe, start, end)
        else:
            data = self._generate_random_data(start, end, timeframe)

        if data.empty:
            logger.warning(f"No data for {symbol} {timeframe}")
            return {}

        logger.info(f"Starting {mode} backtest for {symbol} {timeframe}")
        
        balance = self.initial_balance
        trades = []
        open_position: Optional[Dict] = None
        equity_curve = []
        
        # Pip calculations (assuming standard 4/5 digit forex for now)
        point = 0.0001 if "JPY" not in symbol else 0.01
        if "XAU" in symbol: point = 0.01
        pip_value = 10 * point
        spread = self.spread_pips * point

        # warm up periods
        warm_up = 100
        
        for i in range(warm_up, len(data)):
            candle = data.iloc[i]
            # Optimization: only slice the last 500 bars for indicators
            history_start = max(0, i - 500)
            history = data.iloc[history_start:i+1]
            
            # 1. Manage existing positions
            if open_position:
                exit_price, reason = self._check_exit(open_position, candle, executor, history)
                if exit_price:
                    # Close trade
                    is_buy = open_position["signal"] == Signal.BUY
                    if is_buy:
                        profit_pips = (exit_price - open_position["entry_price"]) / point
                    else:
                        profit_pips = (open_position["entry_price"] - exit_price) / point
                    
                    # Simple P&L: pips * volume * contract_size (approx)
                    # For XAUUSD: 1 lot, 1 pip ($0.01) = $1
                    # For standard Forex: 1 lot, 1 pip ($0.0001) = $10
                    multiplier = 100 if "XAU" in symbol else 100000
                    profit = (profit_pips * point) * open_position["lot_size"] * multiplier
                    profit -= self.commission_per_lot * open_position["lot_size"]
                    
                    balance += profit
                    trades.append({
                        "entry_time": open_position["entry_time"],
                        "exit_time": candle["time"],
                        "signal": open_position["signal"],
                        "entry_price": open_position["entry_price"],
                        "exit_price": exit_price,
                        "profit": profit,
                        "reason": reason
                    })
                    open_position = None

            # 2. Check for new signals
            if not open_position:
                sig = executor.analyze(symbol, history)
                if sig and sig.signal != Signal.HOLD:
                    entry_price = candle["close"] + (spread if sig.signal == Signal.BUY else -spread)
                    open_position = {
                        "signal": sig.signal,
                        "entry_price": entry_price,
                        "stop_loss": sig.stop_loss,
                        "take_profit": sig.take_profit,
                        "entry_time": candle["time"],
                        "lot_size": sig.lot_size or 0.1
                    }
            
            equity_curve.append(balance)

        return self._calculate_metrics(trades, balance, equity_curve)

    def _check_exit(self, pos: Dict, candle: pd.Series, executor: Any, history: pd.DataFrame) -> Tuple[Optional[float], str]:
        """Triggered on every candle/event."""
        # Simple SL/TP check using high/low
        if pos["signal"] == Signal.BUY:
            if candle["low"] <= pos["stop_loss"]:
                return pos["stop_loss"], "SL"
            if candle["high"] >= pos["take_profit"]:
                return pos["take_profit"], "TP"
        else:
            if candle["high"] >= pos["stop_loss"]:
                return pos["stop_loss"], "SL"
            if candle["low"] <= pos["take_profit"]:
                return pos["take_profit"], "TP"
        
        # Strategy/Bot check
        if hasattr(executor, "should_close"):
            # Mock Position object for StrategyBase interface
            mock_pos = Position(
                ticket=0, symbol="", type=pos["signal"], volume=pos["lot_size"],
                open_price=pos["entry_price"], stop_loss=pos["stop_loss"],
                take_profit=pos["take_profit"], profit=0, magic_number=0,
                comment="", open_time=pos["entry_time"]
            )
            if executor.should_close(mock_pos, history):
                return candle["close"], "Strategy Exit"

        return None, ""

    def _calculate_metrics(self, trades: List[Dict], final_balance: float, equity_curve: List[float]) -> Dict[str, Any]:
        if not trades:
            return {"total_trades": 0, "profit": 0}

        profits = [t["profit"] for t in trades]
        win_rate = (sum(1 for p in profits if p > 0) / len(trades)) * 100
        
        # Dropdown
        peak = self.initial_balance
        max_dd = 0
        current_equity = self.initial_balance
        for eq in equity_curve:
            if eq > peak: peak = eq
            dd = peak - eq
            if dd > max_dd: max_dd = dd

        return {
            "initial_balance": self.initial_balance,
            "final_balance": final_balance,
            "net_profit": final_balance - self.initial_balance,
            "total_trades": len(trades),
            "win_rate": f"{win_rate:.2f}%",
            "max_drawdown": f"${max_dd:.2f}",
            "profit_factor": sum(p for p in profits if p > 0) / abs(sum(p for p in profits if p < 0)) if sum(p for p in profits if p < 0) != 0 else float('inf')
        }

    def _generate_random_data(self, start: datetime, end: datetime, timeframe: str) -> pd.DataFrame:
        """Helper for Random Mode."""
        logger.info("Generating random OHLCV data...")
        dates = pd.date_range(start, end, freq="15min") # Simplified
        df = pd.DataFrame(index=dates)
        df["open"] = 2000.0 + np.cumsum(np.random.normal(0, 5, len(df)))
        df["high"] = df["open"] + np.abs(np.random.normal(0, 2, len(df)))
        df["low"] = df["open"] - np.abs(np.random.normal(0, 2, len(df)))
        df["close"] = df["open"] + np.random.normal(0, 2, len(df))
        df["time"] = df.index
        df["volume"] = np.random.randint(100, 1000, len(df))
        return df.reset_index(drop=True)
