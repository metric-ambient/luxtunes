"""Controls → Sleep Timer…: music stops after a while.

Clicking the moon in the media bar (or Controls → Sleep Timer…) opens a small
pop-up that grows up from just above the moon: 15, 30 and 60 minutes and 2, 4
and 8 hours, each with the time (by the computer's clock) the music would
stop. Click one to start the timer; click anywhere else (or press Esc) and the
pop-up goes away. While the timer runs, the moon shows the time left under it;
clicking it turns the timer off. At the end the music fades out over a few
seconds and pauses, and the volume goes back to where it was.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QAbstractButton, QFrame, QLabel, QVBoxLayout, QWidget

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


class SleepChoice(QAbstractButton):
    """One line of the pop-up: "30 minutes" on the left, when the music would
    stop on the right. The one chosen last time is in the accent color."""

    def __init__(self, minutes: int, label: str, parent=None):
        super().__init__(parent)
        self.minutes = minutes
        self.setText(label)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{label}, until {self.end_text()}")

    def end_text(self) -> str:
        return clock(datetime.now() + timedelta(minutes=self.minutes))

    def sizeHint(self):
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + metrics.horizontalAdvance("Tue 12:00 PM")
                     + 56, max(30, metrics.height() + 14))

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        elif event.key() == Qt.Key.Key_Down:
            self.focusNextChild()
        elif event.key() == Qt.Key.Key_Up:
            self.focusPreviousChild()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        from luxtunes.ui import theme
        colors = theme.current
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() or self.hasFocus():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(colors["button_top"]))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0, 1, 0, -1), 5, 5)
        font = QFont(self.font())
        font.setBold(self.isChecked())
        p.setFont(font)
        text_rect = self.rect().adjusted(12, 0, -12, 0)
        p.setPen(QColor(colors["highlight"] if self.isChecked() else colors["text"]))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())
        p.setFont(self.font())
        p.setPen(QColor(colors["text_dim"]))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   self.end_text())
        p.end()


class SleepTimerPopup(QWidget):
    """The little pop-up above the moon. It closes itself when a time is
    chosen, or on a click anywhere outside it (a Qt pop-up does that)."""
    chosen = Signal(int)          # minutes
    closed = Signal()

    GROW_MS = 150

    def __init__(self, minutes: int = 30, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.body = QFrame(self)                 # the rounded panel; it stays at the bottom
        self.body.setObjectName("sleepPopup")    # while the pop-up grows upwards
        column = QVBoxLayout(self.body)
        column.setContentsMargins(6, 8, 6, 6)
        column.setSpacing(0)
        title = QLabel("Stop the music after")
        title.setObjectName("sleepPopupTitle")
        title.setContentsMargins(12, 0, 12, 4)
        column.addWidget(title)
        self.choices: list[SleepChoice] = []
        for value, label in CHOICES:
            choice = SleepChoice(value, label)
            choice.setChecked(value == minutes)
            choice.clicked.connect(lambda _=False, v=value: self._choose(v))
            column.addWidget(choice)
            self.choices.append(choice)
        self.body.setFixedSize(self.body.sizeHint())
        self._clock = QTimer(self)               # keeps the times right as the minutes pass
        self._clock.timeout.connect(lambda: [c.update() for c in self.choices])
        self._clock.start(10_000)
        self._grow = QPropertyAnimation(self, b"geometry", self)
        self._grow.setDuration(self.GROW_MS)
        self._grow.setEasingCurve(QEasingCurve.Type.OutCubic)

    @property
    def minutes(self) -> int:
        return next((c.minutes for c in self.choices if c.isChecked()), 30)

    def _choose(self, minutes: int):
        for choice in self.choices:
            choice.setChecked(choice.minutes == minutes)
        self.chosen.emit(minutes)
        self.close()

    def open_above(self, anchor: QWidget | None, window: QWidget | None = None):
        """Grow up from just above `anchor` (or open in the middle of `window`
        when the anchor isn't on screen)."""
        size = self.body.size()
        if anchor is not None and anchor.isVisible():
            top_left = anchor.mapToGlobal(QPoint(anchor.width() // 2 - size.width() // 2,
                                                 -size.height() - 6))
            screen = anchor.screen()
        else:
            host = window or self.parentWidget()
            center = host.mapToGlobal(host.rect().center()) if host else QPoint(400, 300)
            top_left = center - QPoint(size.width() // 2, size.height() // 2)
            screen = host.screen() if host else None
        if screen is not None:
            area = screen.availableGeometry()
            top_left.setX(max(area.left(), min(top_left.x(), area.right() - size.width())))
            top_left.setY(max(area.top(), top_left.y()))
        final = QRect(top_left, size)
        start = QRect(final.left(), final.bottom(), final.width(), 1)
        self.setGeometry(start)
        self.show()
        current = next((c for c in self.choices if c.isChecked()), self.choices[0])
        current.setFocus()
        self._grow.setStartValue(start)
        self._grow.setEndValue(final)
        self._grow.start()

    def resizeEvent(self, event):
        # The panel keeps its full size, pinned to the bottom: growing the
        # window upwards uncovers it from the moon up.
        self.body.move(0, self.height() - self.body.height())
        super().resizeEvent(event)

    def hideEvent(self, event):
        self.closed.emit()
        super().hideEvent(event)


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

