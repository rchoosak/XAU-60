import argparse
import glob
import os
import re
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
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        # Keep backward compatibility with date-only format while supporting
        # second-level/tick backtests that commonly pass full timestamps.
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


def _normalize_timeframe(raw_value):
    if raw_value is None:
        return None
    tf = str(raw_value).strip().upper()
    aliases = {
        "TICK": "TICK",
        "TICKS": "TICK",
        "S1": "S1",
        "1S": "S1",
        "SEC1": "S1",
        "SECOND": "S1",
        "SECONDS": "S1",
    }
    return aliases.get(tf, tf)


def _timeframe_tokens(timeframe):
    tf = _normalize_timeframe(timeframe)
    if tf == "TICK":
        return ("tick",)
    if tf == "S1":
        return ("s1", "1s", "sec1", "second")
    return ()


def _has_timeframe_token(name_lower, token):
    return re.search(rf"(?<![a-z0-9]){re.escape(token.lower())}(?![a-z0-9])", name_lower) is not None


def _is_timeframe_file(path, timeframe, allow_s1_m1_fallback=False):
    if not path:
        return False
    name = os.path.basename(path).lower()
    tf = _normalize_timeframe(timeframe)
    tokens = _timeframe_tokens(tf)
    if tf == "S1":
        if any(_has_timeframe_token(name, t) for t in tokens):
            return True
        return allow_s1_m1_fallback and _has_timeframe_token(name, "m1")
    return any(_has_timeframe_token(name, t) for t in tokens)


def _resolve_special_timeframe_data_path(args_dict):
    timeframe = _normalize_timeframe(args_dict.get("timeframe"))
    if timeframe not in {"TICK", "S1"}:
        return

    data_path = args_dict.get("data_path")
    symbol = str(args_dict.get("symbol", "XAUUSD"))
    symbol_lower = symbol.lower()

    # If file path already matches requested timeframe, keep it as-is.
    if data_path and os.path.isfile(data_path) and _is_timeframe_file(data_path, timeframe):
        return

    search_dir = None
    fallback_file = None

    if data_path:
        if os.path.isfile(data_path):
            search_dir = os.path.dirname(data_path) or "."
            fallback_file = data_path
        elif os.path.isdir(data_path):
            search_dir = data_path
        else:
            parent = os.path.dirname(data_path)
            if parent and os.path.isdir(parent):
                search_dir = parent
    else:
        default_dir = "data/backtest-db"
        if os.path.isdir(default_dir):
            search_dir = default_dir

    if not search_dir:
        return

    parquet_files = sorted(glob.glob(os.path.join(search_dir, "*.parquet")))
    ranked_matches = []

    for path in parquet_files:
        name = os.path.basename(path).lower()
        if symbol_lower not in name:
            continue

        if timeframe == "TICK":
            if _has_timeframe_token(name, "tick"):
                ranked_matches.append((0, path))
        else:  # S1
            if any(_has_timeframe_token(name, token) for token in _timeframe_tokens("S1")):
                ranked_matches.append((0, path))
            elif _has_timeframe_token(name, "m1"):
                ranked_matches.append((1, path))

    if ranked_matches:
        ranked_matches.sort(key=lambda x: (x[0], x[1]))
        chosen = ranked_matches[0][1]
        args_dict["data_path"] = chosen
        logger.info(f"Resolved {timeframe} data file: {chosen}")
    elif fallback_file:
        args_dict["data_path"] = fallback_file
        logger.warning(
            f"No data file matched timeframe={timeframe} in {search_dir}. "
            f"Keeping provided file: {fallback_file}"
        )
    else:
        logger.warning(
            f"No data file matched timeframe={timeframe} in {search_dir}. "
            "Set data_path explicitly to a parquet file."
        )


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
    if resolved.get("timeframe") is not None:
        resolved["timeframe"] = _normalize_timeframe(resolved.get("timeframe"))

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
    timeframe = _normalize_timeframe(args_dict.get("timeframe", "M15")) or "M15"
    args_dict["timeframe"] = timeframe
    _resolve_special_timeframe_data_path(args_dict)
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
            timeframes = [
                _normalize_timeframe(tf.strip())
                for tf in str(args_dict["timeframes"]).split(",")
                if tf.strip()
            ]
        elif args_dict.get("timeframe"):
            timeframes = [_normalize_timeframe(str(args_dict["timeframe"]).strip())]
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
