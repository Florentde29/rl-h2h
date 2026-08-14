"""Standalone probe for 'the rl-h2h overlay never appears'.

Run this with Rocket League RUNNING (in a match if you can). It answers, in
order, the questions that actually narrow the problem down:

  1. Is Rocket League running, and under what executable name?
  2. Is *anything* listening on the Stats API port?
  3. Is Rocket League itself listening on ANY port? (finds a changed port)
  4. Which StatsAPI .ini files exist, where, and what's in them?
  5. If a socket answers: does it speak raw NDJSON, or does it now want a
     WebSocket handshake?

Deliberately standalone — no imports from rl_h2h, no third-party packages —
so it can be copied anywhere and run against any Python 3.8+.

    python tools\\diag_stats_api.py

Writes a transcript to logs/diag-stats-api.txt next to the repo.
"""
from __future__ import annotations

import ctypes
import glob
import json
import os
import platform
import re
import socket
import subprocess
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

DEFAULT_PORT = 49123
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 6.0

IS_WIN = sys.platform == "win32"
_lines: list[str] = []


def say(text: str = "") -> None:
    _lines.append(text)
    try:
        print(text)
    except Exception:
        pass


def section(title: str) -> None:
    say("")
    say("=" * 70)
    say(title)
    say("=" * 70)


def ok(t: str) -> None:
    say(f"  [OK]    {t}")


def bad(t: str) -> None:
    say(f"  [FAIL]  {t}")


def warn(t: str) -> None:
    say(f"  [WARN]  {t}")


def info(t: str) -> None:
    say(f"          {t}")


# ---------------------------------------------------------------- processes

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
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def list_processes() -> list[tuple[int, str]]:
    """(pid, exe name) for every running process.

    Uses the ToolHelp snapshot rather than OpenProcess, so it still enumerates
    processes that an anti-cheat protects from being queried."""
    if not IS_WIN:
        return []
    out: list[tuple[int, str]] = []
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
            e = _PROCESSENTRY32W()
            e.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            more = k32.Process32FirstW(snap, ctypes.byref(e))
            while more:
                out.append((int(e.th32ProcessID), str(e.szExeFile)))
                more = k32.Process32NextW(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
    except Exception as e:
        warn(f"process enumeration failed: {type(e).__name__}: {e}")
    return out


# ------------------------------------------------------------------- ports

def listening_ports() -> list[dict]:
    """Listening TCP sockets as [{LocalAddress, LocalPort, OwningProcess}].

    Shells out to Get-NetTCPConnection and parses JSON rather than reading
    `netstat` text: netstat's state column is translated on localised Windows
    (this machine is French), and a regex over it would silently match nothing.
    JSON from PowerShell is locale-independent."""
    if not IS_WIN:
        return []
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "Get-NetTCPConnection -State Listen | "
        "Select-Object LocalAddress,LocalPort,OwningProcess | "
        "ConvertTo-Json -Compress",
    ]
    try:
        raw = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        if not raw:
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    except Exception as e:
        warn(f"could not list listening ports: {type(e).__name__}: {e}")
        return []


# ----------------------------------------------------------------- ini files

def steam_libraries() -> list[Path]:
    roots: list[Path] = []
    if not IS_WIN:
        return roots
    try:
        import winreg
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
    except Exception:
        return roots
    libs = list(roots)
    for r in roots:
        vdf = r / "steamapps" / "libraryfolders.vdf"
        try:
            for m in re.finditer(r'"path"\s*"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
                libs.append(Path(m.group(1).replace("\\\\", "\\")))
        except OSError:
            continue
    return libs


def epic_installs() -> list[Path]:
    out: list[Path] = []
    if not IS_WIN:
        return out
    pattern = r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item"
    for f in glob.glob(pattern):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        name = str(d.get("DisplayName", "")) + str(d.get("MainGameAppName", ""))
        if "rocket" in name.lower() or d.get("MainGameAppName") == "Sugar":
            loc = d.get("InstallLocation")
            if loc:
                out.append(Path(loc))
    return out


def rl_install_dirs() -> list[Path]:
    cands: list[Path] = []
    for lib in steam_libraries():
        cands.append(lib / "steamapps" / "common" / "rocketleague")
    cands.extend(epic_installs())
    if IS_WIN:
        cands.append(Path(os.path.expandvars(r"%ProgramFiles(x86)%\Steam\steamapps\common\rocketleague")))
    seen, out = set(), []
    for c in cands:
        key = str(c).lower()
        if key not in seen and c.is_dir():
            seen.add(key)
            out.append(c)
    return out


def config_dirs() -> list[Path]:
    """Every directory that could hold a StatsAPI ini."""
    out = [d / "TAGame" / "Config" for d in rl_install_dirs()]
    if IS_WIN:
        # RL also keeps a per-user config tree; OneDrive may redirect Documents,
        # so resolve the real Documents folder rather than assuming %USERPROFILE%.
        docs = None
        try:
            buf = ctypes.create_unicode_buffer(260)
            # CSIDL_PERSONAL = 5, SHGFP_TYPE_CURRENT = 0
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:
                docs = Path(buf.value)
        except Exception:
            docs = None
        if docs is None:
            docs = Path(os.path.expandvars(r"%USERPROFILE%\Documents"))
        out.append(docs / "My Games" / "Rocket League" / "TAGame" / "Config")
    return [d for d in out if d.is_dir()]


# -------------------------------------------------------------- socket probe

def probe_port(port: int, host: str = "127.0.0.1") -> str:
    """Returns one of: refused, timeout, error, no-data, ndjson, websocket."""
    say("")
    say(f"  --- probing {host}:{port} ---")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(CONNECT_TIMEOUT)
    try:
        s.connect((host, port))
    except ConnectionRefusedError:
        s.close()
        bad(f"connection refused - nothing is listening on {port}")
        return "refused"
    except socket.timeout:
        s.close()
        bad("connect timed out (filtered by a firewall?)")
        return "timeout"
    except OSError as e:
        s.close()
        bad(f"connect failed: {type(e).__name__}: {e}")
        return "error"

    ok(f"TCP connect succeeded on {port}")
    try:
        s.settimeout(READ_TIMEOUT)
        try:
            data = s.recv(4096)
        except socket.timeout:
            data = b""
        if data:
            ok(f"received {len(data)} bytes without sending anything (raw NDJSON):")
            info(data[:400].decode("utf-8", errors="replace"))
            return "ndjson"
        warn(f"connected, but no data within {READ_TIMEOUT:.0f}s - trying a WebSocket handshake")
    finally:
        s.close()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(CONNECT_TIMEOUT)
    try:
        s.connect((host, port))
        s.settimeout(READ_TIMEOUT)
        s.sendall(
            b"GET / HTTP/1.1\r\n"
            b"Host: " + f"{host}:{port}".encode() + b"\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        try:
            resp = s.recv(4096)
        except socket.timeout:
            resp = b""
    except OSError as e:
        bad(f"handshake attempt failed: {type(e).__name__}: {e}")
        return "no-data"
    finally:
        s.close()

    if not resp:
        bad("no response to the WebSocket handshake either - socket is open but silent")
        return "no-data"
    head = resp[:400].decode("utf-8", errors="replace")
    if b"101" in resp.split(b"\r\n", 1)[0]:
        ok("server completed a WebSocket handshake - it now REQUIRES WebSocket, not raw TCP")
    else:
        warn("server replied to the handshake with:")
    info(head)
    return "websocket"


# ------------------------------------------------------------------- report

def main() -> int:
    section("rl-h2h Stats API probe")
    say(f"  when      {datetime.now().isoformat(timespec='seconds')}")
    say(f"  python    {sys.version.split()[0]} ({sys.executable})")
    say(f"  platform  {platform.platform()}")
    if not IS_WIN:
        warn("not running on Windows - process, port and registry checks are skipped")

    section("1. Is Rocket League running?")
    procs = list_processes()
    rl = [(pid, name) for pid, name in procs if "rocket" in name.lower()]
    if rl:
        for pid, name in rl:
            ok(f"{name} (pid {pid})")
    elif IS_WIN:
        bad("no Rocket-League-looking process found")
        info("Start Rocket League and run this again - the Stats API socket only")
        info("exists while the game is running.")
    rl_pids = {pid for pid, _ in rl}
    eac = [(pid, n) for pid, n in procs if "anticheat" in n.lower().replace("_", "") or "eac" in n.lower()]
    for pid, name in eac:
        info(f"anti-cheat process present: {name} (pid {pid})")

    section("2. What is listening, and is any of it Rocket League?")
    listeners = listening_ports()
    by_pid = {pid: name for pid, name in procs}
    target = [l for l in listeners if int(l.get("LocalPort", 0)) == DEFAULT_PORT]
    if target:
        for l in target:
            owner = by_pid.get(int(l.get("OwningProcess", 0)), "?")
            ok(f"port {DEFAULT_PORT} IS open, owned by {owner} (pid {l.get('OwningProcess')})")
    elif IS_WIN:
        bad(f"nothing is listening on {DEFAULT_PORT}")

    rl_listeners = [l for l in listeners if int(l.get("OwningProcess", 0)) in rl_pids]
    if rl_listeners:
        say("")
        ok("Rocket League is listening on:")
        for l in rl_listeners:
            info(f"{l.get('LocalAddress')}:{l.get('LocalPort')}")
        if not target:
            warn(f"...but not on {DEFAULT_PORT} - the Stats API port may have changed.")
    elif rl_pids:
        say("")
        bad("Rocket League has NO listening TCP socket at all")
        info("=> the Stats API is disabled in its .ini, or the .ini was not read.")

    section("3. StatsAPI configuration files")
    dirs = config_dirs()
    if not dirs:
        warn("could not locate a Rocket League config directory")
        for d in ("Steam registry", "Epic manifests"):
            info(f"tried: {d}")
    for d in dirs:
        say("")
        say(f"  {d}")
        try:
            inis = sorted(d.glob("*.ini"))
        except OSError as e:
            bad(f"cannot list: {e}")
            continue
        if not inis:
            warn("no .ini files here")
        for f in inis:
            marker = " <-- stats api" if "statsapi" in f.name.lower() else ""
            info(f"{f.name}{marker}")
        for f in inis:
            if "statsapi" not in f.name.lower():
                continue
            say("")
            say(f"  ----- {f} -----")
            try:
                body = f.read_text(encoding="utf-8", errors="replace").strip()
            except OSError as e:
                bad(f"unreadable: {e}")
                continue
            for line in (body.splitlines() or ["(empty file)"]):
                info(line)
            if re.search(r"^\s*PacketSendRate\s*=\s*0*\.?0*\s*$", body, re.M | re.I):
                bad("PacketSendRate is 0 - the Stats API socket is DISABLED.")
            elif not re.search(r"PacketSendRate", body, re.I):
                bad("no PacketSendRate line at all - socket stays disabled.")

    section("4. Socket probe")
    results = {}
    ports = [DEFAULT_PORT] + [
        int(l["LocalPort"]) for l in rl_listeners
        if int(l.get("LocalPort", 0)) != DEFAULT_PORT
    ]
    for p in ports:
        results[p] = probe_port(p)

    section("Verdict")
    verdict = results.get(DEFAULT_PORT)
    # A working socket anywhere outranks a dead one on the expected port: if RL
    # moved, the fix is a config change, not a protocol change.
    speaking = [p for p, r in results.items() if r in ("ndjson", "websocket")]
    if IS_WIN and not rl_pids:
        say("  Rocket League was not running. Start it, get into a match, re-run.")
    elif speaking and DEFAULT_PORT not in speaking:
        p = speaking[0]
        say(f"  The Stats API moved: it is answering on port {p}, not {DEFAULT_PORT}.")
        say(f'  Fix: set "port": {p} in data/config.json, then restart rl-h2h.')
        if results[p] == "websocket":
            say("  It also wants a WebSocket handshake - send me this output.")
    elif verdict == "ndjson":
        say("  The Stats API is alive and speaking raw NDJSON. The overlay problem")
        say("  is NOT the stats feed - most likely the require_rl_focus gate.")
        say('  Next: set "require_rl_focus": false in data/config.json, restart.')
    elif verdict == "websocket":
        say("  The Stats API now requires a WebSocket handshake. rl-h2h connects")
        say("  with raw TCP, which is why nothing arrives. Send me this output and")
        say("  I'll implement the handshake.")
    elif verdict == "refused":
        say("  Nothing is listening on the Stats API port. This is a Rocket League")
        say("  side problem, not an rl-h2h bug. Check section 3 above: either the")
        say("  .ini is missing/reset (PacketSendRate=0), it is in a folder RL no")
        say("  longer reads, or RL must be fully restarted after editing it.")
        if rl_listeners:
            say("  NOTE: RL is listening on another port - see section 2.")
    else:
        say(f"  Inconclusive (probe result: {verdict}). Send me this whole output.")

    out_path = Path(__file__).resolve().parents[1] / "logs" / "diag-stats-api.txt"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(_lines) + "\n", encoding="utf-8")
        say("")
        say(f"Saved to {out_path}")
        say("Send me that file (or paste this whole output).")
    except OSError as e:
        say(f"\n(could not save transcript: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
