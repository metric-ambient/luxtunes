"""The page shown when an iPod is selected in the sidebar:

  tabs      Summary · Music · Movies · TV Shows · Podcasts · Books · Photos · Games
  bottom    a capacity bar (Audio / Video / Photos / Other / Free, as it
            will be after syncing) and Sync, or Revert + Apply after changes.

The page only edits a copy of the iPod's settings; Apply saves them and asks
the main window to sync (signal sync_requested).
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core.ipod import device as ipod_device
from luxtunes.core.ipod import plan as ipod_plan
from luxtunes.core.ipod.settings import (
    BIT_RATES, EPISODE_RULES, MOVIE_RULES, PODCAST_RULES, PODCAST_SCOPES, DeviceRecord,
    SyncSettings,
)
from luxtunes.ui import formatting
from luxtunes.ui.art_cache import ART_CACHE
from luxtunes.ui.widgets import playlist_icon

TABS = ("Summary", "Music", "Movies", "TV Shows", "Podcasts", "Books", "Photos", "Games")

COLOURS = {"Audio": "#4b9be6", "Video": "#a95fcd", "Photos": "#ef8650", "Other": "#e7c638",
           "Free": "#5d6574"}


# ---- Small pieces -----------------------------------------------------------------------

def heading(text: str, size: float = 1.45) -> QLabel:
    label = QLabel(text)
    label.setObjectName("deviceHeading")
    font = label.font()
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * size)
    label.setFont(font)
    return label


def box() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("deviceBox")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 12)
    return frame, layout


def checklist(height: int = 300) -> QListWidget:
    widget = QListWidget()
    widget.setObjectName("deviceList")
    widget.setAlternatingRowColors(True)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    widget.setMinimumHeight(height)
    return widget


def add_check(widget: QListWidget, text: str, key, checked: bool, icon=None) -> QListWidgetItem:
    item = QListWidgetItem(text)
    if icon is not None:
        item.setIcon(icon)
    item.setData(Qt.ItemDataRole.UserRole, key)
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    widget.addItem(item)
    return item


def checked_keys(widget: QListWidget) -> list:
    return [widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())
            if widget.item(i).checkState() == Qt.CheckState.Checked]


class IPodPicture(QWidget):
    """A drawn iPod classic (silver or black), for the Summary tab."""

    def __init__(self, colour: str = "black", parent=None):
        super().__init__(parent)
        self.colour = colour
        self.setFixedSize(110, 140)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            dark = self.colour != "silver"
            body = QRectF(18, 4, 70, 118)
            gradient = QLinearGradient(body.topLeft(), body.topRight())
            gradient.setColorAt(0, QColor("#3b3b3d" if dark else "#e9eaec"))
            gradient.setColorAt(0.5, QColor("#626266" if dark else "#fafafa"))
            gradient.setColorAt(1, QColor("#2c2c2e" if dark else "#cfd1d4"))
            painter.setPen(QPen(QColor("#1b1b1c" if dark else "#9a9ca0"), 1.2))
            painter.setBrush(gradient)
            painter.drawRoundedRect(body, 9, 9)
            painter.setBrush(QColor("#121214"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(25, 12, 56, 42), 3, 3)
            screen = QLinearGradient(0, 14, 0, 52)
            screen.setColorAt(0, QColor("#5b5d63"))
            screen.setColorAt(1, QColor("#2a2b30"))
            painter.setBrush(screen)
            painter.drawRect(QRectF(28, 15, 50, 36))
            painter.setBrush(QColor("#1e1e20" if dark else "#f4f4f5"))
            painter.setPen(QPen(QColor("#0e0e0f" if dark else "#b9bbbf"), 1))
            painter.drawEllipse(QRectF(32, 62, 42, 42))
            painter.setBrush(QColor("#3b3b3e" if dark else "#d7d9dc"))
            painter.drawEllipse(QRectF(45, 75, 16, 16))
            painter.setPen(QColor("#9a9a9e"))
            font = painter.font()
            font.setPixelSize(5)
            painter.setFont(font)
            painter.drawText(QRectF(32, 63, 42, 8), Qt.AlignmentFlag.AlignCenter, "MENU")
        finally:
            painter.end()


class CapacityBar(QWidget):
    """The rounded bar at the bottom with its coloured legend."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.capacity = 0
        self.parts: list[tuple[str, int]] = []
        self.setMinimumSize(420, 74)

    def set_parts(self, capacity: int, parts: list[tuple[str, int]]):
        self.capacity, self.parts = capacity, parts
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            bar = QRectF(120, 10, self.width() - 140, 22)
            path = QPainterPath()
            path.addRoundedRect(bar, 6, 6)
            painter.setPen(QPen(QColor("#2e3440"), 1))
            painter.setBrush(QColor(COLOURS["Free"]))
            painter.drawPath(path)
            painter.setClipPath(path)
            x = bar.left()
            total = max(1, self.capacity)
            for name, size in self.parts:
                if name == "Free" or size <= 0:
                    continue
                width = bar.width() * min(size, total) / total
                fill = QLinearGradient(0, bar.top(), 0, bar.bottom())
                colour = QColor(COLOURS[name])
                fill.setColorAt(0, colour.lighter(125))
                fill.setColorAt(1, colour.darker(110))
                painter.fillRect(QRectF(x, bar.top(), width, bar.height()), fill)
                x += width
            painter.setClipping(False)

            painter.setPen(QColor("white"))
            bold = QFont(self.font())
            bold.setBold(True)
            painter.setFont(bold)
            painter.drawText(QRectF(0, 6, 112, 18), Qt.AlignmentFlag.AlignRight, "Capacity")
            painter.setFont(self.font())
            painter.drawText(QRectF(0, 24, 112, 16), Qt.AlignmentFlag.AlignRight,
                             ipod_device.format_gb(self.capacity))
            legend_x = bar.left() + 10
            for name, size in self.parts:
                if size <= 0 and name not in ("Free", "Other"):
                    continue
                painter.setPen(QPen(QColor("#2e3440"), 1))
                painter.setBrush(QColor(COLOURS[name]))
                painter.drawRoundedRect(QRectF(legend_x, 42, 11, 11), 2, 2)
                painter.setPen(QColor("white"))
                painter.setFont(bold)
                painter.drawText(QRectF(legend_x + 15, 38, 80, 18), Qt.AlignmentFlag.AlignLeft, name)
                painter.setFont(self.font())
                painter.drawText(QRectF(legend_x, 55, 80, 16), Qt.AlignmentFlag.AlignLeft,
                                 ipod_device.format_gb(max(0, size)))
                legend_x += 82
        finally:
            painter.end()


# ---- The page --------------------------------------------------------------------------

class DevicePage(QWidget):
    sync_requested = Signal()
    settings_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("devicePage")
        self.conn = None
        self.ipod: ipod_device.IPod | None = None
        self.record: DeviceRecord | None = None
        self.settings = SyncSettings()
        self._space = None
        self._loading = False
        self._photo_count_cache: dict[tuple, int] = {}
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._refresh_plan)

        # Tabs along the top.
        tab_bar = QWidget()
        tab_bar.setObjectName("deviceTabs")
        tabs_row = QHBoxLayout(tab_bar)
        tabs_row.setContentsMargins(4, 2, 4, 2)
        tabs_row.addStretch(1)
        self.tab_group = QButtonGroup(self)
        self.pages = QStackedWidget()
        for i, name in enumerate(TABS):
            button = QToolButton()
            button.setObjectName("deviceTab")
            button.setText(name)
            button.setCheckable(True)
            self.tab_group.addButton(button, i)
            tabs_row.addWidget(button)
        tabs_row.addStretch(1)
        self.tab_group.idClicked.connect(self._show_tab)
        self.tab_group.button(0).setChecked(True)

        builders = (self._summary_tab, self._music_tab, self._movies_tab, self._tv_tab,
                    self._podcasts_tab, self._books_tab, self._photos_tab, self._games_tab)
        for build in builders:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            content = build()
            content.setObjectName("devicePage")
            scroll.setWidget(content)
            self.pages.addWidget(scroll)

        # Capacity bar and buttons along the bottom.
        bottom = QWidget()
        bottom.setObjectName("capacityPanel")
        bottom_row = QHBoxLayout(bottom)
        bottom_row.setContentsMargins(12, 8, 16, 8)
        self.capacity_bar = CapacityBar()
        self.warning = QLabel()
        self.warning.setObjectName("capacityWarning")
        self.warning.hide()
        bar_column = QVBoxLayout()
        bar_column.addWidget(self.capacity_bar)
        bar_column.addWidget(self.warning)
        bottom_row.addLayout(bar_column, 1)
        buttons = QVBoxLayout()
        self.revert_button = QPushButton("Revert")
        self.apply_button = QPushButton("Apply")
        self.sync_button = QPushButton("Sync")
        for button in (self.revert_button, self.apply_button, self.sync_button):
            button.setMinimumWidth(90)
            buttons.addWidget(button)
        self.revert_button.clicked.connect(self.revert)
        self.apply_button.clicked.connect(self.apply)
        self.sync_button.clicked.connect(self.sync_requested.emit)
        bottom_row.addLayout(buttons)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(tab_bar)
        layout.addWidget(self.pages, 1)
        layout.addWidget(bottom)
        self._update_buttons()

    # ---- Loading an iPod -------------------------------------------------------------------

    @property
    def is_android(self) -> bool:
        return getattr(self.ipod, "key", "").startswith("android:")

    def set_device(self, conn, ipod, record: DeviceRecord):
        """Show an iPod (ipod_device.IPod) or an Android device (android.device.Phone)."""
        self.conn, self.ipod, self.record = conn, ipod, record
        from luxtunes.core import smart
        smart.refresh_all(conn)              # the counts include smart playlists' current songs
        self.settings = copy.deepcopy(record.settings)
        self._apply_kind()
        if self.is_android:
            self._measure_phone()
        else:
            self._space = ipod_device.space(ipod)
        self._loading = True
        try:
            self._fill_summary()
            self._fill_lists()
            self._load_controls()
        finally:
            self._loading = False
        self._update_buttons()
        self._refresh_plan()

    def _apply_kind(self):
        """Show only what applies: phones have no iPod picture, version box,
        disk use, manual mode, Photos or Games, but choose a storage."""
        android = self.is_android
        self.device_heading.setText("Phone" if android else "iPod")
        self.picture.setVisible(not android)
        for widget in self._ipod_only:
            widget.setVisible(not android)
        self.summary_labels["Format"].setVisible(not android)
        self.summary_values["Format"].setVisible(not android)
        self.storage_label.setVisible(android)
        self.storage.setVisible(android)
        self.delete_unselected.setVisible(android)
        for i, name in enumerate(TABS):
            if name in ("Photos", "Games"):
                self.tab_group.button(i).setVisible(not android)
        if android and TABS[self.pages.currentIndex()] in ("Photos", "Games"):
            self.tab_group.button(0).setChecked(True)
            self._show_tab(0)
        device_word = "phone" if android else "iPod"
        tip = (f"Songs above this rate (and lossless ones) are converted on their way to the "
               f"{device_word}. The files in your library are never changed.")
        self.convert.setToolTip(tip)
        self.convert_kbps.setToolTip(tip)

    @property
    def dirty(self) -> bool:
        return self.record is not None and self.settings.to_json() != self.record.settings.to_json()

    def revert(self):
        if self.record is not None:
            self.set_device(self.conn, self.ipod, self.record)

    def apply(self):
        """Save the settings, then sync."""
        from luxtunes.core.ipod.settings import save_settings
        if self.record is None:
            return
        save_settings(self.conn, self.record.id, self.settings)
        self.record.settings = copy.deepcopy(self.settings)
        self._update_buttons()
        self.settings_saved.emit()
        self.sync_requested.emit()

    def _changed(self, *_):
        if self._loading:
            return
        self._read_controls()
        self._update_buttons()
        self._refresh_timer.start()

    def _storage_changed(self, *_):
        """Another storage chosen: its free space is what counts now."""
        if self._loading or not self.is_android:
            return
        self._read_controls()
        self._measure_phone()
        capacity = self._space.capacity
        self.summary_values["Capacity"].setText(ipod_device.format_gb(capacity) if capacity
                                                else "Unknown")
        self._changed()

    def _measure_phone(self):
        from luxtunes.core.android import device as android_device
        from luxtunes.core.android import sync as android_sync
        storage = self.ipod.storage(self.settings.android_storage)
        self._space = android_device.space(storage, android_sync.media_bytes(storage))

    def set_syncing(self, key: str | None):
        """The iPod that's syncing (None: none). Its page can't start another
        sync; another iPod's Sync button stays usable and the main window
        explains that it has to wait."""
        self._syncing_key = key
        self._update_buttons()

    def _syncing_this(self) -> bool:
        key = getattr(self, "_syncing_key", None)
        return key is not None and self.ipod is not None and self.ipod.key == key

    def _update_buttons(self):
        dirty = self.dirty
        syncing = self._syncing_this()
        self.sync_button.setText("Syncing…" if syncing else "Sync")
        self.sync_button.setEnabled(not syncing)
        self.apply_button.setEnabled(not syncing)
        self.revert_button.setVisible(dirty)
        self.apply_button.setVisible(dirty)
        self.sync_button.setVisible(not dirty)

    def _show_tab(self, index: int):
        self.pages.setCurrentIndex(index)
        if TABS[index] == "Photos":
            self._update_photo_count()
        self._refresh_plan()

    # ---- Summary -----------------------------------------------------------------------------

    def _summary_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(40, 20, 40, 20)

        self.device_heading = heading("iPod")
        column.addWidget(self.device_heading)
        frame, inner = box()
        row = QHBoxLayout()
        self.picture = IPodPicture()
        row.addStretch(1)
        row.addWidget(self.picture)
        row.addSpacing(40)
        grid = QGridLayout()
        self.summary_values = {}
        self.summary_labels = {}
        for i, name in enumerate(("Name", "Capacity", "Software Version", "Serial Number",
                                  "Model", "Format")):
            label = QLabel(f"<b>{name}:</b>")
            label.setAlignment(Qt.AlignmentFlag.AlignRight)
            value = QLabel()
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(label, i, 0)
            grid.addWidget(value, i, 1)
            self.summary_values[name] = value
            self.summary_labels[name] = label
        # Android devices: which storage the LuxTunes folder goes on.
        self.storage_label = QLabel("<b>Sync to:</b>")
        self.storage_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.storage = QComboBox()
        self.storage.currentIndexChanged.connect(self._storage_changed)
        grid.addWidget(self.storage_label, 6, 0)
        grid.addWidget(self.storage, 6, 1)
        row.addLayout(grid)
        row.addStretch(2)
        inner.addLayout(row)
        column.addWidget(frame)

        column.addSpacing(10)
        version_heading = heading("Version")
        column.addWidget(version_heading)
        frame, inner = box()
        self._ipod_only = [version_heading, frame]       # hidden for Android devices
        grid = QGridLayout()
        update = QPushButton("Check for Update")
        restore = QPushButton("Restore")
        for button in (update, restore):
            button.setEnabled(False)
            button.setMinimumWidth(130)
        grid.addWidget(update, 0, 0)
        grid.addWidget(QLabel("iPod software can only be updated with Apple's own software, not with "
                              "LuxTunes."), 0, 1)
        grid.addWidget(restore, 1, 0)
        grid.addWidget(QLabel("Restoring erases the iPod; that also needs Apple's software (or a format "
                              "tool), not LuxTunes."), 1, 1)
        for i in range(2):
            grid.itemAtPosition(i, 1).widget().setWordWrap(True)
        grid.setColumnStretch(1, 1)
        inner.addLayout(grid)
        column.addWidget(frame)

        column.addSpacing(10)
        column.addWidget(heading("Options"))
        frame, inner = box()
        self.open_on_connect = QCheckBox("Open LuxTunes when this iPod is connected")
        self.open_on_connect.setEnabled(False)
        self.only_checked = QCheckBox("Sync only checked songs and videos")
        convert_row = QHBoxLayout()
        self.convert = QCheckBox("Convert higher bit rate songs to")
        self.convert_kbps = QComboBox()
        for rate in BIT_RATES:
            self.convert_kbps.addItem(f"{rate} kbps", rate)
        convert_row.addWidget(self.convert)
        convert_row.addWidget(self.convert_kbps)
        convert_row.addWidget(QLabel("AAC"))
        convert_row.addStretch(1)
        tip = ("Songs above this rate (and lossless ones) are converted on their way to the "
               "iPod. The files in your library are never changed.")
        for widget in (self.convert, self.convert_kbps):
            widget.setToolTip(tip)
        self.convert.toggled.connect(self._changed)
        self.convert_kbps.currentIndexChanged.connect(self._changed)
        self.manual = QCheckBox("Manually manage music and videos")
        self.disk_use = QCheckBox("Enable disk use")
        self._ipod_only += [self.open_on_connect, self.manual, self.disk_use]
        # Phones only (an iPod's database is always kept to the selection).
        self.delete_unselected = QCheckBox("Delete media not in this selection")
        self.delete_unselected.setToolTip(
            "At each sync, songs and videos you no longer sync are taken off the phone. "
            "Only the LuxTunes folder on the phone is ever cleaned; nothing else is touched.")
        inner.addWidget(self.open_on_connect)
        inner.addWidget(self.only_checked)
        inner.addLayout(convert_row)
        inner.addWidget(self.delete_unselected)
        inner.addWidget(self.manual)
        inner.addWidget(self.disk_use)
        column.addWidget(frame)
        column.addStretch(1)
        for widget in (self.only_checked, self.manual, self.disk_use, self.delete_unselected):
            widget.toggled.connect(self._changed)
        return page

    def _fill_summary(self):
        if self.is_android:
            self._fill_phone_summary()
            return
        ipod = self.ipod
        self.picture.colour = ipod.colour or "black"
        self.picture.update()
        values = {
            "Name": self.record.name or ipod.name,
            "Capacity": ipod_device.format_gb(self._space.capacity),
            "Software Version": ipod.software_version or "Unknown",
            "Serial Number": ipod.serial or "Unknown",
            "Model": ipod.model_name or (ipod.model_number or "Unknown"),
            "Format": ipod.format_name,
        }
        for key, value in values.items():
            self.summary_values[key].setText(value)

    def _fill_phone_summary(self):
        phone = self.ipod
        values = {"Name": self.record.name or phone.name,
                  "Capacity": ipod_device.format_gb(self._space.capacity)
                  if self._space.capacity else "Unknown",
                  "Software Version": "Android",
                  "Serial Number": phone.serial or "Unknown",
                  "Model": phone.model_name}
        for key, value in values.items():
            self.summary_values[key].setText(value)
        self.storage.blockSignals(True)
        self.storage.clear()
        for name in sorted(phone.storages, key=lambda n: (0 if "internal" in n.lower() else 1, n)):
            self.storage.addItem(name, name)
        chosen = self.settings.android_storage
        index = self.storage.findData(chosen) if chosen else 0
        self.storage.setCurrentIndex(max(0, index))
        self.storage.blockSignals(False)

    # ---- Music -------------------------------------------------------------------------------

    def _music_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 16, 30, 16)
        top = QHBoxLayout()
        self.sync_music = QCheckBox("Sync Music")
        self.sync_music.setFont(heading("").font())
        self.music_count = heading("", 1.3)
        self.music_count.setObjectName("deviceCount")
        top.addWidget(self.sync_music)
        top.addStretch(1)
        top.addWidget(self.music_count)
        column.addLayout(top)

        frame, inner = box()
        self.music_all = QRadioButton("Entire music library")
        self.music_selected = QRadioButton("Selected playlists, artists, albums, and genres")
        self.music_videos = QCheckBox("Include music videos")
        self.autofill = QCheckBox("Automatically fill free space with songs")
        for widget in (self.music_all, self.music_selected):
            inner.addWidget(widget)
        inner.addSpacing(8)
        inner.addWidget(self.music_videos)
        inner.addWidget(self.autofill)
        column.addWidget(frame)

        self.music_lists = QWidget()
        grid = QGridLayout(self.music_lists)
        grid.setContentsMargins(0, 10, 0, 0)
        self.playlist_list, self.artist_list = checklist(), checklist()
        self.genre_list, self.album_list = checklist(), checklist()
        for i, (title, widget) in enumerate((("Playlists", self.playlist_list),
                                             ("Artists", self.artist_list),
                                             ("Genres", self.genre_list),
                                             ("Albums", self.album_list))):
            grid.addWidget(heading(title, 1.3), (i // 2) * 2, i % 2)
            grid.addWidget(widget, (i // 2) * 2 + 1, i % 2)
            widget.itemChanged.connect(self._changed)
        column.addWidget(self.music_lists)
        column.addStretch(1)
        for widget in (self.sync_music, self.music_all, self.music_selected, self.music_videos,
                       self.autofill):
            widget.toggled.connect(self._changed)
        return page

    # ---- Movies --------------------------------------------------------------------------------

    def _movies_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 16, 30, 16)
        top = QHBoxLayout()
        self.sync_movies = QCheckBox("Sync Movies")
        self.sync_movies.setFont(heading("").font())
        self.movie_count = heading("", 1.3)
        self.movie_count.setObjectName("deviceCount")
        top.addWidget(self.sync_movies)
        top.addStretch(1)
        top.addWidget(self.movie_count)
        column.addLayout(top)
        frame, inner = box()
        row = QHBoxLayout()
        self.movies_auto = QCheckBox("Automatically include")
        self.movies_rule = QComboBox()
        self.movies_rule.addItems(MOVIE_RULES)
        row.addWidget(self.movies_auto)
        row.addWidget(self.movies_rule)
        row.addWidget(QLabel("movies"))
        row.addStretch(1)
        inner.addLayout(row)
        column.addWidget(frame)
        # The heading and list sit in one area that keeps its space when hidden,
        # so the boxes above don't change size when "all" movies are synced.
        self.movie_area = QWidget()
        area = QVBoxLayout(self.movie_area)
        area.setContentsMargins(0, 0, 0, 0)
        policy = self.movie_area.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.movie_area.setSizePolicy(policy)
        self.movies_heading = heading("Movies", 1.3)
        area.addWidget(self.movies_heading)
        self.movie_list = QListWidget()
        self.movie_list.setObjectName("deviceList")
        self.movie_list.setViewMode(QListView.ViewMode.IconMode)
        self.movie_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.movie_list.setMovement(QListView.Movement.Static)
        self.movie_list.setIconSize(QSize(64, 64))
        self.movie_list.setGridSize(QSize(250, 96))
        self.movie_list.setWordWrap(True)
        self.movie_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.movie_list.setMinimumHeight(380)
        self.movie_list.itemChanged.connect(self._changed)
        area.addWidget(self.movie_list, 1)
        column.addWidget(self.movie_area, 1)
        self.sync_movies.toggled.connect(self._changed)
        self.movies_auto.toggled.connect(self._changed)
        self.movies_rule.currentIndexChanged.connect(self._changed)
        return page

    # ---- Shells: TV Shows, Podcasts, Books, Info, Games ----------------------------------------

    def _shell(self, title: str, count_word: str, rules: list[str] | None, second_combo: list[str],
               lists: tuple[str, ...], note: str):
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 16, 30, 16)
        top = QHBoxLayout()
        check = QCheckBox(title)
        check.setFont(heading("").font())
        count = heading(f"0 {count_word}", 1.3)
        count.setObjectName("deviceCount")
        top.addWidget(check)
        top.addStretch(1)
        top.addWidget(count)
        column.addLayout(top)
        auto = combo = None
        if rules is not None:
            frame, inner = box()
            row = QHBoxLayout()
            auto = QCheckBox("Automatically include")
            combo = QComboBox()
            combo.addItems(rules)
            row.addWidget(auto)
            row.addWidget(combo)
            row.addWidget(QLabel("episodes of"))
            second = QComboBox()
            second.addItems(second_combo)
            row.addWidget(second)
            row.addStretch(1)
            inner.addLayout(row)
            column.addWidget(frame)
        if lists:
            grid = QGridLayout()
            for i, name in enumerate(lists):
                grid.addWidget(heading(name, 1.3), 0, i)
                grid.addWidget(checklist(320), 1, i)
            column.addLayout(grid)
        hint = QLabel(note)
        hint.setObjectName("deviceNote")
        hint.setWordWrap(True)
        column.addWidget(hint)
        column.addStretch(1)
        check.toggled.connect(self._changed)
        if auto is not None:
            auto.toggled.connect(self._changed)
            combo.currentIndexChanged.connect(self._changed)
        return page, check, auto, combo

    def _tv_tab(self) -> QWidget:
        page, self.sync_tv, self.tv_auto, self.tv_rule = self._shell(
            "Sync TV Shows", "episodes", EPISODE_RULES, ["all shows"],
            (), "Episodes are chosen for each show by the rule above. Only .m4v, .mp4 and .mov "
            "videos go to the iPod (it can't play .mkv or .avi).")
        self.tv_count = page.findChild(QLabel, "deviceCount")
        return page

    def _podcasts_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 16, 30, 16)
        top = QHBoxLayout()
        self.sync_podcasts = QCheckBox("Sync Podcasts")
        self.sync_podcasts.setFont(heading("").font())
        self.podcast_count = heading("", 1.3)
        self.podcast_count.setObjectName("deviceCount")
        top.addWidget(self.sync_podcasts)
        top.addStretch(1)
        top.addWidget(self.podcast_count)
        column.addLayout(top)
        frame, inner = box()
        row = QHBoxLayout()
        self.podcasts_auto = QCheckBox("Automatically include")
        self.podcasts_rule = QComboBox()
        self.podcasts_rule.addItems(PODCAST_RULES)
        self.podcasts_scope = QComboBox()
        self.podcasts_scope.addItems(PODCAST_SCOPES)
        row.addWidget(self.podcasts_auto)
        row.addWidget(self.podcasts_rule)
        row.addWidget(QLabel("episodes of"))
        row.addWidget(self.podcasts_scope)
        row.addStretch(1)
        inner.addLayout(row)
        column.addWidget(frame)
        # Left: the shows (ticked = "selected podcasts"). Right: the episodes of
        # the show highlighted on the left; ticked ones always go to the iPod.
        grid = QGridLayout()
        grid.addWidget(heading("Podcasts", 1.3), 0, 0)
        grid.addWidget(heading("Episodes", 1.3), 0, 1)
        self.podcast_shows = checklist(320)
        self.podcast_episodes = checklist(320)
        grid.addWidget(self.podcast_shows, 1, 0)
        grid.addWidget(self.podcast_episodes, 1, 1)
        column.addLayout(grid)
        self.podcasts_empty = QLabel("Your library has no podcasts yet. To make an episode one, "
                                     "choose Get Info → Options → Media Kind → Podcast.")
        self.podcasts_empty.setObjectName("deviceNote")
        self.podcasts_empty.setWordWrap(True)
        column.addWidget(self.podcasts_empty)
        column.addStretch(1)
        self._episodes: list[dict] = []
        self._picked_episodes: set[str] = set()
        self.podcast_shows.currentRowChanged.connect(lambda _: self._fill_episodes())
        self.podcast_shows.itemChanged.connect(self._changed)
        self.podcast_episodes.itemChanged.connect(self._episode_ticked)
        for widget in (self.sync_podcasts, self.podcasts_auto):
            widget.toggled.connect(self._changed)
        for combo in (self.podcasts_rule, self.podcasts_scope):
            combo.currentIndexChanged.connect(self._changed)
        return page

    def _fill_episodes(self):
        """The episodes of the show highlighted on the left."""
        item = self.podcast_shows.currentItem()
        show = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.podcast_episodes.blockSignals(True)
        self.podcast_episodes.clear()
        for e in self._episodes:
            if ipod_plan.podcast_show(e) == show:
                unplayed = "" if e.get("play_count") else "●  "
                add_check(self.podcast_episodes, f"{unplayed}{e.get('name') or 'Untitled'}",
                          e["persistent_id"], e["persistent_id"] in self._picked_episodes)
        self.podcast_episodes.blockSignals(False)

    def _episode_ticked(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._picked_episodes.add(key)
        else:
            self._picked_episodes.discard(key)
        self._changed()

    def _books_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 16, 30, 16)
        top = QHBoxLayout()
        self.sync_books = QCheckBox("Sync Audiobooks")
        self.sync_books.setFont(heading("").font())
        self.book_count = heading("", 1.3)
        self.book_count.setObjectName("deviceCount")
        top.addWidget(self.sync_books)
        top.addStretch(1)
        top.addWidget(self.book_count)
        column.addLayout(top)
        frame, inner = box()
        self.books_all = QRadioButton("All audiobooks")
        self.books_selected = QRadioButton("Selected audiobooks")
        inner.addWidget(self.books_all)
        inner.addWidget(self.books_selected)
        column.addWidget(frame)
        self.book_list = checklist(320)
        self.book_list.setMaximumWidth(520)
        column.addWidget(self.book_list)
        self.books_empty = QLabel("Your library has no audiobooks yet. To make a song one, "
                                  "choose Get Info → Options → Media Kind → Audiobook.")
        self.books_empty.setObjectName("deviceNote")
        self.books_empty.setWordWrap(True)
        column.addWidget(self.books_empty)
        column.addStretch(1)
        self.book_list.itemChanged.connect(self._changed)
        for widget in (self.sync_books, self.books_all, self.books_selected):
            widget.toggled.connect(self._changed)
        return page

    def _games_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 16, 30, 16)
        top = QHBoxLayout()
        self.sync_games = QCheckBox("Sync Games")
        self.sync_games.setFont(heading("").font())
        count = heading("0 games", 1.3)
        count.setObjectName("deviceCount")
        top.addWidget(self.sync_games)
        top.addStretch(1)
        top.addWidget(count)
        column.addLayout(top)
        sort = QComboBox()
        sort.addItems(["Sort by Name", "Sort by Kind"])
        sort.setMaximumWidth(160)
        column.addWidget(sort)
        games = checklist(360)
        games.setMaximumWidth(340)
        column.addWidget(games)
        column.addStretch(1)
        self.sync_games.toggled.connect(self._changed)
        return page

    # ---- Photos --------------------------------------------------------------------------------

    def _photos_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 16, 30, 16)
        top = QHBoxLayout()
        self.sync_photos = QCheckBox("Sync Photos from")
        self.sync_photos.setFont(heading("").font())
        self.photo_source = QComboBox()
        self.photo_source.setMinimumWidth(220)
        self.photo_count = heading("", 1.3)
        self.photo_count.setObjectName("deviceCount")
        top.addWidget(self.sync_photos)
        top.addWidget(self.photo_source)
        top.addStretch(1)
        top.addWidget(self.photo_count)
        column.addLayout(top)
        self.photos_off_note = QLabel("Tick “Sync Photos from” to choose a folder of photos for "
                                      "this iPod.")
        self.photos_off_note.setObjectName("deviceNote")
        column.addWidget(self.photos_off_note)
        frame, inner = box()
        self.photos_all = QRadioButton("All folders")
        self.photos_selected = QRadioButton("Selected folders")
        self.full_resolution = QCheckBox("Include full-resolution photos")
        note = QLabel("Copy full-resolution versions of your photos into the Photos folder on your "
                      "iPod, which you can access after enabling disk use.")
        note.setWordWrap(True)
        note.setContentsMargins(24, 0, 0, 0)
        inner.addWidget(self.photos_all)
        inner.addWidget(self.photos_selected)
        inner.addSpacing(8)
        inner.addWidget(self.full_resolution)
        inner.addWidget(note)
        column.addWidget(frame)
        self.photo_folder_list = checklist(260)
        self.photo_folder_list.itemChanged.connect(self._changed)
        column.addWidget(self.photo_folder_list)
        hint = QLabel("Photos go one way: from this folder to the iPod. Each folder inside it "
                      "becomes a photo album on the iPod.")
        hint.setObjectName("deviceNote")
        hint.setWordWrap(True)
        column.addWidget(hint)
        column.addStretch(1)
        self.photo_source.activated.connect(self._photo_source_chosen)
        for widget in (self.sync_photos, self.photos_all, self.photos_selected, self.full_resolution):
            widget.toggled.connect(self._changed)
        return page

    def _fill_photo_sources(self):
        self.photo_source.blockSignals(True)
        self.photo_source.clear()
        folder = self.settings.photos_folder or os.path.expanduser("~/Pictures")
        self.photo_source.addItem(os.path.basename(folder.rstrip("/")) or folder, folder)
        self.photo_source.setItemData(0, folder, Qt.ItemDataRole.ToolTipRole)
        self.photo_source.insertSeparator(1)
        self.photo_source.addItem("Choose Folder…", None)
        self.photo_source.setCurrentIndex(0)
        self.photo_source.blockSignals(False)
        self.photo_folder_list.blockSignals(True)
        self.photo_folder_list.clear()
        chosen = set(self.settings.photo_folders)
        try:
            subfolders = sorted((e.name for e in os.scandir(folder)
                                 if e.is_dir() and not e.name.startswith(".")), key=str.casefold)
        except OSError:
            subfolders = []
        for name in subfolders:
            add_check(self.photo_folder_list, name, name, name in chosen)
        self.photo_folder_list.blockSignals(False)

    def _photo_source_chosen(self, index: int):
        if self.photo_source.itemData(index) is None:
            folder = QFileDialog.getExistingDirectory(self, "Choose a folder of photos",
                                                      self.settings.photos_folder)
            if folder:
                self.settings.photos_folder = folder
                self.settings.photo_folders = []
            self._fill_photo_sources()
            self._update_photo_count()
            self._changed()

    def _update_photo_count(self):
        s = self.settings
        key = (s.photos_folder, s.photos_mode, tuple(sorted(s.photo_folders)))
        if key not in self._photo_count_cache:
            from luxtunes.core.ipod import photos
            albums = photos.chosen_albums(s.photos_folder, s.photos_mode, s.photo_folders)
            self._photo_count_cache[key] = photos.count(albums)
        total = self._photo_count_cache[key]
        self.photo_count.setText(f"{total:,} photo{'s' if total != 1 else ''}"
                                 if s.sync_photos else "")
        # Say why when nothing would go: no folders ticked, or no photos found.
        if s.sync_photos and not total:
            if s.photos_mode == "selected" and not s.photo_folders:
                why = "Tick the folders below whose photos should go on the iPod."
            else:
                why = (f"No photos found in {s.photos_folder}. LuxTunes looks for JPEG, PNG, "
                       "HEIC, WebP, GIF, BMP and TIFF files in it and the folders inside it.")
            self.photos_off_note.setText(why)
            self.photos_off_note.show()
        elif s.sync_photos:
            self.photos_off_note.hide()

    # ---- Settings <-> controls ---------------------------------------------------------------

    def _fill_lists(self):
        choices = ipod_plan.library_choices(self.conn, phone=self.is_android)
        s = self.settings
        lists = (self.playlist_list, self.artist_list, self.genre_list, self.album_list,
                 self.movie_list, self.podcast_shows, self.book_list)
        for widget in lists:
            widget.blockSignals(True)
            widget.clear()
        for p in choices.playlists:
            add_check(self.playlist_list, p["name"], p["persistent_id"], p["persistent_id"] in s.playlists,
                      playlist_icon(p))
        for name in choices.artists:
            add_check(self.artist_list, name, name, name in s.artists)
        for name in choices.genres:
            add_check(self.genre_list, name, name, name in s.genres)
        for key, label in choices.albums:
            add_check(self.album_list, label, key, key in s.albums)
        for movie in choices.movies:
            minutes = round((movie.get("total_time") or 0) / 60000)
            item = add_check(self.movie_list,
                             f"{movie.get('name')}\n{minutes} minutes\n{formatting.format_size(movie.get('size'))}",
                             movie["persistent_id"], movie["persistent_id"] in s.movies)
            art = ART_CACHE.get(movie, 64)
            if art is not None:
                item.setIcon(QIcon(art))
        for show in choices.shows:
            add_check(self.podcast_shows, show, show, show in s.podcast_shows)
        self._episodes = choices.episodes
        self._picked_episodes = set(s.podcast_episodes)
        for key, label in choices.books:
            add_check(self.book_list, label, key, key in s.books)
        for widget in lists:
            widget.blockSignals(False)
        if self.podcast_shows.count():
            self.podcast_shows.setCurrentRow(0)
        self._fill_episodes()
        self.podcasts_empty.setVisible(not choices.episodes)
        self.books_empty.setVisible(not choices.books)
        self._fill_photo_sources()

    def _load_controls(self):
        s = self.settings
        self.only_checked.setChecked(s.only_checked)
        self.convert.setChecked(s.convert)
        self.convert_kbps.setCurrentIndex(max(0, self.convert_kbps.findData(s.convert_kbps)))
        self.manual.setChecked(s.manual)
        self.disk_use.setChecked(s.disk_use)
        self.delete_unselected.setChecked(s.android_delete_unselected)
        self.sync_music.setChecked(s.sync_music)
        (self.music_all if s.music_mode == "all" else self.music_selected).setChecked(True)
        self.music_videos.setChecked(s.include_music_videos)
        self.autofill.setChecked(s.autofill)
        self.sync_movies.setChecked(s.sync_movies)
        self.movies_auto.setChecked(s.movies_auto)
        self.movies_rule.setCurrentText(s.movies_rule)
        self.sync_tv.setChecked(s.sync_tv)
        self.tv_auto.setChecked(s.tv_auto)
        self.tv_rule.setCurrentText(s.tv_rule)
        self.sync_podcasts.setChecked(s.sync_podcasts)
        self.podcasts_auto.setChecked(s.podcasts_auto)
        self.podcasts_rule.setCurrentText(s.podcasts_rule)
        self.podcasts_scope.setCurrentText(s.podcasts_scope)
        self.sync_books.setChecked(s.sync_books)
        (self.books_selected if s.books_mode == "selected" else self.books_all).setChecked(True)
        self.sync_games.setChecked(s.sync_games)
        self.sync_photos.setChecked(s.sync_photos)
        (self.photos_all if s.photos_mode == "all" else self.photos_selected).setChecked(True)
        self.full_resolution.setChecked(s.full_resolution)
        self._enable_controls()

    def _read_controls(self):
        s = self.settings
        s.only_checked = self.only_checked.isChecked()
        if self.is_android and self.storage.currentData():
            s.android_storage = self.storage.currentData()
        s.convert = self.convert.isChecked()
        s.convert_kbps = self.convert_kbps.currentData() or s.convert_kbps
        s.manual = self.manual.isChecked()
        s.disk_use = self.disk_use.isChecked()
        if self.is_android:
            s.android_delete_unselected = self.delete_unselected.isChecked()
        s.sync_music = self.sync_music.isChecked()
        s.music_mode = "all" if self.music_all.isChecked() else "selected"
        s.include_music_videos = self.music_videos.isChecked()
        s.autofill = self.autofill.isChecked()
        s.playlists = checked_keys(self.playlist_list)
        s.artists = checked_keys(self.artist_list)
        s.genres = checked_keys(self.genre_list)
        s.albums = checked_keys(self.album_list)
        s.sync_movies = self.sync_movies.isChecked()
        s.movies_auto = self.movies_auto.isChecked()
        s.movies_rule = self.movies_rule.currentText()
        s.movies = checked_keys(self.movie_list)
        s.sync_tv = self.sync_tv.isChecked()
        s.tv_auto = self.tv_auto.isChecked()
        s.tv_rule = self.tv_rule.currentText()
        s.sync_podcasts = self.sync_podcasts.isChecked()
        s.podcasts_auto = self.podcasts_auto.isChecked()
        s.podcasts_rule = self.podcasts_rule.currentText()
        s.podcasts_scope = self.podcasts_scope.currentText()
        s.podcast_shows = checked_keys(self.podcast_shows)
        s.podcast_episodes = sorted(self._picked_episodes)
        s.sync_books = self.sync_books.isChecked()
        s.books_mode = "selected" if self.books_selected.isChecked() else "all"
        s.books = checked_keys(self.book_list)
        s.sync_games = self.sync_games.isChecked()
        s.sync_photos = self.sync_photos.isChecked()
        s.photos_mode = "all" if self.photos_all.isChecked() else "selected"
        s.photo_folders = checked_keys(self.photo_folder_list)
        s.full_resolution = self.full_resolution.isChecked()
        self._enable_controls()

    def _enable_controls(self):
        s = self.settings
        self.convert_kbps.setEnabled(s.convert)
        music_on = s.sync_music and not s.manual
        for widget in (self.music_all, self.music_selected, self.music_videos, self.autofill):
            widget.setEnabled(music_on)
        self.autofill.setVisible(s.music_mode == "selected")
        self.music_lists.setVisible(s.music_mode == "selected")
        self.music_lists.setEnabled(music_on)
        movies_on = s.sync_movies and not s.manual
        self.movies_auto.setEnabled(movies_on)
        self.movies_rule.setEnabled(movies_on and s.movies_auto)
        self.movie_list.setEnabled(movies_on)
        # Picking movies one by one only shows when not syncing "all".
        pick_by_hand = not (s.movies_auto and s.movies_rule == "all")
        self.movie_area.setVisible(pick_by_hand)
        for check, auto, rule in ((self.sync_tv, self.tv_auto, self.tv_rule),
                                  (self.sync_podcasts, self.podcasts_auto, self.podcasts_rule)):
            auto.setEnabled(check.isChecked())
            rule.setEnabled(check.isChecked() and auto.isChecked())
        podcasts_on = s.sync_podcasts and not s.manual
        self.podcasts_scope.setEnabled(podcasts_on and s.podcasts_auto)
        # Ticking shows only matters for "selected podcasts".
        self.podcast_shows.setEnabled(podcasts_on and s.podcasts_auto
                                      and s.podcasts_scope == "selected podcasts")
        self.podcast_episodes.setEnabled(podcasts_on)
        books_on = s.sync_books and not s.manual
        for widget in (self.books_all, self.books_selected):
            widget.setEnabled(books_on)
        self.book_list.setVisible(s.books_mode == "selected")
        self.book_list.setEnabled(books_on)
        photos_on = s.sync_photos
        self.sync_photos.setEnabled(True)               # always: it turns the rest on
        if not photos_on:
            self.photos_off_note.setText("Tick “Sync Photos from” to choose a folder of photos "
                                         "for this iPod.")
            self.photos_off_note.show()
        for widget in (self.photo_source, self.photos_all, self.photos_selected, self.full_resolution):
            widget.setEnabled(photos_on)
        self.photo_folder_list.setVisible(s.photos_mode == "selected")
        self.photo_folder_list.setEnabled(photos_on)

    # ---- Counts and the capacity bar -------------------------------------------------------

    def current_plan(self):
        if self.conn is None:
            return None
        free = None
        if self._space is not None:
            free = self._space.capacity - self._space.other - self._space.photos
        kbps_for = None
        if self.is_android:
            from luxtunes.core.android import sync as android_sync
            kbps_for = android_sync.transfer_kbps
        return ipod_plan.build_plan(self.conn, self.settings, free_space=free, seed=0,
                                    kbps_for=kbps_for, phone=self.is_android)

    def _refresh_plan(self):
        if self.conn is None or self._space is None:
            return
        space = self._space
        if self.settings.manual:
            self.music_count.setText(f"{self.ipod.song_count or 0:,} songs")
            self.movie_count.setText("")
            self.tv_count.setText("")
            self.podcast_count.setText("")
            self.book_count.setText("")
            parts = [("Audio", space.media), ("Video", 0), ("Photos", space.photos),
                     ("Other", space.other), ("Free", space.free)]
            self.capacity_bar.set_parts(space.capacity, parts)
            self.warning.hide()
            return
        plan = self.current_plan()
        songs = plan.count("music")
        self.music_count.setText(f"{songs:,} songs" if self.settings.sync_music else "")
        self.movie_count.setText(f"{plan.count('movie'):,} movies" if self.settings.sync_movies else "")
        self.tv_count.setText(f"{plan.count('tvshow'):,} episodes" if self.settings.sync_tv else "")
        self.podcast_count.setText(f"{plan.count('podcast'):,} episodes"
                                   if self.settings.sync_podcasts else "")
        books = len({ipod_plan.book_key(t) for t in plan.tracks
                     if t.get("media_kind") == "audiobook"})
        self.book_count.setText(f"{books:,} audiobook{'s' if books != 1 else ''}"
                                if self.settings.sync_books else "")
        self._update_photo_count()
        free = space.capacity - plan.music_bytes - plan.video_bytes - space.photos - space.other
        parts = [("Audio", plan.music_bytes), ("Video", plan.video_bytes), ("Photos", space.photos),
                 ("Other", space.other), ("Free", max(0, free))]
        self.capacity_bar.set_parts(space.capacity, parts)
        if free < 0:
            self.warning.setText(f"⚠ Not enough space: {ipod_device.format_gb(-free)} too much "
                                 "selected.")
            self.warning.show()
        elif plan.missing:
            self.warning.setText(f"{len(plan.missing):,} selected item(s) can't be found on this "
                                 "computer and will be skipped.")
            self.warning.show()
        else:
            self.warning.hide()
        too_big = free < 0
        syncing = self._syncing_this()
        self.apply_button.setEnabled(not too_big and not syncing)
        self.sync_button.setEnabled(not too_big and not syncing)
