"""Startup diagnostics: environment capture, console capture, RL process probe.

This module exists to answer one question from a log file alone: *why did the
overlay not appear?* Under ``start.bat`` the app runs on ``pythonw.exe``, which
gives it no console — ``sys.stdout``/``sys.stderr`` are ``None``, so every
``print()`` in the app is silently discarded (CPython's ``print`` returns early
rather than raising when the target is ``None``). That means the existing
``[stats]`` / ``[ready]`` / ``[hotkey]`` diagnostics have never been visible to
a normal user. :func:`capture_console` fixes that by pointing both streams at a
file before anything else runs.
"""
from __future__ import annotations

import ctypes
import platform
import sys
from ctypes import wintypes
from datetime import datetime

from .paths import CONSOLE_LOG_PATH, STARTUP_LOG_PATH

# Keep the captured console from growing without bound across long sessions.
# Unlike applog's truncate-rotate we roll at startup only: truncating a stream
# that's currently open for append would confuse the file position.
CONSOLE_LOG_CAP = 1024 * 1024


def capture_console() -> bool:
    """Point ``sys.stdout``/``sys.stderr`` at ``logs/console.log`` when the app
    has no console (i.e. launched via ``pythonw``). Returns True if redirected.

    No-op when a real console exists, so running ``python -m rl_h2h`` from a
    terminal still behaves exactly as before."""
    if sys.stdout is not None and sys.stderr is not None:
        return False
    try:
        if CONSOLE_LOG_PATH.exists() and CONSOLE_LOG_PATH.stat().st_size > CONSOLE_LOG_CAP:
            CONSOLE_LOG_PATH.unlink()
        stream = CONSOLE_LOG_PATH.open("a", buffering=1, encoding="utf-8", errors="replace")
    except OSError:
        return False
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    return True


def _dep_version(dist: str) -> str:
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception as e:
        return f"MISSING ({type(e).__name__})"


TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def find_rl_processes() -> list[str]:
    """Names of running processes that look like Rocket League.

    Deliberately uses the ToolHelp snapshot rather than ``OpenProcess``: if an
    anti-cheat is protecting the game process, ``OpenProcess`` fails but the
    snapshot still lists it. That difference is exactly what separates "RL isn't
    running / was renamed" from "RL is running but we can't query it" — the two
    ways :func:`rl_h2h.hotkey.is_rl_focused` can return False."""
    if sys.platform != "win32":
        return []
    found: list[str] = []
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _INVALID_HANDLE_VALUE:
            return []
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                name = entry.szExeFile
                if "rocket" in name.lower():
                    found.append(f"{name} (pid {entry.th32ProcessID})")
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
    except Exception as e:
        return [f"<probe failed: {type(e).__name__}: {e}>"]
    return found


def log_startup(cfg: dict, console_captured: bool) -> None:
    """Write one environment block to ``logs/startup.log``.

    Always on (not behind ``hotkey_debug_log``): it's a handful of lines once
    per launch, and it's the first thing worth asking a user to paste."""
    lines = [
        "=" * 68,
        f"startup {datetime.now().isoformat(timespec='seconds')}",
        f"  python      {sys.version.split()[0]} ({sys.executable})",
        f"  platform    {platform.platform()}",
        f"  console     {'captured to console.log' if console_captured else 'real console / already attached'}",
        f"  PySide6     {_dep_version('PySide6')}",
        f"  pynput      {_dep_version('pynput')}",
        f"  inputs      {_dep_version('inputs')}",
        f"  curl_cffi   {_dep_version('curl_cffi')}",
        f"  stats api   tcp://{cfg.get('host')}:{cfg.get('port')}",
        f"  focus gate  require_rl_focus={cfg.get('require_rl_focus', True)}",
        f"  hotkeys     h2h={cfg.get('hotkeys')} session={cfg.get('session_hotkeys')} "
        f"menu={cfg.get('menu_hotkey')}",
    ]
    rl = find_rl_processes()
    lines.append(f"  rl process  {', '.join(rl) if rl else 'NONE FOUND (is Rocket League running?)'}")
    _write(lines)


def log(msg: str) -> None:
    """Append one timestamped line to ``logs/startup.log``."""
    _write([f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"])


def _write(lines: list[str]) -> None:
    try:
        with STARTUP_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass
