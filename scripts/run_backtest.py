import argparse
import os
import sys
import yaml
from datetime import datetime
from loguru import logger
from concurrent.futures import ProcessPoolExecutor, as_completed

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.backtester import Backtester
from core.bot_engine import BotEngine
from core.strategy_loader import StrategyLoader

_PROCESS_LOADER = None


def _get_loader() -> StrategyLoader:
    """Build strategy catalog once per process."""
    global _PROCESS_LOADER
    if _PROCESS_LOADER is None:
        loader = StrategyLoader()
        loader.discover_strategies()
        _PROCESS_LOADER = loader
    return _PROCESS_LOADER


def _parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    return datetime.strptime(value, "%Y-%m-%d")


def _normalize_strategy_list(raw_value):
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [v.strip() for v in raw_value.split(",") if v.strip()]
    if isinstance(raw_value, list):
        return [str(v).strip() for v in raw_value if str(v).strip()]
    return []


def _normalize_config_keys(config_data):
    if not config_data:
        return {}
    return {k.replace("-", "_"): v for k, v in config_data.items()}


def _resolve_effective_args(args_dict):
    """
    Resolve runtime args with latest config file content (if provided),
    then apply original CLI overrides on top.
    """
    resolved = dict(args_dict)
    config_path = resolved.get("_config_path")
    cli_overrides = resolved.get("_cli_overrides", {})

    if config_path and os.path.exists(config_path):
        with open(config_path, "r") as f:
            config_data = _normalize_config_keys(yaml.safe_load(f) or {})
        resolved = {**config_data, **cli_overrides}
        resolved["_config_path"] = config_path
        resolved["_cli_overrides"] = dict(cli_overrides)

    if resolved.get("strategies") is not None:
        resolved["strategies"] = _normalize_strategy_list(resolved.get("strategies"))

    return resolved


def _build_executor(loader: StrategyLoader, args_dict):
    execution = args_dict.get("execution", "strategy") or "strategy"

    if execution == "strategy":
        strategy_name = args_dict.get("strategy")
        if not strategy_name:
            return None, "Execution mode 'strategy' requires --strategy or config value 'strategy'"
        strategy = loader.load_strategy(strategy_name)
        if not strategy:
            return None, f"Strategy {strategy_name} not found"
        return strategy, None

    strategy_names = _normalize_strategy_list(args_dict.get("strategies"))
    strategies = []
    for name in strategy_names:
        s = loader.load_strategy(name)
        if s:
            strategies.append(s)

    if not strategies:
        return None, "No valid strategies found for bot mode"

    return BotEngine(strategies, args_dict), None


def _build_backtester(args_dict, loader):
    symbol = args_dict.get("symbol", "XAUUSD")
    timeframe = args_dict.get("timeframe", "M15")
    start = _parse_date(args_dict.get("start"))
    end = _parse_date(args_dict.get("end"))
    mode = args_dict.get("mode", "real") or "real"

    if args_dict.get("tui"):
        # TUI already prints open/close events; debug logging is expensive.
        args_dict.setdefault("log_signals", False)
    backtester = Backtester(args_dict)

    executor, err = _build_executor(loader, args_dict)
    if err:
        return None, None, err

    return backtester, (symbol, timeframe, start, end, mode, executor), None


def run_single_backtest(args_dict):
    """Function to be run in a separate process."""
    try:
        loader = _get_loader()
        effective_args = _resolve_effective_args(args_dict)
        backtester, run_ctx, err = _build_backtester(effective_args, loader)
        if err:
            return {"error": err}
        symbol, timeframe, start, end, mode, executor = run_ctx
            
        if effective_args.get("tui"):
            from core.tui_app import BacktestTUI
            backtester.start_simulation(symbol, timeframe, start, end, executor, mode=mode)

            def reload_backtest_state():
                updated_args = _resolve_effective_args(effective_args)
                new_backtester, new_ctx, reload_err = _build_backtester(updated_args, loader)
                if reload_err:
                    return None, reload_err
                rsymbol, rtf, rstart, rend, rmode, rexecutor = new_ctx
                new_backtester.start_simulation(rsymbol, rtf, rstart, rend, rexecutor, mode=rmode)
                return new_backtester, "Config reloaded and simulation restarted."

            tui_steps = effective_args.get("tui_steps") or 20
            tui_interval = effective_args.get("tui_interval") or 0.05
            tui = BacktestTUI(
                backtester,
                steps_per_tick=tui_steps,
                update_interval=tui_interval,
                reload_callback=reload_backtest_state,
            )
            tui.run()
            backtester = tui.backtester
            # After TUI closes, calculate metrics
            result = backtester._calculate_metrics(backtester.trades, backtester.balance, backtester.equity_curve)
        else:
            result = backtester.run(symbol, timeframe, start, end, executor, mode=mode)
        return {
            "symbol": symbol,
            "strategy": effective_args.get("strategy") or effective_args.get("strategies"),
            "result": result
        }
    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Expanded Local Backtest Runner")
    parser.add_argument("-f", "--config", type=str, help="Path to backtest YAML config file")
    
    # These are mostly for CLI overrides
    parser.add_argument("--mode", choices=["random", "real"])
    parser.add_argument("--execution", choices=["strategy", "bot"])
    parser.add_argument("--strategy", type=str)
    parser.add_argument("--strategies", type=str)
    parser.add_argument("--symbol", type=str)
    parser.add_argument("--timeframe", type=str)
    parser.add_argument("--start", type=str)
    parser.add_argument("--end", type=str)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--workers", type=int, help="Max worker processes for parallel runs")
    parser.add_argument("--timeframes", type=str, help="Comma-separated timeframes for --parallel")
    parser.add_argument("--tui", action="store_true", help="Run with Terminal UI dashboard")
    parser.add_argument("--tui-steps", type=int, default=None, help="Backtest steps per TUI frame")
    parser.add_argument("--tui-interval", type=float, default=None, help="TUI frame interval in seconds")
    
    # Added some of the new parameters to CLI too for quick testing
    parser.add_argument("--aggregation", choices=["majority", "weighted", "priority"])
    parser.add_argument("--initial-balance", type=float, dest="initial_balance")
    parser.add_argument("--lot-size", type=float, dest="lot_size")
    parser.add_argument("--leverage", type=float)

    args = parser.parse_args()
    args_dict = vars(args)
    cli_overrides = {k: v for k, v in args_dict.items() if v is not None and k != "config"}
    config_path = args.config

    if args.config:
        if os.path.exists(args.config):
            with open(args.config, "r") as f:
                config_data = yaml.safe_load(f)
                if config_data:
                    # Merge logic: YAML values are defaults, CLI overrides
                    args_dict = {**_normalize_config_keys(config_data), **cli_overrides}
        else:
            logger.error(f"Config file not found: {args.config}")
            sys.exit(1)

    args_dict["_config_path"] = config_path
    args_dict["_cli_overrides"] = cli_overrides
    if args_dict.get("strategies") is not None:
        args_dict["strategies"] = _normalize_strategy_list(args_dict.get("strategies"))
    
    if args_dict.get("parallel"):
        if args_dict.get("timeframes"):
            timeframes = [tf.strip() for tf in str(args_dict["timeframes"]).split(",") if tf.strip()]
        elif args_dict.get("timeframe"):
            timeframes = [str(args_dict["timeframe"]).strip()]
        else:
            timeframes = ["M5", "M15", "H1"]

        # Keep order, remove duplicates
        timeframes = list(dict.fromkeys(timeframes))
        tasks = []
        for tf in timeframes:
            task_args = args_dict.copy()
            task_args["timeframe"] = tf
            tasks.append(task_args)
            
        workers = args_dict.get("workers")
        if workers is None:
            workers = min(len(tasks), os.cpu_count() or 1)
        workers = max(1, min(workers, len(tasks)))

        logger.info(f"Running {len(tasks)} backtests in parallel with {workers} worker(s)...")
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(run_single_backtest, t) for t in tasks]
            for future in as_completed(futures):
                res = future.result()
                print_result(res)
    else:
        result = run_single_backtest(args_dict)
        print_result(result)

def print_result(res):
    if "error" in res:
        logger.error(f"Error: {res['error']}")
        return
        
    print("\n" + "="*40)
    print(f" BACKTEST RESULT: {res['symbol']} | {res['strategy']}")
    print("="*40)
    for k, v in res["result"].items():
        print(f"{k:20}: {v}")
    print("="*40 + "\n")

if __name__ == "__main__":
    main()
