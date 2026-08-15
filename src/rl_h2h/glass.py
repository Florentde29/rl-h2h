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

from PySide6.QtGui import QColor, QFont, QPixmap

# --- palette (from the Glass redesign) --------------------------------------
TEXT = (244, 246, 249)          # primary on-surface
ACCENT = (138, 182, 255)        # blue — playlist tags, links, info chips
WIN = (90, 224, 168)            # green — wins, YOU, positive deltas
LOSS = (255, 124, 138)          # red — losses, offline, negative deltas
CHAMPION = (196, 181, 253)      # violet — champion-tier rank text
GOLD = (240, 198, 116)          # gold-tier rank text
WHITE = (255, 255, 255)
CARD_BASE = (15, 17, 23)        # card fill before the white glass gradient
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


def card_stylesheet(text_rgb: tuple[int, int, int] = TEXT) -> str:
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
    stops = ((0.0, 0.11), (0.55, 0.03), (1.0, 0.06))
    parts = []
    for pos, white_amount in stops:
        r, g, b = _blend(CARD_BASE, WHITE, white_amount)
        parts.append(f"stop:{pos} rgba({r},{g},{b},199)")
    gradient = f"qlineargradient(x1:0, y1:0, x2:1, y2:1, {', '.join(parts)})"
    line = f"rgba({WHITE[0]},{WHITE[1]},{WHITE[2]},{round(A_CARD_LINE * 255)})"
    return (
        "QLabel {"
        f"  color: rgb({text_rgb[0]},{text_rgb[1]},{text_rgb[2]});"
        f"  background: {gradient};"
        f"  border: 1px solid {line};"
        f"  border-radius: {CARD_RADIUS}px;"
        f"  padding: {CARD_PAD_TOP}px {CARD_PAD_X}px {CARD_PAD_BOTTOM}px {CARD_PAD_X}px;"
        "}"
    )
