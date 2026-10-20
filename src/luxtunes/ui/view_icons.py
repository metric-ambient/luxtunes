"""The four little view-button icons (List, Album List, Grid, Cover Flow),
drawn in code so they follow the light/dark theme."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

W, H = 18, 13


def _icon(draw, color: QColor) -> QIcon:
    pixmap = QPixmap(W * 2, H * 2)          # drawn at 2x for sharp edges
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(2, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        draw(painter)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def _list(p):
    for y in (1.5, 4.5, 7.5, 10.5):
        p.drawRect(QRectF(2, y, 14, 1.4))


def _album_list(p):
    for y in (1.5, 4.5, 7.5, 10.5):
        p.drawRect(QRectF(2, y, 2.2, 1.6))      # bullet
        p.drawRect(QRectF(6, y, 10, 1.4))       # line


def _grid(p):
    for x in (2, 7.5, 13):
        for y in (1.5, 7.5):
            p.drawRect(QRectF(x, y, 3.5, 4))


def _cover_flow(p):
    p.drawRect(QRectF(5.5, 1, 7, 11))           # the front cover
    for x in (2, 3.6, 13.6, 15.2):              # covers seen edge-on
        p.drawRect(QRectF(x, 2.5, 0.9, 8))


def view_icons(color: QColor) -> list[QIcon]:
    return [_icon(f, color) for f in (_list, _album_list, _grid, _cover_flow)]
