"""
MT5 CSV to Parquet Converter
===========================
This script converts CSV files exported from the XAU60_BacktestDB EA into Parquet format 
for use with the local backtesting system.

Usage:
    python3 scripts/csv_to_parquet.py --input <path_to_csv> --output <path_to_parquet>

Example:
    python3 scripts/csv_to_parquet.py \
        --input "MQL5/Files/XAUUSD_M5_2024-01-01_2024-03-24.csv" \
        --output "data/backtest-db/XAUUSD_M5_2024-01-01_2024-03-24.parquet"

Requirements:
    - pandas
    - pyarrow
    - loguru
"""

import pandas as pd
import argparse
import os
import sys
from datetime import datetime
from loguru import logger

def detect_timeframe(df: pd.DataFrame) -> int:
    """Detect timeframe in seconds from the most frequent time difference."""
    if len(df) < 2:
        return 0
    diffs = df['time'].diff().dropna().dt.total_seconds().astype(int)
    return int(diffs.mode()[0])

def convert_csv_to_parquet(csv_file: str, output_file: str):
    """Convert MT5 exported CSV to Parquet for backtesting."""
    if not os.path.exists(csv_file):
        logger.error(f"CSV file not found: {csv_file}")
        return False

    try:
        # Load CSV
        df = pd.read_csv(csv_file)
        if df.empty:
            logger.warning("CSV file is empty.")
            return False

        # Ensure 'time' is datetime and sorted
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'], unit='s' if df['time'].dtype == 'int64' else None)
            df = df.sort_values('time')
        else:
            logger.error("CSV must contain a 'time' column.")
            return False

        # Detect timeframe for new data
        new_tf = detect_timeframe(df)
        logger.info(f"Detected timeframe for new data: {new_tf}s")

        # Handle duplicates and merging
        if os.path.exists(output_file):
            try:
                existing_df = pd.read_parquet(output_file)
                if not existing_df.empty:
                    # Detect timeframe for existing data
                    existing_df = existing_df.sort_values('time')
                    existing_tf = detect_timeframe(existing_df)
                    
                    # Validate timeframe match
                    if existing_tf > 0 and new_tf > 0 and existing_tf != new_tf:
                        logger.error(f"Timeframe mismatch! Existing Parquet is {existing_tf}s, but CSV is {new_tf}s.")
                        logger.error("Aborting merge to prevent data corruption.")
                        return False

                logger.info(f"Merging with existing data: {len(existing_df)} rows")
                # Merge and deduplicate
                df = pd.concat([existing_df, df]).drop_duplicates(subset=['time'], keep='last').sort_values('time')
            except Exception as e:
                logger.warning(f"Could not read existing parquet file, creating new one: {e}")
                df = df.drop_duplicates(subset=['time'], keep='last').sort_values('time')
        else:
            df = df.drop_duplicates(subset=['time'], keep='last').sort_values('time')

        final_len = len(df)
        logger.info(f"Total rows after merge and deduplication: {final_len}")

        # Standardize volume column names if needed
        if 'tick_volume' in df.columns and 'volume' not in df.columns:
            df['volume'] = df['tick_volume']

        # Ensure directory exists
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        # Save to Parquet
        df.to_parquet(output_file, index=False)
        logger.success(f"Successfully saved {final_len} rows to {output_file}")
        return True

    except Exception as e:
        logger.error(f"Conversion failed: {e}")
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert MT5 CSV export to Parquet.")
    parser.add_argument("--input", required=True, help="Path to input CSV file")
    parser.add_argument("--output", required=True, help="Path for output Parquet file")
    
    args = parser.parse_args()
    convert_csv_to_parquet(args.input, args.output)
