# MT5 Local Backtesting System Guide

A robust, offline-first system for testing MT5 trading strategies using local Parquet data.

## 1. Quick Start

### Data Sync
Sync historical data from MT5 to your local machine.
```bash
python3 scripts/sync_data.py --symbol XAUUSD --timeframe M15 --start 2024-01-01
```

### Run Backtest (Config File)
The recommended way to run backtests is using a YAML configuration file.
```bash
python3 scripts/run_backtest.py -f config/backtest.yaml
```

---

## 2. Configuration Parameters (`config/backtest.yaml`)

### Data Parameters
- `mode`: `real` (Parquet) or `random` (Synthetic).
- `data_path`: Directory for Parquet files (default: `data/backtest-db`).
- `lookback`: Number of bars provided to the strategy for analysis (default: 100).
- `warmup`: Number of bars to skip at the start of the data (default: 200).

### Trading & Risk
- `initial_balance`: Initial account balance (default: 10000.0).
- `risk_per_trade`: Percentage of balance to risk (0.01 = 1%). Automatic lot calculation based on stop loss.
- `max_open_trades`: Maximum concurrent open positions (default: 3).
- `lot_size`: Fixed lot size fallback (default: 0.1).
- `spread_pips`: Fixed spread in pips (1.0 = 10 points for XAUUSD).
- `commission`: Commission per lot round turn.
- `slippage`: Price slippage factor.

### Position Management
- `use_trailing_stop`: Enable/disable trailing stop (default: true).
- `trailing_stop_pips`: Distance for the trailing stop in pips.
- `tp_multiplier`: Global multiplier for strategy's Take Profit.
- `sl_multiplier`: Global multiplier for strategy's Stop Loss.

### Bot Aggregation
- `aggregation`: `majority` (most strategies agree), `weighted` (based on weights), or `priority` (first match in list).
- `strategy_weights`: Dictionary of weights for each strategy.
- `strategy_priority`: Ordered list of strategy names for priority mode.

### Output & Logging
- `save_trades`: Save all trade details to CSV (default: true).
- `output`: Filename for trade CSV (default: `trades.csv`).
- `log_signals`: Show detailed entry/exit logs in terminal (default: true).

---

## 3. Directory Structure
- `core/`: Backtesting engine, bot orchestration, and strategy loader.
- `scripts/`: CLI runners for sync and backtesting.
- `data/backtest-db/`: Local Parquet storage (automatically created).
- `config/`: Configuration files for strategies and backtests.
- `strategies/`: Your trading strategy implementations.
