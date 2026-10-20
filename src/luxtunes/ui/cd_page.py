"""The page an audio CD shows when you click it under DEVICES:
its tracks with checkboxes, and Import Settings… / Import CD along the
bottom. Names, artists, the album and the genre can be corrected before
importing (the album and genre apply to the whole disc). While importing,
each track shows an orange "importing" mark, then a green tick.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core.cd import Disc
from luxtunes.ui.formatting import format_duration

NUMBER, STATE, CHECK, NAME, TIME, ARTIST, ALBUM, GENRE = range(8)
HEADERS = ["", "", "✓", "Name", "Time", "Artist", "Album", "Genre"]
EDITABLE = (NAME, ARTIST, ALBUM, GENRE)


def state_icon(state: str | None) -> QIcon:
    """importing: orange circle with a wave; done: green tick; failed: red !."""
    if state not in ("importing", "done", "failed"):
        return QIcon()
    pixmap = QPixmap(28, 28)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = {"importing": "#f39a1e", "done": "#46a83a", "failed": "#d23c32"}[state]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawEllipse(QRectF(2, 2, 24, 24))
        pen = QPen(QColor("white"), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if state == "importing":
            path.moveTo(QPointF(7, 14))
            path.cubicTo(QPointF(9, 6), QPointF(12, 6), QPointF(14, 14))
            path.cubicTo(QPointF(16, 22), QPointF(19, 22), QPointF(21, 14))
        elif state == "done":
            path.moveTo(QPointF(8, 14.5))
            path.lineTo(QPointF(12.5, 19))
            path.lineTo(QPointF(20, 9.5))
        else:
            path.moveTo(QPointF(14, 8))
            path.lineTo(QPointF(14, 15.5))
            path.moveTo(QPointF(14, 20))
            path.lineTo(QPointF(14, 20.2))
        painter.drawPath(path)
    finally:
        painter.end()
    return QIcon(pixmap)


class CdPage(QWidget):
    import_clicked = Signal()            # Import CD (or Stop Importing while it runs)
    settings_clicked = Signal()
    lookup_clicked = Signal()            # Get Track Names (asks before going online)
    edited = Signal()                    # a name / artist / album / genre was corrected

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("cdPage")
        self.disc: Disc | None = None
        self._states: dict[int, str] = {}
        self._filling = False
        self._importing = False

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setObjectName("cdTracks")
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.SelectedClicked)
        self.table.verticalHeader().setDefaultSectionSize(19)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        widths = {NUMBER: 30, STATE: 22, CHECK: 22, NAME: 260, TIME: 52, ARTIST: 170, ALBUM: 190,
                  GENRE: 110}
        for column, width in widths.items():
            self.table.setColumnWidth(column, width)
        for column in (NUMBER, STATE, CHECK):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.table.itemChanged.connect(self._on_item_changed)

        bar = QWidget()
        bar.setObjectName("cdBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 6, 12, 6)
        self.status = QLabel()
        self.status.setObjectName("cdStatus")
        self.lookup_button = QPushButton("Get Track Names")
        self.lookup_button.setToolTip("Look up the song names online (LuxTunes asks first)")
        self.settings_button = QPushButton("Import Settings…")
        self.import_button = QPushButton("Import CD")
        for button in (self.lookup_button, self.settings_button, self.import_button):
            button.setObjectName("cdButton")
        self.lookup_button.clicked.connect(self.lookup_clicked)
        self.settings_button.clicked.connect(self.settings_clicked)
        self.import_button.clicked.connect(self.import_clicked)
        row.addWidget(self.status, 1)
        row.addWidget(self.lookup_button)
        row.addWidget(self.settings_button)
        row.addWidget(self.import_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.table, 1)
        layout.addWidget(bar)

    # ---- Filling --------------------------------------------------------------------

    def set_disc(self, disc: Disc | None):
        if disc is not self.disc:
            self._states = {}
        self.disc = disc
        self._filling = True
        try:
            tracks = disc.tracks if disc else []
            self.table.setRowCount(len(tracks))
            for row, track in enumerate(tracks):
                number = QTableWidgetItem(str(track.number))
                number.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                check = QTableWidgetItem()
                check.setCheckState(Qt.CheckState.Checked if track.checked else Qt.CheckState.Unchecked)
                time = QTableWidgetItem(format_duration(track.total_time))
                time.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                cells = {NUMBER: number, STATE: QTableWidgetItem(), CHECK: check,
                         NAME: QTableWidgetItem(track.name),
                         TIME: time, ARTIST: QTableWidgetItem(track.artist or disc.artist),
                         ALBUM: QTableWidgetItem(disc.album if disc.looked_up else ""),
                         GENRE: QTableWidgetItem(disc.genre)}
                for column, item in cells.items():
                    self.table.setItem(row, column, item)
                self._apply_flags(row)
                self._show_state(row)
        finally:
            self._filling = False

    def _apply_flags(self, row: int):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        for column in range(len(HEADERS)):
            item = self.table.item(row, column)
            if item is None:
                continue
            flags = base
            if not self._importing:
                if column == CHECK:
                    flags |= Qt.ItemFlag.ItemIsUserCheckable
                elif column in EDITABLE:
                    flags |= Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)

    def set_track_state(self, number: int, state: str | None):
        """"importing", "done", "failed" or None for the track numbered `number`."""
        if state is None:
            self._states.pop(number, None)
        else:
            self._states[number] = state
        if self.disc is None:
            return
        for row, track in enumerate(self.disc.tracks):
            if track.number == number:
                self._show_state(row)
                if state == "importing":
                    self.table.scrollToItem(self.table.item(row, NAME))

    def _show_state(self, row: int):
        item = self.table.item(row, STATE)
        if item is not None and self.disc is not None:
            item.setIcon(state_icon(self._states.get(self.disc.tracks[row].number)))

    def set_importing(self, importing: bool):
        self._importing = importing
        self.import_button.setText("Stop Importing" if importing else "Import CD")
        self.settings_button.setEnabled(not importing)
        self.lookup_button.setEnabled(not importing)
        self._filling = True
        try:
            for row in range(self.table.rowCount()):
                self._apply_flags(row)
        finally:
            self._filling = False

    def set_status(self, text: str):
        self.status.setText(text)

    # ---- Your corrections ------------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._filling or self.disc is None:
            return
        row, column = item.row(), item.column()
        track = self.disc.tracks[row]
        text = item.text().strip()
        if column == CHECK:
            track.checked = item.checkState() == Qt.CheckState.Checked
            return
        if column == NAME:
            track.name = text or track.name
        elif column == ARTIST:
            track.artist = text
        elif column == ALBUM:
            self.disc.album = text or "Audio CD"
            self.disc.looked_up = self.disc.looked_up or bool(text)
        elif column == GENRE:
            self.disc.genre = text
        if column in (ALBUM, GENRE):            # the whole disc: show it on every row
            disc = self.disc                    # (after this signal: the rows are rebuilt)
            QTimer.singleShot(0, lambda: self.set_disc(disc) if self.disc is disc else None)
        self.edited.emit()
