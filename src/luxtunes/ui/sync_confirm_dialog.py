"""Asked before a sync takes songs off an iPod or a phone. "Show Details"
opens a sortable list of every song that would be removed, and why."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

COLUMNS = ("Name", "Artist", "Album", "Why")


class SyncConfirmDialog(QDialog):
    def __init__(self, ipod_name: str, adding: int, removals: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sync")
        removing = len(removals)
        icon = QLabel()
        icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxQuestion)
                       .pixmap(48, 48))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        text = QLabel(
            f"<b>This sync will remove {removing:,} song{'s' if removing != 1 else ''} from "
            f"“{ipod_name}” and copy {adding:,} from your library.</b><br><br>"
            f"Songs are removed from “{ipod_name}” only; your LuxTunes library isn't changed.")
        text.setWordWrap(True)
        text.setMinimumWidth(380)
        self.text = text
        top = QHBoxLayout()
        top.addWidget(icon)
        top.addWidget(text, 1)

        self.table = QTableWidget(removing, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        for row, r in enumerate(removals):
            for column, value in enumerate((r.name, r.artist, r.album, r.reason)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)      # by artist
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.resizeColumnsToContents()
        for column in range(3):
            self.table.setColumnWidth(column, min(self.table.columnWidth(column), 220))
        self.table.setMinimumHeight(260)
        self.table.hide()

        self.details = QPushButton("Show Details")
        self.details.setCheckable(True)
        self.details.toggled.connect(self._show_details)
        self.details.setEnabled(removing > 0)
        self.copy = QPushButton("Copy List")
        self.copy.setToolTip("Copy the list, to paste into a text file")
        self.copy.clicked.connect(self._copy_list)
        self.copy.hide()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(self.details)
        bottom.addWidget(self.copy)
        bottom.addStretch(1)
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addLayout(bottom)
        self.setMinimumWidth(480)

    def _show_details(self, shown: bool):
        self.table.setVisible(shown)
        self.copy.setVisible(shown)
        self.details.setText("Hide Details" if shown else "Show Details")
        if shown:
            self.setMinimumHeight(0)
            self.resize(max(self.width(), 760), max(self.height(), 480))
        else:
            self._fit()

    def _fit(self):
        """Without the list: tall enough for all of the wrapped text at this
        width (a wrapped label's height depends on its width, which Qt's
        automatic sizing gets wrong, cutting the text off)."""
        layout = self.layout()
        layout.activate()
        width = max(self.width(), self.minimumWidth())
        height = layout.totalHeightForWidth(width)
        if height < 0:
            height = layout.totalSizeHint().height()
        self.setMinimumHeight(height)
        self.resize(width, height)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.table.isVisible():
            self._fit()

    def list_text(self) -> str:
        rows = ["\t".join(COLUMNS)]
        for row in range(self.table.rowCount()):
            rows.append("\t".join(self.table.item(row, c).text() for c in range(len(COLUMNS))))
        return "\n".join(rows) + "\n"

    def _copy_list(self):
        QApplication.clipboard().setText(self.list_text())
