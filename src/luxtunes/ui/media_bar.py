"""Parts of the window's new layout:

  LibraryTab   a library section's tab at the top left (Music, Movies…),
               which also takes songs dropped on it, as the sidebar's
               Library entries did;
  ArtThumb     the playing (or selected) song's art, small, at the bottom
               left of the media bar; click for the Pop-Out Player,
               right-click for full-size art;
  ArtCorner    keeps the opened album art in the bottom-left corner: over
               the media bar's corner and up into the sidebar, as wide as
               the sidebar, with room kept under the sidebar's list for it;
  ElidedLabel  the library stats under the volume: cut short with "…" when
               there isn't room (the whole text is in its tooltip);
  Follow       shows a page's buttons in the media bar only while the page is
               shown (an iPod's Sync, a CD's Import CD…).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QMenu, QToolButton

from luxtunes.ui.track_model import TRACK_IDS_MIME, track_ids_from_mime


class LibraryTab(QToolButton):
    tracks_dropped = Signal(object, list)    # this tab's source, track ids

    def __init__(self, source, label: str, parent=None):
        super().__init__(parent)
        self.source = source
        self.setObjectName("libraryTab")
        self.setText(label)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setCheckable(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(20, 20))
        self.setFixedSize(66, 50)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TRACK_IDS_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event):
        ids = track_ids_from_mime(event.mimeData())
        if ids:
            self.tracks_dropped.emit(self.source, ids)
            event.acceptProposedAction()


class ArtThumb(QLabel):
    clicked = Signal()
    view_requested = Signal()

    SIZE = 76                    # the media bar's height: the art fills its corner

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("artThumb")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to open the Pop-Out Player")
        self.setAccessibleName("Album art: open the Pop-Out Player")

    def set_picture(self, pixmap: QPixmap | None):
        if pixmap is None or pixmap.isNull():
            self.setPixmap(QPixmap())
            self.setText("♪")
            return
        ratio = self.devicePixelRatioF()
        scaled = pixmap.scaled(int(self.SIZE * ratio), int(self.SIZE * ratio),
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.setText("")
        self.setPixmap(scaled)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        view = menu.addAction("View Full Size Album Art", self.view_requested.emit)
        view.setEnabled(self.pixmap() is not None and not self.pixmap().isNull())
        menu.exec(event.globalPos())


class ArtCorner(QObject):
    """Places the opened art over the window's bottom-left corner whenever
    the window, the sidebar or the media bar change size."""

    def __init__(self, art, central, left_panel, room, media_bar, corner_spacer, margin: int):
        super().__init__(central)
        self.art, self.central, self.left_panel = art, central, left_panel
        self.room, self.media_bar, self.corner_spacer = room, media_bar, corner_spacer
        self.margin = margin
        self.open = False
        for widget in (central, left_panel, media_bar):
            widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self.place()
        return False

    def set_open(self, open_: bool):
        self.open = open_
        self.place()

    def place(self):
        shown = self.open and self.media_bar.isVisible() and self.left_panel.isVisible()
        if not shown:
            self.art.hide()
            self.room.setFixedHeight(0)
            self.corner_spacer.setFixedWidth(0)
            self._keep_room()
            return
        width = self.left_panel.width()
        height = self.art.header.sizeHint().height() + min(width, self.art.max_side())
        bar = self.media_bar.geometry()
        self.art.setGeometry(0, bar.bottom() + 1 - height, width, height)
        self.art.show()
        self.art.raise_()
        self.room.setFixedHeight(max(0, height - bar.height()))
        # The arrow sits just right of the art.
        self.corner_spacer.setFixedWidth(max(0, width - self.margin))
        self._keep_room()

    def _keep_room(self):
        """The media bar's left part never gets narrower than the art (or
        thumbnail), its arrow and the moon: when the window is narrow the
        playback buttons move right instead of the art covering them."""
        corner = self.corner_spacer.parentWidget()
        if corner is not None and corner.layout() is not None:
            corner.layout().invalidate()
            corner.setMinimumWidth(corner.layout().minimumSize().width())


class TopBarSizer(QObject):
    """Keeps the top bar's two sides the same width: as wide as the views and
    a full-size search need, less when the window is narrow (the search box
    shrinks first). The display between them takes the rest, so it reaches
    out to the views and stays in the middle."""

    def __init__(self, bar, left, right, layout, views_width: int = 4 * 54 + 3 * 2,
                 search_max: int = 240, search_min: int = 130, display_min: int = 260):
        super().__init__(bar)
        self.bar, self.left, self.right, self.layout = bar, left, right, layout
        self.full = views_width + 10 + search_max
        self.least = views_width + 10 + search_min
        self.display_min = display_min
        bar.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            margins = self.layout.contentsMargins()
            room = self.bar.width() - margins.left() - margins.right() - 2 * self.layout.spacing()
            side = max(self.least, min(self.full, (room - self.display_min) // 2))
            for widget in (self.left, self.right):
                if widget.width() != side:
                    widget.setFixedWidth(side)
        return False


class ElidedLabel(QLabel):
    """A one-line label that shortens its text with "…" to fit, rather than
    pushing the things beside it about. text() is the whole text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""

    def setText(self, text: str):
        self._full = text or ""
        self._fit()
        self.updateGeometry()

    def text(self) -> str:
        return self._full

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 4,
                     super().sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight,
                                              max(0, self.width()))
        super().setText(shown)


class Follow(QObject):
    """Calls back whenever any of the watched widgets is shown or hidden, and
    keeps each (widget → buttons) pair's buttons shown only while the widget is."""

    def __init__(self, parent, callback=None):
        super().__init__(parent)
        self.callback = callback
        self.pairs: dict = {}

    def watch(self, widget, buttons=None):
        if buttons is not None:
            self.pairs[widget] = buttons
            buttons.setVisible(not widget.isHidden() and widget.isVisible())
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide) and not event.spontaneous():
            buttons = self.pairs.get(obj)
            if buttons is not None:
                buttons.setVisible(event.type() == QEvent.Type.Show)
            if self.callback is not None:
                self.callback()
        return False
