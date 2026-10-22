"""Help → LuxTunes Help: short guides, a list of topics beside the chosen one,
with a search box that narrows the list."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTextBrowser,
    QVBoxLayout, QWidget,
)

from luxtunes.ui.help_topics import GUIDES


def _plain(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).casefold()


class HelpWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LuxTunes Help")
        self.setWindowFlag(Qt.WindowType.Window)
        self.resize(820, 540)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.close)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Help")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.topics = QListWidget()
        for title, summary, _ in GUIDES:
            item = QListWidgetItem(title)
            item.setToolTip(summary)
            self.topics.addItem(item)
        self.topics.currentRowChanged.connect(self._show)
        left = QWidget()
        column = QVBoxLayout(left)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.search)
        column.addWidget(self.topics, 1)

        self.page = QTextBrowser()
        self.page.setOpenExternalLinks(False)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.page)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 580])
        layout = QHBoxLayout(self)
        layout.addWidget(splitter)
        self.topics.setCurrentRow(0)

    def _show(self, row: int):
        if not 0 <= row < len(GUIDES):
            self.page.clear()
            return
        title, summary, body = GUIDES[row]
        self.page.setHtml(f"<h2>{title}</h2><p><i>{summary}</i></p>{body}")

    def show_topic(self, title: str):
        for row, (name, _, _) in enumerate(GUIDES):
            if name == title:
                self.topics.setCurrentRow(row)

    def _filter(self, text: str):
        words = text.casefold().split()
        first = None
        for row, (title, summary, body) in enumerate(GUIDES):
            haystack = " ".join((title.casefold(), summary.casefold(), _plain(body)))
            hidden = any(w not in haystack for w in words)
            self.topics.item(row).setHidden(hidden)
            if not hidden and first is None:
                first = row
        current = self.topics.currentRow()
        if first is not None and (current < 0 or self.topics.item(current).isHidden()):
            self.topics.setCurrentRow(first)
        elif first is None:
            self.page.setHtml("<p>No help topics match that.</p>")
