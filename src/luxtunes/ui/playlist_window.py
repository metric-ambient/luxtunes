"""A playlist in its own window ("Open in New Window" on a playlist's right-click menu).

A simple read-only view of the playlist with the same columns as the main
song list. Double-click a song to play it (in that playlist's order).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QTableView, QVBoxLayout, QWidget

from luxtunes.ui.track_model import ALBUM_RATING_COLUMN, RATING_COLUMN, TrackTableModel
from luxtunes.ui.widgets import RatingDelegate


class PlaylistWindow(QWidget):
    play_requested = Signal(list, int)   # tracks, row to start from

    def __init__(self, title: str, tracks: list[dict], header_state=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        close = QShortcut(QKeySequence("Ctrl+W"), self)   # File → Close Window
        close.activated.connect(self.close)
        self.setWindowTitle(title)
        self.resize(900, 500)
        self.model = TrackTableModel(self)
        self.model.set_tracks(tracks, -1)  # playlist order

        self.table = QTableView()
        self.table.setObjectName("tracks")
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(RATING_COLUMN, RatingDelegate(self.table, editable=False))
        self.table.setItemDelegateForColumn(
            ALBUM_RATING_COLUMN, RatingDelegate(self.table, key="album_rating", editable=False))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(19)
        if header_state is not None:
            self.table.horizontalHeader().restoreState(header_state)  # same columns as the main list
        self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.doubleClicked.connect(
            lambda index: self.play_requested.emit(self.model.visible_tracks(), index.row()))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
