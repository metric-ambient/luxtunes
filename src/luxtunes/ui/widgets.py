"""The building blocks of the main window: the star-rating column, the
"LCD" now-playing display, the sidebar, and the album art pane."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSlider,
    QStyleOptionViewItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core import library
from luxtunes.ui.formatting import format_duration
from luxtunes.ui.track_model import TRACK_IDS_MIME, TrackRole, track_ids_from_mime


def themed_icon(names: list[str], fallback: QStyle.StandardPixmap) -> QIcon:
    """An icon from the desktop's icon theme (Mint-Y etc.), or a Qt built-in."""
    for name in names:
        if QIcon.hasThemeIcon(name):
            return QIcon.fromTheme(name)
    return QApplication.style().standardIcon(fallback)


# ---- Star ratings ----------------------------------------------------------

class RatingDelegate(QStyledItemDelegate):
    """Draws 0-5 stars, and sets the rating where you click on them.

    It takes two clicks: the first selects the song, a click on an already
    selected song sets its rating. A click that brings the window to the
    front only does that. Ratings inherited from an album rating are drawn
    as hollow stars.
    """

    STAR, HOLLOW = "★", "☆"
    PADDING = 4
    FOCUS_CLICK = 0.4          # seconds after the window became active

    def __init__(self, parent=None, key="rating", editable=True):
        super().__init__(parent)
        self.key = key                       # "rating" or "album_rating"
        self.computed_key = f"{key}_computed"
        self.editable = editable
        self._activated_at = 0.0
        if editable and QApplication.instance() is not None:
            QApplication.instance().focusChanged.connect(self._focus_changed)

    def _focus_changed(self, old, new):
        # Focus coming in from another window (or another program).
        if new is not None and (old is None or old.window() is not new.window()):
            self._activated_at = time.monotonic()

    def _window_was_in_front(self, widget) -> bool:
        if widget is None or not widget.window().isActiveWindow():
            return False
        return time.monotonic() - self._activated_at > self.FOCUS_CLICK

    @staticmethod
    def _row_selected(view, index) -> bool:
        selection = view.selectionModel() if view is not None else None
        return selection is not None and selection.isRowSelected(index.row(), index.parent())

    def stars_at(self, option, index, x: float) -> int:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)     # the same font the stars are drawn in
        star_width = max(1, QFontMetrics(opt.font).horizontalAdvance(self.STAR))
        x -= option.rect.left() + self.PADDING
        return 0 if x < 0 else min(5, int(x // star_width) + 1)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        track = index.data(TrackRole) or {}
        stars = round((track.get(self.key) or 0) / 20)
        if stars <= 0:
            return
        computed = bool(track.get(self.computed_key))
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        color = opt.palette.color(
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        if computed:
            color.setAlpha(120)
        painter.save()
        painter.setPen(color)
        painter.drawText(
            opt.rect.adjusted(self.PADDING, 0, 0, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            (self.HOLLOW if computed else self.STAR) * stars,
        )
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if not self.editable or getattr(event, "button", lambda: None)() != Qt.MouseButton.LeftButton:
            return False
        kind = event.type()
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            # Checked before the view handles the click (and selects the row).
            if self._window_was_in_front(option.widget) and self._row_selected(option.widget, index):
                model.setData(index, self.stars_at(option, index, event.position().x()) * 20,
                              Qt.ItemDataRole.EditRole)
            # A press still selects the row; a quick second click mustn't play the song.
            return kind == QEvent.Type.MouseButtonDblClick
        if kind == QEvent.Type.MouseButtonRelease:
            return True                      # the press already did it; no text editor
        return False


# ---- Sliders that jump to the click --------------------------------------

class JumpSlider(QSlider):
    """A slider that moves straight to where you click (Qt's default only steps
    a bit closer per click). You can keep holding and drag from there."""

    clicked_to = Signal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            style = self.style()
            groove = style.subControlRect(
                QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
            handle = style.subControlRect(
                QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
            span = max(1, groove.width() - handle.width())
            x = int(event.position().x() - groove.x() - handle.width() / 2)
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), max(0, min(span, x)), span, opt.upsideDown)
            self.setValue(value)
            self.clicked_to.emit(value)
        # Let Qt continue, so the handle (now under the mouse) can be dragged.
        super().mousePressEvent(event)


class ScrollingLabel(QWidget):
    """One line of text that scrolls sideways when it doesn't fit (pausing at
    the start of each pass); centred and still when it does."""

    STEP_MS = 30          # one pixel per step
    PAUSE_MS = 2000       # rest at the start before scrolling again
    GAP = 40              # space between the end of the text and its next pass

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._text = text
        self._offset = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self.setToolTip(text)
        self._restart()

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self._text),
                     self.fontMetrics().height() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(10, self.fontMetrics().height() + 2)

    def _overflows(self) -> bool:
        return self.fontMetrics().horizontalAdvance(self._text) > self.width()

    def _restart(self) -> None:
        self._offset = 0
        self._timer.stop()
        if self._overflows() and self.isVisible():
            self._timer.start(self.PAUSE_MS)
        self.update()

    def _step(self) -> None:
        if not self._overflows():
            self._restart()
            return
        self._timer.setInterval(self.STEP_MS)
        self._offset += 1
        if self._offset >= self.fontMetrics().horizontalAdvance(self._text) + self.GAP:
            self._offset = 0                     # back at the start: rest a moment
            self._timer.setInterval(self.PAUSE_MS)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._restart()

    def showEvent(self, event):
        super().showEvent(event)
        self._restart()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self.updateGeometry()
            self._restart()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        rect = self.rect()
        if not self._overflows():
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
            return
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        width = self.fontMetrics().horizontalAdvance(self._text)
        for x in (-self._offset, width + self.GAP - self._offset):
            painter.drawText(rect.adjusted(x, 0, x + width, 0), flags, self._text)


# ---- The LCD ---------------------------------------------------------------

class NowPlayingDisplay(QFrame):
    """The display in the middle of the toolbar: the song name, then
    "Artist — Album", then a progress bar you can click or drag to seek.

    While something runs in the background (an iPod sync, a CD import) it
    shows that progress instead. With several at once, the button on the left
    steps through each of them and then Now Playing, round and round; the ✕
    on the right stops the one on show. Each task has a key ("sync",
    "import"), and its progress is kept up to date even while it isn't shown."""

    seek_requested = Signal(int)
    stop_requested = Signal(str)        # the key of the task on show

    IDLE, TRACK, SYNC = 0, 1, 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("lcd")
        self.setMinimumWidth(260)
        self.setFixedHeight(46)
        self._playing_page = self.IDLE      # what "Now Playing" shows (idle or the song)
        self._tasks: dict[str, dict] = {}   # key -> title, detail, bar, stop_tip, stoppable, done
        self._shown: str | None = None      # the task on show, or None for Now Playing
        self._end_timers: dict[str, QTimer] = {}

        self._pages = QStackedWidget(self)
        idle = QLabel("LuxTunes")
        idle.setObjectName("lcdIdle")
        idle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pages.addWidget(idle)

        info = QWidget()
        self.title = self._label("lcdTitle")
        self.subtitle = self._label("lcdSubtitle")
        self.elapsed = QLabel("0:00")
        self.remaining = QLabel("-0:00")
        self.slider = JumpSlider(Qt.Orientation.Horizontal)
        self.slider.setObjectName("lcdSlider")
        self.slider.clicked_to.connect(self.seek_requested)
        self.slider.sliderReleased.connect(lambda: self.seek_requested.emit(self.slider.value()))

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.elapsed)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.remaining)

        layout = QVBoxLayout(info)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(0)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        layout.addLayout(bar)
        self._pages.addWidget(info)

        # Sync progress page.
        sync_page = QWidget()
        self.sync_title = self._label("lcdTitle")
        self.sync_detail = self._label("lcdSubtitle")
        self.sync_bar = QProgressBar()
        self.sync_bar.setObjectName("lcdProgress")
        self.sync_bar.setTextVisible(False)
        self.sync_bar.setFixedHeight(8)
        sync_layout = QVBoxLayout(sync_page)
        sync_layout.setContentsMargins(8, 2, 8, 4)
        sync_layout.setSpacing(1)
        sync_layout.addWidget(self.sync_title)
        sync_layout.addWidget(self.sync_detail)
        sync_layout.addWidget(self.sync_bar)
        self._pages.addWidget(sync_page)

        self.toggle_button = QToolButton()
        self.toggle_button.setObjectName("lcdButton")
        self.toggle_button.setText("⇆")
        self.toggle_button.clicked.connect(self.toggle)
        self.stop_button = QToolButton()
        self.stop_button.setObjectName("lcdButton")
        self.stop_button.setText("✕")
        self.stop_button.clicked.connect(
            lambda: self._shown is not None and self.stop_requested.emit(self._shown))
        for button in (self.toggle_button, self.stop_button):
            button.setFixedSize(20, 20)
            button.hide()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 0, 4, 0)
        outer.setSpacing(2)
        outer.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(self._pages, 1)
        outer.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def _label(name: str) -> QLabel:
        label = QLabel()
        label.setObjectName(name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Long titles must not force the window wider.
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    # ---- Now Playing ---------------------------------------------------------------

    def show_track(self, track: dict):
        self.title.setText(track.get("name") or "")
        parts = [t for t in (track.get("artist"), track.get("album")) if t]
        self.subtitle.setText(" — ".join(parts))
        self.update_position(0, track.get("total_time") or 0)
        self._playing_page = self.TRACK
        if self._shown is None:
            self._pages.setCurrentIndex(self.TRACK)

    def clear(self):
        self._playing_page = self.IDLE
        if self._shown is None:
            self._pages.setCurrentIndex(self.IDLE)

    def update_position(self, position: int, duration: int):
        if self.slider.isSliderDown():
            return
        self.slider.setMaximum(max(0, duration))
        self.slider.setValue(position)
        self.elapsed.setText(format_duration(position))
        self.remaining.setText("-" + format_duration(max(0, duration - position)))

    # ---- Background tasks ------------------------------------------------------------

    @property
    def showing_task(self) -> str | None:
        """The key of the task on show, or None while Now Playing shows."""
        return self._shown

    @property
    def task_active(self) -> bool:
        return bool(self._tasks)

    def task_keys(self) -> list[str]:
        return list(self._tasks)

    def start_task(self, key: str, title: str, detail: str, stop_tip: str):
        """A background task starts (or starts again): show it."""
        timer = self._end_timers.pop(key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self._tasks.pop(key, None)              # a restarted task goes to the end of the round
        self._tasks[key] = {"title": title, "detail": detail, "bar": (0, 0, 0),
                            "stop_tip": stop_tip, "stoppable": True, "done": False}
        self._show(key)

    def update_task(self, key: str, title: str | None, detail: str, fraction: float | None):
        """Both lines and the bar (fraction None: busy, no amount known)."""
        task = self._tasks.get(key)
        if task is None:
            return
        if title is not None:
            task["title"] = title
        task["detail"] = detail
        task["bar"] = (0, 0, 0) if fraction is None else \
            (0, 1000, int(max(0.0, min(1.0, fraction)) * 1000))
        self._refresh(key)

    def update_count(self, key: str, text: str, done: int, total: int):
        """"Copying “Song” (3 of 40)" and the bar to match."""
        task = self._tasks.get(key)
        if task is None:
            return
        task["detail"] = f"{text} ({done + 1} of {total})" if total else text
        task["bar"] = (0, total, done) if total else (0, 0, 0)
        self._refresh(key)

    def set_stopping(self, key: str, detail: str):
        """Stop was pressed: say so, and the ✕ can't be pressed again."""
        task = self._tasks.get(key)
        if task is None:
            return
        task["detail"] = detail
        task["stoppable"] = False
        self._refresh(key)

    def finish_task(self, key: str, message: str, seconds: int = 6):
        """A task ended: show how for a few seconds, then it leaves the display."""
        task = self._tasks.get(key)
        if task is None:
            return
        task.update(detail=message, bar=(0, 1, 1), stoppable=False, done=True)
        self._refresh(key)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self.end_task(key))
        self._end_timers[key] = timer
        timer.start(seconds * 1000)

    def end_task(self, key: str):
        timer = self._end_timers.pop(key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        keys = list(self._tasks)
        if key not in keys:
            return
        position = keys.index(key)
        del self._tasks[key]
        if self._shown == key:
            # The next task in the round, or Now Playing when none are left.
            rest = list(self._tasks)
            self._show(rest[position] if position < len(rest) else (rest[0] if rest else None))
        else:
            self._update_buttons()

    def toggle(self):
        """Step to the next task, then Now Playing, then round again."""
        round_ = list(self._tasks) + [None]
        position = round_.index(self._shown) if self._shown in round_ else -1
        self._show(round_[(position + 1) % len(round_)])

    def _show(self, key: str | None):
        self._shown = key if key in self._tasks else None
        if self._shown is None:
            self._pages.setCurrentIndex(self._playing_page)
        else:
            self._refresh(self._shown)
            self._pages.setCurrentIndex(self.SYNC)
        self._update_buttons()

    def _refresh(self, key: str):
        if key != self._shown:
            return
        task = self._tasks[key]
        self.sync_title.setText(task["title"])
        self.sync_detail.setText(task["detail"])
        low, high, value = task["bar"]
        self.sync_bar.setRange(low, high)
        if high:
            self.sync_bar.setValue(value)
        self._update_buttons()

    def _update_buttons(self):
        self.toggle_button.setVisible(bool(self._tasks))
        names = [t["title"] for t in self._tasks.values()]
        self.toggle_button.setToolTip("Show the next: " + ", ".join(names + ["Now Playing"]))
        task = self._tasks.get(self._shown) if self._shown else None
        self.stop_button.setVisible(task is not None and not task["done"])
        if task is not None:
            self.stop_button.setEnabled(task["stoppable"])
            self.stop_button.setToolTip(task["stop_tip"])


# ---- Sidebar ---------------------------------------------------------------

SOURCE_MUSIC = ("library", "music")
SOURCE_MOVIES = ("library", "movie")
SOURCE_TV_SHOWS = ("library", "tvshow")
SOURCE_PODCASTS = ("library", "podcast")
SOURCE_AUDIOBOOKS = ("library", "audiobook")
SOURCE_UP_NEXT = ("queue", "up_next")


def device_icon() -> QIcon:
    return themed_icon(["multimedia-player-apple-ipod", "multimedia-player", "drive-removable-media"],
                       QStyle.StandardPixmap.SP_DriveHDIcon)


def phone_icon() -> QIcon:
    return themed_icon(["phone", "phone-symbolic", "multimedia-player", "drive-removable-media"],
                       QStyle.StandardPixmap.SP_DriveHDIcon)


def cd_icon() -> QIcon:
    return themed_icon(["media-optical-audio", "media-optical-cd-audio", "media-optical"],
                       QStyle.StandardPixmap.SP_DriveCDIcon)


def playlist_icon(p: dict) -> QIcon:
    """The sidebar icon for a playlist: folder, gear (smart) or list."""
    if p["is_folder"]:
        return themed_icon(["folder"], QStyle.StandardPixmap.SP_DirIcon)
    if p["is_smart"]:
        return themed_icon(["emblem-system", "preferences-system"],
                           QStyle.StandardPixmap.SP_FileDialogDetailedView)
    return themed_icon(["view-media-playlist", "audio-x-mp3-playlist", "view-list"],
                       QStyle.StandardPixmap.SP_FileDialogListView)


EJECT_WIDTH = 22


def draw_sync_arrows(painter, colour, cx: float, cy: float, angle: float) -> None:
    """The "syncing" symbol shown instead of eject: a thin ring split into two
    arcs that chase each other clockwise, each ending in an arrowhead that
    points inwards off the ring. Turned by `angle` degrees (it spins)."""
    radius, width, inner, tip_angle, span = 6.0, 1.2, 2.4, 33, 126

    def point(r, degrees):          # Qt's y axis points down, so sine is negated
        a = math.radians(degrees)
        return QPointF(r * math.cos(a), -r * math.sin(a))

    painter.save()
    painter.translate(cx, cy)
    painter.rotate(angle)
    # A fresh pen: the one the painter has may be switched off (Qt.NoPen).
    pen = QPen(colour, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    ring = QRectF(-radius, -radius, 2 * radius, 2 * radius)
    for tail in (126, -54):                          # the two arcs, half a turn apart
        end = tail - span                            # each runs clockwise from its tail
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(ring, int(end * 16), int(span * 16))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPolygon(QPolygonF([point(radius + width / 2, end),
                                       point(radius - inner, end),
                                       point(radius + 0.1, end - tip_angle)]))
    painter.restore()


class SidebarDelegate(QStyledItemDelegate):
    """Draws the little eject button at the right end of a device's row."""

    def paint(self, painter, option, index):
        source = index.data(Qt.ItemDataRole.UserRole)
        if not (isinstance(source, tuple) and source and source[0] == "device"):
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, widget)
        text_opt = QStyleOptionViewItem(opt)
        text_opt.rect = opt.rect.adjusted(0, 0, -EJECT_WIDTH, 0)       # leave room for the button
        text_opt.state &= ~QStyle.StateFlag.State_Selected
        text_opt.backgroundBrush = Qt.BrushStyle.NoBrush
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        text_opt.palette.setColor(QPalette.ColorRole.Text, opt.palette.color(
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, text_opt, painter, widget)
        # The eject symbol: a triangle over a bar.
        painter.save()
        try:
            painter.setRenderHint(painter.RenderHint.Antialiasing)
            colour = opt.palette.color(QPalette.ColorRole.HighlightedText if selected
                                       else QPalette.ColorRole.Text)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            cx = opt.rect.right() - EJECT_WIDTH // 2
            cy = opt.rect.center().y()
            sidebar = self.parent()
            if sidebar is not None and sidebar.is_busy(source[1]):
                draw_sync_arrows(painter, colour, cx, cy, sidebar.spin_angle)
                return
            painter.drawPolygon(QPolygonF([QPointF(cx - 5, cy + 1), QPointF(cx + 5, cy + 1),
                                           QPointF(cx, cy - 5)]))
            painter.drawRect(QRectF(cx - 5, cy + 3, 10, 2))
        finally:
            painter.restore()


class Sidebar(QTreeWidget):
    """LIBRARY / QUEUE / DEVICES / PLAYLISTS. Emits the chosen source:
    ("library", "music"), ("library", "movie"), ("queue", "up_next"),
    ("device", <key>) or ("playlist", <id>)."""

    source_selected = Signal(object)
    tracks_dropped = Signal(object, list)   # target source, track ids
    playlist_renamed = Signal(int, str)     # playlist id, new name
    menu_requested = Signal(object, object)  # source, global position (right-click)
    delete_requested = Signal(object)       # source (Delete key)
    device_renamed = Signal(str, str)       # device key, new name
    eject_requested = Signal(str)           # device key (the eject button next to an iPod)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        # Songs dragged from the song list can be dropped on normal playlists
        # and on Up Next. Only those items carry the "drop enabled" flag.
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)
        self.invisibleRootItem().setFlags(Qt.ItemFlag.ItemIsEnabled)
        # Rename your own playlists by double-clicking them, pressing F2, or
        # clicking one that is already selected (only those items are editable).
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked)
        self.itemChanged.connect(self._on_item_changed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(10)
        self.setIconSize(QSize(16, 16))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.itemSelectionChanged.connect(self._on_selection)
        self._section_color = None
        self._up_next_item: QTreeWidgetItem | None = None
        self.setItemDelegate(SidebarDelegate(self))
        self.setMouseTracking(True)
        self.show_icons = True                   # Preferences → General → Show source icons
        self.syncing_key: str | None = None      # the iPod syncing now (spinning arrows)
        self.busy_keys: set[str] = set()         # e.g. a CD importing (spinning arrows too)
        self.spin_angle = 0.0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(50)
        self._spin_timer.timeout.connect(self._spin)

    def populate(self, playlists: list[dict], has_movies: bool, section_color, queue_length=0,
                 devices: list[tuple[str, str]] = (), has_tv_shows: bool = False,
                 has_podcasts: bool = False, has_audiobooks: bool = False):
        self.blockSignals(True)
        self.clear()
        self._section_color = section_color

        section = self._section("LIBRARY")
        self._item(section, "Music", SOURCE_MUSIC,
                   themed_icon(["audio-x-generic"], QStyle.StandardPixmap.SP_MediaVolume))
        if has_movies:
            self._item(section, "Movies", SOURCE_MOVIES,
                       themed_icon(["video-x-generic"], QStyle.StandardPixmap.SP_MediaPlay))
        if has_tv_shows:
            self._item(section, "TV Shows", SOURCE_TV_SHOWS,
                       themed_icon(["video-display", "computer"], QStyle.StandardPixmap.SP_DesktopIcon))
        if has_podcasts:
            self._item(section, "Podcasts", SOURCE_PODCASTS,
                       themed_icon(["podcast", "application-rss+xml", "network-wireless"],
                                   QStyle.StandardPixmap.SP_MediaVolume))
        if has_audiobooks:
            self._item(section, "Audiobooks", SOURCE_AUDIOBOOKS,
                       themed_icon(["accessories-dictionary", "x-office-address-book",
                                    "x-office-document"], QStyle.StandardPixmap.SP_FileIcon))

        section = self._section("QUEUE")
        self._up_next_item = self._item(
            section, "Up Next", SOURCE_UP_NEXT,
            themed_icon(["view-media-playlist", "media-playlist-append", "view-list"],
                        QStyle.StandardPixmap.SP_ArrowForward),
            droppable=True)
        self.set_queue_length(queue_length)

        if devices:     # iPods and phones (rename like a playlist) and audio CDs: (key, name)
            section = self._section("DEVICES")
            for key, name in devices:
                is_cd = key.startswith("cd:")
                icon = cd_icon() if is_cd else \
                    phone_icon() if key.startswith("android:") else device_icon()
                self._item(section, name, ("device", key), icon, editable=not is_cd)

        section = self._section("PLAYLISTS")
        by_persistent_id = {}
        for p in playlists:
            kind = p["distinguished_kind"]
            if kind in library.LIBRARY_SECTION_KINDS or kind in library.UNSUPPORTED_KINDS:
                continue
            parent = by_persistent_id.get(p["parent_persistent_id"], section)
            mine = library.is_user_playlist(p)
            renamable = mine or bool(p["is_folder"]) or bool(p["is_smart"] and not p["distinguished_kind"])
            item = self._item(parent, p["name"], ("playlist", p["id"]), playlist_icon(p),
                              droppable=mine, editable=renamable)
            by_persistent_id[p["persistent_id"]] = item

        if not self.show_icons:
            stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
            while stack:
                item = stack.pop()
                item.setIcon(0, QIcon())
                stack.extend(item.child(i) for i in range(item.childCount()))
        self.expandAll()
        self.blockSignals(False)

    def set_queue_length(self, length: int):
        if self._up_next_item is not None:
            self._up_next_item.setText(0, f"Up Next ({length})" if length else "Up Next")

    def _section(self, title: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self, [title])
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
        font = QFont(self.font())
        font.setBold(True)
        font.setPointSizeF(max(6.0, font.pointSizeF() - 1))
        item.setFont(0, font)
        if self._section_color is not None:
            item.setForeground(0, self._section_color)
        return item

    @staticmethod
    def _item(parent, text, source, icon, droppable=False, editable=False) -> QTreeWidgetItem:
        item = QTreeWidgetItem(parent, [text])
        item.setData(0, Qt.ItemDataRole.UserRole, source)
        item.setIcon(0, icon)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if droppable:
            flags |= Qt.ItemFlag.ItemIsDropEnabled
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)
        return item

    # ---- Receiving dropped songs -------------------------------------------

    def mimeTypes(self):
        return [TRACK_IDS_MIME]

    def supportedDropActions(self):
        return Qt.DropAction.CopyAction

    def dropMimeData(self, parent, index, data, action):
        ids = track_ids_from_mime(data)
        if parent is None or not ids:
            return False
        source = parent.data(0, Qt.ItemDataRole.UserRole)
        if source is None:
            return False
        self.tracks_dropped.emit(source, ids)
        return True

    def _on_context_menu(self, pos):
        item = self.itemAt(pos)
        source = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if source is not None:
            self.setCurrentItem(item)
            self.menu_requested.emit(source, self.viewport().mapToGlobal(pos))

    def set_syncing(self, key: str | None):
        """Show spinning arrows instead of eject for the iPod that's syncing."""
        self.syncing_key = key
        self._update_spinning()

    def set_busy(self, key: str, busy: bool):
        """Spinning arrows instead of eject for another device (a CD importing)."""
        if busy:
            self.busy_keys.add(key)
        else:
            self.busy_keys.discard(key)
        self._update_spinning()

    def is_busy(self, key) -> bool:
        return key == self.syncing_key or key in self.busy_keys

    def _update_spinning(self):
        if self.syncing_key is None and not self.busy_keys:
            self._spin_timer.stop()
        else:
            self._spin_timer.start()
        self.viewport().update()

    def _spin(self):
        self.spin_angle = (self.spin_angle + 6) % 360    # one slow turn every 3 seconds, clockwise
        self.viewport().update()

    def _eject_hit(self, pos):
        """The iPod whose eject button is under `pos`, if any."""
        item = self.itemAt(pos)
        source = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if source and source[0] == "device" and pos.x() >= self.viewport().width() - EJECT_WIDTH:
            return source[1]
        return None

    def mousePressEvent(self, event):
        key = self._eject_hit(event.position().toPoint())
        if key is not None and event.button() == Qt.MouseButton.LeftButton:
            if not self.is_busy(key):                # no ejecting mid-sync / mid-import
                self.eject_requested.emit(key)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        hit = self._eject_hit(event.position().toPoint())
        tip = ""
        if hit:
            tip = ("Importing…" if hit.startswith("cd:") else "Syncing…") if self.is_busy(hit) else "Eject"
        self.viewport().setToolTip(tip)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.state() != \
                QAbstractItemView.State.EditingState:
            item = self.currentItem()
            source = item.data(0, Qt.ItemDataRole.UserRole) if item else None
            if source is not None:
                self.delete_requested.emit(source)
                return
        super().keyPressEvent(event)

    def rename_source(self, source):
        """Open the in-place name editor with the whole name selected (new playlists)."""
        if self.select_source(source):
            self.editItem(self.currentItem(), 0)

    def _on_item_changed(self, item, column):
        source = item.data(0, Qt.ItemDataRole.UserRole)
        if source and source[0] == "playlist" and item.flags() & Qt.ItemFlag.ItemIsEditable:
            self.playlist_renamed.emit(source[1], item.text(0))
        elif source and source[0] == "device":
            self.device_renamed.emit(source[1], item.text(0))

    def select_source(self, source) -> bool:
        iterator_stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while iterator_stack:
            item = iterator_stack.pop(0)
            if item.data(0, Qt.ItemDataRole.UserRole) == source:
                self.setCurrentItem(item)
                return True
            iterator_stack.extend(item.child(i) for i in range(item.childCount()))
        return False

    def _on_selection(self):
        items = self.selectedItems()
        if items:
            source = items[0].data(0, Qt.ItemDataRole.UserRole)
            if source is not None:
                self.source_selected.emit(source)


# ---- Album art -------------------------------------------------------------

class ArtworkPane(QWidget):
    """The square album art box in the bottom-left. Its size follows the
    sidebar's width. Click the header to switch between the selected song
    and the song that's playing."""

    header_clicked = Signal()
    image_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.header = QLabel("Selected Item")
        self.header.setObjectName("artHeader")
        self.header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.installEventFilter(self)
        self.image = QLabel()
        self.image.setObjectName("artImage")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.image.setCursor(Qt.CursorShape.PointingHandCursor)
        self.image.setToolTip("Click to open the artwork viewer")
        self.image.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.image)
        self._pixmap: QPixmap | None = None
        self._placeholder = "No Artwork"

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonRelease:
            if obj is self.header:
                self.header_clicked.emit()
                return True
            if obj is self.image:
                self.image_clicked.emit()
                return True
        return super().eventFilter(obj, event)

    def set_title(self, text: str):
        self.header.setText(text)

    def set_image(self, data: bytes | None, placeholder: str = "No Artwork"):
        pixmap = QPixmap()
        self._pixmap = pixmap if data and pixmap.loadFromData(data) else None
        self._placeholder = placeholder
        self._rescale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def screen_limit(self) -> int:
        """A quarter of the screen's width (and at most half its height).
        The sidebar is never allowed to be wider than this either."""
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        return min(area.width() // 4, area.height() // 2)

    def max_side(self) -> int:
        """The largest the art box may get: screen_limit(), and never larger
        than the picture itself."""
        limit = self.screen_limit()
        if self._pixmap is not None:
            limit = min(limit, max(self._pixmap.width(), self._pixmap.height()))
        return max(64, limit)

    def _rescale(self):
        # Square box that follows the sidebar's width, up to max_side().
        side = min(self.width(), self.max_side())
        if self.image.height() != side:
            self.image.setFixedHeight(side)
        if self._pixmap is None:
            self.image.setPixmap(QPixmap())
            self.image.setText(self._placeholder)
        else:
            self.image.setPixmap(
                self._pixmap.scaled(
                    side, side,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
