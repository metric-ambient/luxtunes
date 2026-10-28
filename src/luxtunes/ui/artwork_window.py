"""The pop-out artwork player: opens when you click the album art.

It shows the art large and puts playback
controls over the bottom of it while the mouse is over the window. The Lyrics
button (♪ in the controls) lays the song's lyrics over the art; with timed
lyrics (LRC, from Get Lyrics Online… or the file), the line being sung is
highlighted and kept in the middle as the song plays. It opens
at the picture's own size (up to 90% of the screen) and can be stretched
bigger or smaller with the grip in the bottom-right corner. Colours come
from the app theme (light/dark) via the stylesheet in theme.py.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizeGrip,
    QStyle,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core import lrc
from luxtunes.ui.formatting import format_duration
from luxtunes.ui.widgets import JumpSlider, themed_icon

SEEK_STEP_MS = 10_000      # what the ◀◀ / ▶▶ buttons jump
CONTROLS_MAX_WIDTH = 520
HIDE_DELAY_MS = 2500       # controls fade out this long after the mouse stops


class ArtworkWindow(QWidget):
    play_clicked = Signal()
    next_clicked = Signal()
    previous_clicked = Signal()
    seek_requested = Signal(int)
    volume_changed = Signal(int)
    lyrics_toggled = Signal(bool)          # remembered by the main window

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        close = QShortcut(QKeySequence("Ctrl+W"), self)   # File → Close Window
        close.activated.connect(self.close)
        self.setObjectName("artWindow")
        self.setWindowTitle("LuxTunes")
        self.setMinimumSize(220, 220)
        self.setMouseTracking(True)
        self._pixmap: QPixmap | None = None
        self._position = 0
        self._duration = 0

        self.image = QLabel(self)
        self.image.setObjectName("artWindowImage")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMouseTracking(True)

        # Lyrics over the art (the Lyrics button turns them on and off).
        self.lyrics_view = QTextBrowser(self)
        self.lyrics_view.setObjectName("artLyrics")
        self.lyrics_view.setFrameShape(QFrame.Shape.NoFrame)
        self.lyrics_view.viewport().setMouseTracking(True)
        self.lyrics_view.viewport().installEventFilter(self)
        self.lyrics_view.hide()
        self._lyrics = ""
        self._timed: list[tuple[int, str]] = []    # (ms, line) when the lyrics are timed
        self._line = -2                             # the line highlighted now
        self._follow = True             # Preferences → Playback → highlight lyrics as they're sung
        self._timed_text: str | None = None

        # ---- Controls overlay ----------------------------------------------
        self.controls = QFrame(self)
        self.controls.setObjectName("artControls")
        self.controls.installEventFilter(self)

        icon = lambda names, sp: themed_icon(names, sp)
        self.prev_button = self._button(icon(["media-skip-backward"], QStyle.StandardPixmap.SP_MediaSkipBackward), "Previous")
        self.rewind_button = self._button(icon(["media-seek-backward"], QStyle.StandardPixmap.SP_MediaSeekBackward), "Back 10 seconds")
        self.play_button = self._button(icon(["media-playback-start"], QStyle.StandardPixmap.SP_MediaPlay), "Play")
        self.forward_button = self._button(icon(["media-seek-forward"], QStyle.StandardPixmap.SP_MediaSeekForward), "Forward 10 seconds")
        self.next_button = self._button(icon(["media-skip-forward"], QStyle.StandardPixmap.SP_MediaSkipForward), "Next")
        self.lyrics_button = self._button(None, "Show lyrics over the artwork")
        self.lyrics_button.setText("Lyrics")
        self.lyrics_button.setCheckable(True)
        self.lyrics_button.toggled.connect(self._lyrics_toggled)
        self._play_icon = self.play_button.icon()
        self._pause_icon = icon(["media-playback-pause"], QStyle.StandardPixmap.SP_MediaPause)

        self.volume = JumpSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(90)
        quiet, loud = QLabel(), QLabel()
        quiet.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolumeMuted).pixmap(14, 14))
        loud.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume).pixmap(14, 14))

        top = QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(quiet)
        top.addWidget(self.volume)
        top.addWidget(loud)
        top.addStretch(1)
        for button in (self.prev_button, self.rewind_button, self.play_button,
                       self.forward_button, self.next_button):
            top.addWidget(button)
        top.addStretch(1)
        top.addWidget(self.lyrics_button)

        self.elapsed = QLabel("0:00")
        self.remaining = QLabel("-0:00")
        self.seek = JumpSlider(Qt.Orientation.Horizontal)
        self.seek.setObjectName("lcdSlider")
        bottom = QHBoxLayout()
        bottom.addWidget(self.elapsed)
        bottom.addWidget(self.seek, 1)
        bottom.addWidget(self.remaining)

        layout = QVBoxLayout(self.controls)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addLayout(bottom)

        self.grip = QSizeGrip(self)   # drag to resize, past the picture's own size too

        self.prev_button.clicked.connect(self.previous_clicked)
        self.play_button.clicked.connect(self.play_clicked)
        self.next_button.clicked.connect(self.next_clicked)
        self.rewind_button.clicked.connect(
            lambda: self.seek_requested.emit(max(0, self._position - SEEK_STEP_MS)))
        self.forward_button.clicked.connect(
            lambda: self.seek_requested.emit(min(self._duration, self._position + SEEK_STEP_MS)))
        self.volume.valueChanged.connect(self.volume_changed)
        self.seek.clicked_to.connect(self.seek_requested)
        self.seek.sliderReleased.connect(lambda: self.seek_requested.emit(self.seek.value()))

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._maybe_hide_controls)
        self.controls.hide()

    @staticmethod
    def _button(icon, tooltip) -> QToolButton:
        button = QToolButton()
        button.setObjectName("overlayButton")
        if icon is not None:
            button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        return button

    # ---- Updates from the main window ---------------------------------------

    def show_track(self, track: dict | None, art: bytes | None):
        if track:
            parts = [track.get("name"), track.get("artist")]
            self.setWindowTitle(" - ".join(p for p in parts if p) or "LuxTunes")
        pixmap = QPixmap()
        self._pixmap = pixmap if art and pixmap.loadFromData(art) else None
        self._layout_children()

    def open_at_art_size(self):
        """Size the window to the picture's own resolution (max 90% of the screen)."""
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry()
        side = 500
        if self._pixmap is not None:
            side = max(self._pixmap.width(), self._pixmap.height())
        side = max(self.minimumWidth(), min(side, int(area.width() * 0.9), int(area.height() * 0.9)))
        self.resize(side, side)

    def set_lyrics(self, text: str | None, timed: str | None = None):
        """The song's lyrics; `timed` (LRC) highlights the line being sung. Lyrics
        that are themselves in LRC form are timed too."""
        self._lyrics = (text or "").strip()
        self._timed_text = timed
        self._timed = (lrc.parse(timed) or lrc.parse(self._lyrics)) if self._follow else []
        self._line = -2
        if self._timed:
            self._show_timed(lrc.line_at(self._timed, self._position))
        elif self._lyrics:
            self.lyrics_view.setPlainText(lrc.plain(self._lyrics))     # never the raw times
        else:
            self.lyrics_view.setHtml("<p><i>No lyrics for this song.</i></p>")
        if not self._timed:
            self.lyrics_view.document().setDefaultStyleSheet("")
            self._center_lyrics()
        self.lyrics_view.verticalScrollBar().setValue(0)

    def set_follow(self, on: bool):
        """Highlight the line being sung (on), or show timed lyrics as plain text."""
        if on != self._follow:
            self._follow = on
            self.set_lyrics(self._lyrics, self._timed_text)

    def _show_timed(self, current: int):
        """Every line, the one being sung bright and bold, the rest dimmed; the
        current one is scrolled to the middle."""
        import html
        self._line = current
        palette = self.lyrics_view.palette()
        bright = palette.color(palette.ColorRole.Text)
        base = palette.color(palette.ColorRole.Base)
        dim = "#%02x%02x%02x" % tuple(round(b + (t - b) * 0.45) for t, b in (
            (bright.red(), base.red()), (bright.green(), base.green()), (bright.blue(), base.blue())))
        rows = []
        for i, (_, line) in enumerate(self._timed):
            text = html.escape(line) or "&nbsp;"
            if i == current:
                rows.append(f'<p style="color:{bright.name()}; font-weight:bold">{text}</p>')
            else:
                rows.append(f'<p style="color:{dim}">{text}</p>')
        bar = self.lyrics_view.verticalScrollBar()
        self.lyrics_view.setHtml("".join(rows))
        self._center_lyrics()
        if current < 0:
            bar.setValue(0)
            return
        from PySide6.QtGui import QTextCursor
        block = self.lyrics_view.document().findBlockByNumber(current)
        cursor = QTextCursor(block)
        middle = self.lyrics_view.cursorRect(cursor).center().y()
        bar.setValue(bar.value() + middle - self.lyrics_view.viewport().height() // 2)

    def set_lyrics_shown(self, shown: bool):
        self.lyrics_button.setChecked(shown)
        self.lyrics_view.setVisible(shown)

    def _lyrics_toggled(self, shown: bool):
        self.lyrics_view.setVisible(shown)
        self._layout_children()
        if shown and self._timed:
            self._show_timed(lrc.line_at(self._timed, self._position))   # placed: now centre it
        self.lyrics_toggled.emit(shown)

    def _center_lyrics(self):
        from PySide6.QtGui import QTextBlockFormat, QTextCursor
        cursor = QTextCursor(self.lyrics_view.document())
        cursor.select(QTextCursor.SelectionType.Document)
        block = QTextBlockFormat()
        block.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        block.setLineHeight(130, 1)                       # 1 = proportional
        cursor.mergeBlockFormat(block)

    def update_position(self, position: int, duration: int):
        self._position, self._duration = position, duration
        if self._timed:
            line = lrc.line_at(self._timed, position)
            if line != self._line and self.lyrics_view.isVisible():
                self._show_timed(line)
        if self.seek.isSliderDown():
            return
        self.seek.setMaximum(max(0, duration))
        self.seek.setValue(position)
        self.elapsed.setText(format_duration(position))
        self.remaining.setText("-" + format_duration(max(0, duration - position)))

    def set_playing(self, playing: bool):
        self.play_button.setIcon(self._pause_icon if playing else self._play_icon)

    def set_volume(self, value: int):
        self.volume.blockSignals(True)   # don't echo the change back
        self.volume.setValue(value)
        self.volume.blockSignals(False)

    # ---- Layout and showing/hiding the controls -------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self):
        rect = self.rect()
        self.image.setGeometry(rect)
        self.lyrics_view.setGeometry(rect)
        if self.lyrics_view.isVisible():
            self.lyrics_view.raise_()
        if self._pixmap is None:
            self.image.setPixmap(QPixmap())
            self.image.setText("No Artwork")
        else:
            # Scales up past the picture's own size too, if you stretch the window.
            self.image.setPixmap(self._pixmap.scaled(
                rect.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        width = min(rect.width() - 24, CONTROLS_MAX_WIDTH)
        height = self.controls.sizeHint().height()
        self.controls.setGeometry((rect.width() - width) // 2, rect.height() - height - 14, width, height)
        self.controls.raise_()
        grip = self.grip.sizeHint()
        self.grip.setGeometry(rect.width() - grip.width(), rect.height() - grip.height(),
                              grip.width(), grip.height())
        self.grip.raise_()

    def enterEvent(self, event):
        self._show_controls()
        super().enterEvent(event)

    def mouseMoveEvent(self, event):
        self._show_controls()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.controls.hide()
        super().leaveEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.controls and event.type() == QEvent.Type.Enter:
            self._hide_timer.stop()   # never hide while the mouse is on the controls
        elif obj is self.lyrics_view.viewport() and event.type() in (
                QEvent.Type.MouseMove, QEvent.Type.Enter):
            self._show_controls()     # the lyrics cover the window: controls still appear
        return super().eventFilter(obj, event)

    def _show_controls(self):
        self.controls.show()
        self.controls.raise_()
        self._hide_timer.start()

    def _maybe_hide_controls(self):
        if not self.controls.underMouse():
            self.controls.hide()
