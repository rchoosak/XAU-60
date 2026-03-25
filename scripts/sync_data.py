import argparse
import os
import sys
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from datetime import datetime, timedelta
from loguru import logger
import time

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mt5_connector import MT5Connector

def get_chunk_delta(timeframe: str) -> timedelta:
    """M1 -> weekly chunks, others -> monthly chunks."""
    if timeframe.upper() == "M1":
        return timedelta(days=7)
    return timedelta(days=30)

def sync_data(symbol: str, timeframe: str, start_date: datetime, end_date: datetime):
    connector = MT5Connector()
    if not connector.connect():
        logger.error("Failed to connect to MT5")
        return

    db_path = f"data/backtest-db/{symbol}_{timeframe}.parquet"
    
    # Resumable sync: Check existing data
    if os.path.exists(db_path):
        existing_df = pd.read_parquet(db_path)
        if not existing_df.empty:
            last_timestamp = existing_df["time"].max()
            logger.info(f"Existing data found up to {last_timestamp}. Resuming sync.")
            start_date = max(start_date, last_timestamp + timedelta(seconds=1))
    
    if start_date >= end_date:
        logger.info("Data is already up to date.")
        connector.disconnect()
        return

    chunk_delta = get_chunk_delta(timeframe)
    current_start = start_date
    
    while current_start < end_date:
        current_end = min(current_start + chunk_delta, end_date)
        logger.info(f"Syncing {symbol} {timeframe}: {current_start} to {current_end}")
        
        retries = 3
        df = None
        while retries > 0:
            try:
                df = connector.get_historical_data(symbol, timeframe, current_start, current_end)
                if df is not None:
                    break
            except Exception as e:
                logger.warning(f"Error fetching data: {e}. Retrying...")
            
            retries -= 1
            if retries > 0:
                time.sleep(2)
        
        if df is not None and not df.empty:
            # Deduplicate and append
            if os.path.exists(db_path):
                existing_df = pd.read_parquet(db_path)
                combined_df = pd.concat([existing_df, df]).drop_duplicates(subset=["time"]).sort_values("time")
                combined_df.to_parquet(db_path, index=False)
            else:
                df.to_parquet(db_path, index=False)
            
            logger.success(f"Saved {len(df)} rows for chunk {current_start} to {current_end}")
        else:
            logger.warning(f"No data received for {current_start} to {current_end}")
            
        current_start = current_end
        time.sleep(0.5) # Prevents hitting API limits too hard

    connector.disconnect()
    logger.info("Sync completed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync MT5 data to local Parquet database.")
    parser.add_argument("--symbol", type=str, required=True, help="Symbol to sync (e.g. XAUUSD)")
    parser.add_argument("--timeframe", type=str, required=True, help="Timeframe (M1, M5, M15, M30, H1, D1)")
    parser.add_argument("--start", type=str, default="2005-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="End date (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    
    sync_data(args.symbol, args.timeframe, start_dt, end_dt)
