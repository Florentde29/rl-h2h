"""In-game head-to-head card, "Hero MMR" direction.

Replaces the earlier TEAM 1 / TEAM 2 listing. Two things drive the layout:
your own MMR is the number you actually look at, so it leads at 40px; and the
people on screen matter by their relationship to you, not by which half of the
scoreboard they're on — hence AGAINST YOU and WITH YOU rather than team
numbers. The team swatch still rides each section header, so the mapping to
the in-game scoreboard is never lost.

Heights are computed before the canvas is allocated (a QPixmap is fixed size),
so every block's height is a constant here and the paint pass walks the same
ones.
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QPolygonF

from . import colors, glass, hero_data
from .arenas import pretty_arena
from .constants import BUCKET_VS, BUCKET_WITH
from .glass_h2h import (
    H_GRID_ROW, RIGHT_INSET, draw_stat_grid, match_stat_cells, mmr_bits,
)
from .render_h2h import first_keyboard_label, humanize_when

H_HERO = 80
H_HERO_NO_MMR = 52
H_RULE_GAP_TOP = 15
H_RULE_GAP_BOT = 12
H_SECTION = 21
H_TILE = 54
H_TILE_SLIM = 34
H_TILE_GAP = 8
H_SECTION_GAP = 14
H_GRID_GAP_TOP = 14
H_GRID_GAP_BOT = 12
H_FOOT_GAP = 13
H_FOOT_RULE_GAP = 11
H_FOOT = 15

SPARK_W = 104
SPARK_H = 34
NAME_MAX = 20


def _elide(painter: QPainter, text: str, max_w: float) -> str:
    fm = painter.fontMetrics()
    if fm.horizontalAdvance(text) <= max_w:
        return text
    out = text
    while out and fm.horizontalAdvance(out + "…") > max_w and len(out) > 1:
        out = out[:-1]
    return out + "…"


def _tile_rows(roster, my_team, players_db, self_id, mmr_db, mmr_category,
               mmr_enabled) -> tuple[list[dict], list[dict]]:
    """Split the roster into (against you, with you), excluding yourself.

    Grouping is by relationship, so a spectated match — where my_team is unset
    — puts everyone in 'against', which is the honest reading of an unknown
    allegiance rather than a guess."""
    against, with_you = [], []
    for p in sorted(roster, key=lambda q: q["name"].lower()):
        if self_id is not None and p["key"] == self_id:
            continue
        mine = p["team"] == my_team
        bucket = BUCKET_WITH if mine else BUCKET_VS
        rec = players_db.get(p["key"])
        entry = (mmr_db or {}).get(p["key"])
        row = {
            "p": p, "rec": rec, "bucket": bucket, "entry": entry,
            "record": hero_data.together_record(rec, bucket),
            "phrase": hero_data.last_meeting_phrase(rec, bucket, humanize_when),
            "bits": mmr_bits(entry, mmr_category) if mmr_enabled else None,
        }
        has_sub = bool(row["bits"] or row["phrase"])
        row["h"] = H_TILE if has_sub else H_TILE_SLIM
        (with_you if mine else against).append(row)
    return against, with_you


def card_height(against: list[dict], with_you: list[dict], cells: list,
                mmr_enabled: bool) -> int:
    h = (H_HERO if mmr_enabled else H_HERO_NO_MMR) + H_RULE_GAP_TOP + 1 + H_RULE_GAP_BOT
    for i, group in enumerate((against, with_you)):
        if not group:
            continue
        if i and against:
            h += H_SECTION_GAP
        h += H_SECTION + sum(r["h"] for r in group) + (len(group) - 1) * H_TILE_GAP
    if cells:
        h += H_GRID_GAP_TOP + 1 + H_GRID_GAP_BOT
        h += math.ceil(len(cells) / 2) * H_GRID_ROW
    return h + H_FOOT_GAP + 1 + H_FOOT_RULE_GAP + H_FOOT


def _draw_swatch(painter: QPainter, x: float, cy: float, hexc: str,
                 size: float = 8) -> None:
    c = QColor(hexc)
    glow = QColor(c)
    glow.setAlpha(70)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(glow))
    painter.drawEllipse(QRectF(x - 2, cy - size / 2 - 2, size + 4, size + 4))
    painter.setBrush(QBrush(c))
    painter.setPen(QPen(glass.qc(glass.WHITE, 0.45)))
    painter.drawEllipse(QRectF(x, cy - size / 2, size, size))


def _draw_sparkline(painter: QPainter, x: float, y: float,
                    values: list[int]) -> None:
    if len(values) < 2:
        return
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pts = [QPointF(x + 2 + i * (SPARK_W - 4) / (len(values) - 1),
                   y + SPARK_H - 4 - (v - lo) / span * (SPARK_H - 8))
           for i, v in enumerate(values)]
    pen = QPen(glass.qc(glass.TEXT, 0.5))
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawPolyline(QPolygonF(pts))
    # Colour by the LAST step, not the window's overall direction: the form
    # letters beside it report the last match, and a win inside a losing
    # session was showing a green W next to a red endpoint.
    rising = values[-1] >= values[-2]
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(glass.qc(glass.WIN if rising else glass.LOSS)))
    painter.drawEllipse(pts[-1], 3, 3)


def _draw_form(painter: QPainter, family: str, right: float, baseline: float,
               recent: list) -> float:
    """Last five results as letters. Returns the x it started at."""
    painter.setFont(glass.mono(8, bold=True))
    fm = painter.fontMetrics()
    letters = list(recent)[-5:]
    total = sum(fm.horizontalAdvance(r) + 3 for r in letters) - 3 if letters else 0
    cx = right - total
    start = cx
    for r in letters:
        painter.setPen(QPen(glass.qc(glass.WIN if r == "W" else glass.LOSS,
                                     1.0 if r == "W" else 0.85)))
        painter.drawText(int(cx), int(baseline), r)
        cx += fm.horizontalAdvance(r) + 3
    return start


def _draw_hero(painter: QPainter, family: str, x: int, w: int, y: int,
               name: str, my_mmr: Optional[int], bits, delta: Optional[int],
               playlist: str, category: str, spark: list[int],
               recent: list, swatch: str, session, mmr_enabled: bool) -> None:
    """Your row: identity, the number, and where it's heading."""
    right = x + w - RIGHT_INSET

    _draw_swatch(painter, x, y + 6, swatch)
    painter.setFont(glass.font(family, 10, bold=True))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    painter.drawText(x + 15, y + 10, _elide(painter, name, w * 0.5))
    nx = x + 15 + painter.fontMetrics().horizontalAdvance(_elide(painter, name, w * 0.5)) + 7
    glass.chip(painter, nx, y + 10, "YOU",
               glass.font(family, 6, bold=True, letter_spacing=0.14),
               glass.qc(glass.WIN), fill=glass.qc(glass.WIN, 0.14),
               radius=999, pad_x=6)

    if not mmr_enabled:
        # No tracker, no MMR: the session record is the honest hero instead of
        # an empty frame.
        wins, losses = (session.wins, session.losses) if session else (0, 0)
        painter.setFont(glass.mono(20, bold=True))
        painter.setPen(QPen(glass.qc(glass.WIN)))
        painter.drawText(x, y + 44, str(wins))
        cx = x + painter.fontMetrics().horizontalAdvance(str(wins)) + 5
        painter.setFont(glass.mono(13))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(cx, y + 44, "/")
        cx += painter.fontMetrics().horizontalAdvance("/") + 5
        painter.setFont(glass.mono(20, bold=True))
        painter.setPen(QPen(glass.qc(glass.LOSS)))
        painter.drawText(cx, y + 44, str(losses))
        painter.setFont(glass.font(family, 7, letter_spacing=0.10))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(cx + 28, y + 44, "SESSION")
        if recent:
            _draw_form(painter, family, right, y + 44, recent)
        return

    # The number, at the size that made it worth restructuring the card.
    mmr_text = str(my_mmr) if my_mmr is not None else "—"
    painter.setFont(glass.mono(29, bold=True))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    painter.drawText(x, y + 46, mmr_text)
    after = x + painter.fontMetrics().horizontalAdvance(mmr_text) + 9

    if delta is not None:
        sign = "+" if delta > 0 else ""
        if delta > 0:
            bg, fg = glass.WIN, glass.PILL_TEXT_ON_WIN
        elif delta < 0:
            bg, fg = glass.LOSS, glass.PILL_TEXT_ON_LOSS
        else:
            bg, fg = glass.WHITE, glass.CARD_BASE
        glass.chip(painter, after, y + 33, f"{sign}{delta}", glass.mono(8, bold=True),
                   glass.qc(fg), fill=glass.qc(bg, 1.0 if delta else 0.14),
                   radius=glass.CHIP_RADIUS, pad_x=6)
        painter.setFont(glass.font(family, 6, letter_spacing=0.10))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        painter.drawText(int(after), y + 46, "SESSION")

    # Rank line: tier, playlist, distance to the next band.
    parts_y = y + 66
    cx = x
    if bits and len(bits) > 1:
        tier, hexc = bits[0], bits[1]
        painter.setFont(glass.font(family, 8, bold=True))
        painter.setPen(QPen(QColor(hexc)))
        painter.drawText(cx, parts_y, tier)
        cx += painter.fontMetrics().horizontalAdvance(tier) + 8
    # The cycle key walks best / 1v1 / 2v2 / 3v3 / peak, so the card has to say
    # which one it is showing. For best and peak that is not the same thing as
    # the playlist the number came from, so both appear.
    scope = category.upper()
    if category in ("best", "peak") and playlist:
        scope = f"{scope} · {playlist.upper()}"
    # Division comes from TRN alongside the tier, so it is correct for this
    # playlist and this season. It replaces a computed distance-to-next-rank,
    # which was derived from a fixed table that is neither.
    division = bits[4] if (bits and len(bits) > 4) else ""
    for text, fnt, alpha in (
        (scope, glass.font(family, 7, bold=True, letter_spacing=0.10), 0.62),
        (division, glass.font(family, 8), glass.A_FAINT),
    ):
        if not text:
            continue
        painter.setPen(QPen(glass.qc(glass.WHITE, 0.14)))
        painter.drawLine(int(cx), parts_y - 7, int(cx), parts_y + 1)
        cx += 8
        painter.setFont(fnt)
        painter.setPen(QPen(glass.qc(glass.TEXT, alpha)))
        painter.drawText(int(cx), parts_y, text)
        cx += painter.fontMetrics().horizontalAdvance(text) + 8

    if spark:
        _draw_sparkline(painter, right - SPARK_W, y + 2, spark)
    if recent:
        _draw_form(painter, family, right, y + SPARK_H + 14, recent)


def _draw_section(painter: QPainter, family: str, x: int, w: int, y: int,
                  label: str, team_label: str, swatch: Optional[str]) -> None:
    painter.setFont(glass.font(family, 7, bold=True, letter_spacing=0.18))
    painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
    painter.drawText(x, y + 10, label)
    if swatch and team_label:
        painter.setFont(glass.font(family, 7, bold=True, letter_spacing=0.14))
        tw = painter.fontMetrics().horizontalAdvance(team_label)
        right = x + w - RIGHT_INSET
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
        painter.drawText(int(right - tw), y + 10, team_label)
        _draw_swatch(painter, right - tw - 14, y + 6, swatch)


def _draw_tile(painter: QPainter, family: str, x: int, y: int, w: int,
               row: dict, tint: Optional[str], my_mmr: Optional[int],
               mmr_category: str, mmr_enabled: bool) -> None:
    h = row["h"]
    if tint:
        c = QColor(tint)
        fill = QColor(c.red(), c.green(), c.blue(), round(0.08 * 255))
        line = QColor(c.red(), c.green(), c.blue(), round(0.18 * 255))
    else:
        fill = glass.qc(glass.WHITE, glass.A_ROW_FILL)
        line = glass.qc(glass.WHITE, glass.A_ROW_LINE)
    painter.setPen(QPen(line))
    painter.setBrush(QBrush(fill))
    painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, w - 1, h - 1), 11, 11)

    slim = h <= H_TILE_SLIM
    name_base = y + (h // 2 + 5) if slim else y + 21
    sub_base = y + 41
    right = x + w - 11

    record = row["record"]
    if record:
        wins, losses = str(record[0]), str(record[1])
        f_big, f_sep = glass.mono(11, bold=True), glass.mono(8)
        painter.setFont(f_big)
        fmb = painter.fontMetrics()
        painter.setFont(f_sep)
        fms = painter.fontMetrics()
        total = (fmb.horizontalAdvance(wins) + fms.horizontalAdvance("/")
                 + fmb.horizontalAdvance(losses) + 6)
        cx = right - total
        right = cx
        painter.setFont(f_big)
        painter.setPen(QPen(glass.qc(glass.WIN)))
        painter.drawText(int(cx), name_base, wins)
        cx += fmb.horizontalAdvance(wins) + 3
        painter.setFont(f_sep)
        painter.setPen(QPen(glass.qc(glass.TEXT, 0.28)))
        painter.drawText(int(cx), name_base, "/")
        cx += fms.horizontalAdvance("/") + 3
        painter.setFont(f_big)
        painter.setPen(QPen(glass.qc(glass.LOSS)))
        painter.drawText(int(cx), name_base, losses)
    else:
        f = glass.font(family, 6, bold=True, letter_spacing=0.14)
        painter.setFont(f)
        cw = painter.fontMetrics().horizontalAdvance("NEW") + 16
        right -= cw
        glass.chip(painter, right, name_base, "NEW", f,
                   glass.qc(glass.TEXT, glass.A_MUTED),
                   fill=glass.qc(glass.WHITE, 0.07), radius=999, pad_x=8)

    painter.setFont(glass.font(family, 10, bold=bool(record)))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    painter.drawText(x + 11, name_base,
                     _elide(painter, row["p"]["name"], right - (x + 11) - 10))

    if slim:
        return

    cx = x + 11
    bits = row["bits"]
    if mmr_enabled and bits:
        if len(bits) == 1:
            painter.setFont(glass.font(family, 8))
            painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
            painter.drawText(int(cx), sub_base, bits[0])
            cx += painter.fontMetrics().horizontalAdvance(bits[0]) + 7
        else:
            tier, hexc, mmr = bits[0], bits[1], bits[2]
            painter.setFont(glass.font(family, 8, bold=True))
            painter.setPen(QPen(QColor(hexc)))
            painter.drawText(int(cx), sub_base, tier)
            cx += painter.fontMetrics().horizontalAdvance(tier) + 7
            painter.setFont(glass.mono(11, bold=True))
            painter.setPen(QPen(glass.qc(glass.TEXT, 0.92)))
            painter.drawText(int(cx), sub_base, str(mmr))
            cx += painter.fontMetrics().horizontalAdvance(str(mmr)) + 7
            # Against: how far above or below you they sit. With: how you do together.
            extra = None
            if row["bucket"] == BUCKET_WITH and row["record"]:
                extra = f"{row['record'][2]}% together"
            elif my_mmr is not None and isinstance(mmr, int):
                gap = mmr - my_mmr
                extra = f"{gap:+d} on you" if gap else "level"
            if extra:
                painter.setFont(glass.font(family, 7))
                painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
                painter.drawText(int(cx), sub_base, extra)

    phrase = row["phrase"]
    if phrase:
        painter.setFont(glass.font(family, 7))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
        pw = painter.fontMetrics().horizontalAdvance(phrase)
        painter.drawText(int(x + w - 11 - pw), sub_base, phrase)


def render_h2h_pixmap(roster: list[dict], my_team: int, arena: str,
                      players_db: dict, team_colors: dict, cfg: dict,
                      self_id: Optional[str] = None,
                      mmr_db: Optional[dict] = None, mmr_category: str = "best",
                      mmr_enabled: bool = False, match_stats=None,
                      expanded: Optional[bool] = None, session=None,
                      snapshots: Optional[list] = None,
                      matches: Optional[list] = None,
                      session_started_iso: Optional[str] = None,
                      width: int = 344, dpr: float = 1.0) -> QPixmap:
    family = cfg.get("font_family", "Segoe UI")
    cells = match_stat_cells(match_stats) if match_stats is not None else []
    against, with_you = _tile_rows(roster, my_team, players_db, self_id,
                                   mmr_db, mmr_category, mmr_enabled)
    height = card_height(against, with_you, cells, mmr_enabled)
    pix = glass.new_canvas(width, height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    x, w, y = 0, width, 0

    fallback = (colors.C_BLUE, colors.C_ORANGE)

    def swatch_for(team: int) -> str:
        c = team_colors.get(team) if isinstance(team_colors, dict) else None
        return c if isinstance(c, str) and c.startswith("#") else fallback[team % 2]

    self_entry = (mmr_db or {}).get(self_id) if self_id else None
    my_mmr = hero_data.playlist_mmr(self_entry, mmr_category) if mmr_enabled else None
    playlist = mmr_category if mmr_category not in ("best", "peak") else (
        ((self_entry or {}).get("best") or {}).get("playlist") or "")
    spark = (hero_data.sparkline(playlist, snapshots or [], matches or [], cfg)
             if mmr_enabled and playlist else [])
    delta = (hero_data.session_mmr_delta(snapshots or [], playlist,
                                         session_started_iso)
             if mmr_enabled and playlist else None)
    self_name = next((p["name"] for p in roster if p["key"] == self_id), "You")

    _draw_hero(painter, family, x, w, y, self_name, my_mmr,
               mmr_bits(self_entry, mmr_category) if mmr_enabled else None,
               delta, playlist, mmr_category, spark,
               list(session.recent) if session else [], swatch_for(my_team),
               session, mmr_enabled)
    y += H_HERO if mmr_enabled else H_HERO_NO_MMR

    y += H_RULE_GAP_TOP
    glass.rule(painter, x, y, w)
    y += 1 + H_RULE_GAP_BOT

    for idx, (label, group, team) in enumerate(
        (("AGAINST YOU", against, 1 - my_team), ("WITH YOU", with_you, my_team))
    ):
        if not group:
            continue
        if idx and against:
            y += H_SECTION_GAP
        _draw_section(painter, family, x, w, y, label,
                      f"TEAM {team + 1}", swatch_for(team))
        y += H_SECTION
        tint = swatch_for(team) if label == "WITH YOU" else None
        for i, row in enumerate(group):
            _draw_tile(painter, family, x, y, w, row, tint, my_mmr,
                       mmr_category, mmr_enabled)
            y += row["h"] + (H_TILE_GAP if i < len(group) - 1 else 0)

    if cells:
        y += H_GRID_GAP_TOP
        glass.rule(painter, x, y, w)
        y += 1 + H_GRID_GAP_BOT
        draw_stat_grid(painter, family, x, y, w, cells)
        y += math.ceil(len(cells) / 2) * H_GRID_ROW

    y += H_FOOT_GAP
    glass.rule(painter, x, y, w, glass.A_ROW_LINE)
    base = y + H_FOOT_RULE_GAP + 8
    cx = x
    is_expanded = bool(match_stats is not None) if expanded is None else expanded
    for keys_cfg, verb in ((cfg.get("expand_hotkeys"),
                            "hide stats" if is_expanded else "match stats"),
                           (cfg.get("cycle_hotkeys"), mmr_category if mmr_enabled else "cycle MMR")):
        label = first_keyboard_label(keys_cfg or [])
        if not label:
            continue
        cx = glass.key_chip(painter, cx, base, label) + 5
        painter.setFont(glass.font(family, 8))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
        painter.drawText(int(cx), base, verb)
        cx += painter.fontMetrics().horizontalAdvance(verb) + 12
    arena_label = pretty_arena(arena)
    if arena_label:
        painter.setFont(glass.font(family, 7))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_GHOST)))
        aw = painter.fontMetrics().horizontalAdvance(arena_label)
        painter.drawText(int(x + w - aw - RIGHT_INSET), base, arena_label)
    painter.end()
    return pix
