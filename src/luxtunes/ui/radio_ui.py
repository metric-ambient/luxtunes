"""The parts of PLAYING → Radio on screen (core/radio.py picks the songs):

- the heart and the circle-with-a-line in front of each radio song
  (OpinionDelegate): like this song on this radio / not interested;
- the bar along the bottom (RadioBar): the radio's name, Save as Playlist,
  Refresh, Discover and Likes & Dislikes…. View → Radio Likes & Dislikes
  shows the same column there, with a short note in the bar.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStyledItemDelegate, QToolButton, QWidget

from luxtunes.ui.track_model import TrackRole

LIKED = QColor("#e0245e")


class OpinionDelegate(QStyledItemDelegate):
    """Two small buttons in one cell: ♥ (like) and ⊘ (not interested)."""
    clicked = Signal(dict, str)          # the track, "like" or "dislike"

    def paint(self, painter, option, index):
        track = index.data(TrackRole) or {}
        opinion = track.get("radio_opinion") or 0
        super().paint(painter, option, index)            # background and selection
        painter.save()
        heart, ban = self._halves(option.rect)
        font = QFont(option.font)
        font.setPointSizeF(font.pointSizeF() + 1)
        painter.setFont(font)
        dim = option.palette.color(QPalette.ColorRole.PlaceholderText)
        text = option.palette.color(QPalette.ColorRole.Text)
        painter.setPen(LIKED if opinion > 0 else dim)
        painter.drawText(heart, Qt.AlignmentFlag.AlignCenter, "♥" if opinion > 0 else "♡")
        if opinion < 0:
            font.setBold(True)
            painter.setFont(font)
        painter.setPen(text if opinion < 0 else dim)
        painter.drawText(ban, Qt.AlignmentFlag.AlignCenter, "⊘")
        painter.restore()

    @staticmethod
    def _halves(rect: QRect) -> tuple[QRect, QRect]:
        half = rect.width() // 2
        return (QRect(rect.x(), rect.y(), half, rect.height()),
                QRect(rect.x() + half, rect.y(), rect.width() - half, rect.height()))

    def helpEvent(self, event, view, option, index):
        from PySide6.QtWidgets import QToolTip
        heart, _ = self._halves(option.rect)
        tip = "Like this song on Radio Mixes like this one" if heart.contains(event.pos()) \
            else "Not interested: skip it, take it off this Radio Mix and keep it off ones like it"
        QToolTip.showText(event.globalPos(), tip, view)
        return True

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseButtonRelease and \
                event.button() == Qt.MouseButton.LeftButton:
            track = index.data(TrackRole)
            if track:
                heart, _ = self._halves(option.rect)
                self.clicked.emit(track, "like" if heart.contains(event.position().toPoint())
                                  else "dislike")
            return True
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            return True                  # a click on the buttons doesn't play the song
        return super().editorEvent(event, model, option, index)


class RadioBar(QWidget):
    """Along the bottom of PLAYING → Radio (and of View → Radio Likes & Dislikes)."""
    save_clicked = Signal()
    refresh_clicked = Signal()
    discover_toggled = Signal(bool)
    likes_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dupBar")                     # looks like the Duplicates bar
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)
        self.label = QLabel()
        self.label.setObjectName("dupLabel")
        self.save = self._button("Save as Playlist", "Make a playlist with the songs on this radio")
        self.refresh = self._button("Refresh", "A new mix from the same start")
        self.discover = self._button("Discover", "Prioritizes related songs you rarely or never play.")
        self.discover.setCheckable(True)
        # "&&": a single & would be taken as a keyboard shortcut marker and vanish.
        self.likes = self._button("Likes && Dislikes…", "Every song you've liked or disliked on a Radio Mix")
        row.addWidget(self.label, 1)
        for button in (self.save, self.refresh, self.discover, self.likes):
            row.addWidget(button)
        self.save.clicked.connect(self.save_clicked)
        self.refresh.clicked.connect(self.refresh_clicked)
        self.discover.toggled.connect(self.discover_toggled)
        self.likes.clicked.connect(self.likes_clicked)
        self.hide()

    def _button(self, text: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("dupButton")
        button.setText(text)
        button.setToolTip(tip)
        return button

    def show_radio(self, title: str | None, count: int, discover: bool):
        """For PLAYING → Radio: the radio's name and its buttons."""
        if title:
            self.label.setText(f"{title} · {count:,} song{'s' if count != 1 else ''}"
                               + (" · Discover" if discover else ""))
        else:
            self.label.setText("No Radio Mix yet: right-click a song and choose Start Radio Mix.")
        for button in (self.save, self.refresh, self.discover):
            button.setVisible(True)
            button.setEnabled(bool(title))
        self.likes.setVisible(True)
        self.discover.blockSignals(True)
        self.discover.setChecked(discover)
        self.discover.blockSignals(False)
        self.show()

    def show_likes(self, count: int):
        """For View → Radio Likes & Dislikes: a note, no buttons."""
        self.label.setText(f"{count:,} song{'s' if count != 1 else ''} you've liked or disliked "
                           "on radios. Click the heart or circle again (or press Delete) to set "
                           "a song back to neutral.")
        for button in (self.save, self.refresh, self.discover, self.likes):
            button.setVisible(False)
        self.show()
