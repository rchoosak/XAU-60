"""
Project-wide Python startup customization.

Keep bytecode cache files out of source folders by redirecting pycache
to a single project cache directory.
"""
from pathlib import Path
import os
import sys


def _is_truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _configure_pycache_prefix() -> None:
    if _is_truthy(os.getenv("PYTHONDONTWRITEBYTECODE", "")):
        return

    # Respect explicit user override, otherwise force project-local cache.
    if str(os.getenv("PYTHONPYCACHEPREFIX", "")).strip():
        return

    root = Path(__file__).resolve().parent
    target = root / ".cache" / "pycache"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    target_str = str(target)
    if getattr(sys, "pycache_prefix", None) != target_str:
        sys.pycache_prefix = target_str


_configure_pycache_prefix()
