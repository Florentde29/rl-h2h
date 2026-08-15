"""Head-to-head card, painted. Replaces render_h2h.render_html for the overlay.

Heights are computed analytically rather than measured, because a QPixmap has
to be allocated before anything is drawn into it. Every element here has a
fixed height, so ``card_height`` and the paint pass walk the same constants —
keeping them adjacent is what stops the two drifting apart.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap

from . import colors, glass
from .arenas import pretty_arena
from .constants import BUCKET_VS, BUCKET_WITH
from .mmr import tier_color
from .render_h2h import first_keyboard_label, humanize_when

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
def _mmr_bits(entry: Optional[dict], category: str) -> tuple:
    """(tier, tier_hex, mmr, playlist) for the chip, or a placeholder string.

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
            pick.get("mmr"), pick.get("playlist"))


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


def _has_subline(rec: Optional[dict], bucket: str, is_self: bool,
                 mmr_enabled: bool) -> bool:
    """Whether the row's second line will carry anything."""
    if mmr_enabled:
        return True  # always shows a chip, even if only "…" or "—"
    if is_self or not rec:
        return False
    b = rec.get(bucket) or {}
    return bool(b.get("lastSeenAt") and b.get("lastResult"))


def card_height(team_rows: dict[int, list[dict]], stat_cells: list) -> int:
    h = H_HEADER + H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT
    for team in (0, 1):
        rows = team_rows[team]
        h += H_TEAM_HEADER + H_TEAM_HEADER_GAP
        if not rows:
            h += H_ROW_SLIM
            continue
        h += sum(r["h"] for r in rows) + (len(rows) - 1) * H_ROW_GAP
    h += H_TEAM_GAP
    if stat_cells:
        h += H_GRID_GAP_TOP + 1 + H_GRID_GAP_BOT
        h += math.ceil(len(stat_cells) / 2) * H_GRID_ROW
    h += H_FOOT_GAP + 1 + H_FOOT_RULE_GAP + H_FOOT
    return h


# --- painting ---------------------------------------------------------------
def _elide(text: str, limit: int = NAME_MAX) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _draw_team_header(painter: QPainter, family: str, x: int, y: int,
                      label: str, swatch: str, yours: bool) -> None:
    """Colour swatch + team label. The swatch takes the wire's ColorPrimary and
    carries an inner white ring so a black or very dark kit still reads."""
    cy = y + H_TEAM_HEADER // 2
    c = QColor(swatch)
    glow = QColor(c)
    glow.setAlpha(70)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(glow))
    painter.drawEllipse(QRectF(x - 2, cy - 6, 12, 12))
    painter.setBrush(QBrush(c))
    painter.setPen(QPen(glass.qc(glass.WHITE, 0.45)))
    painter.drawEllipse(QRectF(x, cy - 4, 8, 8))

    painter.setFont(glass.font(family, 7, bold=True, letter_spacing=0.16))
    painter.setPen(QPen(glass.qc(glass.TEXT, 0.72)))
    baseline = cy + 4
    painter.drawText(x + 16, baseline, label)
    if yours:
        w = painter.fontMetrics().horizontalAdvance(label)
        painter.setFont(glass.font(family, 7, letter_spacing=0.10))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(x + 16 + w + 8, baseline, "YOURS")


def _draw_player_row(painter: QPainter, family: str, x: int, y: int, w: int,
                     p: dict, rec: Optional[dict], bucket: str, is_self: bool,
                     mmr_entry: Optional[dict], mmr_category: str,
                     mmr_enabled: bool, row_h: int = H_ROW) -> None:
    fill = glass.qc(glass.WIN, 0.10) if is_self else glass.qc(glass.WHITE, glass.A_ROW_FILL)
    line = glass.qc(glass.WIN, 0.20) if is_self else glass.qc(glass.WHITE, glass.A_ROW_LINE)
    painter.setPen(QPen(line))
    painter.setBrush(QBrush(fill))
    # Half-pixel inset: a 1px stroke is centred on the path, so a rect drawn on
    # the canvas bounds loses half its border off-image and the right edge ends
    # up dimmer than the left.
    painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, w - 1, row_h - 1),
                            glass.ROW_RADIUS, glass.ROW_RADIUS)

    # A slim row has no second line, so the name centres instead of sitting high.
    slim = row_h <= H_ROW_SLIM
    name_baseline = y + (row_h // 2 + 5) if slim else y + 20
    sub_baseline = y + 36

    # Right-hand cell first: the name is elided against whatever it leaves.
    right_edge = x + w - 10
    if is_self:
        painter.setFont(glass.font(family, 7, bold=True, letter_spacing=0.16))
        painter.setPen(QPen(glass.qc(glass.WIN)))
        t = "YOU"
        right_edge -= painter.fontMetrics().horizontalAdvance(t)
        painter.drawText(right_edge, name_baseline, t)
    elif rec and (rec[bucket]["wins"] or rec[bucket]["losses"]):
        b = rec[bucket]
        wins, losses = str(b["wins"]), str(b["losses"])
        f_big = glass.mono(12, bold=True)
        f_sep = glass.mono(9)
        painter.setFont(f_big)
        fm_big = painter.fontMetrics()
        painter.setFont(f_sep)
        fm_sep = painter.fontMetrics()
        total = (fm_big.horizontalAdvance(wins) + fm_sep.horizontalAdvance("/")
                 + fm_big.horizontalAdvance(losses) + 4)
        cx = right_edge - total
        right_edge = cx
        painter.setFont(f_big)
        painter.setPen(QPen(glass.qc(glass.WIN)))
        painter.drawText(cx, name_baseline + 2, wins)
        cx += fm_big.horizontalAdvance(wins) + 2
        painter.setFont(f_sep)
        painter.setPen(QPen(glass.qc(glass.TEXT, 0.28)))
        painter.drawText(cx, name_baseline + 2, "/")
        cx += fm_sep.horizontalAdvance("/") + 2
        painter.setFont(f_big)
        painter.setPen(QPen(glass.qc(glass.LOSS)))
        painter.drawText(cx, name_baseline + 2, losses)
    else:
        f = glass.font(family, 6, bold=True, letter_spacing=0.14)
        painter.setFont(f)
        fm = painter.fontMetrics()
        t = "FIRST"
        cw = fm.horizontalAdvance(t) + 16
        right_edge -= cw
        glass.chip(painter, right_edge, name_baseline, t, f,
              glass.qc(glass.TEXT, glass.A_MUTED),
              fill=glass.qc(glass.WHITE, 0.07), radius=999, pad_x=8)

    painter.setFont(glass.font(family, 10, bold=is_self))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    fm = painter.fontMetrics()
    name = _elide(p["name"])
    while name and fm.horizontalAdvance(name) > right_edge - (x + 10) - 8 and len(name) > 2:
        name = name[:-2] + "…"
    painter.drawText(x + 10, name_baseline, name)

    # Sub-line: rank chip, then the last meeting.
    sx = x + 10
    if mmr_enabled:
        bits = _mmr_bits(mmr_entry, mmr_category)
        if len(bits) == 1:
            painter.setFont(glass.font(family, 7))
            painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
            painter.drawText(sx, sub_baseline, bits[0])
            sx += painter.fontMetrics().horizontalAdvance(bits[0]) + 6
        else:
            tier, hexc, mmr, playlist = bits
            painter.setFont(glass.font(family, 8, bold=True))
            painter.setPen(QPen(QColor(hexc)))
            painter.drawText(sx, sub_baseline, tier)
            sx += painter.fontMetrics().horizontalAdvance(tier) + 7
            # The opponent's MMR is the single most-scanned number on this card,
            # so it gets full contrast and a size close to the name rather than
            # the design's muted secondary treatment.
            painter.setFont(glass.mono(9, bold=True))
            painter.setPen(QPen(glass.qc(glass.TEXT)))
            painter.drawText(sx, sub_baseline, str(mmr))
            sx += painter.fontMetrics().horizontalAdvance(str(mmr)) + 7
            if playlist and mmr_category == "best":
                sx = glass.chip(painter, sx, sub_baseline, str(playlist).upper(),
                           glass.font(family, 6, bold=True, letter_spacing=0.10),
                           glass.qc(glass.TEXT, 0.38),
                           border=glass.qc(glass.WHITE, glass.A_CHIP_LINE)) + 6

    if not is_self and rec and rec[bucket].get("lastSeenAt"):
        b = rec[bucket]
        when = humanize_when(b["lastSeenAt"])
        res = b.get("lastResult")
        if when and res:
            painter.setFont(glass.font(family, 7))
            painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
            painter.drawText(sx, sub_baseline, when)
            sx += painter.fontMetrics().horizontalAdvance(when) + 5
            painter.setFont(glass.font(family, 7, bold=True))
            painter.setPen(QPen(glass.qc(glass.WIN if res == "W" else glass.LOSS)))
            painter.drawText(sx, sub_baseline, res)
            sx += painter.fontMetrics().horizontalAdvance(res) + 5
            score = b.get("lastScore")
            if isinstance(score, list) and len(score) == 2:
                painter.setFont(glass.mono(7))
                painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
                painter.drawText(sx, sub_baseline, f"{score[0]}–{score[1]}")


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


def render_h2h_pixmap(roster: list[dict], my_team: int, arena: str,
                      players_db: dict, team_colors: dict, cfg: dict,
                      self_id: Optional[str] = None,
                      mmr_db: Optional[dict] = None, mmr_category: str = "best",
                      mmr_enabled: bool = False, match_stats=None,
                      expanded: Optional[bool] = None,
                      width: int = 344, dpr: float = 1.0) -> QPixmap:
    """Paint the head-to-head card. `match_stats` adds the per-match grid
    (expanded view); pass None for the compact card. `expanded` only labels the
    toggle key — it defaults to whether the grid is present, but stays separate
    so an expanded match with no stats yet still offers to collapse."""
    family = cfg.get("font_family", "Segoe UI")
    cells = match_stat_cells(match_stats) if match_stats is not None else []

    # Resolve every row once: heights feed both the canvas allocation and the
    # paint pass, so the two can't disagree about how tall the card is.
    team_rows: dict[int, list[dict]] = {}
    for team in (0, 1):
        rows = []
        for p in sorted([q for q in roster if q["team"] == team],
                        key=lambda q: q["name"].lower()):
            rec = players_db.get(p["key"])
            bucket = BUCKET_WITH if team == my_team else BUCKET_VS
            is_self = self_id is not None and p["key"] == self_id
            sub = _has_subline(rec, bucket, is_self, mmr_enabled)
            rows.append({"p": p, "rec": rec, "bucket": bucket, "is_self": is_self,
                         "mmr": (mmr_db or {}).get(p["key"]),
                         "h": H_ROW if sub else H_ROW_SLIM})
        team_rows[team] = rows
    height = card_height(team_rows, cells)
    pix = glass.new_canvas(width, height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    x, w = 0, width
    y = 0

    # Header: title + arena on the left, MMR category pill on the right.
    painter.setFont(glass.font(family, 10, bold=True, letter_spacing=0.14))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    painter.drawText(x, y + 15, "HEAD TO HEAD")
    hx = x + painter.fontMetrics().horizontalAdvance("HEAD TO HEAD") + 9
    arena_label = pretty_arena(arena)
    if arena_label:
        painter.setFont(glass.font(family, 8, letter_spacing=0.06))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
        painter.drawText(hx, y + 15, _elide(arena_label, 16))
    if mmr_enabled:
        cat = (mmr_category or "best").upper()
        f_lbl = glass.font(family, 6, bold=True, letter_spacing=0.14)
        f_cat = glass.mono(7, bold=True)
        painter.setFont(f_lbl)
        lw = painter.fontMetrics().horizontalAdvance("MMR")
        painter.setFont(f_cat)
        cw = painter.fontMetrics().horizontalAdvance(cat)
        pill_w = lw + cw + 6 + 16
        px = x + w - pill_w - RIGHT_INSET
        painter.setPen(QPen(glass.qc(glass.WHITE, 0.10)))
        painter.setBrush(QBrush(glass.qc(glass.WHITE, 0.09)))
        painter.drawRoundedRect(QRectF(px, y + 1, pill_w, 18), 9, 9)
        painter.setFont(f_lbl)
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_LABEL)))
        painter.drawText(int(px + 8), y + 14, "MMR")
        painter.setFont(f_cat)
        painter.setPen(QPen(glass.qc(glass.ACCENT)))
        painter.drawText(int(px + 8 + lw + 6), y + 14, cat)

    y += H_HEADER + H_RULE_GAP_TOP
    painter.setPen(QPen(glass.qc(glass.WHITE, glass.A_DIVIDER)))
    painter.drawLine(x, y, x + w, y)
    y += 1 + H_RULE_GAP_BOT

    # Only used when the wire reports no usable ColorPrimary (private and
    # training matches send gray). Read from the palette rather than hardcoded,
    # so the team_blue_fallback / team_orange_fallback config keys still apply.
    fallback = (colors.C_BLUE, colors.C_ORANGE)
    for team in (0, 1):
        rows = team_rows[team]
        swatch = team_colors.get(team) if isinstance(team_colors, dict) else None
        if not isinstance(swatch, str) or not swatch.startswith("#"):
            swatch = fallback[team]
        _draw_team_header(painter, family, x, y, f"TEAM {team + 1}", swatch,
                          yours=(team == my_team))
        y += H_TEAM_HEADER + H_TEAM_HEADER_GAP
        if not rows:
            painter.setFont(glass.font(family, 8))
            painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
            painter.drawText(x + 10, y + 20, "—")
            y += H_ROW_SLIM
        for i, r in enumerate(rows):
            _draw_player_row(
                painter, family, x, y, w, r["p"], r["rec"], r["bucket"],
                is_self=r["is_self"], mmr_entry=r["mmr"],
                mmr_category=mmr_category, mmr_enabled=mmr_enabled,
                row_h=r["h"],
            )
            y += r["h"] + (H_ROW_GAP if i < len(rows) - 1 else 0)
        if team == 0:
            y += H_TEAM_GAP

    if cells:
        y += H_GRID_GAP_TOP
        painter.setPen(QPen(glass.qc(glass.WHITE, glass.A_DIVIDER)))
        painter.drawLine(x, y, x + w, y)
        y += 1 + H_GRID_GAP_BOT
        draw_stat_grid(painter, family, x, y, w, cells)
        y += math.ceil(len(cells) / 2) * H_GRID_ROW

    y += H_FOOT_GAP
    is_expanded = bool(match_stats is not None) if expanded is None else expanded
    _draw_footer(painter, family, x, y, w, cfg,
                 "match / yours" if cells else "", is_expanded)
    painter.end()
    return pix
