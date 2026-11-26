"""File → Podcasts…: the podcasts you subscribe to and every episode their
feeds list, to download older ones by hand, refresh, or unsubscribe.

This window never goes online itself: its buttons ask the main window, which
asks you first (unless Preferences → Network allows podcasts without asking).
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core import podcasts
from luxtunes.ui import formatting


def _date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
    except ValueError:
        return iso[:10]


class PodcastWindow(QDialog):
    def __init__(self, conn, window, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.window = window              # the MainWindow: it does the going online
        self.setWindowTitle("Podcasts")
        self.resize(880, 520)

        self.feed_list = QListWidget()
        self.feed_list.currentRowChanged.connect(lambda _: self._show_episodes())
        self.about = QLabel()
        self.about.setWordWrap(True)
        self.about.setObjectName("deviceNote")
        self.episode_list = QTreeWidget()
        self.episode_list.setHeaderLabels(["Episode", "Released", "Length", ""])
        self.episode_list.setRootIsDecorated(False)
        self.episode_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.episode_list.itemSelectionChanged.connect(self._update_buttons)
        self.episode_list.itemDoubleClicked.connect(lambda *_: self._download())
        self.episode_list.setColumnWidth(0, 360)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.about)
        right_layout.addWidget(self.episode_list, 1)
        splitter = QSplitter()
        splitter.addWidget(self.feed_list)
        splitter.addWidget(right)
        splitter.setSizes([260, 620])

        self.subscribe_button = QPushButton("Subscribe…")
        self.unsubscribe_button = QPushButton("Unsubscribe")
        self.refresh_button = QPushButton("Refresh All")
        self.download_button = QPushButton("Download")
        self.status = QLabel()
        self.status.setObjectName("deviceNote")
        self.subscribe_button.clicked.connect(lambda: self.window.subscribe_to_podcast())
        self.unsubscribe_button.clicked.connect(self._unsubscribe)
        self.refresh_button.clicked.connect(lambda: self.window.refresh_podcasts())
        self.download_button.clicked.connect(self._download)
        buttons = QHBoxLayout()
        for button in (self.subscribe_button, self.unsubscribe_button, self.refresh_button):
            buttons.addWidget(button)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.download_button)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addLayout(buttons)
        self.reload()

    # ---- Showing ---------------------------------------------------------------------------

    def _current_feed(self) -> int | None:
        item = self.feed_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def reload(self, select: int | None = None):
        """Show the feeds again (after a refresh or download), keeping the one shown."""
        keep = select if select is not None else self._current_feed()
        self.feed_list.blockSignals(True)
        self.feed_list.clear()
        for info in podcasts.feeds(self.conn):
            item = QListWidgetItem(info["title"] or info["url"])
            item.setData(Qt.ItemDataRole.UserRole, info["id"])
            item.setToolTip(info["url"] + (f"\nLast problem: {info['last_error']}"
                                           if info["last_error"] else ""))
            self.feed_list.addItem(item)
            if info["id"] == keep:
                self.feed_list.setCurrentItem(item)
        if self.feed_list.currentItem() is None and self.feed_list.count():
            self.feed_list.setCurrentRow(0)
        self.feed_list.blockSignals(False)
        self._show_episodes()
        self.set_busy(self.window.podcasts_busy())

    def _show_episodes(self):
        feed_id = self._current_feed()
        self.episode_list.clear()
        info = podcasts.feed(self.conn, feed_id) if feed_id is not None else None
        if info is None:
            self.about.setText("You don't subscribe to any podcasts yet. Click Subscribe… and "
                               "paste a podcast's feed address (its RSS link).")
            self._update_buttons()
            return
        lines = [f"<b>{_html(info['title'] or '')}</b>"
                 + (f" — {_html(info['author'])}" if info["author"] else "")]
        if info["last_error"]:
            lines.append(f"Last refresh didn't work: {_html(info['last_error'])}")
        elif info["last_checked"]:
            lines.append(f"Checked {_date(info['last_checked'])}")
        self.about.setText("<br>".join(lines))
        for e in podcasts.episodes(self.conn, feed_id):
            length = formatting.format_duration(e["duration"]) if e["duration"] else ""
            state = "In Library" if e["track_id"] else ""
            row = QTreeWidgetItem([e["title"] or "Untitled Episode", _date(e["published"]),
                                   length, state])
            row.setData(0, Qt.ItemDataRole.UserRole, e["id"])
            row.setData(0, Qt.ItemDataRole.UserRole + 1, bool(e["track_id"]))
            row.setToolTip(0, (e["description"] or "")[:600])
            self.episode_list.addTopLevelItem(row)
        self._update_buttons()

    def set_busy(self, busy: bool, text: str = ""):
        """While podcasts are being refreshed or downloaded: one job at a time."""
        for button in (self.subscribe_button, self.refresh_button, self.unsubscribe_button):
            button.setEnabled(not busy)
        self.status.setText(text)
        self._update_buttons()

    def _update_buttons(self):
        busy = self.window.podcasts_busy()
        wanted = [i for i in self.episode_list.selectedItems()
                  if not i.data(0, Qt.ItemDataRole.UserRole + 1)]
        self.download_button.setEnabled(bool(wanted) and not busy)
        self.unsubscribe_button.setEnabled(self._current_feed() is not None and not busy)

    # ---- Doing -------------------------------------------------------------------------------

    def _download(self):
        ids = [i.data(0, Qt.ItemDataRole.UserRole) for i in self.episode_list.selectedItems()
               if not i.data(0, Qt.ItemDataRole.UserRole + 1)]
        if ids and not self.window.podcasts_busy():
            self.window.download_episodes(ids)

    def _unsubscribe(self):
        feed_id = self._current_feed()
        info = podcasts.feed(self.conn, feed_id) if feed_id is not None else None
        if info is None:
            return
        answer = QMessageBox.question(
            self, "Unsubscribe",
            f"Unsubscribe from “{info['title']}”?\n\nThe episodes you downloaded stay in your "
            "library.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            podcasts.unsubscribe(self.conn, feed_id)
            self.reload()


class PodcastBar(QWidget):
    """The Podcasts section's buttons, in the media bar beside the playback
    buttons: Subscribe…, Refresh and Podcasts… (as in the File menu). Its label
    isn't shown here: the window shows it under the volume."""
    changed = Signal()            # the label's text changed

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("barGroup")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.label = QLabel(self)
        self.label.hide()
        self.subscribe = self._button("Subscribe…", "Subscribe to a podcast: paste a podcast's feed address "
                                      "(its RSS link) to get its episodes")
        self.refresh = self._button("Refresh", "Check every podcast for new episodes "
                                    "and download them")
        self.manage = self._button("Podcasts…", "Every episode your podcasts offer, to download "
                                   "older ones, or unsubscribe")
        for button in (self.subscribe, self.refresh, self.manage):
            row.addWidget(button)
        self.subscribe.clicked.connect(lambda: window.subscribe_to_podcast())
        self.refresh.clicked.connect(lambda: window.refresh_podcasts())
        self.manage.clicked.connect(lambda: window._show_podcast_window())
        self.hide()

    @staticmethod
    def _button(text: str, tip: str):
        from PySide6.QtWidgets import QToolButton
        button = QToolButton()
        button.setObjectName("dupButton")
        button.setText(text)
        button.setToolTip(tip)
        return button

    def update_state(self, conn):
        """How many podcasts you subscribe to; the buttons wait while one is busy."""
        count = len(podcasts.feeds(conn))
        busy = self.window.podcasts_busy()
        self.label.setText("Updating podcasts…" if busy else
                           f"{count:,} podcast subscription{'s' if count != 1 else ''}" if count
                           else "No podcast subscriptions yet")
        self.subscribe.setEnabled(not busy)
        self.refresh.setEnabled(not busy and count > 0)
        self.changed.emit()


def _html(text: str) -> str:
    import html
    return html.escape(text or "")
