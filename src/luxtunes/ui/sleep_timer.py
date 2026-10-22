"""Controls → Sleep Timer…: music stops after a while.

The window offers 15, 30 and 60 minutes and 2, 4 and 8 hours, and shows the
time (by the computer's clock) the music will stop. While the timer runs, a
moon button beside the volume shows the time left; clicking it turns the timer
off. At the end the music fades out over a few seconds and pauses, and the
volume goes back to where it was.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDialogButtonBox, QLabel, QRadioButton, QVBoxLayout,
)

CHOICES = [(15, "15 minutes"), (30, "30 minutes"), (60, "1 hour"), (120, "2 hours"),
           (240, "4 hours"), (480, "8 hours")]
FADE_SECONDS = 8


def clock(moment: datetime) -> str:
    """'11:45 PM' (today) or 'Tue 3:15 AM' (another day)."""
    text = moment.strftime("%I:%M %p").lstrip("0")
    return text if moment.date() == datetime.now().date() else moment.strftime("%a ") + text


def left_text(seconds: float) -> str:
    """'1:05' (hours:minutes) or '42m' / '<1m'."""
    minutes = max(0, int(seconds + 59) // 60)
    if minutes >= 60:
        return f"{minutes // 60}:{minutes % 60:02d}"
    return f"{minutes}m" if minutes else "<1m"


class SleepTimerDialog(QDialog):
    def __init__(self, minutes: int = 30, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sleep Timer")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Stop playing music after:"))
        self.group = QButtonGroup(self)
        for value, label in CHOICES:
            button = QRadioButton(label)
            self.group.addButton(button, value)
            layout.addWidget(button)
            button.setChecked(value == minutes)
        if self.group.checkedId() < 0:
            self.group.button(30).setChecked(True)
        self.preview = QLabel()
        font = self.preview.font()
        font.setBold(True)
        self.preview.setFont(font)
        layout.addSpacing(6)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.group.idToggled.connect(lambda *_: self._update_preview())
        self._clock = QTimer(self)                # keeps the preview right as time passes
        self._clock.timeout.connect(self._update_preview)
        self._clock.start(1000)
        self._update_preview()

    @property
    def minutes(self) -> int:
        return self.group.checkedId()

    def _update_preview(self):
        end = datetime.now() + timedelta(minutes=self.minutes)
        self.preview.setText(f"Music will stop at {clock(end)}.")


class SleepTimer(QObject):
    """Counts down; fades the music out and pauses it at the end."""

    changed = Signal()            # started, ticked (each minute), stopped
    finished = Signal()

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.end: datetime | None = None
        self.minutes = 30                         # last choice, offered again
        self._tick = QTimer(self)
        self._tick.setInterval(1000)
        self._tick.timeout.connect(self._check)
        self._fade = QTimer(self)
        self._fade.setInterval(100)
        self._fade.timeout.connect(self._fade_step)
        self._fade_left = 0
        self._last_text = ""

    @property
    def active(self) -> bool:
        return self.end is not None

    def seconds_left(self) -> float:
        return max(0.0, (self.end - datetime.now()).total_seconds()) if self.end else 0.0

    def start(self, minutes: int):
        self.minutes = minutes
        self.end = datetime.now() + timedelta(minutes=minutes)
        self._tick.start()
        self.changed.emit()

    def stop(self):
        """Turn the timer off (a fade in progress stops and the volume comes back)."""
        if self._fade.isActive():
            self._fade.stop()
            self.player.apply_volume()
        self.end = None
        self._tick.stop()
        self.changed.emit()

    def _check(self):
        if self.end is None:
            return
        text = left_text(self.seconds_left())
        if text != self._last_text:
            self._last_text = text
            self.changed.emit()
        if self.seconds_left() <= FADE_SECONDS and not self._fade.isActive():
            if not self.player.is_playing:
                self._done()
                return
            self._fade_left = FADE_SECONDS * 10
            self._fade.start()

    def _fade_step(self):
        self._fade_left -= 1
        if self._fade_left > 0 and self.player.is_playing:
            self.player.fade_to(self._fade_left / (FADE_SECONDS * 10))
            return
        self._fade.stop()
        if self.player.is_playing:
            self.player.toggle()                  # pause
        self.player.apply_volume()                # back to the slider's volume
        self._done()

    def _done(self):
        self.end = None
        self._tick.stop()
        self.changed.emit()
        self.finished.emit()

