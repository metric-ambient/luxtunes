"""LuxTunes' own title bar, with the menus in it (on Linux).

The desktop's title bar is turned off and this one takes its place: the
LuxTunes icon, File / Edit / View / Controls / Help, the window's name in the
middle, and minimize, maximize and close on the right, all in the theme's
colors. What the desktop's bar did still works:

  - drag the bar to move the window (handed to the desktop, so snapping to
    screen edges and moving between screens work as usual);
  - double-click it to maximize or restore;
  - right-click it for Minimize / Maximize / Close;
  - drag any edge or corner of the window to resize it (a thin border is kept
    for that while the window isn't maximized);
  - Alt and the menu letters still open the menus from the keyboard.

Preferences can put the desktop's own title bar back (View → Appearance…),
which takes effect the next time LuxTunes opens. Windows keeps its own
title bar for now: snapping there needs extra work, done separately.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QMenuBar, QSizePolicy, QToolButton, QWidget

EDGE = 5                     # the resize border, in pixels


def wanted(settings) -> bool:
    """LuxTunes' own title bar: on Linux, unless turned off."""
    return sys.platform.startswith("linux") and \
        settings.value("window/own_title_bar", True, type=bool)


def _window_icon(kind: str, color: str, size: int = 12) -> QIcon:
    result = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(color), 1.3))
        s = size
        if kind == "minimize":
            p.drawLine(QRectF(1, s / 2, s - 2, 0).topLeft(), QRectF(1, s / 2, s - 2, 0).topRight())
        elif kind == "maximize":
            p.drawRect(QRectF(1.5, 1.5, s - 3, s - 3))
        elif kind == "restore":
            p.drawRect(QRectF(1.5, 3.5, s - 5, s - 5))
            p.drawPolyline([QRectF(3.5, 1.5, 0, 0).topLeft(), QRectF(s - 1.5, 1.5, 0, 0).topLeft(),
                            QRectF(s - 1.5, s - 3.5, 0, 0).topLeft()])
        elif kind == "close":
            p.drawLine(QRectF(1.5, 1.5, 0, 0).topLeft(), QRectF(s - 1.5, s - 1.5, 0, 0).topLeft())
            p.drawLine(QRectF(s - 1.5, 1.5, 0, 0).topLeft(), QRectF(1.5, s - 1.5, 0, 0).topLeft())
        p.end()
        result.addPixmap(pixmap)
    return result


class TitleBar(QWidget):
    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ = window
        self.setObjectName("titleBar")
        self.setFixedHeight(34)
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setObjectName("titleMenus")
        self.menu_bar.setNativeMenuBar(False)
        self.menu_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.app_icon = QLabel()
        self.app_icon.setObjectName("titleIcon")
        self.title = QLabel(window.windowTitle())
        self.title.setObjectName("titleText")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        window.windowTitleChanged.connect(self.title.setText)
        self.buttons = {}
        for kind, tip in (("minimize", "Minimize"), ("maximize", "Maximize"), ("close", "Close")):
            button = QToolButton()
            button.setObjectName("closeButton" if kind == "close" else "windowButton")
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.setFixedSize(46, 34)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.buttons[kind] = button
        self.buttons["minimize"].clicked.connect(window.showMinimized)
        self.buttons["maximize"].clicked.connect(self.toggle_maximized)
        self.buttons["close"].clicked.connect(window.close)

        # Menus on the left, the name in the exact middle, the buttons on the right.
        left, right = QWidget(), QWidget()
        for side in (left, right):
            policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            policy.setHorizontalStretch(1)
            side.setSizePolicy(policy)
            side.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(10, 0, 0, 0)
        left_row.setSpacing(6)
        left_row.addWidget(self.app_icon)
        left_row.addWidget(self.menu_bar)
        left_row.addStretch(1)
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.setSpacing(0)
        right_row.addStretch(1)
        for button in self.buttons.values():
            right_row.addWidget(button)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(left, 1)
        row.addWidget(self.title, 0)
        row.addWidget(right, 1)

    def refresh(self, colors: dict):
        """Icons in the theme's colors (after a theme change)."""
        text = colors["text"]
        icon = self.window_.windowIcon()
        self.app_icon.setPixmap(icon.pixmap(18, 18) if not icon.isNull() else QPixmap())
        self.buttons["minimize"].setIcon(_window_icon("minimize", text))
        self.buttons["close"].setIcon(_window_icon("close", text))
        self.update_maximized(colors)

    def update_maximized(self, colors: dict | None = None):
        from luxtunes.ui import theme
        colors = colors or theme.current
        maximized = self.window_.isMaximized()
        button = self.buttons["maximize"]
        button.setIcon(_window_icon("restore" if maximized else "maximize", colors["text"]))
        button.setToolTip("Restore" if maximized else "Maximize")
        button.setAccessibleName(button.toolTip())

    def toggle_maximized(self):
        if self.window_.isMaximized():
            self.window_.showNormal()
        else:
            self.window_.showMaximized()

    # An empty part of the bar (not a menu or a button) works like the desktop's.
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window_.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Minimize", self.window_.showMinimized)
        menu.addAction("Restore" if self.window_.isMaximized() else "Maximize",
                       self.toggle_maximized)
        menu.addSeparator()
        menu.addAction("Close", self.window_.close)
        menu.exec(event.globalPos())


class Frame:
    """Turns the desktop's title bar off and handles moving and resizing for
    the window (a QMainWindow): call install() before the window is shown."""

    def __init__(self, window):
        self.window = window
        self.title_bar = TitleBar(window)
        self.edge_cursor = False          # a resize arrow is set on the window
        # The resize arrow is set on the window itself, and every widget in it
        # shows the window's pointer unless it has its own. The window only
        # hears about the pointer while it's over the border, so moving
        # straight off the border into the list (or out of the window) would
        # leave the arrow stuck. While the arrow is up, this checks every
        # tenth of a second where the pointer is, and puts the normal one back
        # once it's off the border. (It runs only then: nothing watches every
        # event, which slowed re-styling the window.)
        self._cursor_check = QTimer(window)
        self._cursor_check.setInterval(100)
        self._cursor_check.timeout.connect(self._check_cursor)

    def install(self):
        w = self.window
        w.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        w.setMenuWidget(self.title_bar)
        w.setMouseTracking(True)
        self.update_margins()

    def update_margins(self):
        edge = 0 if self.window.isMaximized() or self.window.isFullScreen() else EDGE
        self.window.setContentsMargins(edge, edge, edge, edge)
        self.title_bar.update_maximized()

    def edges_at(self, pos: QPoint):
        w = self.window
        if w.isMaximized() or w.isFullScreen():
            return Qt.Edge(0)
        edges = Qt.Edge(0)
        if pos.x() <= EDGE:
            edges |= Qt.Edge.LeftEdge
        if pos.x() >= w.width() - EDGE - 1:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= EDGE:
            edges |= Qt.Edge.TopEdge
        if pos.y() >= w.height() - EDGE - 1:
            edges |= Qt.Edge.BottomEdge
        return edges

    @staticmethod
    def cursor_for(edges):
        e = Qt.Edge
        if edges in (e.LeftEdge | e.TopEdge, e.RightEdge | e.BottomEdge):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (e.RightEdge | e.TopEdge, e.LeftEdge | e.BottomEdge):
            return Qt.CursorShape.SizeBDiagCursor
        if edges in (e.LeftEdge, e.RightEdge):
            return Qt.CursorShape.SizeHorCursor
        if edges in (e.TopEdge, e.BottomEdge):
            return Qt.CursorShape.SizeVerCursor
        return None

    # The window passes its mouse events here (only its thin border gets them).
    def mouse_move(self, event) -> bool:
        shape = self.cursor_for(self.edges_at(event.position().toPoint()))
        if shape is None:
            self.clear_cursor()
        else:
            self.window.setCursor(shape)
            self.edge_cursor = True
            self._cursor_check.start()
        return False

    def _check_cursor(self):
        pos = self.window.mapFromGlobal(QCursor.pos())
        if not self.window.rect().contains(pos) or not self.edges_at(pos):
            self.clear_cursor()

    def clear_cursor(self):
        self._cursor_check.stop()
        if self.edge_cursor:
            self.edge_cursor = False
            self.window.unsetCursor()

    def mouse_press(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        edges = self.edges_at(event.position().toPoint())
        handle = self.window.windowHandle()
        if edges and handle is not None:
            return bool(handle.startSystemResize(edges))
        return False


ICON_SIZE = QSize(18, 18)
