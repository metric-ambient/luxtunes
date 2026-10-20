"""Where movies and TV shows play.

The video fills the main window between the top bar and the bottom bar, in
place of the sidebar and song list. Moving the mouse brings up a floating
control panel (volume, previous, back 10 s, play/pause, forward 10 s, next,
full screen, and a seek bar with times) and a round ✕ in the top-right
corner that closes the video and returns to where you were. Full screen
hides the bars too.

The picture comes from the same player that plays music (Qt Multimedia with
its built-in FFmpeg, so .m4v, .mp4, .mov, .mkv, .avi, .webm... all work). It
arrives as frames through a QVideoSink and is painted here, which lets the
controls float over it (a QVideoWidget is a separate native window that
widgets can't be drawn on top of).

Keys while the video is showing: Space play/pause, F or double-click full
screen, Esc leaves full screen (or closes the video), ←/→ 10 seconds.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtMultimedia import QVideoFrame, QVideoSink
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStyle, QToolButton, QVBoxLayout, QWidget

from luxtunes.ui.formatting import format_duration
from luxtunes.ui.widgets import JumpSlider, themed_icon

SKIP_MS = 10_000
HIDE_DELAY_MS = 2500
CONTROLS_MAX_WIDTH = 560
CLOSE_SIZE = 26

# The controls are always dark, whatever the app theme.
STYLE = """
QFrame#videoControls { background: rgba(28, 28, 30, 225); border: 1px solid rgba(255, 255, 255, 40);
                       border-radius: 8px; }
QFrame#videoControls QLabel { color: #e8e8e8; background: transparent; font-size: 11px; }
QToolButton#videoButton { border: none; padding: 3px; background: transparent; }
QToolButton#videoButton:hover { background: rgba(255, 255, 255, 30); border-radius: 4px; }
QFrame#videoControls QSlider::groove:horizontal { height: 4px; background: rgba(255, 255, 255, 50);
                                                 border-radius: 2px; }
QFrame#videoControls QSlider::sub-page:horizontal { background: rgba(255, 255, 255, 150);
                                                   border-radius: 2px; }
QFrame#videoControls QSlider::handle:horizontal { width: 10px; margin: -4px 0; border-radius: 5px;
                                                 background: #d8d8d8; }
"""


class VideoSurface(QWidget):
    """Paints the frames from `sink`, letterboxed on black."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self.image = QImage()

    def _on_frame(self, frame: QVideoFrame):
        if not frame.isValid():
            return
        self.image = frame.toImage()
        self.update()

    def clear(self):
        self.image = QImage()
        self.update()

    def picture_rect(self) -> QRectF:
        if self.image.isNull():
            return QRectF()
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width() - size.width()) / 2, (self.height() - size.height()) / 2,
                      size.width(), size.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if not self.image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(self.picture_rect(), self.image)


class CloseButton(QToolButton):
    """The small round ✕ in the top-right corner."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(CLOSE_SIZE, CLOSE_SIZE)
        self.setToolTip("Close Video")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hover = self.underMouse()
        painter.setPen(QPen(QColor(255, 255, 255, 110), 1))
        painter.setBrush(QColor(70, 70, 74, 235) if hover else QColor(28, 28, 30, 220))
        painter.drawEllipse(QRectF(1, 1, self.width() - 2, self.height() - 2))
        pen = QPen(QColor("#e8e8e8"), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        c, r = self.width() / 2, self.width() * 0.19
        path = QPainterPath()
        path.moveTo(QPointF(c - r, c - r))
        path.lineTo(QPointF(c + r, c + r))
        path.moveTo(QPointF(c + r, c - r))
        path.lineTo(QPointF(c - r, c + r))
        painter.drawPath(path)


class VideoPanel(QWidget):
    play_clicked = Signal()
    next_clicked = Signal()
    previous_clicked = Signal()
    seek_requested = Signal(int)
    volume_changed = Signal(int)
    close_requested = Signal()          # the ✕ (or Esc): stop the video and go back
    full_screen_requested = Signal()    # toggle full screen

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoPanel")
        self.setStyleSheet(STYLE)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._position = 0
        self._duration = 0
        self.full_screen = False          # the main window tells us

        self.surface = VideoSurface(self)
        self.surface.installEventFilter(self)
        self.sink = self.surface.sink

        # ---- The floating controls ------------------------------------------------
        self.controls = QFrame(self)
        self.controls.setObjectName("videoControls")
        self.controls.installEventFilter(self)
        sp = QStyle.StandardPixmap
        self.prev_button = self._button(themed_icon(["media-skip-backward"], sp.SP_MediaSkipBackward), "Previous")
        self.rewind_button = self._button(themed_icon(["media-seek-backward"], sp.SP_MediaSeekBackward), "Back 10 seconds")
        self.play_button = self._button(themed_icon(["media-playback-pause"], sp.SP_MediaPause), "Play / Pause")
        self.forward_button = self._button(themed_icon(["media-seek-forward"], sp.SP_MediaSeekForward), "Forward 10 seconds")
        self.next_button = self._button(themed_icon(["media-skip-forward"], sp.SP_MediaSkipForward), "Next")
        self.full_button = self._button(themed_icon(["view-fullscreen"], sp.SP_TitleBarMaxButton), "Full Screen (F)")
        self._pause_icon = self.play_button.icon()
        self._play_icon = themed_icon(["media-playback-start"], sp.SP_MediaPlay)

        self.volume = JumpSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(90)
        quiet, loud = QLabel(), QLabel()
        quiet.setPixmap(self.style().standardIcon(sp.SP_MediaVolumeMuted).pixmap(14, 14))
        loud.setPixmap(self.style().standardIcon(sp.SP_MediaVolume).pixmap(14, 14))

        # Volume on the left, full screen on the right, both the same width so
        # the transport buttons sit in the middle.
        left = QWidget()
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(4)
        left_row.addWidget(quiet)
        left_row.addWidget(self.volume)
        left_row.addWidget(loud)
        right = QWidget()
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.addStretch(1)
        right_row.addWidget(self.full_button)
        side = max(left.sizeHint().width(), right.sizeHint().width())
        left.setFixedWidth(side)
        right.setFixedWidth(side)

        top = QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(left)
        top.addStretch(1)
        for button in (self.prev_button, self.rewind_button, self.play_button,
                       self.forward_button, self.next_button):
            top.addWidget(button)
        top.addStretch(1)
        top.addWidget(right)

        self.elapsed = QLabel("0:00")
        self.remaining = QLabel("-0:00")
        self.seek = JumpSlider(Qt.Orientation.Horizontal)
        bottom = QHBoxLayout()
        bottom.addWidget(self.elapsed)
        bottom.addWidget(self.seek, 1)
        bottom.addWidget(self.remaining)

        layout = QVBoxLayout(self.controls)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(2)
        layout.addLayout(top)
        layout.addLayout(bottom)

        self.close_button = CloseButton(self)
        self.close_button.clicked.connect(self.close_requested)
        self.close_button.installEventFilter(self)

        self.prev_button.clicked.connect(self.previous_clicked)
        self.play_button.clicked.connect(self.play_clicked)
        self.next_button.clicked.connect(self.next_clicked)
        self.rewind_button.clicked.connect(lambda: self._skip(-SKIP_MS))
        self.forward_button.clicked.connect(lambda: self._skip(SKIP_MS))
        self.full_button.clicked.connect(self.full_screen_requested)
        self.volume.valueChanged.connect(self.volume_changed)
        self.seek.clicked_to.connect(self.seek_requested)
        self.seek.sliderReleased.connect(lambda: self.seek_requested.emit(self.seek.value()))

        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for keys, slot in (("F", self.full_screen_requested.emit), ("Esc", self._escape),
                           ("Left", lambda: self._skip(-SKIP_MS)), ("Right", lambda: self._skip(SKIP_MS))):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(context)
            shortcut.activated.connect(slot)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._maybe_hide_controls)
        self.controls.hide()
        self.close_button.hide()

    @staticmethod
    def _button(icon, tooltip) -> QToolButton:
        button = QToolButton()
        button.setObjectName("videoButton")
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    # ---- Driven by the main window -------------------------------------------------

    def show_track(self, track: dict):
        self.surface.clear()
        self.update_position(0, track.get("total_time") or 0)
        self.setFocus()
        self.show_controls()

    def update_position(self, position: int, duration: int):
        self._position, self._duration = position, duration
        if self.seek.isSliderDown():
            return
        self.seek.setMaximum(max(0, duration))
        self.seek.setValue(position)
        self.elapsed.setText(format_duration(position))
        self.remaining.setText("-" + format_duration(max(0, duration - position)))

    def set_playing(self, playing: bool):
        self.play_button.setIcon(self._pause_icon if playing else self._play_icon)

    def set_volume(self, value: int):
        self.volume.blockSignals(True)       # don't echo it back
        self.volume.setValue(value)
        self.volume.blockSignals(False)

    def set_full_screen(self, full: bool):
        self.full_screen = full
        self.full_button.setToolTip("Exit Full Screen (Esc)" if full else "Full Screen (F)")
        self.show_controls()

    # ---- Layout and the controls coming and going ----------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self):
        rect = self.rect()
        self.surface.setGeometry(rect)
        width = min(rect.width() - 24, CONTROLS_MAX_WIDTH)
        height = self.controls.sizeHint().height()
        self.controls.setGeometry((rect.width() - width) // 2, rect.height() - height - 24,
                                  width, height)
        self.close_button.move(rect.width() - CLOSE_SIZE - 12, 12)
        self.controls.raise_()
        self.close_button.raise_()

    def show_controls(self):
        self.controls.show()
        self.close_button.show()
        self._layout_children()
        self.surface.unsetCursor()
        self._hide_timer.start()

    def _maybe_hide_controls(self):
        if self.controls.underMouse() or self.close_button.underMouse():
            self._hide_timer.start()
            return
        self.controls.hide()
        self.close_button.hide()
        if self.full_screen:
            self.surface.setCursor(Qt.CursorShape.BlankCursor)

    def eventFilter(self, obj, event):
        kind = event.type()
        if obj is self.surface:
            if kind == QEvent.Type.MouseButtonDblClick:
                self.full_screen_requested.emit()
                return True
            if kind == QEvent.Type.MouseButtonPress:
                self.setFocus()
            if kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
                self.show_controls()
        elif kind == QEvent.Type.Enter:
            self._hide_timer.stop()          # never hide while the mouse is on them
        elif kind == QEvent.Type.Leave:
            self._hide_timer.start()
        return super().eventFilter(obj, event)

    def mouseMoveEvent(self, event):
        self.show_controls()
        super().mouseMoveEvent(event)

    def _skip(self, delta: int):
        self.seek_requested.emit(max(0, min(self._duration, self._position + delta)))
        self.show_controls()

    def _escape(self):
        if self.full_screen:
            self.full_screen_requested.emit()
        else:
            self.close_requested.emit()
