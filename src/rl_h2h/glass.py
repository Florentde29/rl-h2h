"""Painting foundation for the glass redesign: palette, canvas, card chrome.

Qt's RichText engine can express none of this design — no rounded corners on
inline content, no gradients, no per-span opacity, no SVG — so the redesigned
screens are painted with QPainter and pushed through ``Overlay.set_pixmap``,
the path ``graph.py`` has always used. Screens not yet redesigned keep
rendering HTML through ``colors.py``; the two palettes deliberately coexist so
migrating a screen is a self-contained change.

Colours are RGB triples rather than hex strings because almost every one of
them is used at several alphas — ``qc(TEXT, 0.44)`` reads better than a table
of near-identical hex constants.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPixmap

# --- palette (from the Glass redesign) --------------------------------------
TEXT = (244, 246, 249)          # primary on-surface
ACCENT = (138, 182, 255)        # blue — playlist tags, links, info chips
WIN = (90, 224, 168)            # green — wins, YOU, positive deltas
LOSS = (255, 124, 138)          # red — losses, offline, negative deltas
CHAMPION = (196, 181, 253)      # violet — champion-tier rank text
GOLD = (240, 198, 116)          # gold-tier rank text
WHITE = (255, 255, 255)
CARD_BASE = (12, 14, 19)        # card fill before the white glass gradient
CARD_ALPHA = 224                # 0-255; higher = less game showing through
PILL_TEXT_ON_WIN = (13, 26, 20)  # dark text on a saturated green pill
PILL_TEXT_ON_LOSS = (26, 6, 6)

# Text alphas, named so the intent survives (design uses these repeatedly).
# Raised from the mockup's values: those were judged in a browser on a flat
# ground, but this card floats over a bright, moving game at 380px wide, where
# the lower steps washed out. The ramp keeps the same ordering, just lifted.
A_SECONDARY = 0.72
A_LABEL = 0.62
A_MUTED = 0.56
A_FAINT = 0.46
A_GHOST = 0.40
A_DIVIDER = 0.09
A_ROW_FILL = 0.05
A_ROW_LINE = 0.07
A_CHIP_FILL = 0.10
A_CHIP_LINE = 0.12
A_CARD_LINE = 0.13

CARD_RADIUS = 16
ROW_RADIUS = 10
CHIP_RADIUS = 5
PILL_RADIUS = 6

# Card padding, kept here because callers must subtract it to size a canvas.
CARD_PAD_X = 18
CARD_PAD_TOP = 16
CARD_PAD_BOTTOM = 15


def qc(rgb: tuple[int, int, int], alpha: float = 1.0) -> QColor:
    """QColor from an RGB triple at `alpha` (0..1)."""
    c = QColor(*rgb)
    if alpha < 1.0:
        c.setAlpha(max(0, min(255, round(alpha * 255))))
    return c


# --- fonts ------------------------------------------------------------------
# Cached because the overlay repaints up to 4x/second while a hotkey is held;
# building QFonts per tick allocates for nothing. Same reasoning as graph.py's
# original cache, which now lives here so every glass renderer shares it.
MONO_FAMILIES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "SF Mono",
                 "DejaVu Sans Mono", "Menlo", "Courier New"]
_FONT_CACHE: dict[tuple, QFont] = {}


def font(family: str, size: int, bold: bool = False, letter_spacing: float = 0.0) -> QFont:
    key = ("ui", family, size, bold, letter_spacing)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = QFont(family, size)
        f.setBold(bold)
        if letter_spacing:
            # Design specifies tracking in em; Qt wants percent of char width.
            f.setLetterSpacing(QFont.PercentageSpacing, 100 + letter_spacing * 100)
        _FONT_CACHE[key] = f
    return f


def mono(size: int, bold: bool = False) -> QFont:
    """Monospace face. JetBrains Mono is the design's choice but ships with
    nothing, so the list falls through to Consolas, which is on every Windows."""
    key = ("mono", size, bold)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = QFont()
        f.setFamilies(MONO_FAMILIES)
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(size)
        f.setBold(bold)
        _FONT_CACHE[key] = f
    return f


# --- canvas -----------------------------------------------------------------
def new_canvas(width: int, height: int, dpr: float = 1.0) -> QPixmap:
    """Transparent pixmap sized for `dpr`, ready to paint in LOGICAL pixels.

    The pixmap is allocated at physical resolution and tagged with the ratio;
    QPainter then applies the scale itself, so callers keep working in logical
    coordinates and every drawing routine stays resolution-independent. Without
    this the pixmap is painted at 1x and upscaled by the label, which is why
    text looked soft at the 125%/150% scaling most Windows desktops run.
    """
    dpr = max(1.0, float(dpr))
    pix = QPixmap(max(1, round(width * dpr)), max(1, round(height * dpr)))
    pix.setDevicePixelRatio(dpr)
    pix.fill(QColor(0, 0, 0, 0))
    return pix


# --- card chrome ------------------------------------------------------------
def _blend(base: tuple[int, int, int], over: tuple[int, int, int],
           amount: float) -> tuple[int, int, int]:
    return tuple(round(b + (o - b) * amount) for b, o in zip(base, over))  # type: ignore[return-value]


def chip(painter, x: float, baseline: float, text: str, fnt,
         fg: QColor, fill: Optional[QColor] = None, border: Optional[QColor] = None,
         radius: int = 4, pad_x: int = 4) -> float:
    """Small rounded tag. Returns the x just past it, so callers can chain."""
    painter.setFont(fnt)
    fm = painter.fontMetrics()
    w = fm.horizontalAdvance(text) + pad_x * 2
    h = fm.height() + 1
    top = baseline - fm.ascent() - 1
    if fill is not None or border is not None:
        painter.setPen(QPen(border) if border is not None else Qt.NoPen)
        painter.setBrush(QBrush(fill) if fill is not None else Qt.NoBrush)
        painter.drawRoundedRect(QRectF(x, top, w, h), radius, radius)
    painter.setPen(QPen(fg))
    painter.drawText(int(x + pad_x), int(baseline), text)
    return x + w


def key_chip(painter, x: float, baseline: float, label: str) -> float:
    """Keycap for a hotkey hint (F7, TAB…). Returns the x just past it."""
    return chip(painter, x, baseline, label, mono(7, bold=True),
                qc(TEXT, 0.72), fill=qc(WHITE, A_CHIP_FILL),
                border=qc(WHITE, A_CHIP_LINE), radius=CHIP_RADIUS, pad_x=5)


def rule(painter, x: int, y: int, w: int, alpha: float = A_DIVIDER) -> None:
    painter.setPen(QPen(qc(WHITE, alpha)))
    painter.drawLine(x, y, x + w, y)


def section_label(painter, family: str, x: int, baseline: int, text: str) -> None:
    painter.setFont(font(family, 7, bold=True, letter_spacing=0.16))
    painter.setPen(QPen(qc(TEXT, A_LABEL)))
    painter.drawText(x, baseline, text)


def card_stylesheet(text_rgb: tuple[int, int, int] = TEXT,
                    tint: Optional[tuple[int, int, int]] = None) -> str:
    """Qt Style Sheet for the glass card behind a painted screen.

    The chrome stays in QSS rather than the pixmap because QSS already gives
    rounded corners, a border and gradients on the widget itself — and because
    the window is click-through and translucent, so the card must be painted by
    the widget that owns the geometry, not stamped into the content image.

    The design layers a white 145-degree gradient over a dark base fill. QSS
    supports only one background, so the two are pre-composited here: each stop
    is the base colour blended toward white by the design's overlay alpha,
    carrying the base's own translucency so the game still shows through.
    """
    # `tint` colours the first stop and the border — used by the match summary,
    # where the card itself should read as the result before any text is.
    # Darker than the mockup's values: judged in a browser the sheen reads as
    # depth, but over a bright game it lifts the whole card and costs text
    # contrast. Less white in the gradient, more opacity behind it.
    stops = ((0.0, 0.075), (0.55, 0.02), (1.0, 0.04))
    parts = []
    for i, (pos, white_amount) in enumerate(stops):
        over = tint if (tint and i == 0) else WHITE
        amount = 0.11 if (tint and i == 0) else white_amount
        r, g, b = _blend(CARD_BASE, over, amount)
        parts.append(f"stop:{pos} rgba({r},{g},{b},{CARD_ALPHA})")
    gradient = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, {', '.join(parts)})"
    edge = tint or WHITE
    edge_a = round((0.26 if tint else A_CARD_LINE) * 255)
    line = f"rgba({edge[0]},{edge[1]},{edge[2]},{edge_a})"
    return (
        "QLabel {"
        f"  color: rgb({text_rgb[0]},{text_rgb[1]},{text_rgb[2]});"
        f"  background: {gradient};"
        f"  border: 1px solid {line};"
        f"  border-radius: {CARD_RADIUS}px;"
        f"  padding: {CARD_PAD_TOP}px {CARD_PAD_X}px {CARD_PAD_BOTTOM}px {CARD_PAD_X}px;"
        "}"
    )
