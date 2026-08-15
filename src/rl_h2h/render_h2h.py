"""Shared HTML rendering: idle card, settings menu, and the session footer.

The head-to-head card itself moved to glass_h2h.py, which paints instead of
emitting RichText. What remains is the HTML that still backs the screens not
yet redesigned, plus the small helpers the painted renderers reuse.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .paths import parse_iso


def first_keyboard_label(keys: list) -> Optional[str]:
    """Pick the friendliest key label from a hotkeys list — prefer keyboard names
    over gamepad bindings (more recognizable in a footer hint)."""
    if not keys:
        return None
    for k in keys:
        if not k.startswith("pad_"):
            return k.upper()
    # All gamepad — render the first as-is.
    return keys[0]


def humanize_when(iso_ts: Optional[str]) -> str:
    t = parse_iso(iso_ts)
    if t is None:
        return ""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - t).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


