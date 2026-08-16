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
from .arenas import pretty_arena
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
H_EYEBROW = 20
H_HERO = 44
H_SUM_HERO = 46
H_SECTION = 22
H_MENU_ROW = 28


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


# --- settings menu ----------------------------------------------------------
def _binding_label(name: Optional[str]) -> str:
    if not name:
        return "—"
    if name.startswith("pad_"):
        return name[4:].upper().replace("_", " ")
    return name.upper()


def render_session_pixmap(s, cfg: dict, mmr_delta: Optional[int] = None,
                          width: int = 344, dpr: float = 1.0) -> QPixmap:
    """Session card. The record leads at hero size; form and best run sit
    opposite it; everything countable goes to the grid."""
    family = cfg.get("font_family", "Segoe UI")
    cells = _session_cells(s)
    recent = list(s.recent)
    height = (H_EYEBROW + H_HERO
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
    duration = f"{hrs}H {rem // 60:02d}M" if hrs else f"{rem // 60}M"
    painter.setFont(glass.font(family, 7, bold=True, letter_spacing=0.18))
    painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
    painter.drawText(x, y + 10, f"SESSION · {duration}")
    y += H_EYEBROW

    if s.matches:
        wins, losses = str(s.wins), str(s.losses)
        painter.setFont(glass.mono(27, bold=True))
        fmb = painter.fontMetrics()
        base = y + 30
        painter.setPen(QPen(glass.qc(glass.WIN)))
        painter.drawText(x, base, wins)
        cx = x + fmb.horizontalAdvance(wins) + 4
        painter.setFont(glass.mono(16))
        painter.setPen(QPen(glass.qc(glass.TEXT, 0.28)))
        painter.drawText(int(cx), base, "/")
        cx += painter.fontMetrics().horizontalAdvance("/") + 4
        painter.setFont(glass.mono(27, bold=True))
        painter.setPen(QPen(glass.qc(glass.LOSS)))
        painter.drawText(int(cx), base, losses)
        cx += fmb.horizontalAdvance(losses) + 9

        if mmr_delta is not None:
            sign = "+" if mmr_delta > 0 else ""
            bg = glass.WIN if mmr_delta > 0 else (glass.LOSS if mmr_delta < 0 else glass.WHITE)
            fg = (glass.PILL_TEXT_ON_WIN if mmr_delta > 0 else
                  glass.PILL_TEXT_ON_LOSS if mmr_delta < 0 else glass.CARD_BASE)
            glass.chip(painter, cx, base - 13, f"{sign}{mmr_delta}",
                       glass.mono(8, bold=True), glass.qc(fg),
                       fill=glass.qc(bg, 1.0 if mmr_delta else 0.14),
                       radius=glass.CHIP_RADIUS, pad_x=6)
        painter.setFont(glass.font(family, 6, letter_spacing=0.10))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(int(cx), base, f"{s.wins / s.matches * 100:.0f}% WON")
    else:
        painter.setFont(glass.font(family, 10))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
        painter.drawText(x, y + 22, "No matches yet this session")

    right = x + w - RIGHT_INSET
    if recent:
        painter.setFont(glass.mono(8, bold=True))
        fm = painter.fontMetrics()
        letters = recent[-5:]
        total = sum(fm.horizontalAdvance(r) + 3 for r in letters) - 3
        cx = right - total
        for r in letters:
            painter.setPen(QPen(glass.qc(glass.WIN if r == "W" else glass.LOSS,
                                         1.0 if r == "W" else 0.85)))
            painter.drawText(int(cx), y + 12, r)
            cx += fm.horizontalAdvance(r) + 3
    if s.best_win_streak:
        painter.setFont(glass.font(family, 7))
        run = f"W{s.best_win_streak}"
        painter.setFont(glass.mono(7, bold=True))
        rw = painter.fontMetrics().horizontalAdvance(run)
        painter.setPen(QPen(glass.qc(glass.WIN)))
        painter.drawText(int(right - rw), y + 30, run)
        painter.setFont(glass.font(family, 7))
        lbl = "best run "
        lw = painter.fontMetrics().horizontalAdvance(lbl)
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
        painter.drawText(int(right - rw - lw), y + 30, lbl)
    y += H_HERO

    if cells:
        y += H_RULE_GAP_TOP
        glass.rule(painter, x, y, w)
        y += 1 + H_RULE_GAP_BOT
        draw_stat_grid(painter, family, x, y, w, cells)
        y += math.ceil(len(cells) / 2) * H_GRID_ROW

    y += H_FOOT_GAP
    _draw_footer(painter, family, x, y, w,
                 [(first_keyboard_label(cfg.get("expand_hotkeys") or []), "MMR graph")],
                 note="session / yours" if cells and session_has_split(s) else "")
    painter.end()
    return pix


def render_summary_pixmap(payload: dict, ms, cfg: dict,
                          players_db: Optional[dict] = None,
                          mmr_before: Optional[int] = None,
                          mmr_after: Optional[int] = None,
                          width: int = 344, dpr: float = 1.0) -> QPixmap:
    """Result card. Outcome and score lead; the footer names the opponent you
    just played and what the match did to your MMR."""
    family = cfg.get("font_family", "Segoe UI")
    cells = match_stat_cells(ms)
    my_team, winner = payload.get("myTeam"), payload.get("winner")
    won = winner == my_team
    accent = glass.WIN if won else glass.LOSS

    # The post-match MMR poll lands minutes after this card flashes, so before
    # and after are usually the same number. Showing "1095 -> 1095" would imply
    # the match moved nothing; the line simply waits until it has something.
    show_mmr = (isinstance(mmr_before, int) and isinstance(mmr_after, int)
                and mmr_before != mmr_after)
    has_foot = bool(show_mmr or cells)
    height = (H_SUM_HERO
              + (H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT if cells else 0)
              + math.ceil(len(cells) / 2) * H_GRID_ROW
              + (H_FOOT_GAP + 1 + H_FOOT_RULE_GAP + H_FOOT if has_foot else 6))
    pix = glass.new_canvas(width, height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    x, w, y = 0, width, 0

    painter.setFont(glass.font(family, 19, bold=True, letter_spacing=0.06))
    painter.setPen(QPen(glass.qc(accent)))
    painter.drawText(x, y + 22, "WIN" if won else "LOSS")
    sub = " · ".join(p for p in (pretty_arena(payload.get("arena") or ""),
                                 payload.get("playlist") or "") if p)
    if sub:
        painter.setFont(glass.font(family, 8))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
        painter.drawText(x, y + 38, sub)

    score = payload.get("score")
    if isinstance(score, list) and len(score) == 2 and isinstance(my_team, int):
        mine, theirs = str(score[my_team]), str(score[1 - my_team])
        painter.setFont(glass.mono(24, bold=True))
        fmb = painter.fontMetrics()
        painter.setFont(glass.mono(14))
        dash_w = painter.fontMetrics().horizontalAdvance("–")
        total = fmb.horizontalAdvance(mine) + 6 + dash_w + 6 + fmb.horizontalAdvance(theirs)
        cx = x + w - total - RIGHT_INSET
        painter.setFont(glass.mono(24, bold=True))
        painter.setPen(QPen(glass.qc(glass.TEXT)))
        painter.drawText(int(cx), y + 26, mine)
        cx += fmb.horizontalAdvance(mine) + 6
        painter.setFont(glass.mono(14))
        painter.setPen(QPen(glass.qc(glass.TEXT, 0.28)))
        painter.drawText(int(cx), y + 26, "–")
        cx += dash_w + 6
        painter.setFont(glass.mono(24, bold=True))
        painter.setPen(QPen(glass.qc(glass.TEXT, 0.55)))
        painter.drawText(int(cx), y + 26, theirs)
    y += H_SUM_HERO

    if cells:
        y += H_RULE_GAP_TOP
        glass.rule(painter, x, y, w, 0.10)
        y += 1 + H_RULE_GAP_BOT
        draw_stat_grid(painter, family, x, y, w, cells)
        y += math.ceil(len(cells) / 2) * H_GRID_ROW

    if has_foot:
        y += H_FOOT_GAP
        glass.rule(painter, x, y, w, 0.08)
        base = y + H_FOOT_RULE_GAP + 8
        if cells:
            # "2/1" reads like a scoreline unless the card says otherwise —
            # it is the match total against your share of it.
            painter.setFont(glass.font(family, 7))
            painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
            painter.drawText(x, base, "match / yours")
        if show_mmr:
            painter.setFont(glass.font(family, 7))
            text = f"{mmr_before} → {mmr_after}"
            tw = painter.fontMetrics().horizontalAdvance(text)
            painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
            painter.drawText(int(x + w - tw - RIGHT_INSET), base, text)
    painter.end()
    return pix


def _draw_switch(painter: QPainter, x: float, cy: float, on: bool) -> None:
    """34x18 track with a knob — reads as a control, unlike an ON/OFF word."""
    track = glass.qc(glass.WIN, 0.90) if on else glass.qc(glass.WHITE, 0.12)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(track))
    painter.drawRoundedRect(QRectF(x, cy - 9, 34, 18), 9, 9)
    knob = glass.qc(glass.PILL_TEXT_ON_WIN) if on else glass.qc(glass.TEXT, 0.55)
    painter.setBrush(QBrush(knob))
    painter.drawEllipse(QRectF(x + (18 if on else 2), cy - 7, 14, 14))


def render_menu_pixmap(rows: list[dict], selected_index: int, capturing: bool,
                       cfg: dict, menu_key: str = "f5", status: str = "",
                       width: int = 344, dpr: float = 1.0) -> QPixmap:
    """Settings menu. Input-transparent overlay, so this is a static panel:
    selection is a tinted row with an accent bar, not a text cursor."""
    family = cfg.get("font_family", "Segoe UI")
    body = 0
    for row in rows:
        body += {"header": H_SECTION, "spacer": 6}.get(row["type"], H_MENU_ROW)
    height = (H_HEADER + H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT + body
              + H_FOOT_GAP + 1 + H_FOOT_RULE_GAP + H_FOOT)
    pix = glass.new_canvas(width, height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    x, w, y = 0, width, 0

    _draw_header(painter, family, x, w, "SETTINGS", status)
    y += H_HEADER + H_RULE_GAP_TOP
    glass.rule(painter, x, y, w)
    y += 1 + H_RULE_GAP_BOT

    for i, row in enumerate(rows):
        rtype = row["type"]
        if rtype == "spacer":
            y += 6
            continue
        if rtype == "header":
            painter.setFont(glass.font(family, 7, bold=True, letter_spacing=0.18))
            painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
            painter.drawText(x, y + 14, row["label"])
            y += H_SECTION
            continue

        selected = i == selected_index
        capturing_here = capturing and selected and rtype == "binding"
        rect = QRectF(x + 0.5, y + 0.5, w - 1, H_MENU_ROW - 4)
        if capturing_here:
            pen = QPen(glass.qc(glass.ACCENT, 0.40))
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(QBrush(glass.qc(glass.WHITE, 0.06)))
            painter.drawRoundedRect(rect, glass.ROW_RADIUS, glass.ROW_RADIUS)
        elif selected:
            painter.setPen(QPen(glass.qc(glass.ACCENT, 0.26)))
            painter.setBrush(QBrush(glass.qc(glass.ACCENT, 0.10)))
            painter.drawRoundedRect(rect, glass.ROW_RADIUS, glass.ROW_RADIUS)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(glass.qc(glass.ACCENT)))
            painter.drawRoundedRect(QRectF(x + 8, y + 5, 3, 14), 2, 2)
        elif rtype == "toggle":
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(glass.qc(glass.WHITE, 0.04)))
            painter.drawRoundedRect(rect, glass.ROW_RADIUS, glass.ROW_RADIUS)

        baseline = y + 17
        label_x = x + (17 if selected and not capturing_here else 11)
        painter.setFont(glass.font(family, 9))
        painter.setPen(QPen(glass.qc(glass.TEXT,
                                     1.0 if (selected or capturing_here) else glass.A_SECONDARY)))
        painter.drawText(label_x, baseline, row["label"])

        if rtype == "toggle":
            _draw_switch(painter, x + w - 34 - 11 - RIGHT_INSET, y + 11, bool(row["value"]))
        elif capturing_here:
            painter.setFont(glass.font(family, 8))
            painter.setPen(QPen(glass.qc(glass.ACCENT)))
            msg = "press a key or button…"
            mw = painter.fontMetrics().horizontalAdvance(msg)
            painter.drawText(int(x + w - mw - 11 - RIGHT_INSET), baseline, msg)
        else:
            cx = x + w - 11 - RIGHT_INSET
            for label in (_binding_label(row.get("pad")), _binding_label(row.get("kb"))):
                if label == "—":
                    painter.setFont(glass.font(family, 8))
                    lw = painter.fontMetrics().horizontalAdvance(label)
                    cx -= lw
                    painter.setPen(QPen(glass.qc(glass.TEXT, 0.26)))
                    painter.drawText(int(cx), baseline, label)
                    cx -= 6
                    continue
                painter.setFont(glass.mono(7, bold=True))
                lw = painter.fontMetrics().horizontalAdvance(label) + 12
                cx -= lw
                glass.chip(painter, cx, baseline, label, glass.mono(7, bold=True),
                           glass.qc(glass.TEXT, 0.8),
                           fill=glass.qc(glass.WHITE, glass.A_CHIP_FILL),
                           border=glass.qc(glass.WHITE, glass.A_CHIP_LINE),
                           radius=glass.CHIP_RADIUS, pad_x=6)
                cx -= 6
        y += H_MENU_ROW

    y += H_FOOT_GAP
    glass.rule(painter, x, y, w, glass.A_ROW_LINE)
    base = y + H_FOOT_RULE_GAP + 8
    painter.setFont(glass.font(family, 8))
    painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
    cx = x
    for hint in ("↑↓ move", "Enter select", "Esc cancel"):
        painter.drawText(int(cx), base, hint)
        cx += painter.fontMetrics().horizontalAdvance(hint) + 14
    close = f"{menu_key.upper()} close"
    cw = painter.fontMetrics().horizontalAdvance(close)
    painter.drawText(int(x + w - cw - RIGHT_INSET), base, close)
    painter.end()
    return pix
