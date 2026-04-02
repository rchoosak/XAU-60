#!/usr/bin/env python3
"""
High-performance Backtesting Dashboard TUI (Rich + Plotext)
Optimized for 244x66 terminal, but adaptive to any terminal size.

Controls:
- SPACE: Toggle RUNNING / PAUSED
- R: Reload config and restart backtest
- LEFT / RIGHT: Previous / Next ORDER HISTORY page
- Q: Quit
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import random
import select
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

import plotext as plt
from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

TUI_BUILD = "2026-04-01-candle-debug1"


# =========================
# Data Models
# =========================

@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass
class ActiveOrder:
    ticket: int
    side: str
    lots: float
    entry: float
    sl: float
    tp: float
    pnl: float
    open_time: Optional[datetime] = None


@dataclass
class ClosedOrder:
    ticket: int
    strategy: str
    side: str
    lots: float
    entry: float
    exit: float
    profit: float
    open_time: datetime
    close_time: datetime
    reason: str


@dataclass
class DashboardState:
    status: str
    progress: float
    account_info: Dict[str, object]
    backtest_info: Dict[str, object]
    candles: Sequence[Candle]
    active_orders: Sequence[ActiveOrder]
    order_history: Sequence[ClosedOrder]


DEFAULT_BACKTEST_INFO: Dict[str, str] = {}


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_dt(value: Any, default: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return default
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt)
        except ValueError:
            continue
    return default


def _load_run_backtest_module() -> Any:
    try:
        return importlib.import_module("scripts.run_backtest")
    except ModuleNotFoundError:
        module_path = Path(__file__).resolve().parent / "run_backtest.py"
        spec = importlib.util.spec_from_file_location("run_backtest", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load run_backtest module from {module_path}") from None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


# =========================
# Keyboard Polling
# =========================

class KeyPoller:
    """Non-blocking key polling for Unix/macOS and Windows."""

    def __init__(self) -> None:
        self._is_tty = sys.stdin.isatty()
        self._fd = None
        self._old_settings = None
        self._win = sys.platform.startswith("win")

    def __enter__(self) -> "KeyPoller":
        if not self._is_tty:
            return self
        if self._win:
            return self
        import termios
        import tty

        self._fd = sys.stdin.fileno()
        self._old_settings = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._is_tty or self._win:
            return
        if self._fd is None or self._old_settings is None:
            return
        import termios

        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)

    def poll(self) -> Optional[str]:
        if not self._is_tty:
            return None

        if self._win:
            import msvcrt

            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    if msvcrt.kbhit():
                        ext = msvcrt.getwch()
                        if ext == "H":
                            return "UP"
                        if ext == "P":
                            return "DOWN"
                        if ext == "K":
                            return "LEFT"
                        if ext == "M":
                            return "RIGHT"
                        if ext == "I":
                            return "PGUP"
                        if ext == "Q":
                            return "PGDN"
                        return ext
                    return None
                return ch
            return None

        dr, _, _ = select.select([sys.stdin], [], [], 0)
        if dr:
            ch = sys.stdin.read(1)
            if ch != "\x1b":
                return ch
            seq = ""
            for _ in range(5):
                dr2, _, _ = select.select([sys.stdin], [], [], 0)
                if dr2:
                    seq += sys.stdin.read(1)
                else:
                    break
            if seq == "[A":
                return "UP"
            if seq == "[B":
                return "DOWN"
            if seq == "[D":
                return "LEFT"
            if seq == "[C":
                return "RIGHT"
            if seq == "[5~":
                return "PGUP"
            if seq == "[6~":
                return "PGDN"
            return ch
        return None


# =========================
# Mock Feed (replace with real backtest adapter)
# =========================

class MockBacktestFeed:
    def __init__(
        self,
        seed: int = 7,
        max_candles: int = 600,
        initial_balance: float = 10000.0,
        start_price: float = 3395.0,
        start_time: Optional[datetime] = None,
        progress_step: float = 0.0008,
        open_order_chance: float = 0.10,
        max_active_orders: int = 12,
        history_limit: int = 500,
        warmup_candles: int = 240,
        backtest_info: Optional[Dict[str, str]] = None,
    ) -> None:
        random.seed(seed)
        self._running = True
        self._start = start_time or datetime(2025, 1, 1, 8, 0, 0)
        self._current_time = self._start
        self._progress = 0.0
        self._progress_step = max(0.00001, progress_step)

        self._price = start_price
        self._balance = initial_balance
        self._equity = initial_balance
        self._initial_balance = initial_balance
        self._max_dd = 0.0
        self._peak_balance = self._balance
        self._open_order_chance = min(1.0, max(0.0, open_order_chance))
        self._max_active_orders = max(1, max_active_orders)
        self._backtest_info = dict(DEFAULT_BACKTEST_INFO)
        self._backtest_info["Initial Cap"] = f"$ {initial_balance:,.2f}"
        if backtest_info:
            for k, v in backtest_info.items():
                if v is not None and str(v).strip():
                    self._backtest_info[str(k)] = str(v)

        self._candles: Deque[Candle] = deque(maxlen=max_candles)
        self._active: Dict[int, ActiveOrder] = {}
        self._history: Deque[ClosedOrder] = deque(maxlen=history_limit)

        self._ticket = 1880
        self._warmup_candles(max(0, warmup_candles))

    def set_running(self, running: bool) -> None:
        self._running = running

    def _warmup_candles(self, n: int) -> None:
        for _ in range(n):
            self._step_market()

    def step(self) -> bool:
        if not self._running:
            return True
        self._step_market()
        self._step_orders()

        self._progress = min(1.0, self._progress + self._progress_step)
        return True

    def _step_market(self) -> None:
        o = self._price
        drift = random.uniform(-1.2, 1.2)
        c = max(2000.0, o + drift)
        h = max(o, c) + random.uniform(0.1, 1.0)
        l = min(o, c) - random.uniform(0.1, 1.0)

        self._price = c
        self._current_time += timedelta(minutes=1)
        self._candles.append(Candle(ts=self._current_time, open=o, high=h, low=l, close=c))

    def _new_order(self) -> ActiveOrder:
        self._ticket += 1
        side = random.choice(["BUY", "SELL"])
        lots = random.choice([0.01, 0.02, 0.03])
        entry = self._price
        sl_dist = random.uniform(8.0, 14.0)
        tp_dist = random.uniform(10.0, 20.0)
        if side == "BUY":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist
        return ActiveOrder(
            ticket=self._ticket,
            side=side,
            lots=lots,
            entry=entry,
            sl=sl,
            tp=tp,
            pnl=0.0,
            open_time=self._current_time,
        )

    def _step_orders(self) -> None:
        if random.random() < self._open_order_chance and len(self._active) < self._max_active_orders:
            o = self._new_order()
            self._active[o.ticket] = o

        to_close: List[int] = []
        floating = 0.0

        for ticket, o in self._active.items():
            if o.side == "BUY":
                pnl = (self._price - o.entry) * (100.0 * o.lots)
                hit_tp = self._price >= o.tp
                hit_sl = self._price <= o.sl
            else:
                pnl = (o.entry - self._price) * (100.0 * o.lots)
                hit_tp = self._price <= o.tp
                hit_sl = self._price >= o.sl

            o.pnl = pnl
            floating += pnl

            if hit_tp or hit_sl or random.random() < 0.02:
                reason = "TP auto-close" if hit_tp else "SL auto-close" if hit_sl else "Manual Close"
                self._history.appendleft(
                    ClosedOrder(
                        ticket=o.ticket,
                        strategy="Mock Strategy",
                        side=o.side,
                        lots=o.lots,
                        entry=o.entry,
                        exit=self._price,
                        profit=pnl,
                        open_time=o.open_time or self._current_time,
                        close_time=self._current_time,
                        reason=reason,
                    )
                )
                self._balance += pnl
                to_close.append(ticket)

        for ticket in to_close:
            self._active.pop(ticket, None)

        self._equity = self._balance + floating
        self._peak_balance = max(self._peak_balance, self._equity)
        if self._peak_balance > 0:
            self._max_dd = max(self._max_dd, (self._peak_balance - self._equity) / self._peak_balance * 100.0)

    def snapshot(self, running: bool) -> DashboardState:
        gross_profit = sum(max(0.0, o.profit) for o in self._history)
        gross_loss = sum(min(0.0, o.profit) for o in self._history)
        wins = sum(1 for o in self._history if o.profit > 0)
        total = len(self._history)
        pf = (gross_profit / abs(gross_loss)) if gross_loss < 0 else 0.0
        wr = (wins / total * 100.0) if total > 0 else 0.0

        floating = self._equity - self._balance
        net_profit = self._balance - self._initial_balance
        net_profit_pct = (net_profit / self._initial_balance * 100.0) if self._initial_balance > 0 else 0.0
        account = {
            "Balance": f"$ {self._balance:,.2f}",
            "Equity": f"$ {self._equity:,.2f}",
            "Floating PnL": f"{floating:+,.2f}",
            "Max Drawdown": f"{self._max_dd:.2f} %",
            "Profit Factor": f"{pf:.2f}",
            "Win Rate": f"{wr:.1f} %",
            "Net Profit": f"$ {net_profit:,.2f}",
            "Net Profit (%)": f"{net_profit_pct:+.2f} %",
            "Gross Profit": f"$ {gross_profit:,.2f}",
            "Gross Loss": f"$ {gross_loss:,.2f}",
        }

        backtest = dict(self._backtest_info)

        return DashboardState(
            status="RUNNING" if running else "PAUSED",
            progress=self._progress,
            account_info=account,
            backtest_info=backtest,
            candles=list(self._candles),
            active_orders=list(self._active.values()),
            order_history=list(self._history),
        )


class RealBacktestFeed:
    """
    Real backtest feed backed by scripts/run_backtest.py engine.
    This keeps TradingBot/Risk/Executor behavior identical to the old backtest runner.
    """

    def __init__(
        self,
        config_path: str,
        strategy_override: Optional[str] = None,
        strategies_override: Optional[str] = None,
        start_override: Optional[str] = None,
        end_override: Optional[str] = None,
    ) -> None:
        self.config_path = config_path
        self.strategy_override = strategy_override
        self.strategies_override = strategies_override
        self.start_override = start_override
        self.end_override = end_override

        self._running = True
        self.rb = _load_run_backtest_module()
        self.runner = None
        self.cfg_runtime: Dict[str, Any] = {}
        self.sim_cfg: Dict[str, Any] = {}
        self._build_runner()

    def _build_runner(self) -> None:
        cfg_path = Path(self.config_path).expanduser()
        if not cfg_path.exists():
            raise FileNotFoundError(f"Config not found: {cfg_path}")

        raw = load_dashboard_config(str(cfg_path))
        cfg = self.rb._normalize_config_keys(raw)
        data_cfg = self.rb._normalize_config_keys(cfg.get("data", {}) or {})
        sim_cfg = self.rb._normalize_config_keys(cfg.get("simulation", {}) or {})
        exec_cfg = self.rb._normalize_config_keys(cfg.get("execution", {}) or {})

        symbol = cfg.get("symbol") or data_cfg.get("symbol") or "XAUUSD"
        base_timeframe = self.rb._normalize_timeframe(cfg.get("timeframe") or data_cfg.get("timeframe") or "M1")
        data_path = data_cfg.get("path") or cfg.get("data_path")
        if not data_path:
            raise ValueError("Missing data.path in backtest config")

        raw_start = self.start_override if self.start_override is not None else (data_cfg.get("start") or cfg.get("start"))
        raw_end = self.end_override if self.end_override is not None else (data_cfg.get("end") or cfg.get("end"))
        start = self.rb._parse_date(raw_start)
        end = self.rb._parse_date(raw_end)
        if start and end and start > end:
            raise ValueError(f"Invalid date range: start ({start}) > end ({end})")

        cfg.setdefault("data", {})
        if isinstance(cfg.get("data"), dict):
            cfg["data"]["start"] = raw_start
            cfg["data"]["end"] = raw_end

        warmup = int(cfg.get("warmup", data_cfg.get("warmup", 200)))
        df = self.rb._load_data(str(data_path), start, end)

        point = float(sim_cfg.get("point", 0.01 if str(symbol).upper().startswith("XAU") else 0.0001))
        digits = int(sim_cfg.get("digits", 2 if str(symbol).upper().startswith("XAU") else 5))
        contract_size = float(sim_cfg.get("contract_size", 100.0 if str(symbol).upper().startswith("XAU") else 100000.0))
        spread_points = float(sim_cfg.get("spread_points", 25.0 if str(symbol).upper().startswith("XAU") else 10.0))

        replay = self.rb.ReplayMT5Connector(
            symbol=symbol,
            data=df,
            base_timeframe=base_timeframe,
            initial_balance=float(sim_cfg.get("initial_balance", 100000.0)),
            leverage=int(sim_cfg.get("leverage", 100)),
            point=point,
            digits=digits,
            contract_size=contract_size,
            spread_points=spread_points,
            tick_size=float(sim_cfg.get("tick_size", point)),
            tick_value=float(sim_cfg.get("tick_value", 1.0 if str(symbol).upper().startswith("XAU") else 10.0)),
            min_lot=float(sim_cfg.get("min_lot", 0.01)),
            max_lot=float(sim_cfg.get("max_lot", 100.0)),
            lot_step=float(sim_cfg.get("lot_step", 0.01)),
        )

        selected: List[str] = []
        selected.extend(self.rb._parse_strategy_list(exec_cfg.get("strategies")))
        if exec_cfg.get("strategy"):
            selected.extend(self.rb._parse_strategy_list(exec_cfg.get("strategy")))
        if self.strategy_override:
            selected = self.rb._parse_strategy_list(self.strategy_override)
        if self.strategies_override:
            selected = self.rb._parse_strategy_list(self.strategies_override)
        selected = list(dict.fromkeys(selected))

        settings_path = str(cfg.get("settings_path", "config/settings.yaml"))
        bot = self.rb._prepare_bot(
            settings_path=settings_path,
            replay_mt5=replay,
            selected_strategies=selected,
            disable_console_log=True,
            disable_file_log=True,
            log_level_override="ERROR",
        )

        self.runner = self.rb.LiveLikeBotBacktester(
            bot=bot,
            replay_mt5=replay,
            config=cfg,
            warmup=warmup,
            stop_priority=str(sim_cfg.get("stop_priority", "sl_first")),
            close_open_positions_at_end=bool(sim_cfg.get("close_open_positions_at_end", True)),
        )
        self.runner.start()
        self.cfg_runtime = cfg
        self.sim_cfg = sim_cfg
        self._running = True

    def set_running(self, running: bool) -> None:
        self._running = bool(running)

    def step(self) -> bool:
        if not self._running:
            return True
        if self.runner is None:
            return False
        state = self.runner.step()
        return state is not None

    def snapshot(self, running: bool) -> DashboardState:
        if self.runner is None:
            return DashboardState(
                status="PAUSED",
                progress=0.0,
                account_info={},
                backtest_info={},
                candles=[],
                active_orders=[],
                order_history=[],
            )

        runner = self.runner
        rb = self.rb

        total_bars = max(1, len(runner.data) - runner.warmup)
        done_bars = max(0, runner.current_index - runner.warmup)
        progress = min(1.0, done_bars / total_bars)

        account = runner.mt5.get_account_info()
        trades = list(runner.trades)
        wins = [t for t in trades if float(t.get("profit", 0.0)) > 0]
        losses = [t for t in trades if float(t.get("profit", 0.0)) < 0]
        gross_profit = sum(float(t.get("profit", 0.0)) for t in wins)
        gross_loss = sum(float(t.get("profit", 0.0)) for t in losses)
        net_profit = float(runner.balance - runner.initial_balance)
        net_profit_pct = (net_profit / runner.initial_balance * 100.0) if runner.initial_balance > 0 else 0.0
        pf = (gross_profit / abs(gross_loss)) if gross_loss < 0 else 0.0
        wr = (len(wins) / len(trades) * 100.0) if trades else 0.0
        expectancy = (net_profit / len(trades)) if trades else 0.0
        max_dd = rb._calc_max_drawdown(runner.equity_curve, runner.initial_balance)

        account_info: Dict[str, object] = {
            "Balance": f"$ {runner.balance:,.2f}",
            "Equity": f"$ {runner.equity:,.2f}",
            "Floating PnL": f"{(runner.equity - runner.balance):+,.2f}",
            "Max Drawdown": f"{max_dd:.2f} %",
            "Profit Factor": f"{pf:.2f}",
            "Win Rate": f"{wr:.1f} %",
            "Net Profit": f"$ {net_profit:,.2f}",
            "Net Profit (%)": f"{net_profit_pct:+.2f} %",
            "Gross Profit": f"$ {gross_profit:,.2f}",
            "Gross Loss": f"$ {gross_loss:,.2f}",
            "Expectancy": f"$ {expectancy:,.2f}",
            "Trades": str(len(trades)),
        }
        if account is not None:
            account_info["Margin"] = f"$ {float(account.margin):,.2f}"
            account_info["Free Margin"] = f"$ {float(account.free_margin):,.2f}"
            account_info["Margin Level"] = f"{float(account.margin_level):,.2f} %"

        enabled = runner.bot.strategy_loader.get_enabled_strategies()
        strategy_name = ", ".join(enabled.keys()) if enabled else "none"
        data_cfg = self.cfg_runtime.get("data", {}) if isinstance(self.cfg_runtime.get("data"), dict) else {}
        backtest_info: Dict[str, object] = {
            "Symbol": runner.symbol,
            "Timeframe": runner.timeframe,
            "Strategy": strategy_name,
            "Spread": f"{self.sim_cfg.get('spread_points', '')} pts" if self.sim_cfg.get("spread_points") is not None else None,
            "Start Date": data_cfg.get("start"),
            "End Date": data_cfg.get("end"),
            "Initial Cap": f"$ {runner.initial_balance:,.2f}",
            "Warmup": runner.warmup,
            "Leverage": self.sim_cfg.get("leverage"),
            "Data File": data_cfg.get("path"),
            "Stop Priority": self.sim_cfg.get("stop_priority"),
            "Close At End": self.sim_cfg.get("close_open_positions_at_end"),
        }

        pos_by_ticket: Dict[int, Any] = {int(p.ticket): p for p in runner.mt5.get_positions()}
        active_orders: List[ActiveOrder] = []
        for ticket in sorted(pos_by_ticket.keys()):
            p = pos_by_ticket[ticket]
            side = p.type.name if hasattr(p.type, "name") else str(p.type)
            active_orders.append(
                ActiveOrder(
                    ticket=int(p.ticket),
                    side=side,
                    lots=float(getattr(p, "volume", 0.0)),
                    entry=float(getattr(p, "open_price", 0.0)),
                    sl=float(getattr(p, "stop_loss", 0.0)),
                    tp=float(getattr(p, "take_profit", 0.0)),
                    pnl=float(getattr(p, "profit", 0.0)),
                )
            )

        history: List[ClosedOrder] = []
        for idx, rec in enumerate(reversed(trades), start=1):
            signal = str(rec.get("signal", ""))
            close_time = _to_dt(rec.get("exit_time"), datetime.now())
            ticket = _to_int(rec.get("ticket"), 0)
            if ticket <= 0:
                ticket = idx
            history.append(
                ClosedOrder(
                    ticket=ticket,
                    strategy=str(rec.get("strategy", "")) or "Unknown",
                    side=signal,
                    lots=_to_float(rec.get("lot_size"), 0.0),
                    entry=_to_float(rec.get("entry_price"), 0.0),
                    exit=_to_float(rec.get("exit_price"), 0.0),
                    profit=_to_float(rec.get("profit"), 0.0),
                    open_time=_to_dt(rec.get("entry_time"), close_time),
                    close_time=close_time,
                    reason=str(rec.get("reason", "")),
                )
            )

        end_idx = min(len(runner.data), max(1, runner.current_index + 1))
        view = runner.data.iloc[:end_idx].tail(600)
        candles = [
            Candle(
                ts=_to_dt(row["time"], datetime.now()),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            for _, row in view.iterrows()
        ]

        if progress >= 1.0:
            status = "FINISHED"
        else:
            status = "RUNNING" if running else "PAUSED"

        return DashboardState(
            status=status,
            progress=progress,
            account_info=account_info,
            backtest_info=backtest_info,
            candles=candles,
            active_orders=active_orders,
            order_history=history,
        )


def load_dashboard_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path).expanduser()
    if not path.exists():
        return {}

    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")

    if suffix == ".json":
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}

    try:
        import yaml  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"YAML config requires PyYAML: {exc}") from exc

    parsed = yaml.safe_load(text) or {}
    return parsed if isinstance(parsed, dict) else {}


def build_feed_from_config(config: Optional[Dict[str, Any]]) -> MockBacktestFeed:
    cfg = config or {}
    feed_cfg = cfg.get("feed", {}) if isinstance(cfg.get("feed", {}), dict) else {}
    info_cfg = cfg.get("backtest_info", {}) if isinstance(cfg.get("backtest_info", {}), dict) else {}
    data_cfg = cfg.get("data", {}) if isinstance(cfg.get("data", {}), dict) else {}
    sim_cfg = cfg.get("simulation", {}) if isinstance(cfg.get("simulation", {}), dict) else {}
    exec_cfg = cfg.get("execution", {}) if isinstance(cfg.get("execution", {}), dict) else {}

    symbol = str(cfg.get("symbol", "XAUUSD")).strip() or "XAUUSD"
    timeframe = str(cfg.get("timeframe", "M1")).strip() or "M1"
    data_path = str(data_cfg.get("path", "")).strip()
    data_start = data_cfg.get("start")
    data_end = data_cfg.get("end")
    spread_points = _to_float(sim_cfg.get("spread_points"), 25.0)
    initial_balance = _to_float(sim_cfg.get("initial_balance"), 10000.0)
    leverage = _to_int(sim_cfg.get("leverage"), 100)
    warmup = _to_int(cfg.get("warmup"), 240)

    selected_strategy = str(exec_cfg.get("strategy", "")).strip()
    selected_strategies = exec_cfg.get("strategies")
    if not selected_strategy and isinstance(selected_strategies, list):
        selected_strategy = ", ".join(str(s).strip() for s in selected_strategies if str(s).strip())
    if not selected_strategy:
        selected_strategy = "Auto (enabled strategies)"

    clean_info: Dict[str, str] = {}
    clean_info["Symbol"] = symbol
    clean_info["Timeframe"] = timeframe
    clean_info["Strategy"] = selected_strategy
    clean_info["Spread"] = f"{spread_points:g} pts"
    clean_info["Initial Cap"] = f"$ {initial_balance:,.2f}"
    clean_info["Leverage"] = f"{leverage:d}"
    clean_info["Warmup"] = f"{warmup:d}"
    if data_start:
        clean_info["Start Date"] = str(data_start)
    if data_end:
        clean_info["End Date"] = str(data_end)
    if data_path:
        clean_info["Data File"] = data_path

    # Optional model label, if caller provides it in config.
    model_name = str(cfg.get("model") or sim_cfg.get("model") or "").strip()
    if model_name:
        clean_info["Model"] = model_name

    stop_priority = str(sim_cfg.get("stop_priority", "")).strip()
    if stop_priority:
        clean_info["Stop Priority"] = stop_priority

    close_at_end = sim_cfg.get("close_open_positions_at_end")
    if close_at_end is not None:
        clean_info["Close At End"] = str(bool(close_at_end))

    # Allow explicit overrides via backtest_info block.
    for k, v in info_cfg.items():
        if v is None:
            continue
        text = str(v).strip()
        if not text:
            continue
        clean_info[str(k)] = text

    default_start_time = _to_dt(data_start, datetime(2025, 1, 1, 8, 0, 0))

    return MockBacktestFeed(
        seed=_to_int(feed_cfg.get("seed"), 7),
        max_candles=_to_int(feed_cfg.get("max_candles"), 600),
        initial_balance=_to_float(feed_cfg.get("initial_balance"), initial_balance),
        start_price=_to_float(feed_cfg.get("start_price"), 3395.0),
        start_time=_to_dt(feed_cfg.get("start_time"), default_start_time),
        progress_step=_to_float(feed_cfg.get("progress_step"), 0.0008),
        open_order_chance=_to_float(feed_cfg.get("open_order_chance"), 0.10),
        max_active_orders=_to_int(feed_cfg.get("max_active_orders"), 12),
        history_limit=_to_int(feed_cfg.get("history_limit"), 500),
        warmup_candles=_to_int(feed_cfg.get("warmup_candles"), warmup),
        backtest_info=clean_info,
    )


# =========================
# Renderers
# =========================

def make_progress_bar(progress: float, width: int = 44) -> Text:
    width = max(10, int(width))
    p = max(0.0, min(1.0, progress))
    fill = int(round(width * p))
    empty = max(0, width - fill)

    bar = Text("[", style="white")
    if fill > 0:
        bar.append("█" * fill, style="bold bright_green")
    if empty > 0:
        bar.append("░" * empty, style="grey46")
    bar.append("]", style="white")
    return bar


def _numeric_value_style(text: str) -> Optional[str]:
    cleaned = text.replace("$", "").replace(",", "").replace("%", "").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if value > 0:
        return "bold bright_green"
    if value < 0:
        return "bold bright_red"
    return None


def format_fields(fields: Dict[str, object], min_lines: int, colorize_numeric: bool = False) -> Text:
    rows: List[Tuple[str, str, Optional[str]]] = []
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        value_style = _numeric_value_style(text) if colorize_numeric else None
        rows.append((f"{key:<14} ", text, value_style))

    while len(rows) < min_lines:
        rows.append(("", "", None))

    out = Text(no_wrap=True)
    for i, (k, v, value_style) in enumerate(rows):
        if k or v:
            out.append(k, style="white")
            out.append(v, style=value_style or "white")
        if i < len(rows) - 1:
            out.append("\n")
    return out


def build_active_orders_table(orders: Sequence[ActiveOrder], max_rows: int) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, expand=True, show_lines=False)
    table.add_column("TICKET", justify="right", style="cyan", no_wrap=True)
    table.add_column("TYPE", justify="center", no_wrap=True)
    table.add_column("LOTS", justify="right")
    table.add_column("ENTRY", justify="right")
    table.add_column("S/L", justify="right")
    table.add_column("T/P", justify="right")
    table.add_column("PnL", justify="right")

    for o in list(orders)[: max(1, max_rows)]:
        pnl_style = "green" if o.pnl >= 0 else "red"
        side_style = "green" if o.side == "BUY" else "red"
        table.add_row(
            str(o.ticket),
            f"[{side_style}]{o.side}[/{side_style}]",
            f"{o.lots:.2f}",
            f"{o.entry:.2f}",
            f"{o.sl:.2f}",
            f"{o.tp:.2f}",
            f"[{pnl_style}]{o.pnl:+.2f}[/{pnl_style}]",
        )

    if not orders:
        table.add_row("-", "-", "-", "-", "-", "-", "-")

    return table


def build_order_history_table(history: Sequence[ClosedOrder], max_rows: int) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, expand=True, show_lines=False)
    table.add_column("TICKET", justify="right", style="cyan", no_wrap=True)
    table.add_column("STRATEGY", justify="left")
    table.add_column("TYPE", justify="center", no_wrap=True)
    table.add_column("LOTS", justify="right")
    table.add_column("ENTRY", justify="right")
    table.add_column("EXIT", justify="right")
    table.add_column("PROFIT", justify="right")
    table.add_column("OPEN TIME", justify="left", no_wrap=True)
    table.add_column("CLOSE TIME", justify="left", no_wrap=True)
    table.add_column("REASON", justify="left")

    for o in list(history)[: max(1, max_rows)]:
        pnl_style = "green" if o.profit >= 0 else "red"
        side_style = "green" if o.side == "BUY" else "red"
        table.add_row(
            str(o.ticket),
            o.strategy,
            f"[{side_style}]{o.side}[/{side_style}]",
            f"{o.lots:.2f}",
            f"{o.entry:.2f}",
            f"{o.exit:.2f}",
            f"[{pnl_style}]{o.profit:+.2f}[/{pnl_style}]",
            o.open_time.strftime("%Y-%m-%d %H:%M:%S"),
            o.close_time.strftime("%Y-%m-%d %H:%M:%S"),
            o.reason,
        )

    if not history:
        table.add_row("-", "-", "-", "-", "-", "-", "-", "-", "-", "-")

    return table


def build_candlestick_panel(candles: Sequence[Candle], region_width: int, region_height: int) -> Panel:
    # Calculate real plotting area from the layout region to prevent overflow.
    # We reserve some room for panel borders and axis labels.
    plot_width = max(20, region_width - 4)
    plot_height = max(10, region_height - 4)

    if len(candles) < 3:
        return Panel(Text("Waiting for candles..."), title="[CANDLESTICK GRAPH]", padding=(0, 1))

    # Keep fewer bars than panel width so candlesticks remain visually distinct.
    visible_count = min(len(candles), max(28, plot_width // 3))
    visible = candles[-visible_count:]
    x = list(range(len(visible)))
    raw_data = {
        "Open": [c.open for c in visible],
        "Close": [c.close for c in visible],
        "High": [c.high for c in visible],
        "Low": [c.low for c in visible],
    }
    data = {
        "Open": list(raw_data["Open"]),
        "Close": list(raw_data["Close"]),
        "High": list(raw_data["High"]),
        "Low": list(raw_data["Low"]),
    }

    # Flat windows (open=high=low=close for many bars) are common around session gaps
    # and make candlesticks look like a single thin line. Add a tiny display-only
    # minimum wick/body so the chart remains readable without changing strategy data.
    raw_lo = min(raw_data["Low"])
    raw_hi = max(raw_data["High"])
    ref = max(abs(raw_hi), abs(raw_lo), 1.0)
    min_wick = max(ref * 0.00006, 0.20)
    min_body = min_wick * 0.35

    for i in range(len(x)):
        o = data["Open"][i]
        c = data["Close"][i]
        h = data["High"][i]
        l = data["Low"][i]

        if (h - l) < min_wick:
            mid = (h + l) / 2.0
            h = mid + (min_wick / 2.0)
            l = mid - (min_wick / 2.0)

        if abs(c - o) < min_body:
            direction = 1.0 if (i % 2 == 0) else -1.0
            c = o + (direction * min_body)
            if c > h:
                h = c + (min_body * 0.2)
            if c < l:
                l = c - (min_body * 0.2)

        data["Open"][i] = o
        data["Close"][i] = c
        data["High"][i] = h
        data["Low"][i] = l

    plt.clf()
    plt.theme("dark")
    plt.plotsize(plot_width, plot_height)

    plt.candlestick(x, data)
    lo = min(data["Low"])
    hi = max(data["High"])
    span = max(hi - lo, min_wick)
    pad = span * 0.15
    plt.ylim(lo - pad, hi + pad)

    # Sparse x labels to keep chart clean and avoid overlap.
    steps = max(1, len(visible) // 8)
    xt = list(range(0, len(visible), steps))
    xl = [visible[i].ts.strftime("%H:%M") for i in xt]
    plt.xticks(xt, xl)
    plt.grid(True, True)

    chart_ansi = plt.build()
    chart_text = Text.from_ansi(chart_ansi)
    flat_run = 1
    for i in range(len(visible) - 1, 0, -1):
        a = visible[i]
        b = visible[i - 1]
        if a.open == b.open and a.high == b.high and a.low == b.low and a.close == b.close:
            flat_run += 1
        else:
            break
    info_line = (
        f"last={visible[-1].ts.strftime('%Y-%m-%d %H:%M')} | bars={len(candles)} | "
        f"flat_run={flat_run} | mode=candlestick | build={TUI_BUILD}"
    )
    chart_text.append("\n")
    chart_text.append(info_line, style="bold white")
    return Panel(
        chart_text,
        title="[CANDLESTICK GRAPH]",
        padding=(0, 0),
        box=box.ROUNDED,
    )


# =========================
# Main Dashboard
# =========================

class BacktestDashboardTUI:
    def __init__(
        self,
        feed: Any,
        config_path: str,
        refresh_hz: int = 8,
        initial_speed: int = 1,
        feed_builder: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.console = Console()
        self.feed = feed
        self.config_path = config_path
        self.feed_builder = feed_builder
        self.refresh_hz = max(4, min(10, refresh_hz))
        self.running = True
        self.should_quit = False
        self.last_notice = "Ready"
        self.speed_min = 1
        self.speed_max = 100
        self.speed_multiplier = max(self.speed_min, min(self.speed_max, int(initial_speed)))
        self.history_page = 0
        self.log_messages: Deque[str] = deque(maxlen=300)
        self.last_logged_bar_time: Optional[datetime] = None
        self.add_log("Dashboard initialized")
        self.add_log(f"Initial speed {self.speed_multiplier}x")

    def add_log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_messages.append(f"{stamp} | {message}")

    def restart_backtest(self) -> None:
        try:
            if self.feed_builder is None:
                raise RuntimeError("No feed builder configured")
            self.feed = self.feed_builder()
            self.running = True
            self.feed.set_running(True)
            self.history_page = 0
            stamp = datetime.now().strftime("%H:%M:%S")
            self.last_notice = f"Restarted + reloaded config at {stamp}"
            self.last_logged_bar_time = None
            self.add_log(self.last_notice)
        except Exception as exc:
            self.last_notice = f"Restart failed: {exc}"
            self.add_log(self.last_notice)

    def increase_speed(self) -> None:
        old = self.speed_multiplier
        self.speed_multiplier = min(self.speed_max, self.speed_multiplier + 1)
        if self.speed_multiplier != old:
            self.last_notice = f"Speed set to {self.speed_multiplier}x"
            self.add_log(self.last_notice)

    def decrease_speed(self) -> None:
        old = self.speed_multiplier
        self.speed_multiplier = max(self.speed_min, self.speed_multiplier - 1)
        if self.speed_multiplier != old:
            self.last_notice = f"Speed set to {self.speed_multiplier}x"
            self.add_log(self.last_notice)

    def _history_prev_page(self) -> None:
        old = self.history_page
        self.history_page = max(0, self.history_page - 1)
        if self.history_page != old:
            self.last_notice = "History page: previous"

    def _history_next_page(self) -> None:
        old = self.history_page
        self.history_page += 1
        if self.history_page != old:
            self.last_notice = "History page: next"

    def _build_layout(self, state: DashboardState) -> Layout:
        term_w = self.console.size.width
        term_h = self.console.size.height

        left_w = term_w // 2
        right_w = term_w - left_w
        right_top_h = term_h // 2
        right_bottom_h = term_h - right_top_h

        # Single-line header content + borders
        header_h = 3
        info_h = 18 if term_h >= 60 else max(12, term_h // 4)
        min_log_h = 6
        active_h_target = right_bottom_h
        max_active_h = max(8, term_h - header_h - info_h - min_log_h)
        active_h = max(8, min(active_h_target, max_active_h))

        root = Layout(name="root")
        root.split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1),
        )

        root["left"].split_column(
            Layout(name="header", size=header_h),
            Layout(name="info_row", size=info_h),
            Layout(name="logs", ratio=1),
            Layout(name="active", size=active_h),
        )

        root["right"].split_column(
            Layout(name="chart", size=right_top_h),
            Layout(name="history", size=right_bottom_h),
        )

        # Header
        status_color = "green" if state.status == "RUNNING" else "yellow"
        if state.status == "FINISHED":
            status_color = "cyan"
        header_text = Text(no_wrap=True, overflow="crop")
        status_label = f"[● {state.status}]"
        latest_ts = state.candles[-1].ts.strftime("%Y-%m-%d %H:%M") if state.candles else "-"
        left_header = Text()
        left_header.append(status_label, style=f"bold {status_color}")
        left_header.append(" ")
        left_header.append(f"[{latest_ts}]", style="bold bright_cyan")
        left_header.append(" ")
        left_header.append(f"[x{self.speed_multiplier}]", style="bold bright_yellow")

        # Keep progress on the same line, aligned to the right, around 50% width.
        header_inner_w = max(20, left_w - 4)
        desired_bar_width = max(10, int(header_inner_w * 0.5))
        percent_label = f"{int(state.progress * 100)}%"
        progress_text = Text()
        progress_text.append(percent_label, style="bold white")
        progress_text.append(" ")
        progress_text.append_text(make_progress_bar(state.progress, desired_bar_width))

        total_plain = len(left_header.plain) + len(progress_text.plain)
        if total_plain > header_inner_w:
            # Shrink bar to preserve single-line layout.
            overflow = total_plain - header_inner_w
            resized_bar_width = max(10, desired_bar_width - overflow)
            progress_text = Text()
            progress_text.append(percent_label, style="bold white")
            progress_text.append(" ")
            progress_text.append_text(make_progress_bar(state.progress, resized_bar_width))

        padding_left = max(1, header_inner_w - len(left_header.plain) - len(progress_text.plain))
        header_text.append_text(left_header)
        header_text.append(" " * padding_left)
        header_text.append_text(progress_text)
        root["left"]["header"].update(Panel(header_text, padding=(0, 0), box=box.ROUNDED))

        # Info row split 25% + 25% (of total width) => 50/50 of left column
        root["left"]["info_row"].split_row(
            Layout(name="account", ratio=1),
            Layout(name="backtest", ratio=1),
        )

        info_inner_lines = max(3, info_h - 3)
        account_text = format_fields(state.account_info, info_inner_lines, colorize_numeric=True)
        backtest_text = format_fields(state.backtest_info, info_inner_lines)

        root["left"]["info_row"]["account"].update(
            Panel(account_text, title="[ACCOUNT INFO]", padding=(0, 1), box=box.ROUNDED)
        )
        root["left"]["info_row"]["backtest"].update(
            Panel(backtest_text, title="[BACKTEST INFO]", padding=(0, 1), box=box.ROUNDED)
        )

        # Log messages panel (fills space above active orders)
        log_panel_h = max(min_log_h, term_h - header_h - info_h - active_h)
        log_max_lines = max(1, log_panel_h - 3)
        log_lines = list(self.log_messages)[-log_max_lines:]
        if not log_lines:
            log_lines = ["-"]
        log_text = Text("\n".join(log_lines), no_wrap=True)
        root["left"]["logs"].update(
            Panel(log_text, title="[LOG MESSAGES]", padding=(0, 1), box=box.ROUNDED)
        )

        # Active orders table (same height as right-side order history when possible)
        active_max_rows = max(1, active_h - 6)
        active_table = build_active_orders_table(state.active_orders, active_max_rows)
        root["left"]["active"].update(
            Panel(
                active_table,
                title="[ACTIVE ORDERS & GRID STATUS]",
                subtitle=(
                    f"[CONTROLS] [SPACE] Pause/Run  [↑/↓] Speed  [←/→] History Page  [R] Restart  [Q] Quit | "
                    f"{self.last_notice} | build={TUI_BUILD}"
                ),
                subtitle_align="left",
                padding=(0, 1),
                box=box.ROUNDED,
            )
        )

        # Candlestick chart (right top 50%)
        chart_panel = build_candlestick_panel(
            state.candles,
            region_width=right_w,
            region_height=right_top_h,
        )
        root["right"]["chart"].update(chart_panel)

        # Order history (right bottom 50%)
        history_max_rows = max(1, right_bottom_h - 6)
        history_all = list(state.order_history)
        history_total = len(history_all)
        total_pages = max(1, (history_total + history_max_rows - 1) // history_max_rows)
        self.history_page = min(max(0, self.history_page), total_pages - 1)
        page_start = self.history_page * history_max_rows
        visible_history = history_all[page_start:page_start + history_max_rows]

        if history_total > 0:
            row_start = page_start + 1
            row_end = min(history_total, page_start + len(visible_history))
            history_pos = f"page {self.history_page + 1}/{total_pages} | rows {row_start}-{row_end}/{history_total}"
        else:
            history_pos = "page 1/1 | rows 0/0"

        history_table = build_order_history_table(visible_history, history_max_rows)
        root["right"]["history"].update(
            Panel(
                history_table,
                title="[ORDER HISTORY (CLOSED)]",
                subtitle=f"[←/→] Prev/Next Page  |  {history_pos}",
                subtitle_align="left",
                padding=(0, 1),
                box=box.ROUNDED,
            )
        )

        return root

    def run(self, demo_seconds: Optional[float] = None) -> None:
        start = time.monotonic()

        with KeyPoller() as keys:
            with Live(
                self._build_layout(self.feed.snapshot(self.running)),
                console=self.console,
                refresh_per_second=self.refresh_hz,
                screen=True,
            ) as live:
                while not self.should_quit:
                    key = keys.poll()
                    if key:
                        if key == "UP":
                            self.increase_speed()
                        elif key == "DOWN":
                            self.decrease_speed()
                        elif key == "LEFT":
                            self._history_prev_page()
                        elif key == "RIGHT":
                            self._history_next_page()
                        else:
                            k = key.lower()
                            if k == "q":
                                self.add_log("Quit requested")
                                self.should_quit = True
                                break
                            if key == " ":
                                self.running = not self.running
                                self.feed.set_running(self.running)
                                self.add_log("Resumed" if self.running else "Paused")
                            elif k == "r":
                                self.restart_backtest()

                    if self.running:
                        progressed = True
                        for _ in range(max(1, self.speed_multiplier)):
                            progressed = self.feed.step()
                            if progressed is False:
                                break
                        if progressed is False:
                            self.running = False
                            self.feed.set_running(False)
                            self.last_notice = "Backtest finished. Press [R] to reload config and restart."
                            self.add_log("Backtest finished")

                    state = self.feed.snapshot(self.running)
                    if state.candles:
                        bar_ts = state.candles[-1].ts
                        if self.last_logged_bar_time != bar_ts:
                            self.last_logged_bar_time = bar_ts
                            c = state.candles[-1]
                            self.add_log(
                                f"Bar {bar_ts.strftime('%Y-%m-%d %H:%M')} "
                                f"O:{c.open:.3f} H:{c.high:.3f} L:{c.low:.3f} C:{c.close:.3f}"
                            )
                    live.update(self._build_layout(state), refresh=True)

                    if demo_seconds is not None and (time.monotonic() - start) >= demo_seconds:
                        break

                    time.sleep(1.0 / self.refresh_hz)



# =========================
# Entry
# =========================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtesting Dashboard TUI (Rich + Plotext)")
    parser.add_argument("--fps", type=int, default=8, help="Live refresh rate (4-10 Hz)")
    parser.add_argument("--speed", type=int, default=1, help="Initial backtest speed multiplier (1-100x)")
    parser.add_argument(
        "--config",
        type=str,
        default="config/backtest.yaml",
        help="Path to TUI config file (yaml/json). Used at startup and on [R] restart.",
    )
    parser.add_argument("--strategy", type=str, default=None, help="Override single strategy (same semantics as run_backtest.py)")
    parser.add_argument("--strategies", type=str, default=None, help="Override strategies list (comma-separated)")
    parser.add_argument("--start", type=str, default=None, help="Override start datetime (e.g. 2026-01-01)")
    parser.add_argument("--end", type=str, default=None, help="Override end datetime (e.g. 2026-03-01)")
    parser.add_argument(
        "--demo-seconds",
        type=float,
        default=None,
        help="Auto-exit after N seconds (useful for non-interactive testing)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    def _build_feed() -> RealBacktestFeed:
        return RealBacktestFeed(
            config_path=args.config,
            strategy_override=args.strategy,
            strategies_override=args.strategies,
            start_override=args.start,
            end_override=args.end,
        )

    try:
        feed = _build_feed()
    except Exception as exc:
        print(f"Error: failed to initialize real backtest feed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    app = BacktestDashboardTUI(
        feed=feed,
        config_path=args.config,
        refresh_hz=args.fps,
        initial_speed=args.speed,
        feed_builder=_build_feed,
    )
    app.last_notice = f"Loaded config: {Path(args.config).expanduser()}"
    app.add_log(app.last_notice)
    app.run(demo_seconds=args.demo_seconds)


if __name__ == "__main__":
    main()
