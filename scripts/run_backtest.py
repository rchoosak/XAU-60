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

def run_single_backtest(args_dict):
    """Function to be run in a separate process."""
    try:
        # Re-initialize for each process
        loader = StrategyLoader()
        loader.discover_strategies()
        
        symbol = args_dict.get("symbol", "XAUUSD")
        timeframe = args_dict.get("timeframe", "M15")
        start_str = args_dict.get("start")
        end_str = args_dict.get("end")
        
        start = datetime.strptime(start_str, "%Y-%m-%d") if start_str else None
        end = datetime.strptime(end_str, "%Y-%m-%d") if end_str else None
        
        mode = args_dict.get("mode", "real")
        execution = args_dict.get("execution", "strategy")
        
        # Initialize Backtester with all args
        backtester = Backtester(args_dict)
        
        if execution == "strategy":
            strategy_name = args_dict.get("strategy")
            if not strategy_name:
                return {"error": "Execution mode 'strategy' requires --strategy or config value 'strategy'"}
            strategy = loader.load_strategy(strategy_name)
            if not strategy:
                return {"error": f"Strategy {strategy_name} not found"}
            executor = strategy
        else:
            strategy_names_raw = args_dict.get("strategies", "")
            if isinstance(strategy_names_raw, str):
                strategy_names = strategy_names_raw.split(",")
            else:
                strategy_names = strategy_names_raw # could be a list in YAML
                
            strategies = []
            for name in strategy_names:
                if not str(name).strip(): continue
                s = loader.load_strategy(str(name).strip())
                if s: strategies.append(s)
            
            if not strategies:
                return {"error": "No valid strategies found for bot mode"}
            
            # Initialize BotEngine with specific bot args
            executor = BotEngine(strategies, args_dict)
            
        result = backtester.run(symbol, timeframe, start, end, executor, mode=mode)
        return {
            "symbol": symbol,
            "strategy": args_dict.get("strategy") or args_dict.get("strategies"),
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
    
    # Added some of the new parameters to CLI too for quick testing
    parser.add_argument("--aggregation", choices=["majority", "weighted", "priority"])
    parser.add_argument("--initial-balance", type=float, dest="initial_balance")
    parser.add_argument("--lot-size", type=float, dest="lot_size")

    args = parser.parse_args()
    args_dict = vars(args)

    if args.config:
        if os.path.exists(args.config):
            with open(args.config, "r") as f:
                config_data = yaml.safe_load(f)
                if config_data:
                    # Clean up keys: replace - with _ for compatibility
                    config_data = {k.replace("-", "_"): v for k, v in config_data.items()}
                    
                    # Merge logic: YAML values are defaults, CLI overrides
                    cli_overrides = {k: v for k, v in args_dict.items() if v is not None and k != "config"}
                    args_dict = {**config_data, **cli_overrides}
        else:
            logger.error(f"Config file not found: {args.config}")
            sys.exit(1)
    
    if args_dict.get("parallel"):
        timeframes = ["M5", "M15", "H1"]
        tasks = []
        for tf in timeframes:
            task_args = args_dict.copy()
            task_args["timeframe"] = tf
            tasks.append(task_args)
            
        logger.info(f"Running {len(tasks)} backtests in parallel...")
        with ProcessPoolExecutor() as executor:
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
