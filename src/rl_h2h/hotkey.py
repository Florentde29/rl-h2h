"""Foreground-window detection (Windows) and the multi-trigger hotkey listener."""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Optional

from PySide6.QtCore import QObject, Signal
from pynput import keyboard

from .applog import hotkey_log


# Gamepad button name → (inputs.event_type, inputs.event_code, target_value).
# 'thresh' for analog triggers means "treat ≥ THRESHOLD as pressed".
GAMEPAD_BUTTONS = {
    "a":           ("Key", "BTN_SOUTH",  1),    # Xbox A / PS Cross
    "b":           ("Key", "BTN_EAST",   1),    # Xbox B / PS Circle
    "x":           ("Key", "BTN_WEST",   1),    # Xbox X / PS Square
    "y":           ("Key", "BTN_NORTH",  1),    # Xbox Y / PS Triangle
    "lb":          ("Key", "BTN_TL",     1),    # Xbox LB / PS L1
    "rb":          ("Key", "BTN_TR",     1),    # Xbox RB / PS R1
    "back":        ("Key", "BTN_START",  1),    # Xbox Back/View / PS Share  (XInput maps View→BTN_START)
    "start":       ("Key", "BTN_SELECT", 1),    # Xbox Start/Menu / PS Options (XInput maps Menu→BTN_SELECT)
    "lstick":      ("Key", "BTN_THUMBL", 1),    # left stick click
    "rstick":      ("Key", "BTN_THUMBR", 1),    # right stick click
    "dpad_up":     ("Absolute", "ABS_HAT0Y", -1),
    "dpad_down":   ("Absolute", "ABS_HAT0Y",  1),
    "dpad_left":   ("Absolute", "ABS_HAT0X", -1),
    "dpad_right":  ("Absolute", "ABS_HAT0X",  1),
    "lt":          ("Absolute", "ABS_Z",   "thresh"),  # Xbox LT / PS L2
    "rt":          ("Absolute", "ABS_RZ",  "thresh"),  # Xbox RT / PS R2
}
GAMEPAD_TRIGGER_THRESHOLD = 80  # 0..255


# Cached Win32 bindings for is_rl_focused and the menu LL hook — hoisted to
# avoid re-loading WinDLL each poll.
if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)
    ]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    # WH_KEYBOARD_LL hook plumbing for MenuHotkeyListener.
    _HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    )
    _user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD,
    ]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
    ]
    _user32.CallNextHookEx.restype = ctypes.c_long
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
    ]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
else:
    _user32 = _kernel32 = None  # type: ignore[assignment]
    _HOOKPROC = None  # type: ignore[assignment]


# Constants for the LL keyboard hook.
_WH_KEYBOARD_LL = 13
_HC_ACTION = 0
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105
_WM_QUIT = 0x0012


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


def is_rl_focused() -> bool:
    """True when Rocket League is the foreground window. Always True on non-Windows."""
    if _user32 is None or _kernel32 is None:
        return True
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h_proc = _kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h_proc:
            return False
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if _kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                return buf.value.lower().endswith("rocketleague.exe")
        finally:
            _kernel32.CloseHandle(h_proc)
    except Exception:
        return False
    return False


class HotkeyManager(QObject):
    """Multi-trigger keyboard + gamepad listener.

    Emits ``pressed`` when going from no-triggers-held to at-least-one-held, and
    ``released`` when the last held trigger is let go. Holding multiple bindings
    simultaneously is supported (overlay stays up until *all* are released).
    """

    pressed = Signal()
    released = Signal()

    def __init__(self, hotkey_names: list[str]):
        super().__init__()
        self._kb_targets: list[tuple] = []
        self._pad_targets: list[str] = []
        self._down: set[tuple] = set()
        self._kb_listener = keyboard.Listener(
            on_press=self._on_kb_press, on_release=self._on_kb_release,
        )
        self._pad_thread: Optional[threading.Thread] = None
        self._pad_stop = threading.Event()
        self._started = False
        self._apply_bindings(hotkey_names)
        if self._pad_targets:
            self._pad_thread = threading.Thread(
                target=self._pad_loop, daemon=True, name="GamepadListener",
            )

    def _apply_bindings(self, hotkey_names: list[str]) -> None:
        kb_targets: list[tuple] = []
        pad_targets: list[str] = []
        for raw in hotkey_names:
            name = raw.strip().lower()
            if not name:
                continue
            if name.startswith("pad_"):
                pad_name = name[4:]
                if pad_name in GAMEPAD_BUTTONS:
                    pad_targets.append(pad_name)
                else:
                    print(f"[hotkey] unknown gamepad key {raw!r}; "
                          f"valid: {sorted(GAMEPAD_BUTTONS)}", file=sys.stderr)
            else:
                try:
                    kb_targets.append(self._parse_kb(name))
                except ValueError as e:
                    print(f"[hotkey] {e}", file=sys.stderr)
        self._kb_targets = kb_targets
        self._pad_targets = pad_targets
        hotkey_log(f"_apply_bindings: kb={kb_targets} pad={pad_targets} "
                   f"(from {hotkey_names!r})")

    def set_bindings(self, hotkey_names: list[str]) -> None:
        """Replace the active bindings live without restarting the listener.

        Drops any held-trigger state (we'd otherwise leak a stuck `pressed`
        if the user rebinds the action mid-hold). Spawns a gamepad thread
        on demand if pad bindings appear after start()."""
        was_held = bool(self._down)
        self._apply_bindings(hotkey_names)
        if was_held:
            self._down.clear()
            self.released.emit()
        if self._started and self._pad_targets and (
            self._pad_thread is None or not self._pad_thread.is_alive()
        ):
            self._pad_stop = threading.Event()
            self._pad_thread = threading.Thread(
                target=self._pad_loop, daemon=True, name="GamepadListener",
            )
            self._pad_thread.start()

    @staticmethod
    def _parse_kb(name: str):
        if hasattr(keyboard.Key, name):
            return ("special", getattr(keyboard.Key, name))
        if len(name) == 1:
            return ("char", name)
        raise ValueError(
            f"Unknown keyboard key {name!r}. Use 'tab', 'f1', 'shift' (etc.), "
            "a single char like 'h', or prefix with 'pad_' for a gamepad button."
        )

    def _kb_match(self, key, target) -> bool:
        kind, value = target
        if kind == "special":
            return key == value
        return getattr(key, "char", None) == value

    def _on_kb_press(self, key):
        for t in self._kb_targets:
            if self._kb_match(key, t):
                hotkey_log(f"kb press match: {t}")
                self._add_down(("kb",) + t)
                return

    def _on_kb_release(self, key):
        for t in self._kb_targets:
            if self._kb_match(key, t):
                hotkey_log(f"kb release match: {t}")
                self._remove_down(("kb",) + t)
                return

    def _add_down(self, key_id: tuple):
        was_empty = not self._down
        self._down.add(key_id)
        if was_empty:
            hotkey_log(f"pressed emit (first held: {key_id})")
            self.pressed.emit()

    def _remove_down(self, key_id: tuple):
        self._down.discard(key_id)
        if not self._down:
            hotkey_log(f"released emit (last released: {key_id})")
            self.released.emit()

    def _pad_loop(self):
        try:
            import inputs as _inputs
        except ImportError as e:
            print("[hotkey] gamepad bindings configured but 'inputs' is not installed. "
                  "Run: pip install inputs", file=sys.stderr)
            hotkey_log(f"pad_loop ABORT: ImportError on 'inputs' ({e}). "
                       f"Run: pip install inputs")
            return
        hotkey_log(f"pad_loop start: 'inputs' loaded; watching {self._pad_targets!r}; "
                   f"devices.gamepads={len(getattr(_inputs.devices, 'gamepads', []))}")
        active: dict[str, bool] = {}
        warned_no_pad = False
        print(f"[hotkey] gamepad listener watching: {self._pad_targets}", file=sys.stderr)
        while not self._pad_stop.is_set():
            try:
                events = _inputs.get_gamepad()
            except _inputs.UnpluggedError:
                if not warned_no_pad:
                    print("[hotkey] no gamepad detected; will keep watching", file=sys.stderr)
                    hotkey_log("pad_loop: UnpluggedError (no gamepad detected yet)")
                    warned_no_pad = True
                self._pad_stop.wait(2.0)
                continue
            except Exception as e:
                print(f"[hotkey] gamepad read error: {type(e).__name__}: {e}", file=sys.stderr)
                hotkey_log(f"pad_loop: read error {type(e).__name__}: {e}")
                self._pad_stop.wait(2.0)
                continue
            if warned_no_pad:
                hotkey_log("pad_loop: gamepad now responding")
            warned_no_pad = False
            # Rebuild on each batch so set_bindings() applies live without a thread restart.
            wanted: dict[tuple, list[tuple]] = {}
            for pad_name in self._pad_targets:
                etype, ecode, target_val = GAMEPAD_BUTTONS[pad_name]
                wanted.setdefault((etype, ecode), []).append((pad_name, target_val))
            for ev in events:
                key = (ev.ev_type, ev.code)
                if key not in wanted:
                    continue
                for pad_name, target_val in wanted[key]:
                    if target_val == "thresh":
                        is_pressed = ev.state >= GAMEPAD_TRIGGER_THRESHOLD
                    elif ev.ev_type == "Absolute":
                        is_pressed = ev.state == target_val
                    else:
                        is_pressed = ev.state == 1
                    was = active.get(pad_name, False)
                    if is_pressed and not was:
                        hotkey_log(f"pad press: {pad_name} "
                                   f"(ev={ev.ev_type}/{ev.code} state={ev.state})")
                        active[pad_name] = True
                        self._add_down(("pad", pad_name))
                    elif not is_pressed and was:
                        hotkey_log(f"pad release: {pad_name} "
                                   f"(ev={ev.ev_type}/{ev.code} state={ev.state})")
                        active[pad_name] = False
                        self._remove_down(("pad", pad_name))

    def start(self):
        self._kb_listener.start()
        if self._pad_thread is not None:
            self._pad_thread.start()
        self._started = True
        hotkey_log(f"start: kb_listener up, pad_thread={'spawned' if self._pad_thread else 'none'}")

    def stop(self):
        self._kb_listener.stop()
        self._pad_stop.set()


# Windows virtual-key codes for menu navigation. Used by MenuHotkeyListener
# to identify keys inside the low-level keyboard hook (where we get a vk_code,
# not a pynput Key object).
_VK_BY_NAME: dict[str, int] = {
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22,
    "shift": 0x10, "ctrl": 0x11, "alt": 0x12, "caps_lock": 0x14,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},  # F1=0x70..F12=0x7B
}


def _name_to_vk(name: Optional[str]) -> Optional[int]:
    """Config-style binding name → Windows virtual-key code, or None for
    pad bindings / unknowns. Single-char keys map by ord(uppercase)."""
    if not name:
        return None
    n = name.strip().lower()
    if n.startswith("pad_"):
        return None
    if n in _VK_BY_NAME:
        return _VK_BY_NAME[n]
    if len(n) == 1:
        return ord(n.upper())
    return None


class MenuHotkeyListener(QObject):
    """Keyboard listener for the in-game settings menu.

    On Windows, installs its own ``WH_KEYBOARD_LL`` hook (via ctypes) so we
    can selectively suppress events at the OS level. We can't use pynput
    here: its ``win32_event_filter`` runs after the hook callback returns,
    so its return value can't actually stop event propagation.

    Suppression rules:
      - Menu key: suppressed on every press (and auto-repeat) except while
        a rebind is in progress, so it doesn't double-trigger anything in
        Rocket League.
      - ↑ / ↓ / Enter: suppressed only while the menu is visible and we're
        not in capture mode. Otherwise they pass through to RL as normal.

    On non-Windows we fall back to a non-suppressing pynput listener — the
    macOS dev path doesn't need suppression to be useful.
    """

    toggle = Signal()
    up = Signal()
    down = Signal()
    enter = Signal()

    def __init__(self, menu_key_cb, is_visible_cb, is_capturing_cb):
        super().__init__()
        self._menu_key_cb = menu_key_cb
        self._is_visible_cb = is_visible_cb
        self._is_capturing_cb = is_capturing_cb
        self._held: set[int] = set()
        self._hook = None
        self._thread = None
        self._thread_id = 0
        # Holding a reference to the WINFUNCTYPE'd callback is required —
        # ctypes won't keep it alive otherwise and Windows would call into
        # freed memory on the next keypress.
        self._proc = None
        if sys.platform != "win32":
            self._fallback = keyboard.Listener(on_press=self._on_press_fallback)
        else:
            self._fallback = None

    def start(self) -> None:
        if sys.platform == "win32":
            self._proc = _HOOKPROC(self._win_handler)
            self._thread = threading.Thread(
                target=self._run_hook, daemon=True, name="MenuLLHook",
            )
            self._thread.start()
        else:
            self._fallback.start()

    def stop(self) -> None:
        if sys.platform == "win32":
            if self._thread_id:
                _user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
        elif self._fallback is not None:
            self._fallback.stop()

    def _run_hook(self) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        self._hook = _user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            err = ctypes.get_last_error()
            print(f"[hotkey] menu LL hook install failed (error {err})",
                  file=sys.stderr)
            return
        try:
            msg = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _win_handler(self, n_code, w_param, l_param):
        if n_code == _HC_ACTION:
            try:
                kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                vk = int(kb.vkCode)
                msg = int(w_param)
                if msg in (_WM_KEYUP, _WM_SYSKEYUP):
                    self._held.discard(vk)
                elif msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                    if self._handle_keydown(vk):
                        return 1  # suppress
            except Exception as e:
                print(f"[hotkey] menu hook error: {type(e).__name__}: {e}",
                      file=sys.stderr)
        return _user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

    def _handle_keydown(self, vk: int) -> bool:
        """Decide whether to suppress this keydown. Emits Qt signals on the
        leading edge (auto-repeats are silently swallowed but not re-emitted,
        otherwise holding ↓ would scroll the menu at the OS key-repeat rate)."""
        capturing = self._is_capturing_cb()
        visible = self._is_visible_cb()
        menu_vk = _name_to_vk(self._menu_key_cb())
        is_repeat = vk in self._held
        self._held.add(vk)

        if menu_vk is not None and vk == menu_vk:
            # During capture, the menu key is being captured as the new
            # binding — let it through to capture_next_input's listener.
            if capturing:
                return False
            if not is_repeat:
                self.toggle.emit()
            return True

        if not visible or capturing:
            return False

        nav = (
            (_VK_BY_NAME["up"], self.up),
            (_VK_BY_NAME["down"], self.down),
            (_VK_BY_NAME["enter"], self.enter),
        )
        for nav_vk, sig in nav:
            if vk == nav_vk:
                if not is_repeat:
                    sig.emit()
                return True
        return False

    def _on_press_fallback(self, key) -> None:
        # Non-Windows: dispatch but don't suppress.
        name = _kb_event_name(key)
        if name is None:
            return
        capturing = self._is_capturing_cb()
        if name == (self._menu_key_cb() or "f5"):
            if capturing:
                return
            self.toggle.emit()
            return
        if not self._is_visible_cb() or capturing:
            return
        if name == "up":
            self.up.emit()
        elif name == "down":
            self.down.emit()
        elif name == "enter":
            self.enter.emit()


def _kb_event_name(key) -> Optional[str]:
    """pynput key → config-style binding name. None for unbindable events
    (modifier-only releases, dead keys)."""
    if hasattr(key, "name"):  # special keys: Tab, F1, Esc, …
        return key.name.lower()
    char = getattr(key, "char", None)
    if char and len(char) == 1:
        return char.lower()
    return None


def capture_next_input(on_done) -> None:
    """Listen for one keyboard or gamepad press, then call `on_done(name)`.

    `name` is a config-style binding string ('y', 'f1', 'tab', 'pad_lb', …)
    or None if the user pressed Esc to cancel. Listeners stop after firing
    once. Runs concurrently with any active HotkeyManager — pynput supports
    multiple listeners observing the same key stream."""
    done = threading.Event()
    pad_stop = threading.Event()

    def _emit(name: Optional[str]) -> None:
        if done.is_set():
            return
        done.set()
        pad_stop.set()
        on_done(name)

    def _on_press(key) -> bool:
        if key == keyboard.Key.esc:
            _emit(None)
            return False
        name = _kb_event_name(key)
        if name is None:
            return None
        _emit(name)
        return False  # stop the listener after the first valid press

    listener = keyboard.Listener(on_press=_on_press)
    listener.start()

    def _pad_loop():
        try:
            import inputs as _inputs
        except ImportError:
            return
        wanted: dict[tuple, list[tuple]] = {}
        for pad_name, (etype, ecode, target_val) in GAMEPAD_BUTTONS.items():
            wanted.setdefault((etype, ecode), []).append((pad_name, target_val))
        while not pad_stop.is_set():
            try:
                events = _inputs.get_gamepad()
            except _inputs.UnpluggedError:
                pad_stop.wait(1.0)
                continue
            except Exception:
                pad_stop.wait(1.0)
                continue
            for ev in events:
                key = (ev.ev_type, ev.code)
                if key not in wanted:
                    continue
                for pad_name, target_val in wanted[key]:
                    if target_val == "thresh":
                        if ev.state >= GAMEPAD_TRIGGER_THRESHOLD:
                            _emit(f"pad_{pad_name}")
                            return
                    elif ev.ev_type == "Absolute":
                        if ev.state == target_val:
                            _emit(f"pad_{pad_name}")
                            return
                    else:
                        if ev.state == 1:
                            _emit(f"pad_{pad_name}")
                            return
                if done.is_set():
                    return

    threading.Thread(target=_pad_loop, daemon=True, name="GamepadCapture").start()
