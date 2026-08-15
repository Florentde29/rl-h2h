"""Shared HTML rendering: idle card, settings menu, and the session footer.

The head-to-head card itself moved to glass_h2h.py, which paints instead of
emitting RichText. What remains is the HTML that still backs the screens not
yet redesigned, plus the small helpers the painted renderers reuse.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from . import colors
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


def idle_html(message: str) -> str:
    is_disconnected = "disconnect" in message.lower() or "stats api" in message.lower()
    dot_color = "#E5484D" if is_disconnected else colors.C_BLUE
    label = "OFFLINE" if is_disconnected else "STANDBY"
    return (
        "<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;'>"
        "<tr>"
        f"<td align='left' style='color:{colors.C_TEXT};font-size:10pt;font-weight:700;"
        "letter-spacing:0.18em;'>HEAD&middot;TO&middot;HEAD</td>"
        "<td align='right' style='font-size:8pt;font-weight:700;letter-spacing:0.16em;'>"
        f"<span style='color:{dot_color};'>&#9679;</span>"
        f"&nbsp;<span style='color:#7A8290;'>{label}</span>"
        "</td>"
        "</tr>"
        "</table>"
        "<div style='height:10px;font-size:1px;line-height:1px;'>&nbsp;</div>"
        f"<div style='color:{colors.C_DIM};font-size:10pt;letter-spacing:0.01em;line-height:140%;'>"
        f"{message}</div>"
    )


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


def _pretty_binding(name: Optional[str]) -> str:
    """Config name ('y', 'f1', 'tab', 'pad_lb') → friendly menu label."""
    if not name:
        return "&mdash;"
    if name.startswith("pad_"):
        return name[4:].upper().replace("_", " ")
    return name.upper()


def render_menu_html(rows: list[dict], selected_index: int, capturing: bool,
                     menu_key: str = "f5") -> str:
    """In-game settings menu. Hotkey-driven (overlay is input-transparent),
    so this renders a static panel — selection cursor + values only.

    Row dicts:
      {"type": "header",  "label": "Toggles"}
      {"type": "spacer"}
      {"type": "toggle",  "label": "...", "value": bool}
      {"type": "binding", "label": "...", "kb": str|None, "pad": str|None}
    """
    body_parts: list[str] = []
    for i, row in enumerate(rows):
        rtype = row["type"]
        is_sel = (i == selected_index)
        cursor = (
            f"<span style='color:{colors.C_TEXT};'>&gt;</span>"
            if is_sel else "&nbsp;"
        )
        if rtype == "header":
            body_parts.append(
                f"<tr><td colspan='4' style='color:{colors.C_DIM};font-size:9pt;"
                f"font-weight:700;letter-spacing:0.18em;padding:8px 0 2px 0;'>"
                f"{row['label']}</td></tr>"
            )
        elif rtype == "spacer":
            body_parts.append(
                "<tr><td colspan='4' style='font-size:1px;line-height:1px;'>"
                "<div style='height:6px;'>&nbsp;</div></td></tr>"
            )
        elif rtype == "toggle":
            color = colors.C_TEXT if is_sel else colors.C_DIM
            val = "[ ON ]" if row["value"] else "[ OFF ]"
            body_parts.append(
                "<tr>"
                f"<td width='14' style='font-size:10pt;padding:2px 0;'>{cursor}</td>"
                f"<td colspan='2' style='color:{color};font-size:10pt;padding:2px 0;'>"
                f"{row['label']}</td>"
                f"<td align='right' style='color:{color};font-family:Consolas,\"SF Mono\",monospace;"
                f"font-size:9pt;font-weight:700;padding:2px 0;'>{val}</td>"
                "</tr>"
            )
        elif rtype == "binding":
            color = colors.C_TEXT if is_sel else colors.C_DIM
            kb_label = _pretty_binding(row.get("kb"))
            pad_label = _pretty_binding(row.get("pad"))
            if capturing and is_sel:
                kb_label = "press…"
                pad_label = "press…"
            body_parts.append(
                "<tr>"
                f"<td width='14' style='font-size:10pt;padding:2px 0;'>{cursor}</td>"
                f"<td style='color:{color};font-size:10pt;padding:2px 0;'>{row['label']}</td>"
                f"<td align='right' style='color:{color};font-family:Consolas,\"SF Mono\",monospace;"
                f"font-size:9pt;padding:2px 14px 2px 0;'>{kb_label}</td>"
                f"<td align='right' style='color:{color};font-family:Consolas,\"SF Mono\",monospace;"
                f"font-size:9pt;padding:2px 0;'>{pad_label}</td>"
                "</tr>"
            )

    close_label = _pretty_binding(menu_key)
    footer_text = (
        "Press a key or gamepad button&hellip; (Esc to cancel)"
        if capturing else
        f"&uarr;&darr; move &middot; Enter select &middot; {close_label} close"
    )

    return (
        "<table width='100%' cellspacing='0' cellpadding='0' "
        "style='border-collapse:collapse;'>"
        "<tr>"
        f"<td align='left' style='color:{colors.C_TEXT};font-size:10pt;font-weight:700;"
        "letter-spacing:0.18em;'>SETTINGS</td>"
        "</tr>"
        "</table>"
        f"<div style='height:1px;background-color:{colors.C_FAINT};font-size:1px;line-height:1px;"
        "margin-top:8px;'>&nbsp;</div>"
        "<div style='height:6px;font-size:1px;line-height:1px;'>&nbsp;</div>"
        "<table width='100%' cellspacing='0' cellpadding='0' "
        "style='border-collapse:collapse;'>"
        + "".join(body_parts)
        + "</table>"
        "<div style='height:8px;font-size:1px;line-height:1px;'>&nbsp;</div>"
        f"<div style='height:1px;background-color:{colors.C_FAINT};font-size:1px;line-height:1px;'>"
        "&nbsp;</div>"
        "<div style='height:6px;font-size:1px;line-height:1px;'>&nbsp;</div>"
        f"<div style='color:{colors.C_MUTED};font-size:8pt;letter-spacing:0.02em;'>"
        f"{footer_text}</div>"
    )


def session_footer_html(cfg: dict, view: str) -> str:
    """Hotkey hint row for the session card. View-specific copy: the session
    view advertises the expand key to swap to graph; the graph view doesn't
    reach here because it's painted onto a pixmap, not HTML."""
    expand_label = first_keyboard_label(cfg.get("expand_hotkeys") or [])
    if view != "session" or not expand_label:
        return ""
    return (
        "<table cellpadding='0' cellspacing='0' width='100%'>"
        "<tr><td height='10'>&nbsp;</td></tr></table>"
        "<table width='100%' cellspacing='0' cellpadding='0' "
        "style='border-collapse:collapse;'>"
        "<tr>"
        f"<td align='left' style='color:{colors.C_MUTED};font-size:8pt;"
        f"letter-spacing:0.02em;padding:1px 0;'><b>{expand_label}</b> graph</td>"
        f"<td align='right' style='color:{colors.C_MUTED};font-size:8pt;"
        f"letter-spacing:0.02em;padding:1px 0;'>session</td>"
        "</tr>"
        "</table>"
    )


