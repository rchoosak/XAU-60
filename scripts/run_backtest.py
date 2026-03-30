#!/usr/bin/env python3
"""
Backtest runner (live-like).

This runner replays historical OHLCV as if the live TradingBot were receiving
streaming market data from MT5, while keeping the live config stack:
- .env
- config/settings.yaml
- config/strategies/*.yaml

It reuses TradingBot, RiskManager, and TradeExecutor directly.
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml
from loguru import logger

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import TradingBot
from core.risk_manager import DailyStats, RiskLimits, RiskManager
from core.trade_executor import TradeExecutor
from core.mt5_connector import AccountInfo, SymbolInfo
from core.strategy_base import Signal, Position
from core.strategy_loader import StrategyLoader
from utils.trade_journal import append_trade_event


PROJECT_ROOT = Path(__file__).resolve().parent.parent


TIMEFRAME_SECONDS = {
    "S1": 1,
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
}

TIMEFRAME_RULES = {
    "S1": "1s",
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d",
}


@dataclass
class ReplayPosition:
    ticket: int
    symbol: str
    type: Signal
    volume: float
    open_price: float
    stop_loss: float
    take_profit: float
    magic_number: int
    comment: str
    open_time: datetime


def _parse_date(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if len(raw) == 10:
            return datetime.strptime(raw, "%Y-%m-%d")
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
    raise ValueError(f"Unsupported date format: {value}")


def _normalize_timeframe(value: Optional[str]) -> str:
    if not value:
        return "M1"
    raw = str(value).strip().upper()
    aliases = {
        "1S": "S1",
        "SEC1": "S1",
        "SECOND": "S1",
        "SECONDS": "S1",
        "1M": "M1",
        "1H": "H1",
    }
    return aliases.get(raw, raw)


def _normalize_strategy_name(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_config_keys(data: Dict[str, Any]) -> Dict[str, Any]:
    if not data:
        return {}
    return {str(k).replace("-", "_"): v for k, v in data.items()}


def _ensure_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    cols = {str(c).lower(): c for c in df.columns}
    rename: Dict[str, str] = {}

    aliases = {
        "time": ("time", "timestamp", "datetime", "date"),
        "open": ("open", "openprice", "open_price"),
        "high": ("high", "highprice", "high_price"),
        "low": ("low", "lowprice", "low_price"),
        "close": ("close", "closeprice", "close_price"),
        "volume": ("volume", "tick_volume", "tickvolume", "volume_real", "volumereal"),
        "bid": ("bid", "bidprice", "bid_price"),
        "ask": ("ask", "askprice", "ask_price"),
    }

    for canonical, candidates in aliases.items():
        if canonical in cols:
            continue
        for cand in candidates:
            found = cols.get(cand.lower())
            if found is not None:
                rename[found] = canonical
                break

    if rename:
        df = df.rename(columns=rename)

    required = ["time", "open", "high", "low", "close"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Data is missing columns: {missing}")

    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)

    if "volume" not in df.columns:
        df["volume"] = 0

    for col in ["open", "high", "low", "close", "volume", "bid", "ask"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"]).copy()
    df = df.drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)

    return df


def _calc_max_drawdown(equity_curve: List[float], initial_balance: float) -> float:
    peak = initial_balance
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
    return max_dd


class ReplayMT5Connector:
    """MT5-like connector that replays historical data bar-by-bar."""

    def __init__(
        self,
        symbol: str,
        data: pd.DataFrame,
        base_timeframe: str,
        initial_balance: float,
        leverage: int,
        point: float,
        digits: int,
        contract_size: float,
        spread_points: float,
        tick_size: float,
        tick_value: float,
        min_lot: float,
        max_lot: float,
        lot_step: float,
    ):
        self.backend_mode = "replay"
        self.symbol = symbol
        self.base_timeframe = _normalize_timeframe(base_timeframe)
        self.point = float(point)
        self.digits = int(digits)
        self.contract_size = float(contract_size)
        self.spread_points = float(spread_points)
        self.tick_size = float(tick_size)
        self.tick_value = float(tick_value)
        self.min_lot = float(min_lot)
        self.max_lot = float(max_lot)
        self.lot_step = float(lot_step)

        self._data = data.reset_index(drop=True).copy()
        self._tf_cache: Dict[str, pd.DataFrame] = {self.base_timeframe: self._data[["time", "open", "high", "low", "close", "volume"]].copy()}
        self._cursor = 0
        self._connected = False

        self._positions: Dict[int, ReplayPosition] = {}
        self._next_ticket = 1000
        self._forced_exit_price: Dict[int, float] = {}

        self._account = AccountInfo(
            login=999001,
            balance=float(initial_balance),
            equity=float(initial_balance),
            margin=0.0,
            free_margin=float(initial_balance),
            margin_level=0.0,
            profit=0.0,
            currency="USD",
            leverage=int(leverage),
            server="Replay-Backtest",
            company="Replay Engine",
        )

    @property
    def data(self) -> pd.DataFrame:
        return self._data

    @property
    def current_time(self) -> datetime:
        return self._data.iloc[self._cursor]["time"]

    @property
    def finished(self) -> bool:
        return self._cursor >= len(self._data)

    def set_cursor(self, idx: int) -> None:
        self._cursor = max(0, min(idx, len(self._data) - 1))

    def connect(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        path: Optional[str] = None,
        timeout: int = 60000,
    ) -> bool:
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._connected = False

    def _current_row(self) -> pd.Series:
        return self._data.iloc[self._cursor]

    def _current_bid_ask(self) -> Tuple[float, float]:
        row = self._current_row()
        if "bid" in self._data.columns and "ask" in self._data.columns:
            bid = float(row.get("bid", row["close"]))
            ask = float(row.get("ask", row["close"]))
        elif "bid" in self._data.columns:
            bid = float(row.get("bid", row["close"]))
            ask = bid + (self.spread_points * self.point)
        elif "ask" in self._data.columns:
            ask = float(row.get("ask", row["close"]))
            bid = ask - (self.spread_points * self.point)
        else:
            spread = self.spread_points * self.point
            mid = float(row["close"])
            bid = mid - (spread / 2.0)
            ask = mid + (spread / 2.0)

        if ask < bid:
            ask = bid
        bid = round(bid, self.digits)
        ask = round(ask, self.digits)
        return bid, ask

    def _position_profit(self, position: ReplayPosition, mark_price: Optional[float] = None) -> float:
        if mark_price is None:
            bid, ask = self._current_bid_ask()
            mark_price = bid if position.type == Signal.BUY else ask

        if position.type == Signal.BUY:
            raw = (mark_price - position.open_price) * position.volume * self.contract_size
        else:
            raw = (position.open_price - mark_price) * position.volume * self.contract_size
        return float(raw)

    def _refresh_account(self) -> None:
        total_profit = 0.0
        total_margin = 0.0
        for ticket, pos in self._positions.items():
            forced = self._forced_exit_price.get(ticket)
            total_profit += self._position_profit(pos, mark_price=forced)
            total_margin += (pos.open_price * self.contract_size * pos.volume) / max(1, self._account.leverage)

        self._account.profit = round(total_profit, 2)
        self._account.equity = round(self._account.balance + total_profit, 2)
        self._account.margin = round(total_margin, 2)
        self._account.free_margin = round(self._account.equity - total_margin, 2)
        if total_margin > 0:
            self._account.margin_level = round((self._account.equity / total_margin) * 100.0, 2)
        else:
            self._account.margin_level = 0.0

    def get_account_info(self) -> Optional[AccountInfo]:
        if not self._connected:
            return None
        self._refresh_account()
        return self._account

    def get_symbol_info(self, symbol: str) -> Optional[SymbolInfo]:
        if symbol != self.symbol:
            return None

        return SymbolInfo(
            name=symbol,
            description=f"Replay {symbol}",
            point=self.point,
            digits=self.digits,
            spread=max(0, int(round(self.spread_points))),
            min_lot=self.min_lot,
            max_lot=self.max_lot,
            lot_step=self.lot_step,
            tick_size=self.tick_size,
            tick_value=self.tick_value,
            contract_size=self.contract_size,
            trade_mode=4,
        )

    def get_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        if symbol != self.symbol or not self._connected:
            return None

        bid, ask = self._current_bid_ask()
        return {
            "bid": bid,
            "ask": ask,
            "last": round((bid + ask) / 2.0, self.digits),
            "volume": int(self._current_row().get("volume", 0) or 0),
            "time": self.current_time,
        }

    def _resample_ohlcv(self, timeframe: str) -> pd.DataFrame:
        tf = _normalize_timeframe(timeframe)
        if tf in self._tf_cache:
            return self._tf_cache[tf]

        if tf not in TIMEFRAME_SECONDS or self.base_timeframe not in TIMEFRAME_SECONDS:
            logger.warning(f"Unknown timeframe {tf}. Falling back to {self.base_timeframe}.")
            return self._tf_cache[self.base_timeframe]

        base_s = TIMEFRAME_SECONDS[self.base_timeframe]
        tf_s = TIMEFRAME_SECONDS[tf]
        if tf_s < base_s or (tf_s % base_s) != 0:
            logger.warning(
                f"Cannot derive timeframe {tf} from base timeframe {self.base_timeframe}. "
                f"Falling back to {self.base_timeframe}."
            )
            return self._tf_cache[self.base_timeframe]

        rule = TIMEFRAME_RULES.get(tf)
        if not rule:
            return self._tf_cache[self.base_timeframe]

        df = self._data[["time", "open", "high", "low", "close", "volume"]].copy()
        res = (
            df.set_index("time")
            .resample(rule)
            .agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            })
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )
        self._tf_cache[tf] = res
        return res

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        count: int = 100,
        start_time: Optional[datetime] = None,
    ) -> Optional[pd.DataFrame]:
        if symbol != self.symbol or not self._connected:
            return None

        df = self._resample_ohlcv(timeframe)
        sub = df[df["time"] <= self.current_time]
        if start_time is not None:
            sub = sub[sub["time"] >= start_time]
        if sub.empty:
            return None

        return sub.tail(int(count)).reset_index(drop=True)

    def set_forced_exit_price(self, ticket: int, price: float) -> None:
        if ticket in self._positions:
            self._forced_exit_price[ticket] = float(price)

    def place_market_order(
        self,
        symbol: str,
        order_type: Signal,
        volume: float,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        magic: int = 0,
        comment: str = "",
        slippage: int = 10,
    ) -> Tuple[bool, int]:
        if symbol != self.symbol or not self._connected:
            return False, 0

        if volume <= 0:
            return False, 0

        tick = self.get_tick(symbol)
        if not tick:
            return False, 0

        entry_price = float(tick["ask"] if order_type == Signal.BUY else tick["bid"])
        ticket = self._next_ticket
        self._next_ticket += 1

        self._positions[ticket] = ReplayPosition(
            ticket=ticket,
            symbol=symbol,
            type=order_type,
            volume=float(volume),
            open_price=entry_price,
            stop_loss=float(stop_loss),
            take_profit=float(take_profit),
            magic_number=int(magic),
            comment=str(comment),
            open_time=self.current_time,
        )
        self._refresh_account()
        return True, ticket

    def close_position(self, ticket: int) -> bool:
        pos = self._positions.get(ticket)
        if not pos:
            return False

        forced_price = self._forced_exit_price.pop(ticket, None)
        if forced_price is not None:
            close_price = float(forced_price)
        else:
            tick = self.get_tick(pos.symbol)
            if not tick:
                return False
            close_price = float(tick["bid"] if pos.type == Signal.BUY else tick["ask"])

        pnl = self._position_profit(pos, mark_price=close_price)
        self._account.balance = round(self._account.balance + pnl, 2)

        del self._positions[ticket]
        self._refresh_account()
        return True

    def modify_position(
        self,
        ticket: int,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> bool:
        pos = self._positions.get(ticket)
        if not pos:
            return False

        if stop_loss is not None:
            pos.stop_loss = float(stop_loss)
        if take_profit is not None:
            pos.take_profit = float(take_profit)
        return True

    def get_positions(self, symbol: Optional[str] = None, magic: Optional[int] = None) -> List[Position]:
        result: List[Position] = []
        for ticket, pos in self._positions.items():
            if symbol is not None and pos.symbol != symbol:
                continue
            if magic is not None and pos.magic_number != magic:
                continue

            forced = self._forced_exit_price.get(ticket)
            profit = round(self._position_profit(pos, mark_price=forced), 2)

            result.append(
                Position(
                    ticket=pos.ticket,
                    symbol=pos.symbol,
                    type=pos.type,
                    volume=pos.volume,
                    open_price=pos.open_price,
                    stop_loss=pos.stop_loss,
                    take_profit=pos.take_profit,
                    profit=profit,
                    magic_number=pos.magic_number,
                    comment=pos.comment,
                    open_time=pos.open_time,
                )
            )
        return result


class LiveLikeBotBacktester:
    """Adapter that allows BacktestTUI to drive live TradingBot logic."""

    def __init__(
        self,
        bot: TradingBot,
        replay_mt5: ReplayMT5Connector,
        config: Dict[str, Any],
        warmup: int,
        stop_priority: str = "sl_first",
        close_open_positions_at_end: bool = True,
    ):
        self.bot = bot
        self.mt5 = replay_mt5
        self.config = config
        self.warmup = max(0, int(warmup))
        self.stop_priority = str(stop_priority).strip().lower() or "sl_first"
        self.close_open_positions_at_end = bool(close_open_positions_at_end)

        self.symbol = replay_mt5.symbol
        self.timeframe = replay_mt5.base_timeframe
        self.point = replay_mt5.point

        self.data = replay_mt5.data[["time", "open", "high", "low", "close", "volume"]].copy().reset_index(drop=True)
        self.current_index = min(self.warmup, max(0, len(self.data) - 1))

        account = self.mt5.get_account_info()
        self.initial_balance = float(account.balance if account else 0.0)
        self.balance = self.initial_balance
        self.equity = self.initial_balance

        self.positions: List[Dict[str, Any]] = []
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[float] = []

        self.executor = self._primary_executor()
        self._close_reason_by_ticket: Dict[int, str] = {}
        self._wrap_trade_close()

    def _primary_executor(self) -> Any:
        enabled = self.bot.strategy_loader.get_enabled_strategies()
        if enabled:
            return list(enabled.values())[0]
        all_strats = self.bot.strategy_loader.get_all_strategies()
        if all_strats:
            return list(all_strats.values())[0]
        return self.bot

    def _wrap_trade_close(self) -> None:
        original = self.bot.trade_executor.close_trade

        def wrapped(ticket: int, reason: str = "Manual close") -> bool:
            self._close_reason_by_ticket[ticket] = reason
            ok = original(ticket, reason)
            if not ok:
                self._close_reason_by_ticket.pop(ticket, None)
            return ok

        self.bot.trade_executor.close_trade = wrapped  # type: ignore[assignment]

    def start(self) -> None:
        self.current_index = min(self.warmup, max(0, len(self.data) - 1))
        self.mt5.set_cursor(self.current_index)
        self._sync_account_state()

    def _sync_account_state(self) -> None:
        acc = self.mt5.get_account_info()
        if acc:
            self.balance = float(acc.balance)
            self.equity = float(acc.equity)
            self.equity_curve.append(self.equity)

    def _snapshot_positions(self) -> List[Dict[str, Any]]:
        snapshot: List[Dict[str, Any]] = []
        for p in self.mt5.get_positions():
            snapshot.append(
                {
                    "ticket": p.ticket,
                    "signal": p.type,
                    "entry_price": p.open_price,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "entry_time": p.open_time,
                    "lot_size": p.volume,
                    "magic_number": p.magic_number,
                }
            )
        return snapshot

    def _auto_close_by_sl_tp(self, candle: pd.Series) -> None:
        positions = self.mt5.get_positions()
        if not positions:
            return

        for pos in positions:
            hit_reason = ""
            exit_price = 0.0

            if pos.type == Signal.BUY:
                sl_hit = pos.stop_loss > 0 and candle["low"] <= pos.stop_loss
                tp_hit = pos.take_profit > 0 and candle["high"] >= pos.take_profit
                if self.stop_priority == "tp_first":
                    if tp_hit:
                        hit_reason, exit_price = "TP", float(pos.take_profit)
                    elif sl_hit:
                        hit_reason, exit_price = "SL", float(pos.stop_loss)
                else:
                    if sl_hit:
                        hit_reason, exit_price = "SL", float(pos.stop_loss)
                    elif tp_hit:
                        hit_reason, exit_price = "TP", float(pos.take_profit)
            else:
                sl_hit = pos.stop_loss > 0 and candle["high"] >= pos.stop_loss
                tp_hit = pos.take_profit > 0 and candle["low"] <= pos.take_profit
                if self.stop_priority == "tp_first":
                    if tp_hit:
                        hit_reason, exit_price = "TP", float(pos.take_profit)
                    elif sl_hit:
                        hit_reason, exit_price = "SL", float(pos.stop_loss)
                else:
                    if sl_hit:
                        hit_reason, exit_price = "SL", float(pos.stop_loss)
                    elif tp_hit:
                        hit_reason, exit_price = "TP", float(pos.take_profit)

            if hit_reason:
                self.mt5.set_forced_exit_price(pos.ticket, exit_price)
                self.bot.trade_executor.close_trade(pos.ticket, f"{hit_reason} auto-close")

    def _close_all_at_end(self) -> None:
        open_positions = self.mt5.get_positions()
        for pos in open_positions:
            self.bot.trade_executor.close_trade(pos.ticket, "Backtest end")

    def _pull_closed_trade_events(
        self,
        history_before_len: int,
    ) -> List[Dict[str, Any]]:
        history = self.bot.trade_executor.get_trade_history()
        new_records = history[history_before_len:]
        closed_events: List[Dict[str, Any]] = []

        for rec in new_records:
            reason = self._close_reason_by_ticket.pop(rec.ticket, "Close")
            event = {
                "entry_time": rec.open_time,
                "exit_time": rec.close_time,
                "signal": rec.signal.name,
                "strategy": rec.strategy,
                "entry_price": rec.entry_price,
                "exit_price": rec.close_price if rec.close_price is not None else rec.entry_price,
                "lot_size": rec.lot_size,
                "profit": round(rec.profit or 0.0, 2),
                "reason": reason,
            }
            self.trades.append(event)
            closed_events.append(event)

        return closed_events

    def _compute_results(self) -> Dict[str, Any]:
        total_trades = len(self.trades)
        net_profit = self.balance - self.initial_balance
        wins = [t for t in self.trades if t.get("profit", 0) > 0]
        losses = [t for t in self.trades if t.get("profit", 0) < 0]
        gross_profit = sum(t.get("profit", 0.0) for t in wins)
        gross_loss = abs(sum(t.get("profit", 0.0) for t in losses))
        max_dd = _calc_max_drawdown(self.equity_curve, self.initial_balance)

        return {
            "symbol": self.symbol,
            "strategy": ",".join(self.bot.strategy_loader.get_enabled_strategies().keys()) or "none",
            "initial_balance": round(self.initial_balance, 2),
            "final_balance": round(self.balance, 2),
            "net_profit": round(net_profit, 2),
            "total_trades": total_trades,
            "win_rate": f"{(len(wins) / total_trades) * 100:.1f}%" if total_trades else "0.0%",
            "max_drawdown": f"${max_dd:.2f}",
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        }

    def step(self) -> Optional[Dict[str, Any]]:
        if self.current_index >= len(self.data):
            return None

        self.mt5.set_cursor(self.current_index)
        candle = self.data.iloc[self.current_index]

        before_positions = self._snapshot_positions()
        before_tickets = {p["ticket"] for p in before_positions}
        history_before_len = len(self.bot.trade_executor.get_trade_history())

        # Broker-like SL/TP checks before strategy cycle.
        self._auto_close_by_sl_tp(candle)

        # Run exact live bot tick logic.
        self.bot._tick()

        after_positions = self._snapshot_positions()
        opened_positions = [p for p in after_positions if p["ticket"] not in before_tickets]
        closed_trades = self._pull_closed_trade_events(history_before_len)

        self.positions = after_positions
        self._sync_account_state()

        state = {
            "candle": candle,
            "balance": self.balance,
            "equity": self.equity,
            "positions": self.positions,
            "trades_count": len(self.trades),
            "opened_positions": opened_positions,
            "closed_trades": closed_trades,
        }

        self.current_index += 1

        if self.current_index >= len(self.data) and self.close_open_positions_at_end:
            history_len = len(self.bot.trade_executor.get_trade_history())
            self._close_all_at_end()
            more_closed = self._pull_closed_trade_events(history_len)
            if more_closed:
                state["closed_trades"].extend(more_closed)
                self._sync_account_state()

        return state

    def run(self) -> Dict[str, Any]:
        self.start()
        while self.step() is not None:
            pass
        return self._compute_results()


class ReplayRiskManager(RiskManager):
    """RiskManager variant that tracks day boundaries using simulated time."""

    def __init__(self, mt5: ReplayMT5Connector, limits: Optional[RiskLimits], now_func):
        super().__init__(mt5, limits)
        self._now_func = now_func

    def _today(self):
        return self._now_func().date()

    def initialize(self) -> bool:
        account = self.mt5.get_account_info()
        if not account:
            logger.error("Failed to get account info for risk manager")
            return False

        self._balance_offset = self._compute_offset(account.balance, self.limits.capital_base)
        self._equity_offset = self._compute_offset(account.equity, self.limits.capital_base)

        effective_balance = self._effective_balance(account)
        effective_equity = self._effective_equity(account)

        self._starting_balance = effective_balance
        self._peak_balance = effective_equity

        self._daily_stats = DailyStats(
            date=self._today(),
            starting_balance=effective_balance,
            current_pnl=0.0,
            trades_count=0,
            winning_trades=0,
            losing_trades=0,
        )

        mode = (
            f"effective_capital={effective_balance:.2f}"
            if self.limits.capital_base and self.limits.capital_base > 0
            else "effective_capital=full_balance"
        )
        logger.info(f"Risk manager initialized. Balance: {account.balance} {account.currency} | {mode}")
        return True

    def _is_daily_limit_reached(self) -> bool:
        if not self._daily_stats:
            return False

        today = self._today()
        if self._daily_stats.date != today:
            account = self.mt5.get_account_info()
            effective_balance = self._effective_balance(account) if account else 0.0
            self._daily_stats = DailyStats(
                date=today,
                starting_balance=effective_balance,
                current_pnl=0.0,
                trades_count=0,
                winning_trades=0,
                losing_trades=0,
            )

        account = self.mt5.get_account_info()
        if account:
            effective_equity = self._effective_equity(account)
            daily_pnl = effective_equity - self._daily_stats.starting_balance
            if self._daily_stats.starting_balance <= 0:
                self._warn_throttled(
                    "daily_non_positive_start",
                    "Daily stats starting balance is non-positive; skipping daily loss limit check",
                )
                return False
            daily_pnl_percent = (daily_pnl / self._daily_stats.starting_balance) * 100
            if daily_pnl_percent <= -self.limits.max_daily_loss:
                self._warn_throttled(
                    "daily_loss_limit",
                    f"Daily loss limit reached: {daily_pnl_percent:.2f}%",
                    cooldown_seconds=30.0,
                )
                return True
        return False


def _load_data(path: str, start: Optional[datetime], end: Optional[datetime]) -> pd.DataFrame:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Data path not found: {path}")

    df = pd.read_parquet(path)
    df = _ensure_ohlcv(df)

    if start is not None:
        df = df[df["time"] >= start]
    if end is not None:
        df = df[df["time"] <= end]

    if df.empty:
        raise ValueError("No data available after date filtering")

    return df.reset_index(drop=True)


def _parse_strategy_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [v.strip() for v in raw.split(",") if v.strip()]
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    return []


def _prepare_bot(
    settings_path: str,
    replay_mt5: ReplayMT5Connector,
    selected_strategies: List[str],
    disable_console_log: bool = False,
) -> TradingBot:
    bot = TradingBot(config_path=settings_path)
    if not bot.load_config():
        raise RuntimeError("Failed to load bot config")

    # Use normal logging setup path through bot config.
    from utils.logger import setup_logger

    log_cfg = bot.config.get("logging", {})
    setup_logger(
        log_file=log_cfg.get("file"),
        level=log_cfg.get("level", "INFO"),
        rotation=log_cfg.get("rotation", "10 MB"),
        retention=log_cfg.get("retention", "7 days"),
        console=not disable_console_log,
    )

    bot.mt5 = replay_mt5
    if not bot.mt5.connect(timeout=bot.config.get("mt5", {}).get("timeout", 60000)):
        raise RuntimeError("Replay MT5 connect failed")

    risk_cfg = bot.config.get("risk", {})
    limits = RiskLimits(
        max_risk_per_trade=risk_cfg.get("max_risk_per_trade", 2.0),
        max_daily_loss=risk_cfg.get("max_daily_loss", 5.0),
        max_drawdown=risk_cfg.get("max_drawdown", 20.0),
        max_positions=risk_cfg.get("max_positions", 5),
        max_positions_per_symbol=risk_cfg.get("max_positions_per_symbol", 2),
        capital_base=risk_cfg.get("capital_base", 0.0),
    )

    bot.risk_manager = ReplayRiskManager(bot.mt5, limits, now_func=lambda: replay_mt5.current_time)
    if not bot.risk_manager.initialize():
        raise RuntimeError("Failed to initialize risk manager")

    trade_cfg = bot.config.get("trading", {})
    bot.trade_executor = TradeExecutor(
        bot.mt5,
        bot.risk_manager,
        default_magic=trade_cfg.get("default_magic_number", 123456),
        default_lot_size=trade_cfg.get("default_lot_size", 0.01),
        slippage=trade_cfg.get("slippage", 10),
    )

    loader = StrategyLoader()
    loader.load_all_strategies()

    selected_normalized = {_normalize_strategy_name(s) for s in selected_strategies}
    if selected_normalized:
        for name, strat in loader.get_all_strategies().items():
            norm = _normalize_strategy_name(name)
            strat.enabled = norm in selected_normalized

    bot.strategy_loader = loader

    enabled = bot.strategy_loader.get_enabled_strategies()
    if not enabled:
        all_states = []
        for name, strat in bot.strategy_loader.get_all_strategies().items():
            all_states.append(f"{name}={'enabled' if getattr(strat, 'enabled', False) else 'disabled'}")
        details = ", ".join(all_states) if all_states else "none"
        raise RuntimeError(
            "No enabled strategies for this run. "
            "Enable at least one strategy in config/strategies/*.yaml "
            "(enabled: true), or set execution.strategy/execution.strategies "
            "in backtest.yaml, or pass --strategy/--strategies. "
            f"Current states: {details}"
        )

    logger.info(f"Enabled strategies: {', '.join(enabled.keys())}")
    return bot


def _print_result(result: Dict[str, Any]) -> None:
    print("\n" + "=" * 40)
    print(f" BACKTEST: {result['symbol']}")
    print("=" * 40)
    print(f"Strategy      : {result['strategy']}")
    print(f"Initial Balance: ${result['initial_balance']:,.2f}")
    print(f"Final Balance : ${result['final_balance']:,.2f}")
    print(f"Net Profit    : ${result['net_profit']:,.2f}")
    print(f"Trades        : {result['total_trades']}")
    print(f"Win Rate      : {result['win_rate']}")
    print(f"Max Drawdown  : {result['max_drawdown']}")
    print(f"Profit Factor : {result['profit_factor']}")


def _resolve_project_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def _setup_trade_journal(cfg: Dict[str, Any], config_path: str) -> Path:
    journal_cfg = _normalize_config_keys(cfg.get("journal", {}) or {})
    raw_path = str(journal_cfg.get("path", "logs/trade_journal.jsonl")).strip() or "logs/trade_journal.jsonl"
    journal_path = _resolve_project_path(raw_path)
    os.environ["TRADE_JOURNAL_FILE"] = str(journal_path)

    if bool(journal_cfg.get("reset_on_start", False)):
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text("", encoding="utf-8")

    data_cfg = _normalize_config_keys(cfg.get("data", {}) or {})
    data_path_raw = str(data_cfg.get("path") or cfg.get("data_path") or "").strip()
    data_path = str(_resolve_project_path(data_path_raw)) if data_path_raw else None

    append_trade_event(
        {
            "mode": "backtest",
            "event": "RUN_START",
            "config_path": str(_resolve_project_path(config_path)),
            "journal_path": str(journal_path),
            "symbol": cfg.get("symbol") or data_cfg.get("symbol"),
            "timeframe": cfg.get("timeframe") or data_cfg.get("timeframe"),
            "data_path": data_path,
            "start": data_cfg.get("start") or cfg.get("start"),
            "end": data_cfg.get("end") or cfg.get("end"),
        }
    )
    logger.info(f"Trade journal enabled: {journal_path}")
    return journal_path


def _append_run_end_event(
    result: Optional[Dict[str, Any]],
    status: str,
    error: str = "",
) -> None:
    payload: Dict[str, Any] = {
        "mode": "backtest",
        "event": "RUN_END",
        "status": status,
    }
    if error:
        payload["error"] = error
    if isinstance(result, dict):
        payload.update(
            {
                "symbol": result.get("symbol"),
                "strategy": result.get("strategy"),
                "initial_balance": result.get("initial_balance"),
                "final_balance": result.get("final_balance"),
                "net_profit": result.get("net_profit"),
                "total_trades": result.get("total_trades"),
                "win_rate": result.get("win_rate"),
                "max_drawdown": result.get("max_drawdown"),
                "profit_factor": result.get("profit_factor"),
            }
        )
    append_trade_event(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest runner (live-like)")
    parser.add_argument("-f", "--config", default="config/backtest.yaml", help="Path to backtest config")
    parser.add_argument("--strategy", type=str, help="Override to run one strategy (e.g. smc_scalper)")
    parser.add_argument("--strategies", type=str, help="Override to run many strategies (comma-separated)")
    parser.add_argument("--tui", action="store_true", help="Force enable TUI")
    parser.add_argument("--no-tui", action="store_true", help="Force disable TUI")
    parser.add_argument("--tui-steps", type=int, default=None, help="Backtest steps per TUI frame")
    parser.add_argument("--tui-interval", type=float, default=None, help="TUI frame interval in seconds")
    args = parser.parse_args()

    def _build_runtime(force_disable_console_log: Optional[bool] = None) -> Tuple[Dict[str, Any], LiveLikeBotBacktester, bool, int, float]:
        if not os.path.exists(args.config):
            raise FileNotFoundError(f"Config file not found: {args.config}")

        with open(args.config, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        cfg = _normalize_config_keys(raw)
        data_cfg = _normalize_config_keys(cfg.get("data", {}) or {})
        sim_cfg = _normalize_config_keys(cfg.get("simulation", {}) or {})
        exec_cfg = _normalize_config_keys(cfg.get("execution", {}) or {})
        tui_cfg = _normalize_config_keys(cfg.get("tui", {}) or {})

        symbol = cfg.get("symbol") or data_cfg.get("symbol") or "XAUUSD"
        base_timeframe = _normalize_timeframe(cfg.get("timeframe") or data_cfg.get("timeframe") or "M1")
        data_path = data_cfg.get("path") or cfg.get("data_path")
        if not data_path:
            raise ValueError("Missing data path. Set data.path in config.")

        start = _parse_date(data_cfg.get("start") or cfg.get("start"))
        end = _parse_date(data_cfg.get("end") or cfg.get("end"))
        warmup = int(cfg.get("warmup", data_cfg.get("warmup", 200)))

        df = _load_data(str(data_path), start, end)

        initial_balance = float(sim_cfg.get("initial_balance", 100000.0))
        leverage = int(sim_cfg.get("leverage", 100))

        point = float(sim_cfg.get("point", 0.01 if symbol.upper().startswith("XAU") else 0.0001))
        digits = int(sim_cfg.get("digits", 2 if symbol.upper().startswith("XAU") else 5))
        contract_size = float(sim_cfg.get("contract_size", 100.0 if symbol.upper().startswith("XAU") else 100000.0))
        spread_points = float(sim_cfg.get("spread_points", 25.0 if symbol.upper().startswith("XAU") else 10.0))
        tick_size = float(sim_cfg.get("tick_size", point))
        tick_value = float(sim_cfg.get("tick_value", 1.0 if symbol.upper().startswith("XAU") else 10.0))
        min_lot = float(sim_cfg.get("min_lot", 0.01))
        max_lot = float(sim_cfg.get("max_lot", 100.0))
        lot_step = float(sim_cfg.get("lot_step", 0.01))

        selected = []
        selected.extend(_parse_strategy_list(exec_cfg.get("strategies")))
        if exec_cfg.get("strategy"):
            selected.extend(_parse_strategy_list(exec_cfg.get("strategy")))

        # CLI overrides YAML execution selection when provided.
        cli_selected = []
        if args.strategy:
            cli_selected.extend(_parse_strategy_list(args.strategy))
        if args.strategies:
            cli_selected.extend(_parse_strategy_list(args.strategies))
        if cli_selected:
            selected = cli_selected

        # Keep order and uniqueness.
        selected = list(dict.fromkeys(selected))

        enable_tui = bool(tui_cfg.get("enabled", False))
        if args.tui:
            enable_tui = True
        if args.no_tui:
            enable_tui = False

        replay = ReplayMT5Connector(
            symbol=symbol,
            data=df,
            base_timeframe=base_timeframe,
            initial_balance=initial_balance,
            leverage=leverage,
            point=point,
            digits=digits,
            contract_size=contract_size,
            spread_points=spread_points,
            tick_size=tick_size,
            tick_value=tick_value,
            min_lot=min_lot,
            max_lot=max_lot,
            lot_step=lot_step,
        )

        settings_path = str(cfg.get("settings_path", "config/settings.yaml"))
        disable_console_log = enable_tui if force_disable_console_log is None else bool(force_disable_console_log)
        bot = _prepare_bot(settings_path, replay, selected, disable_console_log=disable_console_log)

        stop_priority = str(sim_cfg.get("stop_priority", "sl_first"))
        close_at_end = bool(sim_cfg.get("close_open_positions_at_end", True))

        runner = LiveLikeBotBacktester(
            bot=bot,
            replay_mt5=replay,
            config=cfg,
            warmup=warmup,
            stop_priority=stop_priority,
            close_open_positions_at_end=close_at_end,
        )

        tui_steps = args.tui_steps if args.tui_steps is not None else int(tui_cfg.get("steps_per_tick", 20))
        tui_interval = (
            args.tui_interval if args.tui_interval is not None else float(tui_cfg.get("update_interval", 0.05))
        )
        return cfg, runner, enable_tui, tui_steps, tui_interval

    cfg, runner, enable_tui, tui_steps, tui_interval = _build_runtime()
    _setup_trade_journal(cfg, args.config)

    result: Optional[Dict[str, Any]] = None
    status = "success"
    error = ""

    try:
        if enable_tui:
            from core.tui_app import BacktestTUI

            def reload_replay_state() -> Tuple[Optional[LiveLikeBotBacktester], str]:
                try:
                    _, new_runner, _, _, _ = _build_runtime(force_disable_console_log=True)
                    return new_runner, "Config reloaded and simulation restarted."
                except Exception as e:
                    return None, str(e)

            runner.start()
            tui = BacktestTUI(
                runner,
                steps_per_tick=tui_steps,
                update_interval=tui_interval,
                reload_callback=reload_replay_state,
            )
            tui.run()
            result = tui.backtester._compute_results()
        else:
            result = runner.run()

        _print_result(result)
    except KeyboardInterrupt:
        status = "interrupted"
        raise
    except Exception as exc:
        status = "error"
        error = str(exc)
        raise
    finally:
        _append_run_end_event(result=result, status=status, error=error)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Error: Interrupted by user", file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:
        # Default to concise user-facing errors.
        # Set BACKTEST_TRACEBACK=1 to re-raise with full traceback for debugging.
        if os.getenv("BACKTEST_TRACEBACK") == "1":
            raise
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
