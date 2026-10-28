"""The song list table, with the "Album List" view built in.

In Album List view every run of songs from the same album gets one tall
"Album by Artist" cell on the left (a QTableView *span*), showing the art,
album name, artist and album rating. A line separates albums, and albums
with only a few songs get a taller last row so the art always fits.
The same table is used for the plain song list; album mode just switches
those extras on.
"""

from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QDrag, QFont, QPainter, QPainterPath, QPalette, QPen,
                           QPixmap)
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QTableView

from luxtunes.core import albums
from luxtunes.ui.art_cache import ART_CACHE
from luxtunes.ui.header_view import TrackHeaderView
from luxtunes.ui.track_model import ALBUM_GROUP_COLUMN, TrackRole

ROW_HEIGHT = 19
ART_SIZE = 90
PAD = 8
# An album block is at least this tall, so the art and text fit.
ALBUM_BLOCK_HEIGHT = ART_SIZE + 2 * PAD


class AlbumCellDelegate(QStyledItemDelegate):
    """Paints the tall album cell: art on the left, name / artist / stars beside it."""

    def paint(self, painter, option, index):
        track = index.data(TrackRole) or {}
        rect = option.rect
        palette = option.palette
        painter.save()
        painter.fillRect(rect, palette.color(QPalette.ColorRole.Base))   # never highlighted

        art_rect = QRect(rect.x() + PAD, rect.y() + PAD, ART_SIZE, ART_SIZE)
        pixmap = ART_CACHE.get(track, ART_SIZE)
        if pixmap is not None:
            x = art_rect.x() + (ART_SIZE - pixmap.width()) // 2
            y = art_rect.y() + (ART_SIZE - pixmap.height()) // 2
            painter.drawPixmap(x, y, pixmap)
        else:
            painter.fillRect(art_rect, palette.color(QPalette.ColorRole.AlternateBase))
            painter.setPen(palette.color(QPalette.ColorRole.PlaceholderText))
            painter.drawText(art_rect, Qt.AlignmentFlag.AlignCenter, "♪")

        text_x = art_rect.right() + PAD
        width = max(10, rect.right() - text_x - PAD)
        bold = QFont(option.font)
        bold.setBold(True)
        line = option.fontMetrics.height() + 2

        album = track.get("album") or "Unknown Album"
        artist = ("Various Artists" if track.get("compilation")
                  else track.get("album_artist") or track.get("artist") or "Unknown Artist")
        painter.setFont(bold)
        painter.setPen(palette.color(QPalette.ColorRole.Text))
        painter.drawText(QRect(text_x, rect.y() + PAD, width, line),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         painter.fontMetrics().elidedText(album, Qt.TextElideMode.ElideRight, width))
        painter.setFont(option.font)
        painter.drawText(QRect(text_x, rect.y() + PAD + line, width, line),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         option.fontMetrics.elidedText(artist, Qt.TextElideMode.ElideRight, width))
        # Album rating: filled stars for the rating, hollow for the rest.
        stars = round((track.get("album_rating") or 0) / 20)
        dim = QColor(palette.color(QPalette.ColorRole.Text))
        dim.setAlpha(110)
        painter.setPen(dim)
        painter.drawText(QRect(text_x, rect.y() + PAD + 2 * line, width, line),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         "★" * stars + "☆" * (5 - stars))
        painter.restore()


def drag_icon(count: int, ratio: float = 1.0) -> QPixmap:
    """What follows the mouse while several songs are dragged: a small music
    file with a red bubble saying how many, so you can still see where
    they're going."""
    size = 56
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # The file: a page with its top-right corner folded over.
    left, top, width, height, fold = 8.0, 12.0, 30.0, 38.0, 9.0
    page = QPainterPath()
    page.moveTo(left, top)
    page.lineTo(left + width - fold, top)
    page.lineTo(left + width, top + fold)
    page.lineTo(left + width, top + height)
    page.lineTo(left, top + height)
    page.closeSubpath()
    p.setPen(QPen(QColor(90, 90, 90), 1.2))
    p.setBrush(QColor(250, 250, 250))
    p.drawPath(page)
    corner = QPainterPath()
    corner.moveTo(left + width - fold, top)
    corner.lineTo(left + width - fold, top + fold)
    corner.lineTo(left + width, top + fold)
    p.setBrush(QColor(215, 215, 215))
    p.drawPath(corner)
    # The music note.
    note = QFont(QApplication.font())
    note.setPixelSize(22)
    p.setFont(note)
    p.setPen(QColor(60, 60, 60))
    p.drawText(QRectF(left, top + 6, width, height - 6), Qt.AlignmentFlag.AlignCenter, "\u266b")
    # The bubble with the count.
    text = f"{count:,}" if count < 1000 else "999+"
    badge = QFont(QApplication.font())
    badge.setPixelSize(11)
    badge.setBold(True)
    p.setFont(badge)
    text_width = p.fontMetrics().horizontalAdvance(text)
    bubble_w = max(20.0, text_width + 10.0)
    bubble = QRectF(size - bubble_w - 1, 2, bubble_w, 20)
    p.setPen(QPen(QColor(255, 255, 255), 1.5))
    p.setBrush(QColor(220, 50, 47))
    p.drawRoundedRect(bubble, 10, 10)
    p.setPen(QColor(255, 255, 255))
    p.drawText(bubble, Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return pixmap


class TrackTableView(QTableView):
    """The song list. set_album_mode(True) turns on the Album List look."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Column header with a landing marker and drag-off-to-hide (header_view.py).
        self.setHorizontalHeader(TrackHeaderView(Qt.Orientation.Horizontal, self))
        self.album_mode = False
        self._group_starts: dict[int, int] = {}   # first row -> number of rows
        self._tall_rows: list[int] = []
        self._album_delegate = AlbumCellDelegate(self)
        self.setItemDelegateForColumn(ALBUM_GROUP_COLUMN, self._album_delegate)
        self.clicked.connect(self._on_clicked)

    def setModel(self, model):
        super().setModel(model)
        # Rebuild the album cells whenever the list's contents or order change.
        model.modelReset.connect(self.refresh_album_layout)
        model.layoutChanged.connect(self.refresh_album_layout)

    def set_album_mode(self, on: bool):
        self.album_mode = on
        self.refresh_album_layout()

    def group_at(self, row: int) -> tuple[int, int] | None:
        """(first_row, count) of the album run containing `row`."""
        for start, count in self._group_starts.items():
            if start <= row < start + count:
                return start, count
        return None

    def refresh_album_layout(self, *args):
        self.clearSpans()
        header = self.verticalHeader()
        rows = self.model().rowCount() if self.model() is not None else 0
        for row in self._tall_rows:
            if row < rows:
                header.resizeSection(row, ROW_HEIGHT)
        self._tall_rows = []
        self._group_starts = {}
        if not self.album_mode or rows == 0:
            self.viewport().update()
            return
        tracks = self.model().visible_tracks()
        for start, count in albums.consecutive_groups(tracks):
            self._group_starts[start] = count
            if count > 1:
                self.setSpan(start, ALBUM_GROUP_COLUMN, count, 1)
            missing = ALBUM_BLOCK_HEIGHT - count * ROW_HEIGHT
            if missing > 0:     # short album: make its last row taller so the art fits
                last = start + count - 1
                header.resizeSection(last, ROW_HEIGHT + missing)
                self._tall_rows.append(last)
        self.viewport().update()

    def _on_clicked(self, index):
        # Clicking the album cell selects the whole album.
        if self.album_mode and index.column() == ALBUM_GROUP_COLUMN:
            group = self.group_at(index.row())
            if group:
                start, count = group
                model = self.model()
                selection = QItemSelection(model.index(start, 0),
                                           model.index(start + count - 1, model.columnCount() - 1))
                flags = (QItemSelectionModel.SelectionFlag.ClearAndSelect
                         | QItemSelectionModel.SelectionFlag.Rows)
                self.selectionModel().select(selection, flags)
                self.selectionModel().setCurrentIndex(
                    model.index(start, 1), QItemSelectionModel.SelectionFlag.NoUpdate)

    def startDrag(self, supported_actions):
        """One song drags as its row, as before; two or more as a small file
        icon with a count, instead of a picture of every row."""
        indexes = [i for i in self.selectionModel().selectedIndexes()
                   if self.model().flags(i) & Qt.ItemFlag.ItemIsDragEnabled]
        count = len({i.row() for i in indexes})
        if count < 2:
            super().startDrag(supported_actions)
            return
        data = self.model().mimeData(indexes)
        if data is None:
            return
        drag = QDrag(self)
        drag.setMimeData(data)
        drag.setPixmap(drag_icon(count, self.devicePixelRatioF()))
        drag.setHotSpot(QPoint(20, 30))
        action = self.defaultDropAction()
        if action == Qt.DropAction.IgnoreAction:
            action = Qt.DropAction.CopyAction
        drag.exec(supported_actions, action)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.album_mode or not self._group_starts:
            return
        # A thin line above each album, across the whole width.
        painter = QPainter(self.viewport())
        try:
            color = QColor(self.palette().color(QPalette.ColorRole.Text))
            color.setAlpha(60)
            painter.setPen(color)
            width = self.viewport().width()
            first = max(0, self.rowAt(0))
            last = self.rowAt(self.viewport().height() - 1)
            if last < 0:
                last = self.model().rowCount() - 1
            for row in range(first, last + 1):
                if row in self._group_starts and row > 0:
                    y = self.rowViewportPosition(row)
                    painter.drawLine(0, y, width, y)
        finally:
            painter.end()
