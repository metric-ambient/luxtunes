"""The song list's column header, with two extras over Qt's standard one:

1. While you drag a column to a new place, a highlighted line shows exactly
   where it will land.
2. Drag a column down off the header (into the songs) and let go to hide it,
   the same as unchecking it in the header's right-click menu.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPainter, QPalette, QPolygon
from PySide6.QtWidgets import QApplication, QHeaderView, QStyle, QToolTip

DROP_OFF_MARGIN = 12      # how far below the header counts as "dragged off"
MARKER_WIDTH = 3


class TrackHeaderView(QHeaderView):
    column_dropped_off = Signal(int)    # logical column index the user dragged away

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        # A header we create ourselves starts with these off; QTableView's own has them on.
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        self._press_pos: QPoint | None = None
        self._press_section = -1
        self._press_visual = -1
        self._dragging = False
        self._marker_x: int | None = None
        # Set by the window: which columns may be hidden by dragging them off.
        self.can_hide = lambda section: True

    # ---- Mouse ------------------------------------------------------------------

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._reset()
        if event.button() != Qt.MouseButton.LeftButton or not self.sectionsMovable():
            return
        pos = event.position().toPoint()
        section = self.logicalIndexAt(pos)
        if section < 0 or self._near_edge(section, pos.x()):
            return      # pressing on a column's edge resizes it instead of moving it
        self._press_pos = pos
        self._press_section = section
        self._press_visual = self.visualIndex(section)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if self._press_pos is None:
            return
        pos = event.position().toPoint()
        if not self._dragging:
            if (pos - self._press_pos).manhattanLength() < QApplication.startDragDistance():
                return
            self._dragging = True
        if self._is_dropped_off(pos):
            self._marker_x = None
            title = str(self.model().headerData(self._press_section, Qt.Orientation.Horizontal) or "").strip()
            tip = (f"Release to hide “{title}”" if self.can_hide(self._press_section)
                   else f"“{title}” can't be hidden")
            QToolTip.showText(event.globalPosition().toPoint(), tip, self)
        else:
            QToolTip.hideText()
            self._marker_x = self._landing_x(pos.x())
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        dropped_off = self._dragging and self._is_dropped_off(event.position().toPoint())
        section, original_visual = self._press_section, self._press_visual
        super().mouseReleaseEvent(event)      # Qt finishes its own move first
        self._reset()
        QToolTip.hideText()
        self.viewport().update()
        if dropped_off and section >= 0:
            # Put the column back where it was (so it returns there if shown
            # again later), then let the window hide it.
            current = self.visualIndex(section)
            if current != original_visual:
                self.moveSection(current, original_visual)
            self.column_dropped_off.emit(section)

    # ---- Helpers -----------------------------------------------------------------

    def _reset(self):
        self._press_pos = None
        self._press_section = -1
        self._press_visual = -1
        self._dragging = False
        self._marker_x = None

    def _near_edge(self, section: int, x: int) -> bool:
        margin = self.style().pixelMetric(QStyle.PixelMetric.PM_HeaderGripMargin) + 1
        left = self.sectionViewportPosition(section)
        right = left + self.sectionSize(section)
        return abs(x - left) <= margin or abs(x - right) <= margin

    def _is_dropped_off(self, pos: QPoint) -> bool:
        return pos.y() > self.height() + DROP_OFF_MARGIN

    def _landing_x(self, x: int) -> int | None:
        """Where the dragged column would land: the left edge of the column under
        the mouse when moving left, or its right edge when moving right."""
        target = self.visualIndexAt(x)
        if target < 0:      # past the last column
            target = self.count() - 1
            while target > 0 and self.isSectionHidden(self.logicalIndex(target)):
                target -= 1
        if target == self._press_visual or target < 0:
            return None
        logical = self.logicalIndex(target)
        left = self.sectionViewportPosition(logical)
        return left + self.sectionSize(logical) if target > self._press_visual else left

    # ---- Drawing the marker ----------------------------------------------------------

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._marker_x is None:
            return
        painter = QPainter(self.viewport())
        try:
            color = self.palette().color(QPalette.ColorRole.Highlight)
            x = self._marker_x
            painter.fillRect(x - MARKER_WIDTH // 2, 0, MARKER_WIDTH, self.height(), color)
            # Small arrows at the top and bottom, pointing at the gap.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(QPolygon([QPoint(x - 5, 0), QPoint(x + 5, 0), QPoint(x, 5)]))
            h = self.height()
            painter.drawPolygon(QPolygon([QPoint(x - 5, h), QPoint(x + 5, h), QPoint(x, h - 5)]))
        finally:
            painter.end()
