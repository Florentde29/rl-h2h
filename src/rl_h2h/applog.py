"""File-cap-aware logging used by the MMR client and the API dump."""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from .paths import (
    API_DUMP_PATH, CONSOLE_LOG_PATH, HOTKEY_LOG_PATH, MMR_LOG_PATH, now_iso,
)


# Cap log size at ~256 KB so it doesn't grow forever in long-running sessions.
# We rotate by truncating once we cross the cap — last write wins, no archive.
MMR_LOG_CAP = 256 * 1024
HOTKEY_LOG_CAP = 256 * 1024
API_DUMP_CAP_BYTES = 2 * 1024 * 1024  # 2 MB; truncate-rotate when exceeded

_mmr_log_lock = threading.Lock()
_hotkey_log_lock = threading.Lock()
_api_dump_lock = threading.Lock()

# Opt-in: hotkey/gamepad diagnostics are silent unless the user flips the
# `hotkey_debug_log` config flag. The default keeps the logs/ folder tidy
# for normal users — the file only matters when a friend reports "controller
# binds don't work" and we need to see what the listener thread is doing.
_hotkey_log_enabled = False


def _append_capped(path: Path, line: str, cap_bytes: int, lock: threading.Lock) -> None:
    """Append `line + \\n` under `lock`, truncate-rotating once `path` exceeds
    `cap_bytes`. Swallows OSError — a logging failure must never crash the
    caller. Reused by `mmr_log` and `api_dump`."""
    try:
        with lock:
            try:
                if path.stat().st_size > cap_bytes:
                    path.write_text("", encoding="utf-8")
            except FileNotFoundError:
                pass
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass


def mmr_log(msg: str) -> None:
    """Append a timestamped line to mmr.log AND mirror to stderr.

    Designed to work even under start.bat's `pythonw` launch (which discards
    stdout/stderr) — the log file is the sole source of truth for debugging."""
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    print(f"[mmr] {msg}", file=sys.stderr)
    _append_capped(MMR_LOG_PATH, line, MMR_LOG_CAP, _mmr_log_lock)


def set_hotkey_log_enabled(on: bool) -> None:
    """Toggle the hotkey diagnostic log. Called once at startup from the config
    flag. The setter is module-level (not per-instance) because the listeners
    are spawned across several HotkeyManager instances and we want a single
    switch — not one per manager."""
    global _hotkey_log_enabled
    _hotkey_log_enabled = bool(on)


def hotkey_log(msg: str) -> None:
    """Append a timestamped line to hotkey.log when the diagnostic flag is on.

    Silent no-op when the flag is off, so wiring `hotkey_log(...)` calls into
    hot paths is free for normal users. Stderr mirroring is kept for the rare
    case the user runs from a console — under pythonw both targets are no-ops
    anyway, but the file is the one we can ask them to share."""
    if not _hotkey_log_enabled:
        return
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    print(f"[hotkey] {msg}", file=sys.stderr)
    _append_capped(HOTKEY_LOG_PATH, line, HOTKEY_LOG_CAP, _hotkey_log_lock)


def api_dump(event: str, data: dict) -> None:
    """Append one envelope as a JSON line to api_dump.log."""
    try:
        line = json.dumps({"ts": now_iso(), "Event": event, "Data": data})
    except (TypeError, ValueError):
        return
    _append_capped(API_DUMP_PATH, line, API_DUMP_CAP_BYTES, _api_dump_lock)


CONSOLE_LOG_CAP = 1024 * 1024


def capture_console() -> bool:
    """Point stdout/stderr at logs/console.log when there is no console.

    start.bat launches with pythonw, where both streams are None and CPython's
    print() returns early instead of writing. Every diagnostic in the app —
    connection state, ignored match-end events, failed config saves — was
    therefore invisible to anyone not launching from a terminal. Returns True
    if it redirected."""
    if sys.stdout is not None and sys.stderr is not None:
        return False
    try:
        if (CONSOLE_LOG_PATH.exists()
                and CONSOLE_LOG_PATH.stat().st_size > CONSOLE_LOG_CAP):
            CONSOLE_LOG_PATH.unlink()
        stream = CONSOLE_LOG_PATH.open("a", buffering=1, encoding="utf-8",
                                       errors="replace")
    except OSError:
        return False
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return True
