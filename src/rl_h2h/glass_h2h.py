"""Shared card pieces: the MMR chip states, the stat grid, and the status card.

The head-to-head card itself lives in glass_hero.py. These are the parts more
than one screen paints, kept here so the session card and the match summary
cannot drift from the in-game one.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPen, QPixmap

from . import glass
from .mmr import tier_color
from .render_h2h import first_keyboard_label

# Vertical rhythm (logical px).
H_HEADER = 24
H_RULE_GAP_TOP = 13
H_RULE_GAP_BOT = 14
H_TEAM_HEADER = 14
H_TEAM_HEADER_GAP = 8
H_ROW = 46
# A row with neither an MMR chip nor a previous meeting has nothing to put on
# its second line. MMR is opt-in and off by default, so without this most
# users would see a card of rows each carrying a blank line.
H_ROW_SLIM = 32
H_ROW_GAP = 7
H_TEAM_GAP = 14
H_GRID_GAP_TOP = 16
H_GRID_GAP_BOT = 12
H_GRID_ROW = 20
H_FOOT_GAP = 14
H_FOOT_RULE_GAP = 11
H_FOOT = 15

NAME_MAX = 18
RIGHT_INSET = 3


# --- data shaping -----------------------------------------------------------
def mmr_bits(entry: Optional[dict], category: str) -> tuple:
    """(tier, tier_hex, mmr, playlist, division) or a one-item placeholder.

    Mirrors render_h2h._render_mmr_chip's states so the painted card and the
    HTML one disagree about nothing: no entry means still loading, a not_found
    marker or an unplayed playlist means there is genuinely nothing to show."""
    if entry is None:
        return ("…",)
    if entry.get("not_found"):
        return ("—",)
    if category == "best":
        pick = entry.get("best")
    elif category == "peak":
        pick = entry.get("peak_all_time")
    else:
        pick = (entry.get("playlists") or {}).get(category)
        if pick:
            pick = {**pick, "playlist": category}
    if not pick or pick.get("mmr") is None:
        return ("—",)
    return (pick.get("tier") or "Unranked", tier_color(pick.get("tier")),
            pick.get("mmr"), pick.get("playlist"), pick.get("division") or "")


def match_stat_cells(ms) -> list[tuple[str, str, Optional[str]]]:
    """(label, match value, your value) for every stat that actually fired.

    The design mocked a fixed six-cell grid; the app emits up to ten
    conditionally, so the grid has to flow. Same include-if-non-zero rule as
    render_summary, so the two views never disagree about what happened."""
    def num(v) -> str:
        return str(int(v))

    out: list[tuple[str, str, Optional[str]]] = []
    for label, team_v, self_v in (
        ("Saves", ms.saves, ms.saves_self),
        ("Shots", ms.shots, ms.shots_self),
        ("Demos", ms.demos, ms.demos_self),
        ("Crossbars", ms.crossbars, ms.crossbars_self),
    ):
        if team_v:
            out.append((label, num(team_v), num(self_v) if self_v else None))
    if getattr(ms, "demoed_self", 0):
        out.append(("Demoed", num(ms.demoed_self), None))
    for label, team_v, self_v in (
        ("Goal speed", ms.max_goal_speed, ms.max_goal_speed_self),
        ("Ball speed", ms.max_ball_speed, ms.max_ball_speed_self),
        ("Hardest bar", ms.max_impact_force, ms.max_impact_force_self),
    ):
        if team_v and team_v > 0:
            out.append((label, num(team_v), num(self_v) if self_v > 0 else None))
    if ms.fastest_goal_time:
        me = ms.fastest_goal_time_self
        out.append(("Fastest goal", f"{ms.fastest_goal_time:.1f}s",
                    f"{me:.1f}s" if me else None))
    if getattr(ms, "own_goals", 0):
        out.append(("Own goals", num(ms.own_goals),
                    num(ms.own_goals_self) if ms.own_goals_self else None))
    return out


def draw_stat_grid(painter: QPainter, family: str, x: int, y: int, w: int,
                    cells: list[tuple[str, str, Optional[str]]]) -> None:
    # RIGHT_INSET keeps the right column's value off the canvas edge; ending
    # exactly on it loses the final glyph column to antialiasing.
    w -= RIGHT_INSET
    col_w = (w - 18) // 2
    for i, (label, mine, theirs) in enumerate(cells):
        cx = x + (i % 2) * (col_w + 18)
        cy = y + (i // 2) * H_GRID_ROW + 12
        painter.setFont(glass.font(family, 8))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_LABEL)))
        painter.drawText(cx, cy, label)

        parts = [(mine, glass.qc(glass.TEXT), glass.mono(8, bold=True))]
        if theirs is not None:
            parts.append(("/", glass.qc(glass.TEXT, glass.A_GHOST), glass.mono(8)))
            parts.append((theirs, glass.qc(glass.TEXT, glass.A_SECONDARY),
                          glass.mono(8, bold=True)))
        total = 0
        for t, _c, f in parts:
            painter.setFont(f)
            total += painter.fontMetrics().horizontalAdvance(t)
        vx = cx + col_w - total
        for t, c, f in parts:
            painter.setFont(f)
            painter.setPen(QPen(c))
            painter.drawText(vx, cy, t)
            vx += painter.fontMetrics().horizontalAdvance(t)


def _draw_footer(painter: QPainter, family: str, x: int, y: int, w: int,
                 cfg: dict, note: str, expanded: bool) -> None:
    painter.setPen(QPen(glass.qc(glass.WHITE, glass.A_ROW_LINE)))
    painter.drawLine(x, y, x + w, y)
    baseline = y + H_FOOT_RULE_GAP + 8
    cx = x
    for keys_cfg, verb in ((cfg.get("expand_hotkeys"), "collapse" if expanded else "expand"),
                           (cfg.get("session_hotkeys"), "session"),
                           (cfg.get("cycle_hotkeys"), "cycle MMR")):
        label = first_keyboard_label(keys_cfg or [])
        if not label:
            continue
        cx = glass.key_chip(painter, cx, baseline, label) + 5
        painter.setFont(glass.font(family, 8))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
        painter.drawText(int(cx), baseline, verb)
        cx += painter.fontMetrics().horizontalAdvance(verb) + 14
    if note:
        painter.setFont(glass.font(family, 7))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        nw = painter.fontMetrics().horizontalAdvance(note)
        painter.drawText(x + w - nw - RIGHT_INSET, baseline, note)


def render_idle_pixmap(cfg: dict, title: str,
                       detail: Optional[list[tuple[str, str]]] = None,
                       offline: bool = False,
                       width: int = 344, dpr: float = 1.0) -> QPixmap:
    """Status card shown when there's no match to display.

    `detail` is a list of (kind, text) where kind is "text" or "code"; the code
    parts render as chips. That keeps the caller from embedding markup in a
    string the painter would have to parse back out."""
    family = cfg.get("font_family", "Segoe UI")
    detail = detail or []
    height = H_HEADER + H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT + 20 + (24 if detail else 0)
    pix = glass.new_canvas(width, height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    x, w, y = 0, width, 0

    painter.setFont(glass.font(family, 10, bold=True, letter_spacing=0.14))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    painter.drawText(x, y + 15, "HEAD TO HEAD")

    # Status pill, tinted by state: red when the feed is down, neutral when
    # we're simply between matches.
    tone = glass.LOSS if offline else glass.TEXT
    label = "OFFLINE" if offline else "STANDBY"
    f = glass.font(family, 6, bold=True, letter_spacing=0.14)
    painter.setFont(f)
    pill_w = painter.fontMetrics().horizontalAdvance(label) + 28
    px = x + w - pill_w - RIGHT_INSET
    painter.setPen(QPen(glass.qc(tone, 0.26 if offline else glass.A_CHIP_LINE)))
    painter.setBrush(QBrush(glass.qc(tone, 0.12 if offline else 0.07)))
    painter.drawRoundedRect(QRectF(px, y + 1, pill_w, 18), 9, 9)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(glass.qc(tone, 1.0 if offline else 0.5)))
    painter.drawEllipse(QRectF(px + 9, y + 7, 5, 5))
    painter.setPen(QPen(glass.qc(tone, 1.0 if offline else glass.A_LABEL)))
    painter.drawText(int(px + 19), y + 14, label)

    y += H_HEADER + H_RULE_GAP_TOP
    painter.setPen(QPen(glass.qc(glass.WHITE, glass.A_DIVIDER)))
    painter.drawLine(x, y, x + w, y)
    y += 1 + H_RULE_GAP_BOT

    # Elide against the measured width: the longest status string already runs
    # close to the edge, and a wider font face on another machine would clip it.
    painter.setFont(glass.font(family, 10))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    fm = painter.fontMetrics()
    shown = title
    while shown and fm.horizontalAdvance(shown) > w - RIGHT_INSET and len(shown) > 3:
        shown = shown[:-2] + "…"
    painter.drawText(x, y + 13, shown)

    if detail:
        y += 24
        dx = x
        for kind, text in detail:
            if kind == "code":
                dx = glass.chip(painter, dx, y + 12, text, glass.mono(7, bold=True),
                           glass.qc(glass.ACCENT),
                           fill=glass.qc(glass.ACCENT, 0.10),
                           border=glass.qc(glass.ACCENT, 0.20),
                           radius=glass.CHIP_RADIUS, pad_x=6) + 7
            else:
                painter.setFont(glass.font(family, 8))
                painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_LABEL)))
                painter.drawText(int(dx), y + 12, text)
                dx += painter.fontMetrics().horizontalAdvance(text) + 7
    painter.end()
    return pix


