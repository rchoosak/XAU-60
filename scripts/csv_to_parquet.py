"""
MT5 CSV to Parquet Converter
===========================
This script converts CSV files exported from `dukascopy-node` csv into Parquet format 
for use with the local backtesting system.

Usage:
    python3 scripts/csv_to_parquet.py --input <path_to_csv> --output <path_to_parquet>

Example:
    python3 scripts/csv_to_parquet.py \
        --input "csv/files/XAUUSD_M5_2024-01-01_2024-03-24.csv" \
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
        df = pd.read_csv(csv_file, low_memory=False)
        if df.empty:
            logger.warning("CSV file is empty.")
            return False

        # Handle 'timestamp' mapping to 'time'
        if 'timestamp' in df.columns and 'time' not in df.columns:
            logger.info("Mapping 'timestamp' column to 'time'")
            df = df.rename(columns={'timestamp': 'time'})

        # Detect if 'time' is object (might happen if header is repeated in data)
        if 'time' in df.columns and df['time'].dtype == 'object':
            # Clean up: remove rows that might be headers (e.g. if files were catenated)
            df = df[df['time'] != 'timestamp']
            df = df[df['time'] != 'time']
            # Convert to numeric, errors='coerce' will make non-numeric rows NaN
            df['time'] = pd.to_numeric(df['time'], errors='coerce')
            df = df.dropna(subset=['time'])

        # Ensure 'time' is datetime and sorted
        if 'time' in df.columns:
            # Detect if timestamp is in milliseconds (value > 10^11) or seconds
            if df['time'].dtype in ['int64', 'float64', 'int', 'float']:
                # Filter out obvious outliers if any
                valid_mask = (df['time'] > 0)
                df = df[valid_mask]
                
                is_ms = (df['time'] > 1e11).any()
                unit = 'ms' if is_ms else 's'
                if is_ms:
                    logger.info("Detected millisecond timestamps")
                df['time'] = pd.to_datetime(df['time'], unit=unit)
            else:
                df['time'] = pd.to_datetime(df['time'])
            
            df = df.sort_values('time')
        else:
            logger.error("CSV must contain a 'time' or 'timestamp' column.")
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
