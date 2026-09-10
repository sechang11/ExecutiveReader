"""System-wide keyboard shortcuts.

Uses RegisterHotKey, the documented Windows mechanism, rather than a low-level
keyboard hook. A hook would see every keystroke you type anywhere, which is
both unnecessary here and the kind of thing security software objects to.
RegisterHotKey only ever reports the specific combinations we ask for.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable

_user32 = ctypes.windll.user32

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN,
}

_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "plus": 0xBB, "minus": 0xBD, "equals": 0xBB, "comma": 0xBC,
    "period": 0xBE, "slash": 0xBF, "backslash": 0xDC,
    "semicolon": 0xBA, "quote": 0xDE, "backtick": 0xC0,
    "lbracket": 0xDB, "rbracket": 0xDD,
    # Media keys, so a headset or keyboard transport button can drive playback.
    "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "stopmedia": 0xB2,
}
for _i in range(1, 25):
    _KEYS["f" + str(_i)] = 0x6F + _i


class HotkeyError(RuntimeError):
    pass


def parse(spec: str) -> tuple[int, int]:
    """Turn "ctrl+alt+r" into the (modifiers, virtual-key) pair Windows wants."""
    mods = 0
    key = 0
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise HotkeyError("Empty hotkey")
    for part in parts:
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _KEYS:
            key = _KEYS[part]
        elif len(part) == 1:
            key = ord(part.upper())
        else:
            raise HotkeyError("Unknown key: " + part)
    if not key:
        raise HotkeyError("No key in hotkey: " + spec)
    return mods | MOD_NOREPEAT, key


class Hotkeys:
    """Registers shortcuts on a private thread with its own message loop."""

    def __init__(self) -> None:
        self._bindings: dict[str, tuple[str, Callable[[], None]]] = {}
        self._by_id: dict[int, Callable[[], None]] = {}
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._thread_id = 0
        self._failed: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def bind(self, name: str, spec: str, action: Callable[[], None]) -> None:
        """Queue a binding. Registration happens when start() runs."""
        with self._lock:
            self._bindings[name] = (spec, action)

    @property
    def failed(self) -> list[tuple[str, str]]:
        """Shortcuts another app already owns, as (name, spec) pairs."""
        return list(self._failed)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="earmark-hotkeys")
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop(self) -> None:
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread = None
        self._thread_id = 0

    def _run(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self._failed = []
        with self._lock:
            bindings = list(self._bindings.items())

        registered = []
        for index, (name, (spec, action)) in enumerate(bindings, start=1):
            try:
                mods, key = parse(spec)
            except HotkeyError:
                self._failed.append((name, spec))
                continue
            if _user32.RegisterHotKey(None, index, mods, key):
                self._by_id[index] = action
                registered.append(index)
            else:
                # Almost always means another application holds this combo.
                self._failed.append((name, spec))

        self._ready.set()

        msg = wintypes.MSG()
        while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                action = self._by_id.get(int(msg.wParam))
                if action is not None:
                    # Never let a callback kill the message loop.
                    try:
                        action()
                    except Exception:
                        pass

        for index in registered:
            _user32.UnregisterHotKey(None, index)
