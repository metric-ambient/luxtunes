"""Grid → Artists → an opened artist: "Featured On", the songs (and their
albums) where that artist is a Featured Artist, listed below the artist's
own albums. Hidden when there are none."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from luxtunes.ui import formatting

MOST_ROWS = 8           # taller than this, the list scrolls


class FeaturedPanel(QWidget):
    play = Signal(list, int)            # the songs, and which one to start with

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("featuredPanel")
        self.heading = QLabel("Featured On")
        self.heading.setObjectName("featuredHeading")
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        self.list = QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setColumnCount(4)
        self.list.setHeaderLabels(["Name", "Artist", "Album", "Time"])
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 0)
        layout.setSpacing(2)
        layout.addWidget(self.heading)
        layout.addWidget(self.list)
        self._tracks: list[dict] = []
        self.hide()

    def show_songs(self, tracks: list[dict]):
        """List these songs, or hide the panel if there are none."""
        self._tracks = list(tracks)
        self.list.clear()
        if not tracks:
            self.hide()
            return
        for track in tracks:
            item = QTreeWidgetItem([formatting.name_with_featured(track), track.get("artist") or "",
                                    track.get("album") or "",
                                    formatting.format_duration(track.get("total_time"))])
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.list.addTopLevelItem(item)
        self.list.setColumnWidth(0, 280)
        self.list.setColumnWidth(1, 180)
        self.list.setColumnWidth(2, 220)
        rows = min(len(tracks), MOST_ROWS)
        row_height = self.list.sizeHintForRow(0) if self.list.topLevelItemCount() else 18
        self.list.setFixedHeight(self.list.header().sizeHint().height() + rows * row_height
                                 + 2 * self.list.frameWidth() + 2)
        self.show()

    def _on_double_click(self, item, _column):
        self.play.emit(self._tracks, self.list.indexOfTopLevelItem(item))
