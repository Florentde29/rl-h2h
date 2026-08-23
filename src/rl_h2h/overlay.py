"""Frameless transparent always-on-top widget the renderers paint into."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QGuiApplication
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
        self._label.setFixedWidth(cfg["width"])
        # Every screen is painted now; set_pixmap picks the chrome — plain glass,
        # or a tint when a screen recolours the card (the match summary). Built
        # once here; the tinted variants are cached per tint below since
        # setStyleSheet re-polishes the widget.
        self._chrome = glass.card_stylesheet()
        self._label.setStyleSheet(self._chrome)
        self._tinted_chrome: dict[tuple, str] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self.resize(cfg["width"], 100)
        # Screen the card is currently anchored to, for follow_cursor_screen().
        self._screen_key: str | None = None
        self.hide()

    def _apply_chrome(self, sheet: str) -> None:
        """Swap the card stylesheet, skipping the restyle when unchanged.

        Qt re-polishes the whole widget on setStyleSheet, and this runs on the
        repaint path, so setting it unconditionally would rebuild the style on
        every tick."""
        if sheet != self._chrome:
            self._chrome = sheet
            self._label.setStyleSheet(sheet)

    def set_pixmap(self, pix, tint=None) -> None:
        if tint is None:
            self._apply_chrome(self._chrome)
        else:
            key = tuple(tint)
            sheet = self._tinted_chrome.get(key)
            if sheet is None:
                sheet = glass.card_stylesheet(tint=tint)
                self._tinted_chrome[key] = sheet
            self._apply_chrome(sheet)
        self._label.setPixmap(pix)
        self._label.adjustSize()
        self.adjustSize()
        self._reposition()

    def follow_cursor_screen(self) -> None:
        """Re-anchor to the screen under the cursor — but only when it changed.

        update_overlay runs several times a second while a view is held;
        unconditionally calling _reposition() each tick made Qt process a
        move event per tick even when nothing moved. Cheap screen-name check
        keeps multi-monitor behaviour at zero steady-state cost."""
        screen_obj = QGuiApplication.screenAt(QCursor.pos())
        key = screen_obj.name() if screen_obj is not None else None
        if key != self._screen_key:
            self._reposition()

    def _reposition(self):
        screen_obj = QGuiApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self._screen_key = screen_obj.name()
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
