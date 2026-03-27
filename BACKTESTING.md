# คู่มือการทำ Backtesting (XAU-60)

คู่มือนี้รวบรวมขั้นตอนตั้งแต่การดาวน์โหลดข้อมูลดิบ ไปจนถึงการรันระบบ Backtest สำหรับทองคำ (XAUUSD)

---

## 1. การดาวน์โหลดข้อมูลสำหรับใช้ Backtest

ระบบของเราใช้ข้อมูลคุณภาพสูงจาก Dukascopy ซึ่งเป็นแหล่งข้อมูล Tick Data ที่มีความแม่นยำสูง โดยขั้นตอนการเตรียมข้อมูลมีดังนี้:
1. ดาวน์โหลดข้อมูลดิบเป็นไฟล์ CSV โดยใช้เครื่องมือ `dukascopy-node`
2. แปลงไฟล์ CSV เป็นรูปแบบ Parquet ที่มีขนาดเล็กลงและประมวลผลได้รวดเร็ว
3. (Optional) รวมไฟล์ CSV หากดาวน์โหลดแยกช่วงเวลา

---

## 2. การติดตั้ง dukascopy-node

ก่อนเริ่มดาวน์โหลด คุณต้องติดตั้ง Node.js ในเครื่องก่อน จากนั้นติดตั้ง `dukascopy-node` ผ่าน npm:

```bash
# ติดตั้ง global
npm install -g dukascopy-node
```
รายละเอียดการติดตั้งเพิ่มเติม: [https://www.dukascopy-node.app/installation](https://www.dukascopy-node.app/installation)

---

## 3. การใช้คำสั่งดาวน์โหลดข้อมูล (Dukascopy)

ในการดาวน์โหลดข้อมูล XAUUSD (Bid) ในระดับ 5 นาที (M5) หรือ 15 นาที (M15) ใช้คำสั่งดังนี้:

```bash
# ตัวอย่าง: ดาวน์โหลด XAUUSD ช่วงปี 2024-01-01 ถึง 2026-03-24
dukascopy-node download \
  -i xauusd \
  -from 2024-01-01 \
  -to 2026-03-24 \
  -t m5 \
  -f csv
```

**ตัวเลือกที่สำคัญ:**
- `-i`: เครื่องมือที่ต้องการ (เช่น `xauusd`)
- `-from` / `-to`: ช่วงเวลา (YYYY-MM-DD)
- `-t`: Timeframe (`m1`, `m5`, `m15`, `h1`)
- `-f`: format (`csv`, `json`)

### ข้อมูลย้อนหลังที่สามารถดาวน์โหลดได้ (Dukascopy Earliest Data)

| Timeframe | Earliest available data (UTC) |
| :--- | :--- |
| tick, s1 | May 5, 2003 at 12:01:03 AM |
| m1, m5, m15, m30 | May 5, 2003 at 12:01 AM |
| h1, h4 | May 5, 2003 at 12 AM |
| d1, mn1 | June 3, 1999 |

รายละเอียดเพิ่มเติมสำหรับ XAUUSD: [https://www.dukascopy-node.app/instrument/xauusd](https://www.dukascopy-node.app/instrument/xauusd)

---

## 4. การแปลงไฟล์ CSV เป็น Parquet (`csv_to_parquet.py`)

เมื่อได้ไฟล์ CSV มาแล้ว (เช่น `data/download/xauusd-m5.csv`) ให้แปลงเป็น Parquet เพื่อความรวดเร็วในการทำ Backtest:

```bash
python3 scripts/csv_to_parquet.py --input <path_to_csv> --output <path_to_parquet>
```

**ตัวอย่าง:**
```bash
python3 scripts/csv_to_parquet.py \
  --input data/download/xauusd-m5-bid-2020-01-01-2026-03-24.csv \
  --output data/backtest-db/xauusd-m5-bid-2020-01-01-2026-03-24.parquet
```

**คุณสมบัติของสคริปต์:**
- รองรับคอลัมน์ชื่อ `time` หรือ `timestamp`
- ตรวจจับหน่วย Timestamp (Seconds หรือ Milliseconds) อัตโนมัติ
- จัดการไฟล์ที่มี Header ซ้ำซ้อน (จากการ `cat` รวมไฟล์) ให้อัตโนมัติ

---

## 5. การใช้ `run_backtest.py` และการ Config

### การรันคำสั่ง
รันการทดสอบโดยระบุไฟล์ตั้งค่า (YAML):

```bash
python3 scripts/run_backtest.py -f config/backtest.yaml
```

### การตั้งค่าใน `config/backtest.yaml`
คุณสามารถตั้งค่าตัวแปรต่างๆ ได้ในไฟล์ YAML โดยมีส่วนที่สำคัญคือ:

```yaml
# 1. ข้อมูล (Data Parameters)
mode: real                                       # ต้องเป็น 'real' สำหรับข้อมูลดิบ
symbol: XAUUSD
timeframe: M5
data_path: data/backtest-db/your_file.parquet    # ชี้ตรงที่ไฟล์ parquet ได้เลย

# ช่วงเวลา (หากไม่ระบุ จะ Test ข้อมูลทั้งหมดที่มีในไฟล์)
# start: "2024-01-01"
# end: "2026-03-25"

# 2. การเทรด (Trading Parameters)
initial_balance: 10000.0
risk_per_trade: 0.01                             # เสี่ยง 1% ของพอร์ตต่อออเดอร์
max_open_trades: 3

# 3. กลยุทธ์ (Execution)
execution: bot                                   # [strategy | bot]
strategies: "bollinger_reversion,smc_scalper"     # ชื่อกลยุทธ์ (คั่นด้วย comma)
```

**ข้อแนะนำ:**
- หากต้องการเทรดข้อมูลทั้งหมดในไฟล์ ให้ **comment** หรือลบบรรทัด `start` และ `end` ออก
- หากระบุ `start`/`end` ระบบจะกรองข้อมูล (Scope) ตามช่วงเวลาที่กำหนดเท่านั้น

---

## 6. Live-like Backtest (จำลองเหมือนบอทเทรดจริง)

สำหรับกรณีที่ต้องการให้การคำนวณใช้ flow เดียวกับบอทจริง (โหลด `.env` + `settings.yaml` + strategy config และใช้ `RiskManager`/`TradeExecutor` ตัวเดียวกับโหมด live) ให้ใช้สคริปต์ใหม่:

```bash
python3 scripts/run_replay_backtest.py -f config/replay_backtest.yaml
```

หรือรันแบบไม่เปิด TUI:

```bash
python3 scripts/run_replay_backtest.py -f config/replay_backtest.yaml --no-tui
```

ไฟล์ตั้งค่าตัวอย่างอยู่ที่:

```text
config/replay_backtest.yaml
```

จุดสำคัญ:
- ตัวสคริปต์จะ replay ข้อมูล historical จาก Parquet แล้วป้อนเข้า `TradingBot._tick()` โดยตรง
- Risk และ lot sizing จะอิงค่าจริงจาก `.env`/`config/settings.yaml` และ `risk` ของแต่ละ strategy
- โดย default จะใช้ `enabled` ใน `config/strategies/*.yaml` และใช้ `execution` เฉพาะตอนต้องการ override กลยุทธ์
- รองรับ TUI แบบเดียวกับ backtest เดิม (`p` pause/resume, `q` quit)
