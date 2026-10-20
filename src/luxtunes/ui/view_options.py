"""View → View Options… (Ctrl+J): tick which columns the song list shows.
The same choices as right-clicking a column header, laid out as:
three columns of checkboxes, A-Z going down."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from luxtunes.ui.track_model import COLUMNS, FIXED_COLUMNS

COLUMNS_ACROSS = 3


class ViewOptionsDialog(QDialog):
    def __init__(self, source_name: str, source_icon: QIcon | None, shown: set[int], parent=None):
        super().__init__(parent)
        self.setWindowTitle("View Options")
        title = QHBoxLayout()
        if source_icon is not None and not source_icon.isNull():
            icon = QLabel()
            icon.setPixmap(source_icon.pixmap(QSize(20, 20)))
            title.addWidget(icon)
        name = QLabel(source_name)
        font = name.font()
        font.setBold(True)
        name.setFont(font)
        title.addWidget(name)
        title.addStretch(1)

        choices = sorted(((i, c) for i, c in enumerate(COLUMNS) if i not in FIXED_COLUMNS),
                         key=lambda item: item[1].title.casefold())
        rows = -(-len(choices) // COLUMNS_ACROSS)      # rounded up
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(2)
        self.boxes: dict[int, QCheckBox] = {}
        for n, (index, column) in enumerate(choices):
            box = QCheckBox(column.title)
            box.setChecked(index in shown)
            self.boxes[index] = box
            grid.addWidget(box, n % rows, n // rows)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(title)
        heading = QLabel("Show Columns")
        layout.addWidget(heading)
        layout.addLayout(grid)
        layout.addSpacing(8)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    def shown_columns(self) -> set[int]:
        return {i for i, box in self.boxes.items() if box.isChecked()}
