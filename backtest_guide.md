# Local Backtesting System Guide

This system provides a fully offline backtesting infrastructure for MT5 trading strategies. It uses Parquet files for data storage and supports multi-strategy aggregation.

## 1. Data Sync Tool

Before running a backtest, you must sync data from MT5 to your local database.

**Script**: `scripts/sync_data.py`

**Usage**:
```bash
python scripts/sync_data.py --symbol XAUUSD --timeframe M5 --start 2010-01-01
```

### Key Features:
- **Chunking**: M1 data is fetched in weekly chunks, while higher timeframes use monthly chunks. This prevents API timeouts and memory issues.
- **Resumable**: If a sync is interrupted, running the same command will resume from the last timestamp found in the local file.
- **Performance**: Uses `pandas` and `pyarrow` for efficient storage and retrieval.

---

## 2. Backtest Execution

Run backtests using the unified runner script.

**Script**: `scripts/run_backtest.py`

### Strategy Mode (Single Strategy)
Runs a single strategy against historical data.

```bash
python3 scripts/run_backtest.py \
  --mode real \
  --execution strategy \
  --strategy "SMC Scalper" \
  --symbol XAUUSD \
  --timeframe M15
```

### Config File Mode (Recommended)
You can also store all parameters in a YAML file and run it with the `-f` flag.

**Example Config (`config/backtest.yaml`)**:
```yaml
mode: real
execution: bot
symbol: XAUUSD
timeframe: M15
start: "2024-01-01"
strategies: "SMC Scalper,Bollinger Reversion"
```

**Usage**:
```bash
python3 scripts/run_backtest.py -f config/backtest.yaml
```

### Bot Mode (Multi-Strategy)
```bash
python3 scripts/run_backtest.py \
  --mode real \
  --execution bot \
  --strategies "SMC Scalper,Bollinger Reversion" \
  --symbol XAUUSD \
  --timeframe M15
```

---

## 3. Random Mode

Used for stress-testing risk management without needing real data.

```bash
python scripts/run_backtest.py --mode random --execution bot
```

---

## 4. Signal Aggregation Logic

When running in **Bot Mode**, the `BotEngine` aggregates signals using one of the following methods (configurable in `core/bot_engine.py`):

1. **Majority Voting**: The direction with the most votes wins.
2. **Weighted Confidence**: Signals are weighted by their confidence score.
3. **Priority-based**: Takes the signal from the highest-priority strategy.

---

## 5. Performance Metrics

The system calculates:
- **Net Profit**: Total gain/loss.
- **Win Rate**: Percentage of winning trades.
- **Max Drawdown**: Maximum peak-to-trough decline.
- **Profit Factor**: Gross Profit / Gross Loss.

---

## 6. Tips & Warnings

- **Large Data**: Syncing M1 data from 2005 can result in ~7 million rows. Ensure you have enough disk space and memory (Parquet is efficient, but loading long periods into memory still requires RAM).
- **Multiprocessing**: Use `--parallel` to run backtests across multiple timeframes or symbols simultaneously.
- **Offline Mode**: Once synced, you do not need MT5 running or an internet connection to run backtests.
