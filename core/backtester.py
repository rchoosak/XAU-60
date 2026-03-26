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
    Expanded Local Backtesting Engine.
    Supports advanced risk management, position control, and csv exports.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        
        # Primary Parameters
        self.initial_balance = self.config.get("initial_balance", 10000.0)
        self.spread_pips = self.config.get("spread_pips", 2.0)
        self.commission = self.config.get("commission", 7.0) # per lot
        self.leverage = self.config.get("leverage", 100.0)
        self.slippage = self.config.get("slippage", 0.0001)
        self.data_dir = self.config.get("data_path", "data/backtest-db")
        
        # Risk Parameters
        self.risk_per_trade = self.config.get("risk_per_trade", 0.01)
        self.max_open_trades = self.config.get("max_open_trades", 3)
        self.default_lot_size = self.config.get("lot_size", 0.1)
        
        # Position Management
        self.use_trailing_stop = self.config.get("use_trailing_stop", True)
        self.trailing_stop_pips = self.config.get("trailing_stop_pips", 50.0)
        self.tp_multiplier = self.config.get("tp_multiplier", 1.0)
        self.sl_multiplier = self.config.get("sl_multiplier", 1.0)
        
        # Execution / Data
        self.lookback = self.config.get("lookback", 100)
        self.warmup = self.config.get("warmup", 200)
        
        # Logging / Output
        self.save_trades = self.config.get("save_trades", True)
        self.output_csv = self.config.get("output", "trades.csv")
        self.log_signals = self.config.get("log_signals", True)

    def load_data(self, symbol: str, timeframe: str, start: Optional[datetime] = None, end: Optional[datetime] = None) -> pd.DataFrame:
        """Load data from file or directory, with optional date filtering."""
        if os.path.isfile(self.data_dir):
            path = self.data_dir
        else:
            path = os.path.join(self.data_dir, f"{symbol}_{timeframe}.parquet")
            
        if not os.path.exists(path):
            raise FileNotFoundError(f"Data file not found: {path}")
        
        df = pd.read_parquet(path)
        if "time" not in df.columns:
            # Fallback for older formats if any
            if "timestamp" in df.columns:
                df = df.rename(columns={"timestamp": "time"})
            else:
                raise ValueError("Data file must contain a 'time' or 'timestamp' column")

        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time")
        
        if start:
            df = df[df["time"] >= start]
        if end:
            df = df[df["time"] <= end]
            
        return df

    def run(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime],
        executor: Union[StrategyBase, BotEngine],
        mode: str = "real"
    ) -> Dict[str, Any]:
        """
        Run the backtest loop with expanded features.
        """
        if mode == "real":
            data = self.load_data(symbol, timeframe, start, end)
        else:
            # For random data, we still need dates, so use defaults if None
            r_start = start or datetime(2024, 1, 1)
            r_end = end or datetime.now()
            data = self._generate_random_data(r_start, r_end, timeframe)

        if data.empty:
            return {"error": "No data found"}

        logger.info(f"Starting {mode} backtest for {symbol} {timeframe}")
        
        balance = self.initial_balance
        trades = []
        positions: List[Dict] = []
        equity_curve = []
        
        # Point calculations
        point = 0.0001 if "JPY" not in symbol else 0.01
        if "XAU" in symbol: point = 0.01
        spread = self.spread_pips * point

        for i in range(self.warmup, len(data)):
            candle = data.iloc[i]
            history = data.iloc[max(0, i - self.lookback):i+1]
            
            # 1. Update existing positions & Trailing Stops
            for pos in positions[:]:
                # Trailing stop logic
                if self.use_trailing_stop:
                    pos = self._update_trailing_stop(pos, candle, point)
                
                exit_price, reason = self._check_exit(pos, candle, executor, history)
                if exit_price:
                    closed_trade = self._close_position(pos, exit_price, reason, symbol, point)
                    balance += closed_trade["profit"]
                    trades.append(closed_trade)
                    positions.remove(pos)

            # 2. Check for new signals (Risk: Max positions)
            if len(positions) < self.max_open_trades:
                sig = executor.analyze(symbol, history)
                if sig and sig.signal != Signal.HOLD:
                    # Risk Management: Lot Size Calculation
                    lot_size = self._calculate_lot_size(balance, sig, point, candle["close"], positions, symbol)
                    
                    entry_price = candle["close"] + (spread if sig.signal == Signal.BUY else -spread)
                    
                    # Apply multipliers
                    sl = sig.stop_loss
                    tp = sig.take_profit
                    if self.sl_multiplier != 1.0:
                        risk = abs(entry_price - sl)
                        sl = entry_price - (risk * self.sl_multiplier) if sig.signal == Signal.BUY else entry_price + (risk * self.sl_multiplier)
                    if self.tp_multiplier != 1.0:
                        reward = abs(tp - entry_price)
                        tp = entry_price + (reward * self.tp_multiplier) if sig.signal == Signal.BUY else entry_price - (reward * self.tp_multiplier)

                    positions.append({
                        "signal": sig.signal,
                        "entry_price": entry_price,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "entry_time": candle["time"],
                        "lot_size": lot_size,
                        "high_water_mark": entry_price
                    })
                    if self.log_signals:
                        logger.debug(f"Signal: {sig.signal.name} at {entry_price}")

            equity_curve.append(balance)

        results = self._calculate_metrics(trades, balance, equity_curve)
        
        if self.save_trades and trades:
            pd.DataFrame(trades).to_csv(self.output_csv, index=False)
            logger.info(f"Trades saved to {self.output_csv}")
            
        return results

    def _calculate_lot_size(self, balance: float, sig: TradeSignal, point: float, current_price: float, positions: List[Dict], symbol: str) -> float:
        """Risk per trade % based lot calculation with leverage/margin limit."""
        if sig.stop_loss == 0 or sig.entry_price == sig.stop_loss:
            return self.default_lot_size
        
        # 1. Calculate risk-based lot
        risk_amount = balance * self.risk_per_trade
        sl_pips = abs(sig.entry_price - sig.stop_loss) / point
        if sl_pips == 0: return self.default_lot_size
        
        # 1 lot, 1 pip Gold = $10 (0.1 point), Forex = $10 (0.0001)
        lot = risk_amount / (sl_pips * 10)
        
        # 2. Leverage/Margin Check
        # Contract size (Standard: 100,000 for Forex, 100 for Gold)
        contract_size = 100 if "XAU" in symbol else 100000
        
        # Current margin used by open positions
        used_margin = 0
        for pos in positions:
            # margin = (price * contract_size * lots) / leverage
            used_margin += (pos["entry_price"] * contract_size * pos["lot_size"]) / self.leverage
        
        free_margin = balance - used_margin
        
        # Max lot allowed by free margin (at 100% margin usage)
        # lots = (margin * leverage) / (price * contract_size)
        max_lot_margin = (free_margin * self.leverage) / (current_price * contract_size)
        
        # Cap lot size based on margin (leave 10% buffer for spread/volatility)
        final_lot = min(lot, max_lot_margin * 0.9)
        
        if final_lot < 0.01:
            return 0.01 # minimum lot
            
        return round(final_lot, 2)

    def _update_trailing_stop(self, pos: Dict, candle: pd.Series, point: float) -> Dict:
        trail_dist = self.trailing_stop_pips * point
        if pos["signal"] == Signal.BUY:
            if candle["close"] > pos["high_water_mark"]:
                pos["high_water_mark"] = candle["close"]
                new_sl = candle["close"] - trail_dist
                pos["stop_loss"] = max(pos["stop_loss"], new_sl)
        else:
            if candle["close"] < pos["high_water_mark"]:
                pos["high_water_mark"] = candle["close"]
                new_sl = candle["close"] + trail_dist
                pos["stop_loss"] = min(pos["stop_loss"], new_sl) if pos["stop_loss"] != 0 else new_sl
        return pos

    def _check_exit(self, pos: Dict, candle: pd.Series, executor: Any, history: pd.DataFrame) -> Tuple[Optional[float], str]:
        if pos["signal"] == Signal.BUY:
            if candle["low"] <= pos["stop_loss"]: return pos["stop_loss"], "SL"
            if candle["high"] >= pos["take_profit"] and pos["take_profit"] != 0: return pos["take_profit"], "TP"
        else:
            if candle["high"] >= pos["stop_loss"]: return pos["stop_loss"], "SL"
            if candle["low"] <= pos["take_profit"] and pos["take_profit"] != 0: return pos["take_profit"], "TP"
        
        # Strategy override
        if hasattr(executor, "should_close"):
            mock_pos = Position(0, "", pos["signal"], pos["lot_size"], pos["entry_price"], pos["stop_loss"], pos["take_profit"], 0, 0, "", pos["entry_time"])
            if executor.should_close(mock_pos, history):
                return candle["close"], "Strategy"
        return None, ""

    def _close_position(self, pos: Dict, exit_price: float, reason: str, symbol: str, point: float) -> Dict:
        is_buy = pos["signal"] == Signal.BUY
        pips = (exit_price - pos["entry_price"]) / point if is_buy else (pos["entry_price"] - exit_price) / point
        multiplier = 100 if "XAU" in symbol else 100000
        profit = (pips * point) * pos["lot_size"] * multiplier
        profit -= self.commission * pos["lot_size"]
        
        return {
            "entry_time": pos["entry_time"],
            "exit_time": datetime.now(), # In real backtest use candle["time"]
            "signal": pos["signal"].name,
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "lot_size": pos["lot_size"],
            "profit": round(profit, 2),
            "reason": reason
        }

    def _calculate_metrics(self, trades: List[Dict], balance: float, equity: List[float]) -> Dict[str, Any]:
        if not trades: return {"profit": 0, "trades": 0}
        df = pd.DataFrame(trades)
        wins = df[df["profit"] > 0]
        return {
            "initial_balance": self.initial_balance,
            "final_balance": round(balance, 2),
            "net_profit": round(balance - self.initial_balance, 2),
            "total_trades": len(trades),
            "win_rate": f"{(len(wins)/len(trades))*100:.1f}%",
            "max_drawdown": self._get_max_dd(equity),
            "profit_factor": round(df[df["profit"] > 0]["profit"].sum() / abs(df[df["profit"] < 0]["profit"].sum()), 2) if any(df["profit"] < 0) else float('inf')
        }

    def _get_max_dd(self, equity: List[float]) -> str:
        peak = self.initial_balance
        max_dd = 0
        for eq in equity:
            if eq > peak: peak = eq
            dd = peak - eq
            if dd > max_dd: max_dd = dd
        return f"${max_dd:.2f}"

    def _generate_random_data(self, start: datetime, end: datetime, timeframe: str) -> pd.DataFrame:
        vol = self.config.get("random_volatility", 0.01)
        freq = "15min" if "15" in timeframe else "5min"
        dates = pd.date_range(start, end, freq=freq)
        prices = 2000.0 + np.cumsum(np.random.normal(0, vol * 2000, len(dates)))
        df = pd.DataFrame({"time": dates, "open": prices, "close": prices + np.random.normal(0, vol, len(dates))})
        df["high"] = df[["open", "close"]].max(axis=1) + np.abs(np.random.normal(0, vol, len(dates)))
        df["low"] = df[["open", "close"]].min(axis=1) - np.abs(np.random.normal(0, vol, len(dates)))
        df["volume"] = np.random.randint(100, 1000, len(df))
        return df
