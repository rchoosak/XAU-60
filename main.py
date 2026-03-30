#!/usr/bin/env python3
"""
Modular MT5 Trading Bot - Main Entry Point.
"""
import sys
import time
import signal
import argparse
import json
import os
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, Dict, List, Tuple
from copy import deepcopy
import yaml
from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.mt5_connector import MT5Connector
from core.strategy_loader import StrategyLoader
from core.risk_manager import RiskManager, RiskLimits
from core.trade_executor import TradeExecutor
from core.strategy_base import Signal, TradeSignal
from utils.logger import setup_logger
from utils.config import load_config
from utils.trade_journal import append_trade_event


class TradingBot:
    """
    Main trading bot orchestrator.

    Coordinates:
    - MT5 connection
    - Strategy loading
    - Risk management
    - Trade execution
    - Alert notifications
    """

    def __init__(self, config_path: str = "config/settings.yaml"):
        """
        Initialize trading bot.

        Args:
            config_path: Path to main configuration file (used as fallback)
        """
        self.config_path = Path(config_path)
        self.config = {}
        self.running = False

        self.mt5: Optional[MT5Connector] = None
        self.strategy_loader: Optional[StrategyLoader] = None
        self.risk_manager: Optional[RiskManager] = None
        self.trade_executor: Optional[TradeExecutor] = None
        self._last_analyzed_bar = {}
        self._processed_trade_history_count = 0
        self._loss_streak: Dict[str, int] = {}
        self._loss_streak_day: Dict[str, str] = {}
        self._cooldown_bars_left: Dict[str, int] = {}

        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers."""
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info("Shutdown signal received...")
        self.running = False

    def load_config(self) -> bool:
        """
        Load configuration from environment variables and YAML file.
        Environment variables take precedence over YAML settings.
        """
        try:
            # Start with fresh environment config (reload .env each call)
            self.config = load_config(reload_env=True).to_dict()

            # Load YAML config as fallback for non-sensitive settings
            if self.config_path.exists():
                with open(self.config_path, "r") as f:
                    yaml_config = yaml.safe_load(f) or {}

                # Merge YAML config (env vars take precedence)
                self._merge_config(yaml_config)

            logger.info("Configuration loaded successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            return False

    def _merge_config(self, yaml_config: dict):
        """
        Merge YAML config into existing config.
        Environment variables (already in self.config) take precedence.
        """
        def env_is_set(var_name: str) -> bool:
            value = os.getenv(var_name)
            return value is not None and value != ""

        env_map = {
            "mt5": {
                "mode": "MT5_MODE",
                "login": "MT5_LOGIN",
                "password": "MT5_PASSWORD",
                "server": "MT5_SERVER",
                "path": "MT5_PATH",
                "timeout": "MT5_TIMEOUT",
                "bridge_enabled": "MT5_BRIDGE_ENABLED",
                "bridge_host": "MT5_BRIDGE_HOST",
                "bridge_port": "MT5_BRIDGE_PORT",
                "bridge_token": "MT5_BRIDGE_TOKEN",
                "bridge_timeout": "MT5_BRIDGE_TIMEOUT",
                "bridge_poll_timeout": "MT5_BRIDGE_POLL_TIMEOUT",
            },
            "risk": {
                "max_risk_per_trade": "MAX_RISK_PER_TRADE",
                "max_daily_loss": "MAX_DAILY_LOSS",
                "max_drawdown": "MAX_DRAWDOWN",
                "max_positions": "MAX_POSITIONS",
                "max_positions_per_symbol": "MAX_POSITIONS_PER_SYMBOL",
                "capital_base": "RISK_CAPITAL_BASE",
            },
            "trading": {
                "default_lot_size": "DEFAULT_LOT_SIZE",
                "default_magic_number": "DEFAULT_MAGIC_NUMBER",
                "slippage": "SLIPPAGE",
                "check_interval": "CHECK_INTERVAL",
            },
            "logging": {
                "level": "LOG_LEVEL",
                "file": "LOG_FILE",
                "rotation": "LOG_ROTATION",
                "retention": "LOG_RETENTION",
            },
            "ui": {
                "refresh_rate": "UI_REFRESH_RATE",
                "theme": "UI_THEME",
            },
        }

        alert_env_map = {
            "telegram": {
                "enabled": "TELEGRAM_ENABLED",
                "token": "TELEGRAM_BOT_TOKEN",
                "chat_id": "TELEGRAM_CHAT_ID",
            },
            "discord": {
                "enabled": "DISCORD_ENABLED",
                "webhook_url": "DISCORD_WEBHOOK_URL",
            },
        }

        merged = deepcopy(self.config)

        for section, key_map in env_map.items():
            yaml_section = yaml_config.get(section, {})
            if not isinstance(yaml_section, dict):
                continue
            merged.setdefault(section, {})
            for key, yaml_value in yaml_section.items():
                env_var = key_map.get(key)
                if env_var and env_is_set(env_var):
                    continue
                merged[section][key] = yaml_value

        yaml_alerts = yaml_config.get("alerts", {})
        if isinstance(yaml_alerts, dict):
            merged.setdefault("alerts", {})
            for channel, key_map in alert_env_map.items():
                channel_yaml = yaml_alerts.get(channel, {})
                if not isinstance(channel_yaml, dict):
                    continue
                merged["alerts"].setdefault(channel, {})
                for key, yaml_value in channel_yaml.items():
                    env_var = key_map.get(key)
                    if env_var and env_is_set(env_var):
                        continue
                    merged["alerts"][channel][key] = yaml_value

        self.config = merged

    def initialize(self) -> bool:
        """Initialize all components."""
        if not self.load_config():
            return False

        # Setup logging
        log_config = self.config.get("logging", {})
        setup_logger(
            log_file=log_config.get("file"),
            level=log_config.get("level", "INFO"),
            rotation=log_config.get("rotation", "10 MB"),
            retention=log_config.get("retention", "7 days")
        )

        logger.info("Initializing Trading Bot...")

        # Initialize MT5
        self.mt5 = MT5Connector()
        mt5_config = self.config.get("mt5", {})

        if not self.mt5.connect(
            login=mt5_config.get("login"),
            password=mt5_config.get("password"),
            server=mt5_config.get("server"),
            path=mt5_config.get("path") or None,
            timeout=mt5_config.get("timeout", 60000)
        ):
            logger.error("Failed to connect to MT5")
            return False

        # Initialize Risk Manager
        risk_config = self.config.get("risk", {})
        risk_limits = RiskLimits(
            max_risk_per_trade=risk_config.get("max_risk_per_trade", 2.0),
            max_daily_loss=risk_config.get("max_daily_loss", 5.0),
            max_drawdown=risk_config.get("max_drawdown", 20.0),
            max_positions=risk_config.get("max_positions", 5),
            max_positions_per_symbol=risk_config.get("max_positions_per_symbol", 2),
            capital_base=risk_config.get("capital_base", 0.0),
        )

        self.risk_manager = RiskManager(self.mt5, risk_limits)
        if not self.risk_manager.initialize():
            logger.error("Failed to initialize risk manager")
            return False

        # Initialize Trade Executor
        trading_config = self.config.get("trading", {})
        self.trade_executor = TradeExecutor(
            self.mt5,
            self.risk_manager,
            default_magic=trading_config.get("default_magic_number", 123456),
            default_lot_size=trading_config.get("default_lot_size", 0.01),
            slippage=trading_config.get("slippage", 10),
        )

        # Load Strategies
        self.strategy_loader = StrategyLoader()
        self.strategy_loader.load_all_strategies()

        strategies = self.strategy_loader.get_enabled_strategies()
        logger.info(f"Loaded {len(strategies)} enabled strategies")

        for name, strategy in strategies.items():
            logger.info(f"  - {strategy}")

        return True

    def run(self):
        """Run the main trading loop."""
        if not self.initialize():
            logger.error("Failed to initialize bot. Exiting.")
            return

        self.running = True
        check_interval = self.config.get("trading", {}).get("check_interval", 1)

        logger.info("Trading bot started. Press Ctrl+C to stop.")

        try:
            last_export = 0
            while self.running:
                self._tick()
                
                # Export live state every 5 seconds for Dashboard
                now = time.time()
                if now - last_export >= 5:
                    self._export_state()
                    last_export = now
                    
                time.sleep(check_interval)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
        finally:
            self.shutdown()

    def _tick(self):
        """Process one tick cycle."""
        strategies = self.strategy_loader.get_enabled_strategies()
        self._sync_trade_outcomes(strategies)

        for name, strategy in strategies.items():
            for symbol in strategy.symbols:
                bar_processed = False
                state_key = self._strategy_symbol_key(name, symbol)
                try:
                    # Get market data
                    data = self.mt5.get_ohlcv(symbol, strategy.timeframe, 100)
                    if data is None:
                        continue

                    bar_time = data.iloc[-1]["time"]
                    bar_key = (name, symbol, strategy.timeframe)
                    if self._last_analyzed_bar.get(bar_key) == bar_time:
                        continue

                    self._last_analyzed_bar[bar_key] = bar_time
                    bar_processed = True
                    self._reset_loss_streak_if_new_day(state_key, bar_time)

                    # Analyze for signals
                    signal_obj = strategy.analyze(symbol, data)
                    if not signal_obj or signal_obj.signal.value == 0:
                        if self._should_log_decision(strategy, "no_signal"):
                            decision_context = self._build_decision_context(
                                strategy_name=name,
                                strategy=strategy,
                                symbol=symbol,
                                data=data,
                                signal=signal_obj,
                            )
                            self._log_decision_event(
                                strategy_name=name,
                                strategy=strategy,
                                symbol=symbol,
                                decision="no_signal",
                                reason="strategy_returned_hold_or_none",
                                signal=signal_obj,
                                context=decision_context,
                            )
                        continue

                    decision_context = self._build_decision_context(
                        strategy_name=name,
                        strategy=strategy,
                        symbol=symbol,
                        data=data,
                        signal=signal_obj,
                    )

                    allowed, reason = self._passes_execution_filters(
                        strategy_name=name,
                        strategy=strategy,
                        symbol=symbol,
                        signal=signal_obj,
                        bar_time=bar_time,
                        decision_context=decision_context,
                    )

                    if not allowed:
                        decision_context["filter_reason"] = reason
                        self._log_decision_event(
                            strategy_name=name,
                            strategy=strategy,
                            symbol=symbol,
                            decision="blocked",
                            reason=reason,
                            signal=signal_obj,
                            context=decision_context,
                        )
                        continue

                    self._log_decision_event(
                        strategy_name=name,
                        strategy=strategy,
                        symbol=symbol,
                        decision="passed",
                        reason="all_filters_passed",
                        signal=signal_obj,
                        context=decision_context,
                    )

                    strategy_risk = {}
                    if isinstance(getattr(strategy, "config", None), dict):
                        strategy_risk = strategy.config.get("risk", {}) or {}

                    try:
                        ticket = self.trade_executor.execute_signal(
                            signal_obj,
                            name,
                            strategy_risk,
                            decision_context=decision_context,
                        )
                    except TypeError as exc:
                        # Keep compatibility with lightweight test stubs that do not
                        # support the decision_context keyword yet.
                        if "decision_context" not in str(exc):
                            raise
                        ticket = self.trade_executor.execute_signal(
                            signal_obj,
                            name,
                            strategy_risk,
                        )
                    if ticket:
                        opened_position = next(
                            (p for p in self.mt5.get_positions() if p.ticket == ticket),
                            None
                        )
                        if opened_position is not None:
                            strategy.on_trade_opened(opened_position)
                    else:
                        self._log_decision_event(
                            strategy_name=name,
                            strategy=strategy,
                            symbol=symbol,
                            decision="executor_rejected",
                            reason="trade_executor_rejected_signal",
                            signal=signal_obj,
                            context=decision_context,
                        )

                except Exception as e:
                    logger.error(f"Error processing {symbol} with {name}: {e}")
                finally:
                    if bar_processed:
                        self._advance_cooldown_bar(state_key)

        # Manage open positions
        try:
            self.trade_executor.manage_positions(strategies)
        except Exception as e:
            logger.error(f"Error managing positions: {e}")

        self._sync_trade_outcomes(strategies)

    def _runtime_mode(self) -> str:
        backend = str(getattr(self.mt5, "backend_mode", "")).strip().lower()
        if backend in {"replay", "backtest"}:
            return "backtest"
        return "live"

    @staticmethod
    def _strategy_symbol_key(strategy_name: str, symbol: str) -> str:
        return f"{strategy_name}::{symbol}"

    @staticmethod
    def _normalize_keys(raw: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        return {str(k).replace("-", "_"): v for k, v in raw.items()}

    @staticmethod
    def _sanitize_context(context: Dict[str, Any]) -> Dict[str, Any]:
        sanitized: Dict[str, Any] = {}
        if not isinstance(context, dict):
            return sanitized
        for key, value in context.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                sanitized[str(key)] = value
        return sanitized

    @staticmethod
    def _as_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_datetime(value: Any) -> Optional[datetime]:
        if isinstance(value, datetime):
            return value
        if hasattr(value, "to_pydatetime"):
            try:
                return value.to_pydatetime()
            except Exception:
                return None
        return None

    @staticmethod
    def _normalize_weekday(raw: Any) -> Optional[int]:
        if isinstance(raw, bool) or raw is None:
            return None
        if isinstance(raw, (int, float)):
            day = int(raw)
            return day if 0 <= day <= 6 else None
        if isinstance(raw, str):
            text = raw.strip().lower()
            if text.isdigit():
                day = int(text)
                return day if 0 <= day <= 6 else None
            names = {
                "mon": 0, "monday": 0,
                "tue": 1, "tuesday": 1,
                "wed": 2, "wednesday": 2,
                "thu": 3, "thursday": 3,
                "fri": 4, "friday": 4,
                "sat": 5, "saturday": 5,
                "sun": 6, "sunday": 6,
            }
            return names.get(text)
        return None

    @staticmethod
    def _hour_blocked(hour: int, blocked_hours: Any) -> bool:
        if not isinstance(blocked_hours, list):
            return False

        for item in blocked_hours:
            if isinstance(item, (int, float)) and not isinstance(item, bool):
                if hour == int(item):
                    return True
                continue

            if isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                if "-" in text:
                    left, right = text.split("-", 1)
                    try:
                        start = int(left.strip())
                        end = int(right.strip())
                    except ValueError:
                        continue
                    if 0 <= start <= 23 and 0 <= end <= 23 and start <= end and start <= hour <= end:
                        return True
                    continue
                if text.isdigit() and hour == int(text):
                    return True

            if isinstance(item, dict):
                start = TradingBot._as_float(item.get("start"))
                end = TradingBot._as_float(item.get("end"))
                if start is None or end is None:
                    continue
                i_start = int(start)
                i_end = int(end)
                if 0 <= i_start <= 23 and 0 <= i_end <= 23 and i_start <= hour <= i_end:
                    return True
        return False

    def _decision_logging_config(self, strategy: Any) -> Dict[str, Any]:
        cfg = {}
        if isinstance(getattr(strategy, "config", None), dict):
            cfg = self._normalize_keys(strategy.config.get("decision_logging", {}) or {})
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "log_passed": bool(cfg.get("log_passed", True)),
            "log_blocked": bool(cfg.get("log_blocked", True)),
            "log_no_signal": bool(cfg.get("log_no_signal", False)),
            "log_executor_rejected": bool(cfg.get("log_executor_rejected", True)),
        }

    def _should_log_decision(self, strategy: Any, decision: str) -> bool:
        cfg = self._decision_logging_config(strategy)
        if not cfg["enabled"]:
            return False
        if decision == "no_signal":
            return cfg["log_no_signal"]
        if decision == "blocked":
            return cfg["log_blocked"]
        if decision == "passed":
            return cfg["log_passed"]
        if decision == "executor_rejected":
            return cfg["log_executor_rejected"]
        return True

    def _execution_filters_config(self, strategy: Any) -> Dict[str, Any]:
        raw = {}
        if isinstance(getattr(strategy, "config", None), dict):
            raw = self._normalize_keys(strategy.config.get("execution_filters", {}) or {})
        return {
            "enabled": bool(raw.get("enabled", False)),
            "allow_long": bool(raw.get("allow_long", True)),
            "allow_short": bool(raw.get("allow_short", True)),
            "blocked_hours": raw.get("blocked_hours", []) or [],
            "blocked_weekdays": raw.get("blocked_weekdays", []) or [],
            "max_spread_points": float(raw.get("max_spread_points", 0.0) or 0.0),
            "cooldown_bars_after_loss": max(0, int(raw.get("cooldown_bars_after_loss", 0) or 0)),
            "max_consecutive_losses": max(0, int(raw.get("max_consecutive_losses", 0) or 0)),
        }

    def _build_decision_context(
        self,
        strategy_name: str,
        strategy: Any,
        symbol: str,
        data: Any,
        signal: Optional[TradeSignal],
    ) -> Dict[str, Any]:
        bar = data.iloc[-1]
        bar_time_raw = bar.get("time")
        bar_time = self._extract_datetime(bar_time_raw)

        get_tick = getattr(self.mt5, "get_tick", None)
        tick = get_tick(symbol) if callable(get_tick) else {}
        tick = tick or {}

        get_symbol_info = getattr(self.mt5, "get_symbol_info", None)
        symbol_info = get_symbol_info(symbol) if callable(get_symbol_info) else None
        point = float(getattr(symbol_info, "point", 0.0) or 0.0)

        bid = self._as_float(tick.get("bid"))
        ask = self._as_float(tick.get("ask"))
        spread_points = None
        if bid is not None and ask is not None and point > 0:
            spread_points = (ask - bid) / point

        get_positions = getattr(self.mt5, "get_positions", None)
        positions = get_positions() if callable(get_positions) else []
        positions = positions or []
        symbol_positions = [p for p in positions if p.symbol == symbol]

        signal_side = "HOLD"
        if signal and hasattr(signal, "signal"):
            signal_side = getattr(signal.signal, "name", str(signal.signal))

        strategy_risk = {}
        if isinstance(getattr(strategy, "config", None), dict):
            strategy_risk = strategy.config.get("risk", {}) or {}

        key = self._strategy_symbol_key(strategy_name, symbol)
        ctx = {
            "strategy": strategy_name,
            "symbol": symbol,
            "timeframe": getattr(strategy, "timeframe", ""),
            "bar_time": bar_time.isoformat() if bar_time else str(bar_time_raw),
            "bar_open": self._as_float(bar.get("open")),
            "bar_high": self._as_float(bar.get("high")),
            "bar_low": self._as_float(bar.get("low")),
            "bar_close": self._as_float(bar.get("close")),
            "bar_volume": self._as_float(bar.get("volume")),
            "bid": bid,
            "ask": ask,
            "spread_points": round(spread_points, 2) if spread_points is not None else None,
            "signal_side": signal_side,
            "signal_entry_price": self._as_float(getattr(signal, "entry_price", None)) if signal else None,
            "signal_stop_loss": self._as_float(getattr(signal, "stop_loss", None)) if signal else None,
            "signal_take_profit": self._as_float(getattr(signal, "take_profit", None)) if signal else None,
            "signal_lot_size": self._as_float(getattr(signal, "lot_size", None)) if signal else None,
            "signal_comment": str(getattr(signal, "comment", "")) if signal else "",
            "open_positions_total": len(positions),
            "open_positions_symbol": len(symbol_positions),
            "loss_streak": self._loss_streak.get(key, 0),
            "cooldown_bars_left": self._cooldown_bars_left.get(key, 0),
            "risk_percent_cfg": self._as_float(strategy_risk.get("max_risk_percent")),
            "capital_base_cfg": self._as_float(strategy_risk.get("capital_base")),
        }
        return self._sanitize_context(ctx)

    def _log_decision_event(
        self,
        strategy_name: str,
        strategy: Any,
        symbol: str,
        decision: str,
        reason: str,
        signal: Optional[TradeSignal],
        context: Dict[str, Any],
    ) -> None:
        if not self._should_log_decision(strategy, decision):
            return

        signal_side = "HOLD"
        if signal and hasattr(signal, "signal"):
            signal_side = getattr(signal.signal, "name", str(signal.signal))

        append_trade_event({
            "mode": self._runtime_mode(),
            "event": "DECISION",
            "strategy": strategy_name,
            "symbol": symbol,
            "timeframe": getattr(strategy, "timeframe", ""),
            "signal": signal_side,
            "decision": decision,
            "reason": reason,
            "entry_price": self._as_float(getattr(signal, "entry_price", None)) if signal else None,
            "stop_loss": self._as_float(getattr(signal, "stop_loss", None)) if signal else None,
            "take_profit": self._as_float(getattr(signal, "take_profit", None)) if signal else None,
            "lot_size": self._as_float(getattr(signal, "lot_size", None)) if signal else None,
            "context": self._sanitize_context(context),
        })

    def _passes_execution_filters(
        self,
        strategy_name: str,
        strategy: Any,
        symbol: str,
        signal: TradeSignal,
        bar_time: Any,
        decision_context: Dict[str, Any],
    ) -> Tuple[bool, str]:
        filters = self._execution_filters_config(strategy)
        if not filters["enabled"]:
            return True, "filters_disabled"

        if signal.signal == Signal.BUY and not filters["allow_long"]:
            return False, "long_entries_disabled"
        if signal.signal == Signal.SELL and not filters["allow_short"]:
            return False, "short_entries_disabled"

        bar_dt = self._extract_datetime(bar_time)
        if bar_dt:
            blocked_weekdays = set()
            for raw_day in filters["blocked_weekdays"]:
                day = self._normalize_weekday(raw_day)
                if day is not None:
                    blocked_weekdays.add(day)
            if blocked_weekdays and bar_dt.weekday() in blocked_weekdays:
                return False, f"blocked_weekday_{bar_dt.weekday()}"

            if self._hour_blocked(bar_dt.hour, filters["blocked_hours"]):
                return False, f"blocked_hour_{bar_dt.hour}"

        max_spread_points = filters["max_spread_points"]
        spread_points = self._as_float(decision_context.get("spread_points"))
        if max_spread_points > 0 and spread_points is not None and spread_points > max_spread_points:
            return False, f"spread_too_wide_{spread_points:.2f}_gt_{max_spread_points:.2f}"

        key = self._strategy_symbol_key(strategy_name, symbol)
        cooldown_left = self._cooldown_bars_left.get(key, 0)
        if cooldown_left > 0:
            return False, f"cooldown_after_loss_{cooldown_left}_bars_left"

        max_consecutive_losses = filters["max_consecutive_losses"]
        streak = self._loss_streak.get(key, 0)
        if max_consecutive_losses > 0 and streak >= max_consecutive_losses:
            return False, f"max_consecutive_losses_reached_{streak}"

        return True, "passed"

    def _reset_loss_streak_if_new_day(self, key: str, ref_time: Any) -> None:
        dt = self._extract_datetime(ref_time)
        if not dt:
            return
        today_key = dt.date().isoformat()
        last_day = self._loss_streak_day.get(key)
        if last_day is None:
            self._loss_streak_day[key] = today_key
            return
        if last_day != today_key:
            self._loss_streak_day[key] = today_key
            self._loss_streak[key] = 0
            self._cooldown_bars_left[key] = 0

    def _advance_cooldown_bar(self, key: str) -> None:
        current = self._cooldown_bars_left.get(key, 0)
        if current > 0:
            self._cooldown_bars_left[key] = current - 1

    def _sync_trade_outcomes(self, strategies: Dict[str, Any]) -> None:
        get_trade_history = getattr(self.trade_executor, "get_trade_history", None)
        if not callable(get_trade_history):
            return

        history = get_trade_history()
        if not isinstance(history, list):
            return
        if self._processed_trade_history_count >= len(history):
            return

        for record in history[self._processed_trade_history_count:]:
            key = self._strategy_symbol_key(record.strategy or "unknown", record.symbol)
            close_time = record.close_time or datetime.now()
            self._reset_loss_streak_if_new_day(key, close_time)

            profit = self._as_float(record.profit) or 0.0
            if profit < 0:
                self._loss_streak[key] = self._loss_streak.get(key, 0) + 1
                self._loss_streak_day[key] = close_time.date().isoformat()

                strategy = strategies.get(record.strategy or "")
                filters = self._execution_filters_config(strategy) if strategy else {}
                cooldown = int(filters.get("cooldown_bars_after_loss", 0) or 0)
                if cooldown > 0:
                    self._cooldown_bars_left[key] = max(self._cooldown_bars_left.get(key, 0), cooldown)
            elif profit > 0:
                self._loss_streak[key] = 0
                self._loss_streak_day[key] = close_time.date().isoformat()
                self._cooldown_bars_left[key] = 0

        self._processed_trade_history_count = len(history)

    def _export_state(self):
        """Export live state to JSON for Dashboard."""
        try:
            os.makedirs("data", exist_ok=True)
            
            acc_info = self.mt5.get_account_info()
            positions = self.mt5.get_positions()
            
            acc_dict = acc_info.__dict__ if hasattr(acc_info, "__dict__") else {}
            
            # Serialize positions converting Enums and datetime
            pos_list = []
            for p in (positions or []):
                if hasattr(p, "__dict__"):
                    p_dict = {}
                    for k, v in p.__dict__.items():
                        if hasattr(v, "name") and type(v).__name__ in ("Signal", "Enum", "SignalType"):
                            p_dict[k] = v.name
                        elif hasattr(v, "isoformat"):
                            p_dict[k] = v.isoformat()
                        else:
                            p_dict[k] = v
                    pos_list.append(p_dict)
            
            trades = []
            if hasattr(self, "trade_executor") and hasattr(self.trade_executor, "_trade_history"):
                for t in self.trade_executor._trade_history[-20:]:  # last 20
                    signal_val = getattr(t, "signal", None)
                    if hasattr(signal_val, "name"):
                        signal_str = signal_val.name
                    else:
                        signal_str = str(signal_val)

                    trades.append({
                        "ticket": getattr(t, "ticket", 0),
                        "symbol": getattr(t, "symbol", ""),
                        "signal": signal_str,
                        "entry_price": getattr(t, "entry_price", 0),
                        "lot_size": getattr(t, "lot_size", 0),
                        "strategy": getattr(t, "strategy", ""),
                        "open_time": str(getattr(t, "open_time", "")),
                    })
                
            state = {
                "last_updated": time.time(),
                "account_info": acc_dict,
                "positions": pos_list,
                "recent_trades": trades
            }
            
            with open("data/live_state.json", "w") as f:
                json.dump(state, f)
                
        except Exception as e:
            logger.error(f"Failed to export state: {e}")

    def shutdown(self):
        """Cleanup and shutdown."""
        logger.info("Shutting down trading bot...")

        if self.mt5:
            self.mt5.disconnect()

        logger.info("Trading bot stopped.")

    def dry_run(self):
        """Run a dry test without trading."""
        if not self.load_config():
            print("Failed to load config")
            return False

        print("\n=== DRY RUN - Testing Configuration ===\n")

        # Show config source
        print("[0] Configuration source:")
        mt5_config = self.config.get("mt5", {})
        print(f"    ✓ MT5 Mode: {mt5_config.get('mode', 'auto')}")
        if mt5_config.get("login"):
            print(f"    ✓ MT5 Login: {mt5_config['login']}")
            print(f"    ✓ MT5 Server: {mt5_config.get('server', 'Not set')}")
        else:
            print("    ⚠ MT5 credentials not configured")
            print("    → Set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in .env file")

        # Test MT5 connection
        print("\n[1] Testing MT5 connection...")
        self.mt5 = MT5Connector()
        if self.mt5.connect(
            login=mt5_config.get("login"),
            password=mt5_config.get("password"),
            server=mt5_config.get("server"),
            path=mt5_config.get("path") or None
        ):
            print(f"    ✓ Backend: {self.mt5.backend_mode}")
            account = self.mt5.get_account_info()
            if account:
                print(f"    ✓ Connected to {account.server}")
                print(f"    ✓ Account: {account.login}")
                print(f"    ✓ Balance: {account.balance} {account.currency}")
            self.mt5.disconnect()
        else:
            print("    ✗ Failed to connect to MT5")
            print("    → Check your credentials in .env file")
            return False

        # Test strategy loading
        print("\n[2] Loading strategies...")
        self.strategy_loader = StrategyLoader()
        strategies = self.strategy_loader.load_all_strategies()
        print(f"    ✓ Loaded {len(strategies)} strategies")
        for name, strategy in strategies.items():
            status = "enabled" if strategy.enabled else "disabled"
            print(f"      - {name} v{strategy.version} [{status}]")

        # Test data retrieval
        print("\n[3] Testing market data...")
        self.mt5.connect(
            login=mt5_config.get("login"),
            password=mt5_config.get("password"),
            server=mt5_config.get("server"),
            path=mt5_config.get("path") or None
        )
        for name, strategy in strategies.items():
            for symbol in strategy.symbols:
                data = self.mt5.get_ohlcv(symbol, strategy.timeframe, 10)
                if data is not None:
                    print(f"    ✓ {symbol} {strategy.timeframe}: {len(data)} bars")
                else:
                    print(f"    ✗ {symbol}: Failed to get data")
        self.mt5.disconnect()

        # Check alerts config
        print("\n[4] Alert configuration:")
        alerts = self.config.get("alerts", {})
        telegram = alerts.get("telegram", {})
        discord = alerts.get("discord", {})

        if telegram.get("enabled"):
            print(f"    ✓ Telegram: Enabled (Chat ID: {telegram.get('chat_id', 'Not set')})")
        else:
            print("    ○ Telegram: Disabled")

        if discord.get("enabled"):
            print("    ✓ Discord: Enabled")
        else:
            print("    ○ Discord: Disabled")

        print("\n=== DRY RUN COMPLETE ===\n")
        return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Modular MT5 Trading Bot")
    parser.add_argument(
        "--config", "-c",
        default="config/settings.yaml",
        help="Path to configuration file (fallback for non-sensitive settings)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without trading to test configuration"
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch Streamlit UI dashboard only (does not execute live trading loop)"
    )
    parser.add_argument(
        "--ui-with-bot",
        action="store_true",
        help="Launch Streamlit UI and run live trading loop in the same command"
    )

    args = parser.parse_args()

    if args.ui_with_bot:
        import subprocess
        print(
            "[INFO] Starting live bot + UI mode. "
            "Orders can be executed while dashboard is open."
        )
        bot = TradingBot(args.config)
        bot_thread = threading.Thread(target=bot.run, name="TradingBotThread", daemon=True)
        bot_thread.start()
        try:
            subprocess.run(["streamlit", "run", "ui/app.py"])
        finally:
            bot.running = False
            if bot_thread.is_alive():
                bot_thread.join(timeout=5)
        return

    if args.ui:
        # Launch Streamlit UI
        import subprocess
        print(
            "[INFO] UI mode launches dashboard only. "
            "Run `python main.py` in another terminal, or use `python main.py --ui-with-bot`."
        )
        subprocess.run(["streamlit", "run", "ui/app.py"])
        return

    bot = TradingBot(args.config)

    if args.dry_run:
        bot.dry_run()
    else:
        bot.run()


if __name__ == "__main__":
    main()
