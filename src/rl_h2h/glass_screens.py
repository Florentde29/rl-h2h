"""The remaining painted screens: session card, post-match summary, settings menu.

These three had no mockup — the design bundle covered only H2H, idle and the
graph — so they extend that vocabulary rather than invent one: same card
chrome, same header shape, the same flowing two-column stat grid, the same key
chips. Where the old HTML stacked one label/value row per stat, these use the
grid, because 14 single-column rows made the session card taller than the
screens it sits beside.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPen, QPixmap

from . import glass
from .constants import SF_DEMOLISH, SF_SAVE, SF_SHOT
from .glass_h2h import (
    H_GRID_ROW, H_HEADER, H_RULE_GAP_BOT, H_RULE_GAP_TOP, RIGHT_INSET,
    draw_stat_grid, match_stat_cells,
)
from .render_h2h import first_keyboard_label
from .session_stats import session_has_split

H_FOOT_GAP = 14
H_FOOT_RULE_GAP = 11
H_FOOT = 15
H_HERO = 46
H_PIPS = 18
H_SECTION = 22
H_MENU_ROW = 26


def _draw_header(painter: QPainter, family: str, x: int, w: int,
                 title: str, right: str = "", right_accent=None) -> None:
    painter.setFont(glass.font(family, 10, bold=True, letter_spacing=0.14))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    painter.drawText(x, 15, title)
    if right:
        painter.setFont(glass.font(family, 8, letter_spacing=0.06))
        painter.setPen(QPen(glass.qc(right_accent or glass.TEXT,
                                     1.0 if right_accent else glass.A_MUTED)))
        rw = painter.fontMetrics().horizontalAdvance(right)
        painter.drawText(x + w - rw - RIGHT_INSET, 15, right)


def _draw_footer(painter: QPainter, family: str, x: int, y: int, w: int,
                 hints: list[tuple[Optional[str], str]], note: str = "") -> None:
    glass.rule(painter, x, y, w, glass.A_ROW_LINE)
    baseline = y + H_FOOT_RULE_GAP + 8
    cx = x
    for label, verb in hints:
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


# --- session ----------------------------------------------------------------
def _session_cells(s) -> list[tuple[str, str, Optional[str]]]:
    """Everything below the hero, as (label, scope value, your value)."""
    def pair(t, me) -> tuple[str, Optional[str]]:
        return (str(int(t)), str(int(me)) if me else None)

    out: list[tuple[str, str, Optional[str]]] = []
    if s.matches:
        diff = s.goals_for - s.goals_against
        out.append(("Goals", f"{s.goals_for}–{s.goals_against}",
                    f"{diff:+d}" if diff else None))
    if s.win_streak >= 2:
        out.append(("Streak", f"W{s.win_streak}", None))
    elif s.loss_streak >= 2:
        out.append(("Streak", f"L{s.loss_streak}", None))
    if s.best_win_streak:
        out.append(("Best run", f"W{s.best_win_streak}", None))
    for label, key in (("Saves", SF_SAVE), ("Shots", SF_SHOT), ("Demos", SF_DEMOLISH)):
        total = s.statfeed_counts.get(key, 0)
        if total:
            out.append((label, *pair(total, s.statfeed_counts_self.get(key, 0))))
    if s.crossbars:
        out.append(("Crossbars", str(int(s.crossbars)), None))
    for label, t, me in (("Goal speed", s.max_goal_speed, s.max_goal_speed_self),
                         ("Ball speed", s.max_ball_speed, s.max_ball_speed_self),
                         ("Hardest bar", s.max_impact_force, s.max_impact_force_self)):
        if t and t > 0:
            out.append((label, *pair(t, me)))
    if s.fastest_goal_time:
        me = s.fastest_goal_time_self
        out.append(("Fastest goal", f"{s.fastest_goal_time:.1f}s",
                    f"{me:.1f}s" if me else None))
    if s.own_goals:
        out.append(("Own goals", *pair(s.own_goals, s.own_goals_self)))
    return out


def render_session_pixmap(s, cfg: dict, width: int = 344,
                          dpr: float = 1.0) -> QPixmap:
    """Session card: record and form up top, everything else in the grid."""
    family = cfg.get("font_family", "Segoe UI")
    cells = _session_cells(s)
    recent = list(s.recent)
    height = (H_HEADER + H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT
              + H_HERO + (H_PIPS if recent else 0)
              + (H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT if cells else 0)
              + math.ceil(len(cells) / 2) * H_GRID_ROW
              + H_FOOT_GAP + 1 + H_FOOT_RULE_GAP + H_FOOT)
    pix = glass.new_canvas(width, height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    x, w, y = 0, width, 0

    elapsed = int((datetime.now(timezone.utc) - s.started_at).total_seconds())
    hrs, rem = divmod(elapsed, 3600)
    duration = f"{hrs}h {rem // 60:02d}m" if hrs else f"{rem // 60} min"
    _draw_header(painter, family, x, w, "SESSION", duration)

    y += H_HEADER + H_RULE_GAP_TOP
    glass.rule(painter, x, y, w)
    y += 1 + H_RULE_GAP_BOT

    # Hero: the record at a size you can read mid-match, win rate to its right.
    if s.matches:
        painter.setFont(glass.mono(18, bold=True))
        fm = painter.fontMetrics()
        wins, losses = str(s.wins), str(s.losses)
        cx, base = x, y + 26
        painter.setPen(QPen(glass.qc(glass.WIN)))
        painter.drawText(cx, base, wins)
        cx += fm.horizontalAdvance(wins) + 6
        painter.setFont(glass.mono(12))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(cx, base, "–")
        cx += painter.fontMetrics().horizontalAdvance("–") + 6
        painter.setFont(glass.mono(18, bold=True))
        painter.setPen(QPen(glass.qc(glass.LOSS)))
        painter.drawText(cx, base, losses)

        pct = f"{s.wins / s.matches * 100:.0f}%"
        painter.setFont(glass.mono(11, bold=True))
        pw = painter.fontMetrics().horizontalAdvance(pct)
        glass.chip(painter, x + w - pw - 16 - RIGHT_INSET, base - 3, pct,
                   glass.mono(11, bold=True), glass.qc(glass.TEXT),
                   fill=glass.qc(glass.WHITE, glass.A_CHIP_FILL),
                   border=glass.qc(glass.WHITE, glass.A_CHIP_LINE),
                   radius=glass.PILL_RADIUS, pad_x=8)
        painter.setFont(glass.font(family, 7, letter_spacing=0.10))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(x, y + 40, f"{s.matches} match{'es' if s.matches != 1 else ''}")
    else:
        painter.setFont(glass.font(family, 10))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
        painter.drawText(x, y + 22, "No matches yet this session")
    y += H_HERO

    if recent:
        # Newest last, matching how the deque reads.
        px = x
        for r in recent:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(glass.qc(glass.WIN if r == "W" else glass.LOSS,
                                             0.95)))
            painter.drawEllipse(QRectF(px, y, 7, 7))
            px += 11
        painter.setFont(glass.font(family, 7, letter_spacing=0.10))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(px + 4, y + 7, "recent")
        y += H_PIPS

    if cells:
        y += H_RULE_GAP_TOP
        glass.rule(painter, x, y, w)
        y += 1 + H_RULE_GAP_BOT
        draw_stat_grid(painter, family, x, y, w, cells)
        y += math.ceil(len(cells) / 2) * H_GRID_ROW

    y += H_FOOT_GAP
    _draw_footer(painter, family, x, y, w,
                 [(first_keyboard_label(cfg.get("expand_hotkeys") or []), "graph"),
                  (first_keyboard_label(cfg.get("cycle_hotkeys") or []), "playlist")],
                 # Only label the pair format when some stat actually splits;
                 # otherwise every value is a bare number and the note is noise.
                 note="session / yours" if cells and session_has_split(s) else "")
    painter.end()
    return pix


# --- post-match summary -----------------------------------------------------
def render_summary_pixmap(payload: dict, ms, cfg: dict, width: int = 344,
                          dpr: float = 1.0) -> QPixmap:
    """Result card flashed when a match ends. Outcome and score lead; the
    per-match stats reuse the same grid as the expanded H2H card."""
    family = cfg.get("font_family", "Segoe UI")
    cells = match_stat_cells(ms)
    height = (H_HERO + (H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT if cells else 0)
              + math.ceil(len(cells) / 2) * H_GRID_ROW + 6)
    pix = glass.new_canvas(width, height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    x, w, y = 0, width, 0

    my_team, winner = payload.get("myTeam"), payload.get("winner")
    won = winner == my_team
    accent = glass.WIN if won else glass.LOSS
    painter.setFont(glass.font(family, 15, bold=True, letter_spacing=0.16))
    painter.setPen(QPen(glass.qc(accent)))
    painter.drawText(x, 26, "WIN" if won else "LOSS")

    score = payload.get("score")
    if isinstance(score, list) and len(score) == 2 and isinstance(my_team, int):
        mine, theirs = str(score[my_team]), str(score[1 - my_team])
        # Measure each run with the font it will actually be drawn in — the
        # separator is a smaller face, and assuming its width clipped the score.
        painter.setFont(glass.mono(16, bold=True))
        fm = painter.fontMetrics()
        painter.setFont(glass.mono(12))
        dash_w = painter.fontMetrics().horizontalAdvance("–")
        total = (fm.horizontalAdvance(mine) + 6 + dash_w + 6
                 + fm.horizontalAdvance(theirs))
        cx = x + w - total - RIGHT_INSET
        painter.setFont(glass.mono(16, bold=True))
        painter.setPen(QPen(glass.qc(glass.TEXT)))
        painter.drawText(cx, 26, mine)
        cx += fm.horizontalAdvance(mine) + 6
        painter.setFont(glass.mono(12))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(cx, 26, "–")
        cx += painter.fontMetrics().horizontalAdvance("–") + 6
        painter.setFont(glass.mono(16, bold=True))
        painter.setPen(QPen(glass.qc(glass.TEXT)))
        painter.drawText(cx, 26, theirs)
    y += H_HERO

    if cells:
        y += H_RULE_GAP_TOP - 8
        glass.rule(painter, x, y, w)
        y += 1 + H_RULE_GAP_BOT
        draw_stat_grid(painter, family, x, y, w, cells)
    painter.end()
    return pix


# --- settings menu ----------------------------------------------------------
def _binding_label(name: Optional[str]) -> str:
    if not name:
        return "—"
    if name.startswith("pad_"):
        return name[4:].upper().replace("_", " ")
    return name.upper()


def render_menu_pixmap(rows: list[dict], selected_index: int, capturing: bool,
                       cfg: dict, menu_key: str = "f5", width: int = 344,
                       dpr: float = 1.0) -> QPixmap:
    """In-game settings menu. Input-transparent overlay, so this is a static
    panel: the selected row is a filled pill rather than a text cursor."""
    family = cfg.get("font_family", "Segoe UI")
    body = 0
    for row in rows:
        body += H_SECTION if row["type"] == "header" else (
            6 if row["type"] == "spacer" else H_MENU_ROW)
    height = (H_HEADER + H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT + body
              + H_FOOT_GAP + 1 + H_FOOT_RULE_GAP + H_FOOT)
    pix = glass.new_canvas(width, height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    x, w, y = 0, width, 0

    _draw_header(painter, family, x, w, "SETTINGS",
                 "CAPTURING" if capturing else "", glass.ACCENT if capturing else None)
    y += H_HEADER + H_RULE_GAP_TOP
    glass.rule(painter, x, y, w)
    y += 1 + H_RULE_GAP_BOT

    for i, row in enumerate(rows):
        rtype = row["type"]
        if rtype == "spacer":
            y += 6
            continue
        if rtype == "header":
            glass.section_label(painter, family, x, y + 15, row["label"])
            y += H_SECTION
            continue

        selected = i == selected_index
        if selected:
            painter.setPen(QPen(glass.qc(glass.ACCENT, 0.22)))
            painter.setBrush(QBrush(glass.qc(glass.ACCENT, 0.10)))
            painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, w - 1, H_MENU_ROW - 3),
                                    glass.ROW_RADIUS, glass.ROW_RADIUS)
        baseline = y + 17
        painter.setFont(glass.font(family, 9, bold=selected))
        painter.setPen(QPen(glass.qc(glass.TEXT, 1.0 if selected else glass.A_SECONDARY)))
        painter.drawText(x + 10, baseline, row["label"])

        if rtype == "toggle":
            on = bool(row["value"])
            text = "ON" if on else "OFF"
            tone = glass.WIN if on else glass.TEXT
            painter.setFont(glass.mono(7, bold=True))
            tw = painter.fontMetrics().horizontalAdvance(text) + 16
            glass.chip(painter, x + w - tw - 10 - RIGHT_INSET, baseline, text,
                       glass.mono(7, bold=True),
                       glass.qc(tone, 1.0 if on else glass.A_MUTED),
                       fill=glass.qc(tone, 0.14 if on else 0.06),
                       border=glass.qc(tone, 0.24 if on else glass.A_CHIP_LINE),
                       radius=glass.PILL_RADIUS, pad_x=8)
        elif rtype == "binding":
            kb = "press…" if (capturing and selected) else _binding_label(row.get("kb"))
            pad = "press…" if (capturing and selected) else _binding_label(row.get("pad"))
            cx = x + w - 10 - RIGHT_INSET
            for label in (pad, kb):
                painter.setFont(glass.mono(7, bold=True))
                lw = painter.fontMetrics().horizontalAdvance(label) + 10
                cx -= lw
                glass.chip(painter, cx, baseline, label, glass.mono(7, bold=True),
                           glass.qc(glass.TEXT, 0.85 if selected else glass.A_MUTED),
                           fill=glass.qc(glass.WHITE, glass.A_CHIP_FILL),
                           border=glass.qc(glass.WHITE, glass.A_CHIP_LINE),
                           radius=glass.CHIP_RADIUS, pad_x=5)
                cx -= 6
        y += H_MENU_ROW

    y += H_FOOT_GAP
    _draw_footer(painter, family, x, y, w,
                 [(menu_key.upper(), "close"), ("↑↓", "move"), ("ENTER", "change")])
    painter.end()
    return pix
