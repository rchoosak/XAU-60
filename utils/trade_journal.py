"""
Structured trade journaling for live and backtest flows.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from loguru import logger


def _default_path() -> Path:
    custom = os.getenv("TRADE_JOURNAL_FILE", "").strip()
    if custom:
        return Path(custom)
    return Path("logs/trade_journal.jsonl")


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def append_trade_event(event: Dict[str, Any]) -> None:
    """
    Append one trade event as JSONL.
    """
    path = _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "logged_at": datetime.utcnow().isoformat() + "Z",
        **{k: _normalize(v) for k, v in event.items()},
    }

    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception as e:
        logger.warning(f"Failed to append trade journal event: {e}")
