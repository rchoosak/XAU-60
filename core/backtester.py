import pandas as pd
import numpy as np
import os
from typing import List, Dict, Any, Optional, Tuple, Union
from loguru import logger
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor

from .strategy_base import StrategyBase, Signal, TradeSignal, Position
from .bot_engine import BotEngine
from utils.trade_journal import append_trade_event

class Backtester:
    """
    Expanded Local Backtesting Engine.
    Supports advanced risk management, position control, and csv exports.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

        def _cfg(name: str, default: Any) -> Any:
            value = self.config.get(name)
            return default if value is None else value
        
        # Primary Parameters
        self.initial_balance = float(_cfg("initial_balance", 10000.0))
        self.spread_pips = float(_cfg("spread_pips", 2.0))
        self.commission = float(_cfg("commission", 7.0))
        self.leverage = float(_cfg("leverage", 100.0))
        if self.leverage <= 0:
            self.leverage = 100.0
        self.slippage = float(_cfg("slippage", 0.0001))
        self.data_dir = _cfg("data_path", "data/backtest-db")
        
        # Risk Parameters
        self.risk_per_trade = float(_cfg("risk_per_trade", 0.01))
        self.max_open_trades = int(_cfg("max_open_trades", 3))
        self.default_lot_size = float(_cfg("lot_size", 0.1))
        self.min_lot = float(_cfg("min_lot", 0.01))
        self.lot_step = float(_cfg("lot_step", 0.01))
        if self.lot_step <= 0:
            self.lot_step = 0.01
        
        # Position Management
        self.use_trailing_stop = self.config.get("use_trailing_stop")
        if self.use_trailing_stop is None: self.use_trailing_stop = True
        self.trailing_stop_pips = float(_cfg("trailing_stop_pips", 50.0))
        self.tp_multiplier = float(_cfg("tp_multiplier", 1.0))
        self.sl_multiplier = float(_cfg("sl_multiplier", 1.0))
        
        # Execution / Data
        self.lookback = int(_cfg("lookback", 100))
        self.warmup = int(_cfg("warmup", 200))
        
        # Logging / Output
        self.save_trades = self.config.get("save_trades")
        if self.save_trades is None: self.save_trades = True
        self.output_csv = _cfg("output", "trades.csv")
        self.log_signals = self.config.get("log_signals")
        if self.log_signals is None: self.log_signals = True
        
        # Simulation State
        self.data: pd.DataFrame = pd.DataFrame()
        self.current_index = 0
        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.trades: List[Dict] = []
        self.positions: List[Dict] = []
        self.equity_curve: List[float] = []
        self.executor: Optional[Union[StrategyBase, BotEngine]] = None
        self.symbol = ""
        self.timeframe = ""
        self.point = 0.01
        self.spread = 0.0

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
        
        # Ensure OHLC are numeric
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
                
        df = df.sort_values("time")
        
        if start:
            df = df[df["time"] >= start]
        if end:
            df = df[df["time"] <= end]
            
        return df

    def start_simulation(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime],
        executor: Union[StrategyBase, BotEngine],
        mode: str = "real"
    ):
        """Initialize the data and state for a step-by-step simulation."""
        if mode == "real":
            self.data = self.load_data(symbol, timeframe, start, end)
        else:
            r_start = start or datetime(2024, 1, 1)
            r_end = end or datetime.now()
            self.data = self._generate_random_data(r_start, r_end, timeframe)

        if self.data.empty:
            raise ValueError("No data found for backtest")

        self.symbol = symbol
        self.timeframe = timeframe
        self.executor = executor
        self.current_index = self.warmup
        self.balance = self.initial_balance
        self.equity = self.initial_balance
        self.trades = []
        self.positions = []
        self.equity_curve = []
        
        # Point calculations
        self.point = 0.0001 if "JPY" not in symbol else 0.01
        if "XAU" in symbol: self.point = 0.01
        self.spread = self.spread_pips * self.point

    def step(self) -> Optional[Dict[str, Any]]:
        """Process one candle and return the current state."""
        if self.current_index >= len(self.data):
            return None

        candle = self.data.iloc[self.current_index]
        history = self.data.iloc[max(0, self.current_index - self.lookback):self.current_index+1]
        opened_positions: List[Dict[str, Any]] = []
        closed_trades: List[Dict[str, Any]] = []
        
        # 1. Update existing positions & Trailing Stops
        for pos in self.positions[:]:
            if self.use_trailing_stop:
                pos = self._update_trailing_stop(pos, candle, self.point)
            
            exit_price, reason = self._check_exit(pos, candle, self.executor, history)
            if exit_price:
                closed_trade = self._close_position(
                    pos,
                    exit_price,
                    reason,
                    self.symbol,
                    self.point,
                    candle["time"]
                )
                self.balance += closed_trade["profit"]
                self.trades.append(closed_trade)
                closed_trades.append(closed_trade)
                append_trade_event({
                    "mode": "backtest",
                    "event": "CLOSE",
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "strategy": closed_trade.get("strategy", "unknown"),
                    "side": closed_trade["signal"],
                    "entry_price": closed_trade["entry_price"],
                    "exit_price": closed_trade["exit_price"],
                    "lot_size": closed_trade["lot_size"],
                    "profit": closed_trade["profit"],
                    "reason": closed_trade["reason"],
                    "open_time": closed_trade["entry_time"],
                    "close_time": closed_trade["exit_time"],
                })
                self.positions.remove(pos)

        # 2. Check for new signals
        if len(self.positions) < self.max_open_trades:
            sig = self.executor.analyze(self.symbol, history)
            if sig and sig.signal != Signal.HOLD:
                lot_size = self._calculate_lot_size(self.balance, sig, self.point, candle["close"], self.positions, self.symbol)
                if lot_size <= 0:
                    if self.log_signals:
                        logger.debug("Signal skipped due to non-positive calculated lot size")
                else:
                    entry_price = candle["close"] + (self.spread if sig.signal == Signal.BUY else -self.spread)
                    
                    sl = sig.stop_loss
                    tp = sig.take_profit
                    if self.sl_multiplier != 1.0:
                        risk = abs(entry_price - sl)
                        sl = entry_price - (risk * self.sl_multiplier) if sig.signal == Signal.BUY else entry_price + (risk * self.sl_multiplier)
                    if self.tp_multiplier != 1.0:
                        reward = abs(tp - entry_price)
                        tp = entry_price + (reward * self.tp_multiplier) if sig.signal == Signal.BUY else entry_price - (reward * self.tp_multiplier)

                    self.positions.append({
                        "signal": sig.signal,
                        "entry_price": entry_price,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "entry_time": candle["time"],
                        "lot_size": lot_size,
                        "high_water_mark": entry_price,
                        "strategy": self._strategy_label(sig),
                        "signal_comment": getattr(sig, "comment", ""),
                    })
                    opened_positions.append(self.positions[-1].copy())
                    append_trade_event({
                        "mode": "backtest",
                        "event": "OPEN",
                        "symbol": self.symbol,
                        "timeframe": self.timeframe,
                        "strategy": self.positions[-1]["strategy"],
                        "side": sig.signal.name,
                        "entry_price": entry_price,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "lot_size": lot_size,
                        "reason": "Signal execution",
                        "open_time": candle["time"],
                        "signal_comment": getattr(sig, "comment", ""),
                    })
                    if self.log_signals:
                        logger.debug(f"Signal: {sig.signal.name} at {entry_price}")

        # Update equity
        total_unrealized_pnl = 0
        for pos in self.positions:
            is_buy = pos["signal"] == Signal.BUY
            pips = (candle["close"] - pos["entry_price"]) / self.point if is_buy else (pos["entry_price"] - candle["close"]) / self.point
            multiplier = 100 if "XAU" in self.symbol else 100000
            pnl = (pips * self.point) * pos["lot_size"] * multiplier
            total_unrealized_pnl += pnl
            
        self.equity = round(self.balance + total_unrealized_pnl, 2)
        self.equity_curve.append(self.equity)
        self.current_index += 1
        
        return {
            "candle": candle,
            "balance": self.balance,
            "equity": self.equity,
            "positions": self.positions,
            "trades_count": len(self.trades),
            "opened_positions": opened_positions,
            "closed_trades": closed_trades,
        }

    def run(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[datetime],
        end: Optional[datetime],
        executor: Union[StrategyBase, BotEngine],
        mode: str = "real"
    ) -> Dict[str, Any]:
        """Run the backtest loop to completion."""
        self.start_simulation(symbol, timeframe, start, end, executor, mode)
        
        while self.step():
            pass

        results = self._calculate_metrics(self.trades, self.balance, self.equity_curve)
        
        if self.save_trades and self.trades:
            pd.DataFrame(self.trades).to_csv(self.output_csv, index=False)
            logger.info(f"Trades saved to {self.output_csv}")
            
        return results

    def _calculate_lot_size(self, balance: float, sig: TradeSignal, point: float, current_price: float, positions: List[Dict], symbol: str) -> float:
        """Risk per trade % based lot calculation with leverage/margin limit."""
        if sig.stop_loss == 0 or sig.entry_price == sig.stop_loss:
            return max(0.0, self.default_lot_size)

        if self.risk_per_trade <= 0:
            return max(0.0, self.default_lot_size)
        
        # 1. Calculate risk-based lot
        risk_amount = balance * self.risk_per_trade
        if risk_amount <= 0:
            return max(0.0, self.default_lot_size)
        sl_pips = abs(sig.entry_price - sig.stop_loss) / point
        if sl_pips == 0:
            return max(0.0, self.default_lot_size)
        
        contract_size = 100 if "XAU" in symbol else 100000
        pip_value_per_lot = contract_size * point
        if pip_value_per_lot <= 0:
            return 0.0

        lot = risk_amount / (sl_pips * pip_value_per_lot)
        
        # 2. Leverage/Margin Check
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
        
        min_lot = float(self.min_lot)
        lot_step = float(self.lot_step) if float(self.lot_step) > 0 else 0.01
        if final_lot <= 0:
            return 0.0

        if min_lot > 0 and final_lot < min_lot:
            return 0.0

        stepped = round(final_lot / lot_step) * lot_step
        if min_lot > 0 and stepped < min_lot:
            return 0.0

        decimals = max(2, len(f"{lot_step:.8f}".rstrip("0").split(".")[-1]))
        return round(stepped, decimals)

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

    def _close_position(
        self,
        pos: Dict,
        exit_price: float,
        reason: str,
        symbol: str,
        point: float,
        exit_time: Any
    ) -> Dict:
        is_buy = pos["signal"] == Signal.BUY
        pips = (exit_price - pos["entry_price"]) / point if is_buy else (pos["entry_price"] - exit_price) / point
        multiplier = 100 if "XAU" in symbol else 100000
        profit = (pips * point) * pos["lot_size"] * multiplier
        profit -= self.commission * pos["lot_size"]
        
        return {
            "entry_time": pos["entry_time"],
            "exit_time": exit_time,
            "signal": pos["signal"].name,
            "strategy": pos.get("strategy", "unknown"),
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "lot_size": pos["lot_size"],
            "profit": round(profit, 2),
            "reason": reason
        }

    def _strategy_label(self, sig: TradeSignal) -> str:
        if isinstance(self.executor, StrategyBase):
            return self.executor.name
        comment = getattr(sig, "comment", "") or ""
        if "_" in comment:
            return comment.split("_", 1)[0]
        return self.executor.__class__.__name__ if self.executor is not None else "unknown"

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
