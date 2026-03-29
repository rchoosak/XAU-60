#!/usr/bin/env python3
"""
Analyze trade journal and suggest loss-window filters.

Reads logs/trade_journal.jsonl, focuses on CLOSE events, and prints:
- summary metrics
- worst loss windows by hour/day
- YAML-ready execution_filters suggestions
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd


WEEKDAY_NAMES = {
    0: "mon",
    1: "tue",
    2: "wed",
    3: "thu",
    4: "fri",
    5: "sat",
    6: "sun",
}


def _series_or_default(df: pd.DataFrame, column: str, default: Any) -> pd.Series:
    if column in df.columns:
        return df[column]
    return pd.Series([default] * len(df), index=df.index)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[WARN] Skip invalid JSON at line {line_no}")
                continue
            if isinstance(row, dict):
                events.append(row)
    return events


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _prepare_close_df(
    events: List[Dict[str, Any]],
    mode: str,
    strategy: str,
    symbol: str,
) -> pd.DataFrame:
    if not events:
        return pd.DataFrame()

    df = pd.DataFrame(events)
    if df.empty:
        return df

    df["event"] = _series_or_default(df, "event", "").astype(str)
    df["mode"] = _series_or_default(df, "mode", "unknown").astype(str)
    df["strategy"] = _series_or_default(df, "strategy", "unknown").map(_norm_text).replace("", "unknown")
    df["symbol"] = _series_or_default(df, "symbol", "").map(_norm_text).replace("", "unknown")

    if mode != "all":
        df = df[df["mode"].str.lower() == mode.lower()]

    if strategy:
        strategy_lc = strategy.lower()
        df = df[df["strategy"].str.lower() == strategy_lc]

    if symbol:
        symbol_uc = symbol.upper()
        df = df[df["symbol"].str.upper() == symbol_uc]

    close_df = df[df["event"].str.upper() == "CLOSE"].copy()
    if close_df.empty:
        return close_df

    close_df["profit"] = pd.to_numeric(_series_or_default(close_df, "profit", 0.0), errors="coerce").fillna(0.0)
    close_source = close_df["close_time"] if "close_time" in close_df.columns else _series_or_default(close_df, "logged_at", None)
    close_df["close_time_dt"] = pd.to_datetime(close_source, errors="coerce", utc=True)
    entry_bar_source = None
    if "entry_context" in close_df.columns:
        entry_bar_source = close_df["entry_context"].map(
            lambda ctx: ctx.get("bar_time") if isinstance(ctx, dict) else None
        )
    if entry_bar_source is None:
        analysis_source = close_source
    else:
        analysis_source = entry_bar_source.where(entry_bar_source.notna(), close_source)
    close_df["analysis_time_dt"] = pd.to_datetime(analysis_source, errors="coerce", utc=True)
    close_df["open_time_dt"] = pd.to_datetime(_series_or_default(close_df, "open_time", None), errors="coerce", utc=True)
    close_df = close_df.dropna(subset=["analysis_time_dt"]).copy()
    if close_df.empty:
        return close_df

    close_df["hour"] = close_df["analysis_time_dt"].dt.hour.astype(int)
    close_df["weekday"] = close_df["analysis_time_dt"].dt.dayofweek.astype(int)
    close_df["weekday_name"] = close_df["weekday"].map(WEEKDAY_NAMES)
    close_df["is_loss"] = close_df["profit"] < 0
    close_df["is_win"] = close_df["profit"] > 0
    close_df["duration_minutes"] = pd.to_numeric(_series_or_default(close_df, "duration_minutes", None), errors="coerce")
    return close_df


def _aggregate_windows(close_df: pd.DataFrame, by: List[str]) -> pd.DataFrame:
    total = close_df.groupby(by).agg(
        closes=("profit", "count"),
        wins=("is_win", "sum"),
        losses=("is_loss", "sum"),
        net_profit=("profit", "sum"),
        avg_profit=("profit", "mean"),
    )
    total["loss_rate"] = total["losses"] / total["closes"]
    return total.reset_index()


def _print_top(
    title: str,
    table: pd.DataFrame,
    sort_cols: List[str],
    max_rows: int,
) -> None:
    print(f"\n=== {title} ===")
    if table.empty:
        print("No data")
        return

    asc = [True if c in {"net_profit", "net_loss"} else False for c in sort_cols]
    out = table.sort_values(sort_cols, ascending=asc).head(max_rows)
    for _, row in out.iterrows():
        parts = []
        if "strategy" in row:
            parts.append(f"strategy={row['strategy']}")
        if "symbol" in row:
            parts.append(f"symbol={row['symbol']}")
        if "reason" in row:
            parts.append(f"reason={row['reason']}")
        if "hour" in row:
            parts.append(f"hour={int(row['hour']):02d}")
        if "weekday_name" in row:
            parts.append(f"weekday={row['weekday_name']}")
        if "closes" in row:
            parts.append(f"closes={int(row['closes'])}")
        if "count" in row and "closes" not in row:
            parts.append(f"count={int(row['count'])}")
        if "losses" in row:
            parts.append(f"losses={int(row['losses'])}")
        if "loss_rate" in row:
            parts.append(f"loss_rate={float(row['loss_rate']):.2%}")
        if "net_profit" in row:
            parts.append(f"net={float(row['net_profit']):.2f}")
        elif "net_loss" in row:
            parts.append(f"net_loss={float(row['net_loss']):.2f}")
        if "avg_profit" in row:
            parts.append(f"avg={float(row['avg_profit']):.2f}")
        print(" | ".join(parts))


def _suggest_blocks(
    table: pd.DataFrame,
    key_col: str,
    min_samples: int,
    min_loss_rate: float,
) -> Dict[Tuple[str, str], List[Any]]:
    if table.empty:
        return {}

    mask = (
        (table["closes"] >= min_samples)
        & (table["loss_rate"] >= min_loss_rate)
        & (table["net_profit"] < 0)
    )
    candidates = table[mask]
    suggestions: Dict[Tuple[str, str], List[Any]] = {}
    for _, row in candidates.iterrows():
        key = (_norm_text(row["strategy"]), _norm_text(row["symbol"]))
        suggestions.setdefault(key, []).append(row[key_col])
    return suggestions


def _merge_suggestions(
    hours: Dict[Tuple[str, str], List[Any]],
    weekdays: Dict[Tuple[str, str], List[Any]],
) -> Dict[Tuple[str, str], Dict[str, List[Any]]]:
    keys = set(hours.keys()) | set(weekdays.keys())
    out: Dict[Tuple[str, str], Dict[str, List[Any]]] = {}
    for key in sorted(keys):
        h_vals = sorted({int(v) for v in hours.get(key, [])})
        d_vals = sorted({int(v) for v in weekdays.get(key, [])})
        out[key] = {"blocked_hours": h_vals, "blocked_weekdays": d_vals}
    return out


def _print_yaml_suggestions(merged: Dict[Tuple[str, str], Dict[str, List[Any]]]) -> None:
    print("\n=== YAML Suggestions (execution_filters) ===")
    if not merged:
        print("No windows passed thresholds. Try lowering --min-samples or --min-loss-rate.")
        return

    for (strategy, symbol), conf in merged.items():
        weekdays_name = [WEEKDAY_NAMES.get(int(d), str(int(d))) for d in conf["blocked_weekdays"]]
        print(f"\n# strategy={strategy} symbol={symbol}")
        print("execution_filters:")
        print("  enabled: true")
        print("  allow_long: true")
        print("  allow_short: true")
        print(f"  blocked_hours: {conf['blocked_hours']}")
        print(f"  blocked_weekdays: {weekdays_name}")
        print("  max_spread_points: 0")
        print("  cooldown_bars_after_loss: 0")
        print("  max_consecutive_losses: 0")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze loss windows from trade_journal.jsonl")
    parser.add_argument("--journal", default="logs/trade_journal.jsonl", help="Path to trade journal JSONL")
    parser.add_argument("--mode", default="all", choices=["all", "live", "backtest"], help="Filter mode")
    parser.add_argument("--strategy", default="", help="Filter one strategy")
    parser.add_argument("--symbol", default="", help="Filter one symbol")
    parser.add_argument("--min-samples", type=int, default=5, help="Minimum close samples before suggesting blocks")
    parser.add_argument("--min-loss-rate", type=float, default=0.65, help="Minimum loss rate threshold")
    parser.add_argument("--top", type=int, default=10, help="Rows to display in worst-window tables")
    args = parser.parse_args()

    path = Path(args.journal)
    if not path.exists():
        raise SystemExit(f"Journal not found: {path}")

    events = _read_jsonl(path)
    close_df = _prepare_close_df(
        events=events,
        mode=args.mode,
        strategy=args.strategy,
        symbol=args.symbol,
    )

    print("\n=== Journal Stats ===")
    print(f"journal: {path}")
    print(f"rows: {len(events)}")
    print(f"close_events: {len(close_df)}")

    if close_df.empty:
        print("No CLOSE events matched filters.")
        return

    total_closes = len(close_df)
    wins = int(close_df["is_win"].sum())
    losses = int(close_df["is_loss"].sum())
    net_profit = float(close_df["profit"].sum())
    win_rate = (wins / total_closes) * 100 if total_closes else 0.0
    avg_loss = float(close_df.loc[close_df["is_loss"], "profit"].mean()) if losses else 0.0
    avg_win = float(close_df.loc[close_df["is_win"], "profit"].mean()) if wins else 0.0

    print(f"wins={wins} losses={losses} win_rate={win_rate:.2f}% net_profit={net_profit:.2f}")
    print(f"avg_win={avg_win:.2f} avg_loss={avg_loss:.2f}")

    reason_loss = (
        close_df[close_df["is_loss"]]
        .groupby("reason")
        .agg(count=("profit", "count"), net_loss=("profit", "sum"))
        .reset_index()
        .sort_values(["net_loss", "count"], ascending=[True, False])
    )
    _print_top("Loss Reasons", reason_loss, ["net_loss", "count"], args.top)

    by_hour = _aggregate_windows(close_df, ["strategy", "symbol", "hour"])
    _print_top("Worst Hours", by_hour, ["net_profit", "losses"], args.top)

    by_weekday = _aggregate_windows(close_df, ["strategy", "symbol", "weekday", "weekday_name"])
    _print_top("Worst Weekdays", by_weekday, ["net_profit", "losses"], args.top)

    hour_suggestions = _suggest_blocks(by_hour, "hour", args.min_samples, args.min_loss_rate)
    day_suggestions = _suggest_blocks(by_weekday, "weekday", args.min_samples, args.min_loss_rate)
    merged = _merge_suggestions(hour_suggestions, day_suggestions)
    _print_yaml_suggestions(merged)


if __name__ == "__main__":
    main()
