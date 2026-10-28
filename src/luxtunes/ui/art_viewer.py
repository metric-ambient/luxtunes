"""Right-click the album art in the bottom-left → View Full Size Album Art: the
picture on its own in a window you can resize as you like, while the music
carries on. Unlike the Pop-Out Player, it shows the art of the song you
picked, not the one playing."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import QApplication, QWidget


class ArtViewer(QWidget):
    def __init__(self, data: bytes, title: str = "", parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title or "Album Art")
        self._pixmap = QPixmap()
        self._pixmap.loadFromData(data)
        self.setMinimumSize(120, 120)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        QShortcut(QKeySequence.StandardKey.Close, self, activated=self.close)
        self.resize(self._first_size())

    def has_image(self) -> bool:
        return not self._pixmap.isNull()

    def _first_size(self) -> QSize:
        """The picture's own size, but no more than most of the screen."""
        screen = self.screen() or QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        size = self._pixmap.size() if not self._pixmap.isNull() else QSize(500, 500)
        if area is not None:
            size = size.scaled(min(size.width(), area.width() * 8 // 10),
                               min(size.height(), area.height() * 8 // 10),
                               Qt.AspectRatioMode.KeepAspectRatio)
        return size.expandedTo(QSize(120, 120))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        if self._pixmap.isNull():
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        shown = self._pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        x = (self.width() - shown.width()) // 2
        y = (self.height() - shown.height()) // 2
        painter.drawPixmap(x, y, shown.width(), shown.height(), self._pixmap)
