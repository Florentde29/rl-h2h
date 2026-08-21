"""Frameless transparent always-on-top widget the renderers paint into."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QFont, QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from . import glass


VALID_POSITIONS = ("top-left", "top-center", "top-right", "bottom-left", "bottom-right")


class Overlay(QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._label = QLabel(self)
        self._label.setTextFormat(Qt.RichText)
        self._label.setWordWrap(True)
        self._label.setFont(QFont(cfg["font_family"], cfg["font_size"]))
        self._label.setFixedWidth(cfg["width"])
        bg_rgba = ",".join(str(v) for v in cfg.get("background_rgba") or [16, 20, 21, 200])
        border_rgba = ",".join(str(v) for v in cfg.get("border_rgba") or [255, 255, 255, 28])
        radius = int(cfg.get("border_radius_px", 4))
        # Two chromes coexist while the redesign is rolled out screen by screen:
        # HTML screens keep the original flat card, painted screens get the
        # glass one. set_html/set_pixmap pick — a screen's look follows from how
        # it renders, so migrating one needs no extra call.
        self._legacy_chrome = (
            "QLabel {"
            f"  color: {cfg['text_color']};"
            f"  background-color: rgba({bg_rgba});"
            f"  border: 1px solid rgba({border_rgba});"
            f"  border-radius: {radius}px;"
            "  padding: 14px 16px 16px 16px;"
            "}"
        )
        self._glass_chrome = glass.card_stylesheet()
        self._tinted_chrome: dict[tuple, str] = {}
        self._chrome = None
        self._apply_chrome(self._legacy_chrome)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self.resize(cfg["width"], 100)
        self.hide()

    def _apply_chrome(self, sheet: str) -> None:
        """Swap the card stylesheet, skipping the restyle when unchanged.

        Qt re-polishes the whole widget on setStyleSheet, and update_overlay
        runs on a 250ms timer, so setting it unconditionally would rebuild the
        style on every tick."""
        if sheet != self._chrome:
            self._chrome = sheet
            self._label.setStyleSheet(sheet)

    def set_html(self, html: str):
        self._apply_chrome(self._legacy_chrome)
        self._label.setText(html)
        self._label.adjustSize()
        self.adjustSize()
        self._reposition()

    def set_pixmap(self, pix, tint=None) -> None:
        """Switches the QLabel from HTML mode to image mode. QLabel handles
        the mode flip internally; the next call to set_html() switches back
        cleanly. Used by every painted screen — the graph, and each screen as
        it moves off the RichText engine (no rounded corners, no gradients,
        no per-span opacity, no SVG)."""
        # `tint` recolours the card itself — the match summary uses it so the
        # result reads before any text does. Cached per tint since setStyleSheet
        # re-polishes the widget and this runs on the repaint timer.
        if tint is None:
            self._apply_chrome(self._glass_chrome)
        else:
            sheet = self._tinted_chrome.get(tint)
            if sheet is None:
                sheet = glass.card_stylesheet(tint=tint)
                self._tinted_chrome[tint] = sheet
            self._apply_chrome(sheet)
        self._label.setPixmap(pix)
        self._label.adjustSize()
        self.adjustSize()
        self._reposition()

    def _reposition(self):
        screen_obj = QGuiApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        screen = screen_obj.availableGeometry()
        m, w, h = self.cfg["margin"], self.width(), self.height()
        pos = self.cfg["position"]
        if pos not in VALID_POSITIONS:
            print(f"[overlay] unknown position '{pos}', using top-right", file=sys.stderr)
            pos = "top-right"
        coords = {
            "top-left":     (screen.left() + m,                          screen.top() + m),
            "top-center":   (screen.left() + (screen.width() - w) // 2,  screen.top() + m),
            "top-right":    (screen.right() - w - m,                     screen.top() + m),
            "bottom-left":  (screen.left() + m,                          screen.bottom() - h - m),
            "bottom-right": (screen.right() - w - m,                     screen.bottom() - h - m),
        }
        x, y = coords[pos]
        self.move(x, y)
