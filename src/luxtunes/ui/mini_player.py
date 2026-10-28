"""The Mini Player (Controls > Mini Player): a small window with the playing
song and its controls, no artwork, while the main window is minimized.

⤢ (or closing it) brings the main window back.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStyle, QToolButton, QVBoxLayout, QWidget

from luxtunes.ui.formatting import format_duration
from luxtunes.ui.widgets import JumpSlider, ScrollingLabel, themed_icon


class MiniPlayer(QWidget):
    play_clicked = Signal()
    next_clicked = Signal()
    previous_clicked = Signal()
    seek_requested = Signal(int)
    volume_changed = Signal(int)
    restore_requested = Signal()          # bring back the main window

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("LuxTunes Mini Player")
        self.setObjectName("miniPlayer")
        self.setMinimumWidth(380)
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(self.close)
        QShortcut(QKeySequence("Space"), self).activated.connect(self.play_clicked)

        icon = themed_icon
        self.prev_button = self._button(
            icon(["media-skip-backward"], QStyle.StandardPixmap.SP_MediaSkipBackward), "Previous")
        self.play_button = self._button(
            icon(["media-playback-start"], QStyle.StandardPixmap.SP_MediaPlay), "Play")
        self.next_button = self._button(
            icon(["media-skip-forward"], QStyle.StandardPixmap.SP_MediaSkipForward), "Next")
        self._play_icon = self.play_button.icon()
        self._pause_icon = icon(["media-playback-pause"], QStyle.StandardPixmap.SP_MediaPause)
        self.restore_button = self._button(
            icon(["view-fullscreen", "window-maximize"], QStyle.StandardPixmap.SP_TitleBarMaxButton),
            "Show the LuxTunes window")

        # Names too long for the window scroll sideways.
        self.title = ScrollingLabel("LuxTunes")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.subtitle = ScrollingLabel("")

        self.elapsed = QLabel("0:00")
        self.remaining = QLabel("-0:00")
        self.seek = JumpSlider(Qt.Orientation.Horizontal)
        self.seek.setObjectName("lcdSlider")
        self.volume = JumpSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(80)
        self.volume.setToolTip("Volume")

        buttons = QHBoxLayout()
        buttons.setSpacing(2)
        for button in (self.prev_button, self.play_button, self.next_button):
            buttons.addWidget(button)
        buttons.addSpacing(8)
        buttons.addWidget(self.volume)
        text = QVBoxLayout()
        text.setSpacing(0)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        top = QHBoxLayout()
        top.addLayout(buttons)
        top.addLayout(text, 1)
        top.addWidget(self.restore_button)
        bottom = QHBoxLayout()
        bottom.addWidget(self.elapsed)
        bottom.addWidget(self.seek, 1)
        bottom.addWidget(self.remaining)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addLayout(bottom)

        self.prev_button.clicked.connect(self.previous_clicked)
        self.play_button.clicked.connect(self.play_clicked)
        self.next_button.clicked.connect(self.next_clicked)
        self.restore_button.clicked.connect(self.close)
        self.volume.valueChanged.connect(self.volume_changed)
        self.seek.clicked_to.connect(self.seek_requested)
        self.seek.sliderReleased.connect(lambda: self.seek_requested.emit(self.seek.value()))
        self.resize(460, self.sizeHint().height())

    def set_on_top(self, on: bool):
        """Preferences → Playback → Keep the Mini Player on top of other windows."""
        if bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) != on:
            visible = self.isVisible()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
            if visible:
                self.show()                  # changing the flag hides the window

    @staticmethod
    def _button(icon, tooltip) -> QToolButton:
        button = QToolButton()
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        return button

    # ---- Updates from the main window ---------------------------------------

    def show_track(self, track: dict | None):
        if not track:
            self.title.setText("LuxTunes")
            self.subtitle.setText("")
            self.setWindowTitle("LuxTunes Mini Player")
            self.update_position(0, 0)
            return
        name = track.get("name") or ""
        self.title.setText(name)
        self.subtitle.setText(" — ".join(p for p in (track.get("artist"), track.get("album")) if p))
        self.setWindowTitle(" - ".join(p for p in (name, track.get("artist")) if p) or "LuxTunes")

    def update_position(self, position: int, duration: int):
        if self.seek.isSliderDown():
            return
        self.seek.setMaximum(max(0, duration))
        self.seek.setValue(position)
        self.elapsed.setText(format_duration(position))
        self.remaining.setText("-" + format_duration(max(0, duration - position)))

    def set_playing(self, playing: bool):
        self.play_button.setIcon(self._pause_icon if playing else self._play_icon)
        self.play_button.setToolTip("Pause" if playing else "Play")

    def set_volume(self, value: int):
        self.volume.blockSignals(True)          # don't echo the change back
        self.volume.setValue(value)
        self.volume.blockSignals(False)

    def closeEvent(self, event):
        self.restore_requested.emit()
        super().closeEvent(event)
