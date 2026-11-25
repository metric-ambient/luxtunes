"""The page shown when an iPod is selected in the sidebar:

  tabs      Summary · Music · Movies · TV Shows · Podcasts · Audiobooks · Photos · Games
  bottom    a capacity bar (Audio / Video / Photos / Other / Free, as it
            will be after syncing) and Sync, or Revert + Apply after changes.

The page only edits a copy of the iPod's settings; Apply saves them and asks
the main window to sync (signal sync_requested).
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
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
    TV_SCOPES,
    BIT_RATES, EPISODE_RULES, MOVIE_RULES, PODCAST_RULES, PODCAST_SCOPES, DeviceRecord,
    SyncSettings,
)
from luxtunes.ui import formatting
from luxtunes.ui.art_cache import ART_CACHE
from luxtunes.ui.widgets import playlist_icon

TABS = ("Summary", "Music", "Movies", "TV Shows", "Podcasts", "Audiobooks", "Photos", "Games")

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


class CheckList(QListWidget):
    """A list of things to sync, each with a checkbox. Clicking anywhere on a
    row ticks or unticks it (not just the little box), and so does Space.
    click_anywhere False: only the box ticks (a row click does something else,
    like showing a podcast's episodes)."""

    def __init__(self, click_anywhere: bool = True, parent=None):
        super().__init__(parent)
        self.click_anywhere = click_anywhere
        if click_anywhere:
            self.itemClicked.connect(self._toggle)

    @staticmethod
    def _toggle(item: QListWidgetItem):
        if item.flags() & Qt.ItemFlag.ItemIsEnabled:
            item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                               else Qt.CheckState.Checked)

    def keyPressEvent(self, event):
        if self.click_anywhere and event.key() == Qt.Key.Key_Space and self.currentItem():
            self._toggle(self.currentItem())
            return
        super().keyPressEvent(event)


def checklist(height: int = 160, click_anywhere: bool = True) -> CheckList:
    """A list that grows to fill the page (at least `height` pixels tall)."""
    widget = CheckList(click_anywhere)
    widget.setObjectName("deviceList")
    widget.setAlternatingRowColors(True)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    widget.setMinimumHeight(height)
    return widget


def tile_list(height: int = 220) -> CheckList:
    """Movies and audiobooks: tiles with a cover, ticked by clicking anywhere."""
    widget = CheckList()
    widget.setObjectName("deviceList")
    widget.setViewMode(QListView.ViewMode.IconMode)
    widget.setResizeMode(QListView.ResizeMode.Adjust)
    widget.setMovement(QListView.Movement.Static)
    widget.setIconSize(QSize(ART_SIZE, ART_SIZE))
    widget.setGridSize(QSize(260, ART_SIZE + 32))
    widget.setWordWrap(True)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    widget.setMinimumHeight(height)
    return widget


ART_SIZE = 72


def keep_space(widget):
    """A list that keeps its place on the page while hidden (e.g. when "all"
    is chosen), so the boxes above it don't stretch to fill the page."""
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)
    return widget


def add_check(widget: QListWidget, text: str, key, checked: bool, icon=None) -> QListWidgetItem:
    item = QListWidgetItem(text)
    if icon is not None:
        item.setIcon(icon)
    item.setData(Qt.ItemDataRole.UserRole, key)
    # A CheckList ticks on a click anywhere; the plain box-only kind ticks on the box.
    flags = Qt.ItemFlag.ItemIsEnabled
    if not (isinstance(widget, CheckList) and widget.click_anywhere):
        flags |= Qt.ItemFlag.ItemIsUserCheckable
    item.setFlags(flags)
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    widget.addItem(item)
    return item


class ArtIcons(QObject):
    """Covers for a list's items, read in the background (ui/art_cache.py):
    an item gets its picture as soon as it's ready."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waiting: list[tuple[QListWidgetItem, dict]] = []

    def clear(self):
        self._waiting = []

    def set(self, item: QListWidgetItem, track: dict | None):
        if not track:
            return
        art = ART_CACHE.get(track, ART_SIZE, notify=self)
        if art is not None:
            item.setIcon(QIcon(art))
        elif ART_CACHE.loading(track, ART_SIZE):
            self._waiting.append((item, track))

    def update(self):
        """Called by the art cache when a picture has been read."""
        still = []
        for item, track in self._waiting:
            ready, art = ART_CACHE.peek(track, ART_SIZE)
            try:
                if ready and art is not None:
                    item.setIcon(QIcon(art))
                elif not ready:
                    still.append((item, track))
            except RuntimeError:                  # the list was refilled meanwhile
                pass
        self._waiting = still


def matches_filter(item: QListWidgetItem, words: list[str]) -> bool:
    if not words:
        return True
    text = formatting.fold(item.text())
    return all(word in text for word in words)


def checked_keys(widget: QListWidget) -> list:
    return [widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())
            if widget.item(i).checkState() == Qt.CheckState.Checked]


# Body colours for the iPod picture (from the model's colour name).
BODY_COLOURS = {
    "black": "#2c2c2e", "white": "#f2f2f3", "silver": "#d9dbde", "grayscale": "#f2f2f3",
    "color": "#f2f2f3", "stainless": "#cfd1d4", "pink": "#e9a6c3", "blue": "#7cafdd",
    "green": "#a0cf85", "gold": "#d9c58b", "red": "#c8333c", "yellow": "#e7d35b",
    "purple": "#9b83c7", "orange": "#ec9b4b",
}

# How each kind of iPod is drawn, in the 110 x 140 picture: the body, where the
# screen sits and what the controls are (a click wheel, a wheel with buttons
# around it, a row of buttons, a round control pad, a single button, or none).
# Rough outlines only, to tell the kinds apart at a glance.
SHAPES = {
    "wheel":     dict(body=(18, 4, 74, 118, 9), screen=(25, 12, 60, 42), wheel=(55, 83, 21)),
    "early":     dict(body=(18, 4, 74, 118, 9), screen=(25, 12, 60, 42), wheel=(55, 83, 21),
                      wheel_buttons=True),
    "third":     dict(body=(18, 4, 74, 118, 9), screen=(25, 12, 60, 40), wheel=(55, 89, 18),
                      button_row=60),
    "mini":      dict(body=(28, 14, 54, 104, 8), screen=(33, 21, 44, 32), wheel=(55, 88, 17)),
    "nano_tall": dict(body=(33, 6, 44, 116, 6), screen=(37, 12, 36, 32), wheel=(55, 90, 16)),
    "nano_4":    dict(body=(33, 6, 44, 116, 6), screen=(37, 12, 36, 44), wheel=(55, 96, 15)),
    "nano_5":    dict(body=(33, 6, 44, 116, 6), screen=(37, 11, 36, 50), wheel=(55, 98, 14)),
    "nano_3":    dict(body=(22, 22, 66, 90, 9), screen=(29, 29, 52, 38), wheel=(55, 92, 15)),
    "nano_6":    dict(body=(25, 30, 60, 66, 7), screen=(29, 34, 52, 58)),
    "nano_7":    dict(body=(34, 16, 42, 104, 6), screen=(37, 23, 36, 76), button=(55, 111, 4)),
    "shuffle_1": dict(body=(42, 18, 26, 100, 6), pad=(55, 50, 10)),
    "shuffle_2": dict(body=(24, 40, 62, 50, 6), pad=(55, 65, 16)),
    "shuffle_3": dict(body=(46, 34, 18, 72, 5)),
    "shuffle_4": dict(body=(27, 38, 56, 60, 7), pad=(55, 68, 17)),
    "slab":      dict(body=(28, 12, 54, 112, 10), screen=(32, 23, 46, 82), button=(55, 115, 4)),
}
FAMILY_SHAPES = {
    "first": "early", "second": "early", "third": "third", "mini": "mini", "mini_1": "mini",
    "mini_2": "mini", "nano_1": "nano_tall", "nano_2": "nano_tall", "nano_4": "nano_4",
    "nano_5": "nano_5", "nano_3": "nano_3", "nano_6": "nano_6", "nano_7": "nano_7",
    "shuffle_1": "shuffle_1", "shuffle_2": "shuffle_2", "shuffle_3": "shuffle_3",
    "shuffle_4": "shuffle_4", "touch": "slab", "iphone": "slab", "ipad": "slab",
}


class IPodPicture(QWidget):
    """A drawn iPod for the Summary tab, shaped like the kind of iPod it is
    (classic, mini, nano, shuffle...) and in its colour."""

    def __init__(self, colour: str = "black", family: str = "", parent=None):
        super().__init__(parent)
        self.colour = colour
        self.family = family
        self.setFixedSize(110, 140)

    @property
    def default_colour(self) -> str:
        """When the model's colour isn't known: the colour most of its kind came in."""
        if self.family.startswith(("mini", "nano", "shuffle")):
            return "silver"
        if self.family in ("first", "second", "third", "fourth", "photo"):
            return "white"
        return "black"

    @property
    def shape(self) -> dict:
        return SHAPES[FAMILY_SHAPES.get(self.family, "wheel")]

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            shape = self.shape
            base = QColor(BODY_COLOURS.get((self.colour or "").lower(),
                                           BODY_COLOURS[self.default_colour]))
            dark = base.lightness() < 110
            x, y, w, h, radius = shape["body"]
            body = QRectF(x, y, w, h)
            gradient = QLinearGradient(body.topLeft(), body.topRight())
            gradient.setColorAt(0, base.lighter(112) if dark else base.darker(104))
            gradient.setColorAt(0.5, base.lighter(150) if dark else base.lighter(106))
            gradient.setColorAt(1, base.darker(115))
            painter.setPen(QPen(base.darker(170 if dark else 140), 1.2))
            painter.setBrush(gradient)
            painter.drawRoundedRect(body, radius, radius)

            if "screen" in shape:
                sx, sy, sw, sh = shape["screen"]
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#121214"))
                painter.drawRoundedRect(QRectF(sx, sy, sw, sh), 3, 3)
                glass = QLinearGradient(0, sy, 0, sy + sh)
                glass.setColorAt(0, QColor("#5b5d63"))
                glass.setColorAt(1, QColor("#2a2b30"))
                painter.setBrush(glass)
                painter.drawRect(QRectF(sx + 3, sy + 3, sw - 6, sh - 6))

            control = QColor("#1e1e20") if dark else QColor("#f6f6f7")
            edge = QPen(QColor("#0e0e0f") if dark else base.darker(125), 1)
            if "wheel" in shape:
                cx, cy, r = shape["wheel"]
                painter.setBrush(control)
                painter.setPen(edge)
                painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
                painter.setBrush(base.darker(108) if not dark else QColor("#3b3b3e"))
                painter.drawEllipse(QRectF(cx - r * 0.38, cy - r * 0.38, r * 0.76, r * 0.76))
                if shape.get("wheel_buttons"):        # buttons around the wheel
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QRectF(cx - r - 4, cy - r - 4, 2 * r + 8, 2 * r + 8))
            if "button_row" in shape:                 # a row of four buttons
                painter.setBrush(control)
                painter.setPen(edge)
                for i in range(4):
                    painter.drawEllipse(QRectF(29 + i * 14, shape["button_row"], 10, 10))
            if "pad" in shape:                        # a round control pad
                cx, cy, r = shape["pad"]
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(base.darker(150), 2.4))
                painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
                painter.setPen(edge)
                painter.setBrush(base.darker(115))
                painter.drawEllipse(QRectF(cx - r * 0.35, cy - r * 0.35, r * 0.7, r * 0.7))
            if "button" in shape:                     # a single round button
                cx, cy, r = shape["button"]
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(base.darker(150), 1.2))
                painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
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
    stop_requested = Signal()          # Stop Sync (the Sync button while this device syncs)
    settings_saved = Signal()
    # What the search box searches on the tab shown ("Search Audiobooks"), or ""
    # when the tab has nothing to search (Summary, Games).
    search_hint_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("devicePage")
        self.conn = None
        self.ipod: ipod_device.IPod | None = None
        self.record: DeviceRecord | None = None
        self.settings = SyncSettings()
        self._space = None
        self._spaces: dict[str, ipod_device.Space] = {}   # last measured, by iPod
        self._loading = False
        self._photo_count_cache: dict[tuple, tuple[int, int]] = {}   # -> (photos, file bytes)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._refresh_plan)
        self._filter_words: list[str] = []
        self.book_art = ArtIcons(self)
        self.movie_art = ArtIcons(self)

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
        self.sync_button.clicked.connect(self._on_sync_button)
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
            self._measure_ipod(ipod)
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
        others = [] if android else list(getattr(self.ipod, "other_firmware", None) or [])
        self.summary_labels["Also Runs"].setVisible(bool(others))
        self.summary_values["Also Runs"].setVisible(bool(others))
        self.rockbox.setVisible("Rockbox" in others)
        self.storage_label.setVisible(android)
        self.storage.setVisible(android)
        self.delete_unselected.setVisible(android)
        hidden = self.hidden_tabs()
        for i, name in enumerate(TABS):
            self.tab_group.button(i).setVisible(name not in hidden)
        if TABS[self.pages.currentIndex()] in hidden:
            self.tab_group.button(0).setChecked(True)
            self._show_tab(0)
        self._show_model_note()
        device_word = "phone" if android else "iPod"
        tip = (f"Songs above this rate (and lossless ones) are converted on their way to the "
               f"{device_word}. The files in your library are never changed.")
        if not android:
            tip = ("Songs above this rate are converted to it on their way to the iPod, and "
                   "WAV and AIFF songs as chosen below. The files in your library are never "
                   "changed.")
        self.convert.setToolTip(tip)
        self.convert_kbps.setToolTip(tip)
        # Older iPods (1st/2nd generation, shuffle 1st/2nd) don't play Apple Lossless.
        alac_ok = android or getattr(self.ipod, "supports_alac", True)
        item = self.lossless_format.model().item(1)
        if item is not None:
            item.setEnabled(alac_ok)
            item.setToolTip("" if alac_ok else "This iPod doesn't play Apple Lossless.")
        if not alac_ok and self.lossless_format.currentIndex() == 1:
            self.lossless_format.setCurrentIndex(0)

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
        """Another storage chosen: remembered for this phone straight away (no
        Apply needed), and its free space is what counts now."""
        if self._loading or not self.is_android or not self.storage.currentData():
            return
        from luxtunes.core.android.device import storage_kind
        from luxtunes.core.ipod.settings import save_settings
        name = self.storage.currentData()
        for s in (self.settings, self.record.settings):
            s.android_storage, s.android_storage_kind = name, storage_kind(name)
        save_settings(self.conn, self.record.id, self.record.settings)
        self._measure_phone()
        capacity = self._space.capacity
        self.summary_values["Capacity"].setText(ipod_device.format_gb(capacity) if capacity
                                                else "Unknown")
        self._changed()

    def _measure_ipod(self, ipod):
        """Adding up the iPod's files over USB takes a while, so it happens on
        another thread. Until it's done the page shows the last measure, or
        an empty bar the first time."""
        from luxtunes.ui import background
        self._space = self._spaces.get(ipod.key)

        def measured(space):
            if isinstance(space, Exception):
                return                          # (unplugged meanwhile, ...)
            self._spaces[ipod.key] = space
            if self.ipod is ipod:               # still the iPod on show
                self._space = space
                self._fill_summary()
                self._refresh_plan()
        background.run(lambda: ipod_device.space(ipod), measured, self)

    def _capacity(self) -> int:
        """The device's size, even before its contents are measured."""
        if self._space is not None:
            return self._space.capacity
        try:
            import shutil
            return shutil.disk_usage(self.ipod.mountpoint).total
        except (OSError, AttributeError):
            return 0

    def _measure_phone(self):
        from luxtunes.core.android import device as android_device
        from luxtunes.core.android import sync as android_sync
        storage = self.ipod.storage(self.settings.android_storage,
                                    self.settings.android_storage_kind)
        self._space = android_device.space(storage, android_sync.media_bytes(storage))

    def set_syncing(self, key: str | None):
        """The iPod that's syncing (None: none). Its page can't start another
        sync; another iPod's Sync button stays usable and the main window
        explains that it has to wait."""
        if key != getattr(self, "_syncing_key", None):
            self._stopping = False                  # a new sync (or none): not stopping
        self._syncing_key = key
        self._update_buttons()

    def _on_sync_button(self):
        """Sync, or while this device is syncing, Stop Sync."""
        if self._syncing_this():
            self._stopping = True
            self._update_buttons()
            self.stop_requested.emit()
        else:
            self.sync_requested.emit()

    def _syncing_this(self) -> bool:
        key = getattr(self, "_syncing_key", None)
        return key is not None and self.ipod is not None and self.ipod.key == key

    def _update_buttons(self):
        dirty = self.dirty
        syncing = self._syncing_this()
        unsupported = bool(getattr(self.ipod, "unsupported", "")) and not self.is_android
        stopping = syncing and getattr(self, "_stopping", False)
        self.sync_button.setText("Stopping…" if stopping else "Stop Sync" if syncing else "Sync")
        stop_tip = ("Stop now (what's copied so far stays, and the next sync carries on)"
                    if self.is_android else
                    "Stop after the song being copied (what's copied so far stays)")
        self.sync_button.setToolTip(stop_tip if syncing else "")
        self.sync_button.setEnabled((syncing and not stopping) or (not syncing and not unsupported))
        self.apply_button.setEnabled(not syncing)
        self.revert_button.setVisible(dirty)
        self.apply_button.setVisible(dirty)
        self.sync_button.setVisible(not dirty)

    def _show_tab(self, index: int):
        self.pages.setCurrentIndex(index)
        if TABS[index] == "Photos":
            self._update_photo_count()
        self._apply_filter()
        self.search_hint_changed.emit(self.search_hint())
        self._refresh_plan()

    # ---- Searching the tab shown ---------------------------------------------------------------

    def _searchable(self) -> dict[str, list[QListWidget]]:
        return {"Music": [self.playlist_list, self.artist_list, self.genre_list, self.album_list],
                "Movies": [self.movie_list],
                "TV Shows": [self.tv_shows, self.tv_episodes],
                "Podcasts": [self.podcast_shows, self.podcast_episodes],
                "Audiobooks": [self.book_list],
                "Photos": [self.photo_folder_list]}

    def search_hint(self) -> str:
        """The search box's placeholder for the tab shown, or "" if it has no lists."""
        name = TABS[self.pages.currentIndex()]
        return f"Search {name}" if name in self._searchable() else ""

    def set_filter(self, text: str):
        """The search box's text: only matching rows show (accents and capitals
        don't matter). Ticked rows that are hidden stay ticked."""
        self._filter_words = formatting.search_words(text or "")
        self._apply_filter()

    def _apply_filter(self):
        lists = self._searchable().get(TABS[self.pages.currentIndex()], [])
        for widget in lists:
            for i in range(widget.count()):
                item = widget.item(i)
                item.setHidden(not matches_filter(item, self._filter_words))

    # ---- Summary -----------------------------------------------------------------------------

    def _summary_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(40, 20, 40, 20)

        self.device_heading = heading("iPod")
        column.addWidget(self.device_heading)
        # What this model can't do (or why it can't be synced), when that applies.
        self.model_note = QLabel()
        self.model_note.setObjectName("deviceNote")
        self.model_note.setWordWrap(True)
        self.model_note.hide()
        column.addWidget(self.model_note)
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
                                  "Model", "Format", "Also Runs")):
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
        grid.addWidget(self.storage_label, 7, 0)
        grid.addWidget(self.storage, 7, 1)
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
        # iPods: what lossless songs (FLAC above all) become, and a word on it.
        lossless_row = QHBoxLayout()
        self.lossless_label = QLabel("Sync lossless songs (FLAC, WAV, AIFF) as")
        self.lossless_format = QComboBox()
        self.lossless_format.addItem("AAC", "aac")
        self.lossless_format.addItem("Apple Lossless", "alac")
        self.lossless_format.setToolTip(
            "AAC: the rate above (256 kbps when that box is off), small files. Apple "
            "Lossless: full quality, about 4–5 times bigger. High-resolution files become "
            "16-bit, 44.1 or 48 kHz, which the iPod plays. Your library's files are never changed.")
        self.lossless_format.currentIndexChanged.connect(self._changed)
        lossless_row.addWidget(self.lossless_label)
        lossless_row.addWidget(self.lossless_format)
        lossless_row.addStretch(1)
        self.convert_note = QLabel("Songs the iPod can't play as they are (FLAC, Ogg, Opus) are "
                                   "converted while they sync; your library keeps the originals.")
        self.convert_note.setObjectName("deviceNote")
        self.convert_note.setWordWrap(True)
        self.manual = QCheckBox("Manually manage music and videos")
        self.disk_use = QCheckBox("Enable disk use")
        self._ipod_only += [self.manual, self.disk_use, self.lossless_label, self.lossless_format,
                            self.convert_note]
        # iPods that also run Rockbox (shown only for those).
        self.rockbox = QCheckBox("Make songs and playlists work in Rockbox too")
        self.rockbox.setToolTip(
            "This iPod also runs Rockbox, which reads song details and covers from the song files "
            "rather than from Apple's song list. At each sync, LuxTunes also writes your playlists "
            "into the iPod's Playlists folder, gives the iPod's copies of your songs their details "
            "and a cover Rockbox can show, and reads plays from Rockbox's scrobbler log. The files "
            "in your library are never changed.")
        # Phones only (an iPod's database is always kept to the selection).
        self.delete_unselected = QCheckBox("Delete media not in this selection")
        self.delete_unselected.setToolTip(
            "At each sync, songs and videos you no longer sync are taken off the phone. "
            "Only the LuxTunes folder on the phone is ever cleaned; nothing else is touched.")
        inner.addWidget(self.only_checked)
        inner.addLayout(convert_row)
        inner.addLayout(lossless_row)
        inner.addWidget(self.convert_note)
        inner.addWidget(self.delete_unselected)
        inner.addWidget(self.manual)
        inner.addWidget(self.disk_use)
        inner.addWidget(self.rockbox)
        column.addWidget(frame)
        column.addStretch(1)
        for widget in (self.only_checked, self.manual, self.disk_use, self.delete_unselected,
                       self.rockbox):
            widget.toggled.connect(self._changed)
        return page

    def hidden_tabs(self) -> set[str]:
        """Tabs that don't apply: a phone has no Photos or Games; an iPod
        without video has no Movies or TV Shows, one without a colour screen
        no Photos."""
        if self.is_android:
            return {"Photos", "Games"}
        hidden = set()
        if not getattr(self.ipod, "supports_video", True):
            hidden |= {"Movies", "TV Shows"}
        if not getattr(self.ipod, "supports_photos", True):
            hidden.add("Photos")
        return hidden

    def _show_model_note(self):
        ipod = self.ipod
        text = ""
        if not self.is_android and ipod is not None:
            if getattr(ipod, "unsupported", ""):
                text = f"LuxTunes can't sync this iPod. {ipod.unsupported}"
            else:
                missing = []
                if not getattr(ipod, "supports_video", True):
                    missing.append("videos")
                if not getattr(ipod, "supports_photos", True):
                    missing.append("photos")
                parts = []
                if missing:
                    parts.append(f"This iPod doesn't play {' or '.join(missing)}, so "
                                 f"{'they aren' if len(missing) > 1 else 'those aren'}'t "
                                 "synced to it.")
                identity = getattr(ipod, "identity", None)
                if identity is not None and identity.family.note:
                    parts.append(identity.family.note)
                text = " ".join(parts)
        self.model_note.setText(text)
        self.model_note.setVisible(bool(text))

    def _fill_summary(self):
        if self.is_android:
            self._fill_phone_summary()
            return
        ipod = self.ipod
        self.picture.colour = ipod.colour or ""
        identity = getattr(ipod, "identity", None)
        self.picture.family = identity.family_key if identity is not None else ""
        self.picture.update()
        values = {
            "Name": self.record.name or ipod.name,
            "Capacity": ipod_device.format_gb(self._capacity()),
            "Software Version": ipod.software_version or "Unknown",
            "Serial Number": ipod.serial or "Unknown",
            "Model": self._model_text(ipod),
            "Format": ipod.format_name,
            # Rockbox or RepriseOS installed beside Apple's software (one sync serves all)
            "Also Runs": ", ".join(getattr(ipod, "other_firmware", None) or []),
        }
        for key, value in values.items():
            self.summary_values[key].setText(value)

    @staticmethod
    def _model_text(ipod) -> str:
        name = ipod.model_name or ipod.model_number or "Unknown"
        identity = getattr(ipod, "identity", None)
        size = identity.capacity_label if identity is not None else ""
        return f"{name}, {size}" if size else name

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
        # The storage last chosen for this phone (or one of the same kind).
        chosen = phone.storage_name(self.settings.android_storage,
                                    self.settings.android_storage_kind)
        self.storage.setCurrentIndex(max(0, self.storage.findData(chosen)))
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
        self.music_all = QRadioButton("Entire music library and all playlists")
        self.music_library_lists = QRadioButton("Entire music library and selected playlists")
        self.music_selected = QRadioButton("Selected playlists, artists, albums, and genres")
        self.music_videos = QCheckBox("Include music videos")
        # Only for "Selected…", but it keeps its row, so the box is the same size either way.
        self.autofill = keep_space(QCheckBox("Automatically fill free space with songs"))
        for widget in (self.music_all, self.music_library_lists, self.music_selected):
            inner.addWidget(widget)
        inner.addSpacing(8)
        inner.addWidget(self.music_videos)
        inner.addWidget(self.autofill)
        column.addWidget(frame)

        self.music_lists = keep_space(QWidget())
        grid = QGridLayout(self.music_lists)
        grid.setContentsMargins(0, 10, 0, 0)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(3, 1)
        self._music_grid = grid
        self.playlist_list, self.artist_list = checklist(), checklist()
        self.genre_list, self.album_list = checklist(), checklist()
        self._music_list_titles = {}
        for i, (title, widget) in enumerate((("Playlists", self.playlist_list),
                                             ("Artists", self.artist_list),
                                             ("Genres", self.genre_list),
                                             ("Albums", self.album_list))):
            label = heading(title, 1.3)
            self._music_list_titles[widget] = label
            grid.addWidget(label, (i // 2) * 2, i % 2)
            grid.addWidget(widget, (i // 2) * 2 + 1, i % 2)
            widget.itemChanged.connect(self._changed)
        column.addWidget(self.music_lists, 1)
        for widget in (self.sync_music, self.music_all, self.music_library_lists,
                       self.music_selected, self.music_videos, self.autofill):
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
        self.movie_list = tile_list()
        self.movie_list.itemChanged.connect(self._changed)
        area.addWidget(self.movie_list, 1)
        column.addWidget(self.movie_area, 1)
        self.sync_movies.toggled.connect(self._changed)
        self.movies_auto.toggled.connect(self._changed)
        self.movies_rule.currentIndexChanged.connect(self._changed)
        return page

    # ---- TV Shows, Podcasts, Audiobooks, Games -----------------------------------------------

    def _tv_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(30, 16, 30, 16)
        top = QHBoxLayout()
        self.sync_tv = QCheckBox("Sync TV Shows")
        self.sync_tv.setFont(heading("").font())
        self.tv_count = heading("", 1.3)
        self.tv_count.setObjectName("deviceCount")
        top.addWidget(self.sync_tv)
        top.addStretch(1)
        top.addWidget(self.tv_count)
        column.addLayout(top)
        frame, inner = box()
        row = QHBoxLayout()
        self.tv_auto = QCheckBox("Automatically include")
        self.tv_rule = QComboBox()
        self.tv_rule.addItems(EPISODE_RULES)
        self.tv_scope = QComboBox()
        self.tv_scope.addItems(TV_SCOPES)
        row.addWidget(self.tv_auto)
        row.addWidget(self.tv_rule)
        row.addWidget(QLabel("episodes of"))
        row.addWidget(self.tv_scope)
        row.addStretch(1)
        inner.addLayout(row)
        column.addWidget(frame)
        # Left: the shows (ticked = "selected shows"). Right: the episodes of
        # the show highlighted on the left; ticked ones always go.
        grid = QGridLayout()
        grid.addWidget(heading("Shows", 1.3), 0, 0)
        grid.addWidget(heading("Episodes", 1.3), 0, 1)
        grid.setRowStretch(1, 1)
        # A click on a show lists its episodes, so only its box ticks it.
        self.tv_shows = checklist(click_anywhere=False)
        self.tv_shows.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tv_episodes = checklist()
        grid.addWidget(self.tv_shows, 1, 0)
        grid.addWidget(self.tv_episodes, 1, 1)
        column.addLayout(grid, 1)
        self.tv_note = QLabel("Only .m4v, .mp4 and .mov videos go to the iPod (it can't play "
                              ".mkv or .avi).")
        self.tv_note.setObjectName("deviceNote")
        self.tv_note.setWordWrap(True)
        column.addWidget(self.tv_note)
        self._tv_list: list[dict] = []
        self._picked_tv: set[str] = set()
        self.tv_shows.currentRowChanged.connect(lambda _: self._fill_tv_episodes())
        self.tv_shows.itemChanged.connect(self._changed)
        self.tv_episodes.itemChanged.connect(self._tv_episode_ticked)
        for widget in (self.sync_tv, self.tv_auto):
            widget.toggled.connect(self._changed)
        for combo in (self.tv_rule, self.tv_scope):
            combo.currentIndexChanged.connect(self._changed)
        return page

    def _fill_tv_episodes(self):
        """The episodes of the show highlighted on the left, in order."""
        item = self.tv_shows.currentItem()
        show = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.tv_episodes.blockSignals(True)
        self.tv_episodes.clear()
        for e in self._tv_list:
            if ipod_plan.show_of(e) != show:
                continue
            season, number = e.get("season"), e.get("episode_number") or e.get("track_number")
            where = " ".join(part for part in (f"S{season}" if season else "",
                                                 f"E{number}" if number else "") if part)
            unwatched = "" if e.get("play_count") else "●  "
            label = f"{unwatched}{where + ' · ' if where else ''}{e.get('name') or 'Untitled'}"
            item = add_check(self.tv_episodes, label, e["persistent_id"],
                             e["persistent_id"] in self._picked_tv)
            item.setHidden(not matches_filter(item, self._filter_words))
        self.tv_episodes.blockSignals(False)

    def _tv_episode_ticked(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._picked_tv.add(key)
        else:
            self._picked_tv.discard(key)
        self._changed()

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
        grid.setRowStretch(1, 1)
        # A click on a podcast shows its episodes, so only its box ticks it.
        self.podcast_shows = checklist(click_anywhere=False)
        self.podcast_shows.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.podcast_episodes = checklist()
        grid.addWidget(self.podcast_shows, 1, 0)
        grid.addWidget(self.podcast_episodes, 1, 1)
        column.addLayout(grid, 1)
        self.podcasts_empty = QLabel("Your library has no podcasts yet. To make an episode one, "
                                     "choose Get Info → Options → Media Kind → Podcast.")
        self.podcasts_empty.setObjectName("deviceNote")
        self.podcasts_empty.setWordWrap(True)
        column.addWidget(self.podcasts_empty)
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
                item = add_check(self.podcast_episodes, f"{unplayed}{e.get('name') or 'Untitled'}",
                                 e["persistent_id"], e["persistent_id"] in self._picked_episodes)
                item.setHidden(not matches_filter(item, self._filter_words))
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
        self.book_list = keep_space(tile_list())
        column.addWidget(self.book_list, 1)
        self.books_empty = QLabel("Your library has no audiobooks yet. To make a song one, "
                                  "choose Get Info → Options → Media Kind → Audiobook.")
        self.books_empty.setObjectName("deviceNote")
        self.books_empty.setWordWrap(True)
        column.addWidget(self.books_empty)
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
        self.photo_folder_list = keep_space(checklist())
        self.photo_folder_list.itemChanged.connect(self._changed)
        column.addWidget(self.photo_folder_list, 1)
        hint = QLabel("Photos go one way: from this folder to the iPod. Each folder inside it "
                      "becomes a photo album on the iPod.")
        hint.setObjectName("deviceNote")
        hint.setWordWrap(True)
        column.addWidget(hint)
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

    def _chosen_photos(self) -> tuple[int, int]:
        """(how many photos are chosen, their files' size), counted once per choice."""
        s = self.settings
        key = (s.photos_folder, s.photos_mode, tuple(sorted(s.photo_folders)))
        if key not in self._photo_count_cache:
            from luxtunes.core.ipod import photos
            albums = photos.chosen_albums(s.photos_folder, s.photos_mode, s.photo_folders)
            self._photo_count_cache[key] = (photos.count(albums), photos.files_size(albums))
        return self._photo_count_cache[key]

    def _planned_photo_space(self) -> int:
        """About the space photos will take after the sync: the chosen photos'
        thumbnails (and full-resolution copies); none if LuxTunes will take its
        photos off. What's there now if LuxTunes can't tell."""
        space = self._space.photos
        if self.is_android or self.ipod is None or not getattr(self.ipod, "supports_photos", False):
            return space
        from luxtunes.core.ipod import photos
        s = self.settings
        if not s.sync_photos:
            return 0 if photos.ours_on(self.ipod.mountpoint) else space
        per_photo = photos.thumbnail_bytes(self.ipod.identity.family_key)
        if not per_photo:
            return space
        count, files = self._chosen_photos()
        return count * per_photo + (files if s.full_resolution else 0)

    def _update_photo_count(self):
        s = self.settings
        total = self._chosen_photos()[0]
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
                 self.movie_list, self.podcast_shows, self.book_list, self.tv_shows)
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
        self.movie_art.clear()
        self.book_art.clear()
        for movie in choices.movies:
            minutes = round((movie.get("total_time") or 0) / 60000)
            item = add_check(self.movie_list,
                             f"{movie.get('name')}\n{minutes} minutes\n{formatting.format_size(movie.get('size'))}",
                             movie["persistent_id"], movie["persistent_id"] in s.movies)
            self.movie_art.set(item, movie)
        for show in choices.shows:
            add_check(self.podcast_shows, show, show, show in s.podcast_shows)
        for show in choices.tv_shows:
            add_check(self.tv_shows, show or "Untitled", show, show in s.tv_shows)
        self._tv_list = choices.tv_episodes
        self._picked_tv = set(s.tv_episodes)
        self._episodes = choices.episodes
        self._picked_episodes = set(s.podcast_episodes)
        for key, label in choices.books:
            book = choices.book_details.get(key, {})
            text = "\n".join(part for part in (
                book.get("title") or label, book.get("author") or "",
                formatting.format_duration(book.get("time")) if book.get("time") else "")
                if part)
            item = add_check(self.book_list, text, key, key in s.books)
            item.setToolTip(label)
            self.book_art.set(item, book.get("track"))
        for widget in lists:
            widget.blockSignals(False)
        if self.podcast_shows.count():
            self.podcast_shows.setCurrentRow(0)
        self._fill_episodes()
        if self.tv_shows.count():
            self.tv_shows.setCurrentRow(0)
        self._fill_tv_episodes()
        self.tv_note.setText(
            ("Your library has no TV shows yet. To make a video one, choose Get Info → Options → "
             "Media Kind → TV Show. " if not choices.tv_episodes else "")
            + ("" if self.is_android else
               "Only .m4v, .mp4 and .mov videos go to the iPod (it can't play .mkv or .avi)."))
        self.podcasts_empty.setVisible(not choices.episodes)
        self.books_empty.setVisible(not choices.books)
        self._fill_photo_sources()
        self._apply_filter()

    def _load_controls(self):
        s = self.settings
        self.only_checked.setChecked(s.only_checked)
        self.convert.setChecked(s.convert)
        self.convert_kbps.setCurrentIndex(max(0, self.convert_kbps.findData(s.convert_kbps)))
        self.lossless_format.setCurrentIndex(max(0, self.lossless_format.findData(s.lossless_format)))
        item = self.lossless_format.model().item(self.lossless_format.currentIndex())
        if item is not None and not item.isEnabled():
            self.lossless_format.setCurrentIndex(0)       # this iPod doesn't play Apple Lossless
        self.manual.setChecked(s.manual)
        self.disk_use.setChecked(s.disk_use)
        self.rockbox.setChecked(s.rockbox)
        self.delete_unselected.setChecked(s.android_delete_unselected)
        self.sync_music.setChecked(s.sync_music)
        {"library_playlists": self.music_library_lists,
         "selected": self.music_selected}.get(s.music_mode, self.music_all).setChecked(True)
        self.music_videos.setChecked(s.include_music_videos)
        self.autofill.setChecked(s.autofill)
        self.sync_movies.setChecked(s.sync_movies)
        self.movies_auto.setChecked(s.movies_auto)
        self.movies_rule.setCurrentText(s.movies_rule)
        self.sync_tv.setChecked(s.sync_tv)
        self.tv_auto.setChecked(s.tv_auto)
        self.tv_rule.setCurrentText(s.tv_rule)
        self.tv_scope.setCurrentText(s.tv_scope)
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
        s.convert = self.convert.isChecked()
        s.convert_kbps = self.convert_kbps.currentData() or s.convert_kbps
        if not self.is_android:
            s.lossless_format = self.lossless_format.currentData() or "aac"
        s.manual = self.manual.isChecked()
        s.disk_use = self.disk_use.isChecked()
        s.rockbox = self.rockbox.isChecked()
        if self.is_android:
            s.android_delete_unselected = self.delete_unselected.isChecked()
        s.sync_music = self.sync_music.isChecked()
        s.music_mode = ("library_playlists" if self.music_library_lists.isChecked()
                        else "selected" if self.music_selected.isChecked() else "all")
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
        s.tv_scope = self.tv_scope.currentText()
        s.tv_shows = checked_keys(self.tv_shows)
        s.tv_episodes = sorted(self._picked_tv)
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
        for widget in (self.music_all, self.music_library_lists, self.music_selected,
                       self.music_videos, self.autofill):
            widget.setEnabled(music_on)
        self.autofill.setVisible(s.music_mode == "selected")
        # "…and selected playlists" shows just the playlists to tick.
        self.music_lists.setVisible(s.music_mode != "all")
        for widget in (self.artist_list, self.genre_list, self.album_list):
            widget.setVisible(s.music_mode == "selected")
            self._music_list_titles[widget].setVisible(s.music_mode == "selected")
        self.music_lists.setEnabled(music_on)
        # Only the playlists: they take the whole area.
        self._music_grid.setRowStretch(3, 1 if s.music_mode == "selected" else 0)
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
        tv_on = s.sync_tv and not s.manual
        self.tv_scope.setEnabled(tv_on and s.tv_auto)
        # Ticking shows only matters for "selected shows".
        self.tv_shows.setEnabled(tv_on and s.tv_auto and s.tv_scope == "selected shows")
        self.tv_episodes.setEnabled(tv_on)
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
            free = self._space.capacity - self._space.other - self._planned_photo_space()
        kbps_for = None
        if self.is_android:
            from luxtunes.core.android import sync as android_sync
            kbps_for = android_sync.transfer_kbps
        else:
            alac_ok = getattr(self.ipod, "supports_alac", True)
            kbps_for = lambda song, settings: ipod_plan.transfer_kbps(song, settings, alac_ok)
        return ipod_plan.build_plan(self.conn, self.settings, free_space=free, seed=0,
                                    kbps_for=kbps_for, phone=self.is_android,
                                    video=self.is_android or getattr(self.ipod, "supports_video",
                                                                      True))

    def _refresh_plan(self):
        if self.conn is None:
            return
        if self._space is None:                 # still measuring the iPod
            self.capacity_bar.set_parts(self._capacity(), [])
            self.warning.setText("Measuring what's on the iPod…")
            self.warning.show()
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
        photo_space = self._planned_photo_space()
        free = space.capacity - plan.music_bytes - plan.video_bytes - photo_space - space.other
        parts = [("Audio", plan.music_bytes), ("Video", plan.video_bytes), ("Photos", photo_space),
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
        unsupported = bool(getattr(self.ipod, "unsupported", "")) and not self.is_android
        self.apply_button.setEnabled(not too_big and not syncing)
        if not syncing:                     # while syncing it's Stop Sync (see _update_buttons)
            self.sync_button.setEnabled(not too_big and not unsupported)
