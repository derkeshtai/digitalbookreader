"""Platform-aware paths for persistent reader data and disposable caches."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Digital Book Reader"


def data_dir() -> Path:
    """Return the per-user application data directory (LocalAppData on Windows)."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / APP_NAME
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "digital-book-reader"


def cache_dir() -> Path:
    """Return the per-user LocalAppData cache directory on Windows."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / APP_NAME / "Cache"
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "digital-book-reader"
