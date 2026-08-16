"""Filesystem layout for the app.

Source code lives at ``<repo>/src/rl_h2h/``; runtime data and logs are written to
``<repo>/data/`` and ``<repo>/logs/`` respectively. Folders are created on
import so every other module can write without a guarded ``mkdir``.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "logs"
ASSETS_DIR = ROOT_DIR / "assets"

for _d in (DATA_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
MATCHES_PATH = DATA_DIR / "matches.jsonl"
PLAYERS_PATH = DATA_DIR / "players.json"
MMR_CACHE_PATH = DATA_DIR / "mmr_cache.json"
MMR_HISTORY_PATH = DATA_DIR / "mmr_history.jsonl"

MMR_LOG_PATH = LOG_DIR / "mmr.log"
MY_MMR_LOG_PATH = LOG_DIR / "my_mmr.log"
API_DUMP_PATH = LOG_DIR / "api_dump.log"
HOTKEY_LOG_PATH = LOG_DIR / "hotkey.log"
CONSOLE_LOG_PATH = LOG_DIR / "console.log"


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def safe_atomic_write_text(path: Path, text: str, tag: str) -> bool:
    """Write `text` atomically; on OSError log to stderr with `tag` and return False.

    Persistent failure modes (e.g. OneDrive holding a file lock on the
    Documents folder) are common on Windows — callers want a one-liner
    that won't crash the app and produces a recognizable log line.
    """
    try:
        atomic_write_text(path, text)
        return True
    except OSError as e:
        print(f"[{tag}] could not write {path.name}: {e}", file=sys.stderr)
        return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """``datetime.fromisoformat`` with defensive handling for stored or wire data."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def load_jsonl(path: Path, tag: str) -> list[dict]:
    """Parse one JSON object per line, skipping malformed lines silently.

    Used by both the matches log and the MMR-history log — one bad line
    must never blank the whole file's worth of data.
    """
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        print(f"[{tag}] read failed: {e}", file=sys.stderr)
    return out
