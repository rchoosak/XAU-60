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
from pathlib import Path
from datetime import datetime
from typing import Optional
import yaml
from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.mt5_connector import MT5Connector
from core.strategy_loader import StrategyLoader
from core.risk_manager import RiskManager, RiskLimits
from core.trade_executor import TradeExecutor
from utils.logger import setup_logger
from utils.config import config as env_config, load_config


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
            # Start with environment config
            self.config = env_config.to_dict()

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
        # Only use YAML values if env vars are not set (default values)
        mt5_yaml = yaml_config.get("mt5", {})
        if not self.config["mt5"]["login"] and mt5_yaml.get("login"):
            self.config["mt5"]["login"] = mt5_yaml["login"]
        if not self.config["mt5"]["password"] and mt5_yaml.get("password"):
            self.config["mt5"]["password"] = mt5_yaml["password"]
        if not self.config["mt5"]["server"] and mt5_yaml.get("server"):
            self.config["mt5"]["server"] = mt5_yaml["server"]
        if not self.config["mt5"]["path"] and mt5_yaml.get("path"):
            self.config["mt5"]["path"] = mt5_yaml["path"]

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

        for name, strategy in strategies.items():
            for symbol in strategy.symbols:
                try:
                    # Get market data
                    data = self.mt5.get_ohlcv(symbol, strategy.timeframe, 100)
                    if data is None:
                        continue

                    # Analyze for signals
                    signal = strategy.analyze(symbol, data)

                    if signal and signal.signal.value != 0:
                        # Execute the signal
                        ticket = self.trade_executor.execute_signal(signal, name)
                        if ticket:
                            strategy.on_trade_opened(None)

                except Exception as e:
                    logger.error(f"Error processing {symbol} with {name}: {e}")

        # Manage open positions
        try:
            self.trade_executor.manage_positions(strategies)
        except Exception as e:
            logger.error(f"Error managing positions: {e}")

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
        help="Launch Streamlit UI instead of CLI"
    )

    args = parser.parse_args()

    if args.ui:
        # Launch Streamlit UI
        import subprocess
        subprocess.run(["streamlit", "run", "ui/app.py"])
        return

    bot = TradingBot(args.config)

    if args.dry_run:
        bot.dry_run()
    else:
        bot.run()


if __name__ == "__main__":
    main()
