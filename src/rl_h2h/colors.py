"""Palette knobs honored by the current UI + config override plumbing.

Two palettes coexist historically: this module's hex strings (tray icon,
tier-color fallbacks) and ``glass.py``'s RGB triples (every painted screen).
``apply_overrides`` patches BOTH so user config keeps working end to end —
glass consumers read its constants through module attributes at draw time,
so mutating them here takes effect on the next paint.

Renderers must access these via ``colors.C_TEXT`` (not ``from colors import
C_TEXT``) — binding the name at import time would freeze the default and
ignore overrides.
"""
from __future__ import annotations

from typing import Optional

# Hex baseline (Qt stylesheets / tray icon). Each can be overridden via cfg.
C_TEXT     = "#E0E3E5"  # on-surface
C_BLUE     = "#3B9EFF"  # fallback only — wire ColorPrimary preferred
C_ORANGE   = "#FF7A29"  # fallback only
C_WIN      = "#CCFF00"  # lime — wins, streaks, tray icon accent


def _rgb(hex_color: str) -> Optional[tuple[int, int, int]]:
    """'#RRGGBB' → (r, g, b), or None if it isn't parseable."""
    h = hex_color.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return None


def apply_overrides(cfg: dict) -> None:
    """Patch palettes from cfg. Called once at startup, before any rendering."""
    global C_TEXT, C_BLUE, C_ORANGE, C_WIN

    from . import glass

    if isinstance(cfg.get("text_color"), str):
        C_TEXT = cfg["text_color"]
        rgb = _rgb(C_TEXT)
        if rgb:
            glass.TEXT = rgb
    palette = cfg.get("colors") or {}
    for key, target in (
        ("win", "WIN"),
        ("loss", "LOSS"),
    ):
        val = palette.get(key)
        if isinstance(val, str):
            rgb = _rgb(val)
            if rgb:
                setattr(glass, target, rgb)
    if isinstance(palette.get("team_blue_fallback"), str):
        C_BLUE = palette["team_blue_fallback"]
    if isinstance(palette.get("team_orange_fallback"), str):
        C_ORANGE = palette["team_orange_fallback"]
    if isinstance(palette.get("win"), str):
        C_WIN = palette["win"]
