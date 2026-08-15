"""MMR-evolution graph painted into a QPixmap (Qt RichText can't render it).

First screen migrated to the glass design. Data handling — point attribution,
y-range padding, the empty-state messages — is unchanged; everything visual is
rebuilt on ``glass.py``: new palette, a rounded plot well with a tier-tinted
band, an area fill under the trace, and a key-chip footer with a W/L legend.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QPolygon,
)

from . import glass
from .mmr import MMR_RANK_ZONES, attribute_mmr_points
from .render_h2h import first_keyboard_label
from .storage import match_playlist

# Vertical rhythm, in logical px. The design's margins collapse into these.
_HEADER_H = 26
_RULE_GAP_TOP = 12
_RULE_GAP_BOTTOM = 10
_PLOT_H = 150
_FOOTER_GAP = 12
_FOOTER_RULE_GAP = 11
_FOOTER_H = 16
_PLOT_RADIUS = glass.ROW_RADIUS

GRAPH_HEIGHT = (_HEADER_H + _RULE_GAP_TOP + 1 + _RULE_GAP_BOTTOM + _PLOT_H
                + _FOOTER_GAP + _FOOTER_RULE_GAP + _FOOTER_H)


def _draw_marker(painter: QPainter, x: int, y: int, color: QColor, radius: float) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(color))
    painter.drawEllipse(QPointF(x, y), radius, radius)


def _draw_key_chip(painter: QPainter, x: int, baseline: int, label: str) -> int:
    """Rounded key cap (e.g. F7). Returns the x just past it."""
    painter.setFont(glass.mono(7, bold=True))
    fm = painter.fontMetrics()
    w = fm.horizontalAdvance(label) + 10
    h = fm.height() + 2
    top = baseline - fm.ascent() - 2
    painter.setPen(QPen(glass.qc(glass.WHITE, glass.A_CHIP_LINE)))
    painter.setBrush(QBrush(glass.qc(glass.WHITE, glass.A_CHIP_FILL)))
    painter.drawRoundedRect(QRectF(x, top, w, h), glass.CHIP_RADIUS, glass.CHIP_RADIUS)
    painter.setPen(QPen(glass.qc(glass.TEXT, 0.72)))
    painter.drawText(x + 5, baseline, label)
    return x + w


def _tier_at(mmr: float) -> tuple[str, str] | None:
    for lo, hi, name, color in MMR_RANK_ZONES:
        if lo <= mmr <= hi:
            return name, color
    return None


def render_graph_pixmap(playlist: str, snapshots: list[dict],
                        matches: list[dict], cfg: dict,
                        canvas_width: int = 344,
                        canvas_height: int = GRAPH_HEIGHT,
                        dpr: float = 1.0) -> QPixmap:
    """Paint the MMR-evolution graph for ``playlist``. Caller writes the result
    via Overlay.set_pixmap(), which also switches the card to glass chrome."""
    grace = int(cfg.get("graph_match_grace_seconds", 120))
    window = int(cfg.get("graph_match_window", 30))
    points = attribute_mmr_points(playlist, snapshots, matches,
                                  grace_seconds=grace, window=window)

    pix = glass.new_canvas(canvas_width, canvas_height, dpr)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    family = cfg.get("font_family", "Segoe UI")
    left, right = 0, canvas_width
    plot_top = _HEADER_H + _RULE_GAP_TOP + 1 + _RULE_GAP_BOTTOM
    plot_bottom = plot_top + _PLOT_H

    _draw_header(painter, family, left, right, playlist, points)

    rule_y = _HEADER_H + _RULE_GAP_TOP
    painter.setPen(QPen(glass.qc(glass.WHITE, glass.A_DIVIDER)))
    painter.drawLine(left, rule_y, right, rule_y)

    if len(points) >= 2:
        _draw_plot(painter, family, left, right, plot_top, plot_bottom, points)
    else:
        _draw_empty_plot(painter, family, left, right, plot_top, plot_bottom,
                         playlist, snapshots, matches)

    _draw_footer(painter, cfg, left, right, canvas_height)
    painter.end()
    return pix


def _draw_header(painter: QPainter, family: str, left: int, right: int,
                 playlist: str, points: list[dict]) -> None:
    """MMR / playlist / sample size on the left, current value + delta pill right."""
    baseline = 15
    painter.setFont(glass.font(family, 10, bold=True, letter_spacing=0.14))
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    painter.drawText(left, baseline, "MMR")
    x = left + painter.fontMetrics().horizontalAdvance("MMR") + 9

    painter.setFont(glass.mono(8, bold=True))
    painter.setPen(QPen(glass.qc(glass.ACCENT)))
    pl = playlist.upper()
    painter.drawText(x, baseline, pl)
    x += painter.fontMetrics().horizontalAdvance(pl) + 9

    if points:
        painter.setFont(glass.font(family, 8))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
        painter.drawText(x, baseline, f"last {len(points)}")

    if len(points) < 2:
        return

    # Delta pill sits hard right; the current value is placed just left of it.
    delta = points[-1]["mmr"] - points[0]["mmr"]
    if delta > 0:
        pill_bg, pill_fg, sign = glass.WIN, glass.PILL_TEXT_ON_WIN, "+"
    elif delta < 0:
        pill_bg, pill_fg, sign = glass.LOSS, glass.PILL_TEXT_ON_LOSS, ""
    else:
        pill_bg, pill_fg, sign = glass.WHITE, glass.CARD_BASE, ""
    delta_text = f"{sign}{delta}"

    painter.setFont(glass.mono(8, bold=True))
    fm = painter.fontMetrics()
    pill_w = fm.horizontalAdvance(delta_text) + 14
    pill_h = fm.height() + 4
    pill_x = right - pill_w
    pill_y = (_HEADER_H - pill_h) // 2 - 1
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(glass.qc(pill_bg, 1.0 if delta else glass.A_CHIP_FILL)))
    painter.drawRoundedRect(QRectF(pill_x, pill_y, pill_w, pill_h),
                            glass.PILL_RADIUS, glass.PILL_RADIUS)
    painter.setPen(QPen(glass.qc(pill_fg if delta else glass.TEXT, 1.0 if delta else 0.6)))
    painter.drawText(pill_x + 7, pill_y + pill_h - fm.descent() - 2, delta_text)

    value = str(points[-1]["mmr"])
    painter.setFont(glass.mono(14, bold=True))
    vfm = painter.fontMetrics()
    painter.setPen(QPen(glass.qc(glass.TEXT)))
    painter.drawText(pill_x - 8 - vfm.horizontalAdvance(value), baseline + 3, value)


def _draw_plot(painter: QPainter, family: str, left: int, right: int,
               top: int, bottom: int, points: list[dict]) -> None:
    mmr_values = [p["mmr"] for p in points if isinstance(p["mmr"], (int, float))]
    mmr_min, mmr_max = min(mmr_values), max(mmr_values)
    # Pad the y-range so points don't sit on the edge. Always show at least
    # 100 MMR of vertical span so single-game graphs aren't squashed.
    span = max(mmr_max - mmr_min, 100)
    pad = max(20, int(span * 0.15))
    y_min, y_max = max(0, mmr_min - pad), mmr_max + pad
    plot_w, plot_h = right - left, bottom - top

    # Inset the trace so the first and last markers (and the last one's 7px
    # halo) sit inside the well instead of being clipped by its edge.
    x_inset = 9

    def to_x(i: int) -> int:
        if len(points) == 1:
            return left + plot_w // 2
        usable = plot_w - x_inset * 2
        return left + x_inset + int(i * usable / (len(points) - 1))

    def to_y(mmr: float) -> int:
        if y_max == y_min:
            return top + plot_h // 2
        return bottom - int((mmr - y_min) / (y_max - y_min) * plot_h)

    # Rounded well. Clipping to it keeps the tier bands and the area fill from
    # squaring off the corners the design rounds.
    well = QPainterPath()
    well.addRoundedRect(QRectF(left, top, plot_w, plot_h), _PLOT_RADIUS, _PLOT_RADIUS)
    painter.save()
    painter.setClipPath(well)

    for lo, hi, _name, color in MMR_RANK_ZONES:
        if hi < y_min or lo > y_max:
            continue
        y_top, y_bot = to_y(min(hi, y_max)), to_y(max(lo, y_min))
        painter.fillRect(left, y_top, plot_w, y_bot - y_top,
                         QBrush(glass.qc(QColor(color).getRgb()[:3], 0.10)))

    start_y = to_y(points[0]["mmr"])
    anchor = QPen(glass.qc(glass.WHITE, 0.16))
    anchor.setStyle(Qt.CustomDashLine)
    anchor.setDashPattern([3, 5])
    painter.setPen(anchor)
    painter.drawLine(left + 26, start_y, right - 14, start_y)

    line_pts = [QPoint(to_x(i), to_y(p["mmr"])) for i, p in enumerate(points)]

    fill = QPainterPath(QPointF(line_pts[0]))
    for pt in line_pts[1:]:
        fill.lineTo(QPointF(pt))
    fill.lineTo(QPointF(line_pts[-1].x(), bottom))
    fill.lineTo(QPointF(line_pts[0].x(), bottom))
    fill.closeSubpath()
    grad = QLinearGradient(0, top, 0, bottom)
    grad.setColorAt(0.0, glass.qc(glass.TEXT, 0.22))
    grad.setColorAt(1.0, glass.qc(glass.TEXT, 0.0))
    painter.fillPath(fill, QBrush(grad))

    pen = QPen(glass.qc(glass.TEXT))
    pen.setWidth(2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline(QPolygon(line_pts))

    last = len(points) - 1
    for i, p in enumerate(points):
        marker = p.get("marker") or "snap"
        x, y = to_x(i), to_y(p["mmr"])
        if marker == "snap":
            _draw_marker(painter, x, y, glass.qc(glass.TEXT, 0.40), 2.5)
            continue
        base = glass.qc(glass.WIN if marker == "W" else glass.LOSS)
        if i == last:
            halo = QColor(base)
            halo.setAlpha(82)
            _draw_marker(painter, x, y, halo, 7)
            _draw_marker(painter, x, y, base, 4)
        else:
            _draw_marker(painter, x, y, base, 3.5)

    painter.restore()

    # Axis bounds are read, not decoration — the design's faint treatment made
    # them hard to pick out against the tier band.
    painter.setFont(glass.mono(7, bold=True))
    painter.setPen(QPen(glass.qc(glass.TEXT, 0.66)))
    painter.drawText(left + 9, top + 14, str(y_max))
    painter.drawText(left + 9, bottom - 6, str(y_min))

    tier = _tier_at(points[-1]["mmr"])
    if tier:
        name, color = tier
        painter.setFont(glass.font(family, 6, bold=True, letter_spacing=0.10))
        painter.setPen(QPen(glass.qc(QColor(color).getRgb()[:3], 0.55)))
        painter.drawText(left + 9, top + plot_h // 2 - 4, name.upper())


def _draw_empty_plot(painter: QPainter, family: str, left: int, right: int,
                     top: int, bottom: int, playlist: str,
                     snapshots: list[dict], matches: list[dict]) -> None:
    if not snapshots:
        msg = "MMR not tracked yet — enable in tray menu and play a match"
    elif not any((s.get("playlists") or {}).get(playlist) is not None for s in snapshots):
        msg = f"No {playlist} MMR yet — play a ranked {playlist} match"
    elif not any(match_playlist(m) == playlist for m in matches):
        msg = f"No {playlist} matches yet — play one to start the graph"
    else:
        msg = "Need at least 2 MMR snapshots — keep playing"
    well = QPainterPath()
    well.addRoundedRect(QRectF(left, top, right - left, bottom - top),
                        _PLOT_RADIUS, _PLOT_RADIUS)
    painter.fillPath(well, QBrush(glass.qc(glass.WHITE, 0.03)))
    painter.setFont(glass.font(family, 8))
    painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
    fm = painter.fontMetrics()
    painter.drawText(left + (right - left - fm.horizontalAdvance(msg)) // 2,
                     top + (bottom - top) // 2, msg)


def _draw_footer(painter: QPainter, cfg: dict, left: int, right: int,
                 canvas_height: int) -> None:
    """Key chips on the left, W/L legend on the right, above a hairline rule."""
    rule_y = canvas_height - _FOOTER_H - _FOOTER_RULE_GAP
    painter.setPen(QPen(glass.qc(glass.WHITE, glass.A_ROW_LINE)))
    painter.drawLine(left, rule_y, right, rule_y)

    baseline = canvas_height - 4
    family = cfg.get("font_family", "Segoe UI")
    x = left
    for keys_cfg, verb in ((cfg.get("expand_hotkeys"), "session"),
                           (cfg.get("cycle_hotkeys"), "playlist")):
        label = first_keyboard_label(keys_cfg or [])
        if not label:
            continue
        x = _draw_key_chip(painter, x, baseline, label) + 5
        painter.setFont(glass.font(family, 8))
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_MUTED)))
        painter.drawText(x, baseline, verb)
        x += painter.fontMetrics().horizontalAdvance(verb) + 14

    # Laid out right-to-left from a small inset: measuring left-to-right left
    # the last label sitting exactly on the canvas edge, where it clipped.
    painter.setFont(glass.font(family, 7))
    fm = painter.fontMetrics()
    dot_r, dot_gap, item_gap, inset = 3, 6, 12, 2
    lx = right - inset
    for text, color in reversed([("win", glass.WIN), ("loss", glass.LOSS)]):
        lx -= fm.horizontalAdvance(text)
        painter.setPen(QPen(glass.qc(glass.TEXT, glass.A_FAINT)))
        painter.drawText(lx, baseline, text)
        lx -= dot_gap + dot_r
        _draw_marker(painter, lx, baseline - fm.ascent() // 2 + 1, glass.qc(color), dot_r)
        lx -= dot_r + item_gap
