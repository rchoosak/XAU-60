# XAU-60 Trading Bot - Project Summary

## Overview
XAU-60 เป็นระบบเทรดอัตโนมัติที่พัฒนาด้วย Python และ MetaTrader 5 สำหรับการซื้อขายสินทรัพย์ XAU/USD (Gold) บน MetaTrader 5 โดยมีคุณสมบัติครบครันในการวิเคราะห์เทคนิค เทรดอัตโนมัติ และ Dashboard ควบคุม

**Repo**: https://github.com/rchoosak/XAU-60  
**License**: MIT License

---

## Architecture

```
XAU-60 Trading Bot
├── Core Engine (core/)
│   ├── mt5_connector.py      # MT5 connection & data fetching
│   ├── strategy_base.py      # Strategy interface (strategy pattern)
│   ├── strategy_loader.py    # Load strategies from config
│   ├── trade_executor.py     # Order execution & position management
│   ├── bridge_server.py      # HTTP bridge (MT5 ↔ Python)
│   └── backtest_engine.py    # Historical testing
├── Strategies (strategies/)
│   ├── crt_tbs.py            # CRT-TBS Strategy
│   ├── smc_scalper.py        # SMC Scalper Strategy
│   └── trend_break_trauma.py # Trend Break Trauma Strategy
├── Indicators (indicators/)
│   ├── common.py             # Shared indicator functions
│   ├── smc_utils.py          # SMC analysis tools (support/resistance)
│   └── trend_utils.py        # Trend analysis (EMA, MA)
├── Alerts (alerts/)
│   ├── telegram_bot.py       # Telegram notifications
│   └── discord_bot.py        # Discord notifications
├── UI (ui/)
│   ├── app.py                # Streamlit dashboard
│   └── pages/                # Dashboard pages
│       ├── dashboard.py      # Main dashboard
│       ├── strategies.py     # Strategy management
│       └── backtest.py       # Backtesting interface
├── Utils (utils/)
│   ├── config.py             # Configuration loader
│   ├── logger.py             # Logging utility
│   └── mt5_mock.py           # Mock objects for testing
└── Main (main.py)            # Entry point
```

---

## Key Components

### 1. Core Engine (`core/`)

#### `mt5_connector.py`
- **MT5Connector Class**: จัดการการเชื่อมต่อกับ MT5 (Terminal/Server/HTTP)
- ฟังก์ชันหลัก:
  - `connect()`: เชื่อมต่อ MT5 (Auto-detect mode)
  - `get_symbol_data()`: ดึงข้อมูล historical OHLC
  - `calculate_indicators()`: คำนวณ EMA, MA, BB
  - `execute_order()`: ส่งคำสั่งซื้อ/ขาย
  - `close_position()`: ปิดตำแหน่ง
  - `get_account_info()`: ข้อมูลบัญชี & Equity

#### `strategy_base.py`
- **StrategyBase Class**: Interface สำหรับ Strategy
- Method: `__init__()`, `get_name()`, `on_tick()`, `get_symbols()`
- ตัวอย่าง implementation: CRT-TBS, SMC Scalper, Trend Break Trauma

#### `trade_executor.py`
- **TradeExecutor Class**: จัดการ order execution
- Features:
  - Risk management (lot size, TP/SL)
  - Position tracking
  - Error handling
  - MT5 order execution

#### `strategy_loader.py`
- Load strategies จาก config files
- Dynamic strategy instantiation

#### `bridge_server.py`
- **HTTP Bridge**: เชื่อมต่อ MT5 Terminal ผ่าน HTTP API
- Endpoints:
  - `POST /execute` - Send order to MT5
  - `GET /symbol_data?symbol=...&period=...&count=...` - Get OHLC data
  - `GET /account_info` - Account information
  - `POST /close_position` - Close position

#### `backtest_engine.py`
- Backtesting engine สำหรับ historical testing
- รองรับ multiple strategies

---

### 2. Strategies (`strategies/`)

#### CRT-TBS (Creations - Trend Break System)
- **Concept**: Bounce from key levels + trend confirmation
- Indicators: EMA20, SMA50, Volume
- **Rules**:
  - Buy: Price above EMA20 + SMA50 support + volume surge
  - Sell: Price below EMA20 + SMA50 resistance + volume spike
- Entry: Breakout/retest of EMA20

#### SMC Scalper (Smart Money Concepts)
- **Concept**: Scalers ระยะสั้นจาก liquidity + structure
- Indicators: Support/Resistance zones (S/R), Market Structure (Highs/Lows)
- **Rules**:
  - Buy: Breakout of prior swing high + retest as support
  - Sell: Breakdown of prior swing low + retest as resistance
- TP/SL based on ATR

#### Trend Break Trauma
- **Concept**: Follow strong trend breakouts with SL
- Indicators: EMA, Trend lines
- **Rules**:
  - Buy: Breakout above resistance + bullish engulfing
  - Sell: Breakdown below support + bearish engulfing
- Strict SL to manage "trauma" (drawdown control)

---

### 3. Indicators (`indicators/`)

#### Common Indicators
- `calculate_ema()`: Exponential moving average
- `calculate_sma()`: Simple moving average
- `calculate_bollinger_bands()`: Bollinger Bands (20, 2)
- `calculate_atr()`: Average True Range

#### SMC Utils
- Support/Resistance zone detection
- Market structure analysis (Higher Highs/Lower Lows)
- Liquidity pool identification

#### Trend Utils
- EMA slope analysis
- Trend strength measurement
- MA alignment detection

---

### 4. Alerts (`alerts/`)

#### Telegram Bot
```python
TelegramBot(token, chat_id)
- send_message(message)
- send_signal(pair, direction, entry, tp, sl)
```

#### Discord Bot
```python
DiscordBot(webhook_url)
- send_signal_embed(pair, direction, entry, tp, sl)
```

---

### 5. UI (`ui/`)

#### Streamlit Dashboard (`app.py`)
- **Main Pages**:
  - `Dashboard`: Live trades, P/L, account info
  - `Strategies`: Manage strategy activation
  - `Backtest`: Run historical tests

#### Components
- Charts with Plotly integration
- Real-time updates
- Strategy toggle controls

---

## Configuration (`config/`)

### Strategies Config Files
1. **`crt_tbs.yaml`** - CRT-TBS parameters (EMA, volume thresholds)
2. **`smc_scalper.yaml`** - SMC parameters (SR zones, ATR multiplier)
3. **`trend_break_trauma.yaml`** - Trend break parameters (SL, TP)

### Settings (`config/settings.yaml`)
- Global settings (symbols, timeframes)
- MT5 connection config
- Alert settings

---

## Data Flow

```
MT5 Terminal/Server
    ↓ (Market data)
MT5Connector.get_symbol_data()
    ↓
Indicators.calculate_*()
    ↓
Strategy.on_tick() (check conditions)
    ↓
TradeExecutor.execute_order() / close_position()
    ↓
Alerts (Telegram/Discord)
```

---

## Entry Points

### CLI (`main.py`)
```bash
python main.py [start|backtest]
```
- `start`: Start live trading bot
- `backtest`: Run backtesting

### Dashboard (`ui/app.py`)
```bash
streamlit run ui/app.py
```

---

## Dependencies

**Python:**
- MetaTrader5 (`mt5`)
- Plotly (charts)
- PyYAML (config)
- pandas, numpy (data analysis)
- requests (HTTP bridge)
- streamlit (UI)

**MetaTrader 5:**
- MT5 Terminal (desktop app)
- HTTP API bridge (`bridge_server.py`)

---

## Installation

1. Clone repo
2. Install dependencies: `pip install -r requirements.txt`
3. Configure `.env` file (MT5 credentials, alerts)
4. Run: `python main.py start` or `streamlit run ui/app.py`

---

## Key Features

| Feature | Status |
|---------|--------|
| Live Trading | ✅ |
| Backtesting | ✅ |
| Multiple Strategies | ✅ (3 strategies) |
| Alert Notifications | ✅ (Telegram, Discord) |
| Dashboard UI | ✅ (Streamlit) |
| Risk Management | ✅ (Lot, TP/SL) |
| HTTP Bridge | ✅ |

---

## Technical Highlights

1. **Strategy Pattern**: Interface-based strategy design for extensibility
2. **Auto-Detect Mode**: Automatic detection of MT5 connection method
3. **HTTP Bridge**: Secure communication between Python and MT5 Terminal
4. **Multiple Timeframes**: Support for various timeframe analysis
5. **Backtesting Framework**: Historical performance testing

---

## Supported Trading Pairs
- XAU/USD (Gold)
- USD/JPY (configured in strategies)

---

## Project Status
- ✅ Core Engine: Complete
- ✅ 3 Strategies Implemented
- ✅ Alert System
- ✅ Dashboard UI
- 🚀 Live Trading Ready

---

## GitHub Activity
- Latest Commit: `0a4805eb242077de3086a2442112daf7e7b0b455`
- Repository: https://github.com/rchoosak/XAU-60
