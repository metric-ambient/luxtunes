"""The song list table, with the "Album List" view built in.

In Album List view every run of songs from the same album gets one tall
"Album by Artist" cell on the left (a QTableView *span*), showing the art,
album name, artist and album rating. A line separates albums, and albums
with only a few songs get a taller last row so the art always fits.
The same table is used for the plain song list; album mode just switches
those extras on.
"""

from __future__ import annotations

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import QStyledItemDelegate, QTableView

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
