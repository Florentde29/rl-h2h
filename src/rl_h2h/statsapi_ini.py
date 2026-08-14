"""Locate and sanity-check Rocket League's Stats API .ini files.

The overlay is useless without the game's Stats API socket, and that socket is
off unless ``PacketSendRate`` is greater than zero. A Rocket League patch can
rewrite the config and reset that value — v2.72 did, in August 2026 — after
which the app connects to nothing and silently shows an empty overlay forever.
Checking the .ini at startup turns that into a message the user can act on.

Two files matter, and they are not interchangeable:

``<install>\\TAGame\\Config\\DefaultStatsAPI.ini``
    The template that ships with the game. Lives under Program Files, so
    editing it usually needs admin rights.
``<Documents>\\My Games\\Rocket League\\TAGame\\Config\\TAStatsAPI.ini``
    The per-user config the game actually reads at launch, regenerated from
    the template when the template's ``[IniVersion]`` is newer.
"""
from __future__ import annotations

import ctypes
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

RECOMMENDED_RATE = 2
_RATE_RE = re.compile(r"^\s*PacketSendRate\s*=\s*([0-9]*\.?[0-9]+)\s*$", re.M | re.I)


def _steam_libraries() -> list[Path]:
    roots: list[Path] = []
    try:
        import winreg
    except ImportError:
        return roots
    for hive, key, name in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
    ):
        try:
            with winreg.OpenKey(hive, key) as k:
                roots.append(Path(winreg.QueryValueEx(k, name)[0]))
        except OSError:
            continue
    libs = list(roots)
    for r in roots:
        try:
            vdf = (r / "steamapps" / "libraryfolders.vdf").read_text(
                encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(r'"path"\s*"([^"]+)"', vdf):
            libs.append(Path(m.group(1).replace("\\\\", "\\")))
    return libs


def _epic_installs() -> list[Path]:
    out: list[Path] = []
    for f in glob.glob(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item"):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        label = f"{d.get('DisplayName', '')}{d.get('MainGameAppName', '')}".lower()
        if "rocket" in label or d.get("MainGameAppName") == "Sugar":
            loc = d.get("InstallLocation")
            if loc:
                out.append(Path(loc))
    return out


def _documents_dir() -> Path:
    """Real Documents folder — OneDrive redirects it, so %USERPROFILE% lies."""
    try:
        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_PERSONAL = 5, SHGFP_TYPE_CURRENT = 0
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
            return Path(buf.value)
    except Exception:
        pass
    return Path(os.path.expandvars(r"%USERPROFILE%\Documents"))


def config_dirs() -> list[Path]:
    """Existing directories that can hold a Stats API .ini."""
    if sys.platform != "win32":
        return []
    dirs: list[Path] = []
    for lib in _steam_libraries():
        dirs.append(lib / "steamapps" / "common" / "rocketleague")
    dirs.extend(_epic_installs())
    dirs.append(Path(os.path.expandvars(
        r"%ProgramFiles(x86)%\Steam\steamapps\common\rocketleague")))
    out = [d / "TAGame" / "Config" for d in dirs]
    out.append(_documents_dir() / "My Games" / "Rocket League" / "TAGame" / "Config")
    seen: set[str] = set()
    uniq: list[Path] = []
    for d in out:
        key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if d.is_dir():
                uniq.append(d)
        except OSError:
            continue
    return uniq


def find_inis() -> list[Path]:
    """Every ``*StatsAPI*.ini`` we can find, install tree and user tree alike."""
    out: list[Path] = []
    for d in config_dirs():
        try:
            out.extend(sorted(
                f for f in d.glob("*.ini") if "statsapi" in f.name.lower()
            ))
        except OSError:
            continue
    return out


def packet_send_rate(path: Path) -> Optional[float]:
    """``PacketSendRate`` from `path`, or None if absent/unreadable."""
    try:
        m = _RATE_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def diagnose() -> Optional[str]:
    """A one-line description of why the Stats API won't emit, or None if fine.

    Returns None when everything looks right *and* when we can't find the game
    at all — an unlocatable install is not evidence of a misconfiguration, and
    warning about it would just be noise for anyone with a custom install."""
    inis = find_inis()
    if not inis:
        return None
    rates = {f: packet_send_rate(f) for f in inis}
    if any(r for r in rates.values() if r):  # non-zero, non-None
        return None
    zeroed = [f for f, r in rates.items() if r == 0]
    if zeroed:
        names = ", ".join(f.name for f in zeroed)
        return (f"Rocket League's Stats API is disabled: PacketSendRate=0 in {names}. "
                f"Set it to {RECOMMENDED_RATE} in ALL of: "
                + "; ".join(str(f) for f in zeroed)
                + " — with Rocket League fully closed, then relaunch the game. "
                  "A game patch resets this file.")
    return (f"Rocket League's Stats API has no PacketSendRate setting in "
            f"{', '.join(f.name for f in inis)}; the socket stays off. "
            f"Add PacketSendRate={RECOMMENDED_RATE} with the game closed.")


if __name__ == "__main__":
    # `python -m rl_h2h.statsapi_ini` — answers the question without needing the
    # GUI, a tray, or Windows notifications to be enabled.
    found = find_inis()
    if not found:
        print("No StatsAPI .ini found — could not locate a Rocket League install.")
        print("Point Notepad at <RL install>\\TAGame\\Config\\ yourself, or run")
        print("tools/diag_stats_api.py with Rocket League running.")
        raise SystemExit(2)
    for f in found:
        print(f"{f}\n    PacketSendRate = {packet_send_rate(f)}")
    print()
    problem = diagnose()
    print(problem or "Stats API config looks OK.")
    raise SystemExit(1 if problem else 0)
