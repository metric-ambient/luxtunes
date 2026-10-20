"""Cover Flow: album covers in a row on black, the centred one facing
you and the others turned sideways, with reflections underneath.

- Scroll with the scrollbar, the mouse wheel or the arrow keys, or click a
  side cover: it glides to the centre and `album_centered` is sent, so the
  song list below can highlight that album's first song.
- Double-click the centre cover to play the album.
- center_on_track() moves it quietly (no signal) when you pick a song in the list.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPolygonF, QTransform
from PySide6.QtWidgets import QScrollBar, QVBoxLayout, QWidget

from luxtunes.core.albums import album_key
from luxtunes.ui.art_cache import ART_CACHE

ART_LOAD_SIZE = 320       # art is loaded at this size and scaled when drawn
VISIBLE_EACH_SIDE = 7     # how many covers are drawn left and right of the centre
# Proportions (all as fractions of the front cover's size):
SIDE_WIDTH = 0.62         # a turned cover's visible width
SIDE_SPACING = 0.18       # how far apart the stacked side covers are
FIRST_GAP = 0.81          # centre of the first side cover, from the middle (touches the front cover)
OUTER_EDGE = 0.94         # height of a side cover's outer edge (it faces you)
INNER_EDGE = 0.72         # height of its inner edge (it recedes behind the front cover)


def quad_transform(source: QPolygonF, target: QPolygonF) -> QTransform | None:
    """The perspective transform that maps one 4-corner shape onto another.
    (Qt fills in the QTransform it's given; this also copes with a PySide
    version that returns it instead.)"""
    transform = QTransform()
    result = QTransform.quadToQuad(source, target, transform)
    if isinstance(result, QTransform):
        return result
    if isinstance(result, tuple):
        ok, *rest = result
        return next((r for r in rest if isinstance(r, QTransform)), transform) if ok else None
    return transform if result else None


class CoverFlow(QWidget):
    album_centered = Signal(dict)    # the album the user moved to
    album_activated = Signal(dict)   # double-clicked: play it

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("coverFlow")
        self.setMinimumHeight(180)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.albums: list[dict] = []
        self._index_of_key: dict = {}
        self._pos = 0.0                  # current (animated) position, in albums
        self._target = 0
        self._hit_boxes: list[tuple[QPolygonF, int]] = []

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_animate)

        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self.scrollbar.setObjectName("coverFlowScroll")
        self.scrollbar.setPageStep(5)
        self.scrollbar.valueChanged.connect(lambda v: self._go_to(v, user=True))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 0, 60, 10)
        layout.addStretch(1)
        layout.addWidget(self.scrollbar)

    # ---- Data -------------------------------------------------------------------

    def set_albums(self, albums: list[dict]):
        """Show these albums (in this order), keeping the current one centred if possible."""
        current_key = self.albums[self._target]["key"] if self.albums else None
        self.albums = albums
        self._index_of_key = {a["key"]: i for i, a in enumerate(albums)}
        index = self._index_of_key.get(current_key, 0)
        self._target = index
        self._pos = float(index)
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, max(0, len(albums) - 1))
        self.scrollbar.setValue(index)
        self.scrollbar.blockSignals(False)
        self.update()

    def center_on_track(self, track: dict | None):
        """Glide to the track's album without sending album_centered."""
        if not track or not self.albums:
            return
        index = self._index_of_key.get(album_key(track))
        if index is not None and index != self._target:
            self._go_to(index, user=False)

    # ---- Movement ---------------------------------------------------------------

    def _go_to(self, index: int, user: bool):
        if not self.albums:
            return
        index = max(0, min(len(self.albums) - 1, index))
        self._target = index
        self.scrollbar.blockSignals(True)
        self.scrollbar.setValue(index)
        self.scrollbar.blockSignals(False)
        self._animation.stop()
        self._animation.setStartValue(float(self._pos))
        self._animation.setEndValue(float(index))
        self._animation.start()
        if user:
            self.album_centered.emit(self.albums[index])

    def _on_animate(self, value):
        self._pos = float(value)
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            self._go_to(self._target + (-1 if delta > 0 else 1), user=True)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            self._go_to(self._target - 1, user=True)
        elif event.key() == Qt.Key.Key_Right:
            self._go_to(self._target + 1, user=True)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.albums:
            self.album_activated.emit(self.albums[self._target])
        else:
            super().keyPressEvent(event)

    def _album_at(self, pos) -> int | None:
        for polygon, index in reversed(self._hit_boxes):   # front-most first
            if polygon.containsPoint(QPointF(pos), Qt.FillRule.OddEvenFill):
                return index
        return None

    def mousePressEvent(self, event):
        index = self._album_at(event.position())
        if index is not None and index != self._target:
            self._go_to(index, user=True)
        self.setFocus()

    def mouseDoubleClickEvent(self, event):
        index = self._album_at(event.position())
        if index is not None and index == self._target:
            self.album_activated.emit(self.albums[index])

    # ---- Drawing ----------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()   # always close it, or Qt warns "endPaint() called with active painter"

    def _paint(self, painter):
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("black"))
        self._hit_boxes = []
        if not self.albums:
            painter.setPen(QColor("#777"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No albums")
            return

        bar_space = self.scrollbar.height() + 24
        size = max(60.0, min((self.height() - bar_space) * 0.72, self.width() * 0.3))
        cx = self.width() / 2
        top = 18.0
        floor = top + size                    # where covers stand; reflections go below

        first = max(0, int(self._pos) - VISIBLE_EACH_SIDE)
        last = min(len(self.albums) - 1, int(self._pos) + VISIBLE_EACH_SIDE + 1)
        # Draw the farthest covers first so nearer ones overlap them.
        order = sorted(range(first, last + 1), key=lambda i: -abs(i - self._pos))
        for i in order:
            self._draw_cover(painter, i, i - self._pos, cx, top, size)

        # Fade the reflections into the black floor.
        fade = QLinearGradient(0, floor, 0, floor + size * 0.45)
        fade.setColorAt(0.0, QColor(0, 0, 0, 120))
        fade.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(QRectF(0, floor, self.width(), self.height() - floor), fade)

        # Title and artist of the centred album.
        album = self.albums[self._target]
        bold = QFont(self.font())
        bold.setBold(True)
        bold.setPointSizeF(bold.pointSizeF() + 1)
        painter.setFont(bold)
        painter.setPen(QColor("white"))
        text_top = floor + 6
        painter.drawText(QRectF(0, text_top, self.width(), 20), Qt.AlignmentFlag.AlignHCenter,
                         album["title"])
        painter.setFont(self.font())
        painter.setPen(QColor("#bbbbbb"))
        painter.drawText(QRectF(0, text_top + 20, self.width(), 18), Qt.AlignmentFlag.AlignHCenter,
                         album["subtitle"])

    def _draw_cover(self, painter, index, offset, cx, top, size):
        """offset = how far this cover is from the centre, in albums (can be fractional)."""
        distance = abs(offset)
        side = 1 if offset > 0 else -1
        turn = min(1.0, distance)                    # 0 = facing you, 1 = fully turned
        width = size * (1 - turn * (1 - SIDE_WIDTH))
        if distance <= 1:
            centre_x = cx + side * distance * size * FIRST_GAP
        else:
            centre_x = cx + side * (size * FIRST_GAP + (distance - 1) * size * SIDE_SPACING)
        # Side covers face inward: the OUTER edge is toward you and
        # the INNER edge (next to the front cover) recedes. Both shrink evenly
        # above and below the middle line, as real perspective does.
        outer_h = size * (1 - turn * (1 - OUTER_EDGE))
        inner_h = size * (1 - turn * (1 - INNER_EDGE))
        middle = top + size / 2
        left_x, right_x = centre_x - width / 2, centre_x + width / 2
        if side > 0:   # on the right: the left edge is the inner one
            left_h, right_h = inner_h, outer_h
        else:          # on the left: the right edge is the inner one
            left_h, right_h = outer_h, inner_h
        quad = QPolygonF([QPointF(left_x, middle - left_h / 2), QPointF(right_x, middle - right_h / 2),
                          QPointF(right_x, middle + right_h / 2), QPointF(left_x, middle + left_h / 2)])

        album = self.albums[index]
        pixmap = ART_CACHE.get(album.get("art_track"), ART_LOAD_SIZE)
        source = QRectF(0, 0, pixmap.width(), pixmap.height()) if pixmap else QRectF(0, 0, 100, 100)
        source_quad = QPolygonF([source.topLeft(), source.topRight(),
                                 source.bottomRight(), source.bottomLeft()])
        transform = quad_transform(source_quad, quad)
        if transform is None:
            return
        painter.save()
        # Darken covers as they turn away.
        shade = int(150 * min(1.0, distance / 3))
        painter.setTransform(transform)
        if pixmap is not None:
            painter.drawPixmap(0, 0, pixmap)
        else:
            painter.fillRect(source, QColor("#3a3a3a"))
            painter.setPen(QColor("#888"))
            painter.drawText(source, Qt.AlignmentFlag.AlignCenter, "♪")
        if shade:
            painter.fillRect(source, QColor(0, 0, 0, shade))
        # Reflection: the same picture flipped below the floor line.
        flip = QTransform(1, 0, 0, -1, 0, 2 * source.height()) * transform
        painter.setTransform(flip)
        painter.setOpacity(0.35)
        if pixmap is not None:
            painter.drawPixmap(0, 0, pixmap)
        painter.restore()
        self._hit_boxes.append((quad, index))
