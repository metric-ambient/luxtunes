"""The Grid view: a wall of album art tiles (dark or light, see Preferences).

Tabs at the top choose the grouping: Albums, Artists, Genres or Composers.
- Hover a tile: a round play button appears on the art; click it to play
  that album (or artist, ...) from the top.
- Double-click a tile: opens it (the window shows just those songs in the
  Album List style, with an "◀ All Albums" bar to come back).
- Single click: selects it (the album art pane shows it).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygon
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core.albums import GRID_GROUPINGS, GRID_TABS, grid_groups
from luxtunes.ui import theme
from luxtunes.ui.art_cache import ART_CACHE

ART = 150
TILE_W, TILE_H = 186, 206
PLAY_BUTTON = 30         # diameter of the hover play button
GroupRole = Qt.ItemDataRole.UserRole


class GroupModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups: list[dict] = []

    def set_groups(self, groups: list[dict]):
        self.beginResetModel()
        self.groups = groups
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.groups)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        group = self.groups[index.row()]
        if role == GroupRole:
            return group
        if role == Qt.ItemDataRole.DisplayRole:
            return group["title"]
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{group['title']}\n{group['subtitle']}"
        return None


def art_rect(tile: QRect) -> QRect:
    return QRect(tile.x() + (tile.width() - ART) // 2, tile.y() + 10, ART, ART)


def play_button_rect(tile: QRect) -> QRect:
    art = art_rect(tile)
    return QRect(art.x() + 8, art.bottom() - 8 - PLAY_BUTTON, PLAY_BUTTON, PLAY_BUTTON)


class TileDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(TILE_W, TILE_H)

    def paint(self, painter, option, index):
        group = index.data(GroupRole)
        tile = option.rect
        art = art_rect(tile)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pixmap = ART_CACHE.get(group.get("art_track"), ART)
        if pixmap is not None:
            x = art.x() + (ART - pixmap.width()) // 2
            y = art.y() + (ART - pixmap.height())       # sit on the same baseline
            painter.drawPixmap(x, y, pixmap)
        else:
            painter.fillRect(art, QColor(theme.grid_colors["grid_empty_art"]))
            painter.setPen(QColor("#9a9a9a"))
            big = QFont(option.font)
            big.setPointSizeF(big.pointSizeF() * 3)
            painter.setFont(big)
            painter.drawText(art, Qt.AlignmentFlag.AlignCenter, "♪")

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:   # blue rounded outline
            painter.setPen(QPen(option.palette.highlight().color(), 4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(art.adjusted(-3, -3, 3, 3), 5, 5)
        if hovered:    # round play button
            button = play_button_rect(tile)
            painter.setPen(QPen(QColor(255, 255, 255, 220), 2))
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawEllipse(button)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("white"))
            c = button.center()
            painter.drawPolygon(QPolygon([QPoint(c.x() - 4, c.y() - 7), QPoint(c.x() - 4, c.y() + 7),
                                          QPoint(c.x() + 8, c.y())]))

        width = tile.width() - 8
        title_rect = QRect(tile.x() + 4, art.bottom() + 8, width, option.fontMetrics.height())
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QColor(theme.grid_colors["grid_title"]))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignHCenter,
                         painter.fontMetrics().elidedText(group["title"], Qt.TextElideMode.ElideRight, width))
        painter.setFont(option.font)
        painter.setPen(QColor(theme.grid_colors["grid_subtitle"]))
        painter.drawText(title_rect.translated(0, option.fontMetrics.height() + 1), Qt.AlignmentFlag.AlignHCenter,
                         option.fontMetrics.elidedText(group["subtitle"], Qt.TextElideMode.ElideRight, width))
        painter.restore()


class GridView(QWidget):
    group_selected = Signal(dict)     # single click
    group_opened = Signal(dict)       # double-click: show its songs
    group_play = Signal(dict)         # hover play button
    grouping_changed = Signal()       # a different tab was picked
    menu_requested = Signal(dict, QPoint)   # right-click: the tile and where

    TAB_TITLES = {"albums": "Albums", "artists": "Artists", "genres": "Genres",
                  "composers": "Composers", "books": "Books", "authors": "Authors",
                  "book_categories": "Categories", "podcasts": "Podcasts",
                  "podcast_categories": "Categories", "unplayed": "Unplayed"}
    # For the status bar: "3 albums", "2 categories"...
    NOUNS = {"albums": "album", "artists": "artist", "genres": "genre", "composers": "composer",
             "books": "book", "authors": "author", "book_categories": "category",
             "podcasts": "podcast", "podcast_categories": "category", "unplayed": "podcast"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("gridPage")
        self.grouping = "albums"
        self._tracks: list[dict] = []
        self._section = "music"                 # which library section's tabs show
        self._chosen: dict[str, str] = {}       # the tab picked last in each section

        tabs = QWidget()
        tabs.setObjectName("gridTabs")
        row = QHBoxLayout(tabs)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(2)
        row.addStretch(1)
        self._tab_row = row
        self._tab_group = QButtonGroup(self)
        self._tab_buttons: dict[str, QToolButton] = {}
        self._make_tabs(GRID_GROUPINGS)
        row.addStretch(1)

        self.model = GroupModel(self)
        self.list = QListView()
        self.list.setObjectName("grid")
        self.list.setModel(self.model)
        self.list.setItemDelegate(TileDelegate(self.list))
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setUniformItemSizes(True)
        self.list.setGridSize(QSize(TILE_W, TILE_H))
        self.list.setSpacing(6)
        self.list.setMouseTracking(True)              # for the hover play button
        self.list.setSelectionMode(QListView.SelectionMode.SingleSelection)
        self.list.viewport().installEventFilter(self)
        self.list.clicked.connect(lambda i: self.group_selected.emit(i.data(GroupRole)))
        self.list.doubleClicked.connect(lambda i: self.group_opened.emit(i.data(GroupRole)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._on_context_menu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(tabs)
        layout.addWidget(self.list, 1)

    def _make_tabs(self, keys):
        for button in self._tab_buttons.values():
            self._tab_group.removeButton(button)
            button.deleteLater()
        self._tab_buttons = {}
        for i, key in enumerate(keys):
            button = QToolButton()
            button.setObjectName("gridTab")
            button.setText(self.TAB_TITLES[key])
            button.setCheckable(True)
            button.setChecked(key == self.grouping)
            button.clicked.connect(lambda checked=False, k=key: self.set_grouping(k))
            self._tab_group.addButton(button)
            self._tab_row.insertWidget(1 + i, button)       # between the two stretches
            self._tab_buttons[key] = button

    def tab_keys(self) -> list[str]:
        return list(self._tab_buttons)

    def set_section(self, kind: str):
        """Show the tabs for a library section: "podcast" and "audiobook" have
        their own; everything else gets Albums / Artists / Genres / Composers."""
        section = kind if kind in GRID_TABS else "music"
        if section == self._section:
            return
        self._chosen[self._section] = self.grouping
        self._section = section
        keys = GRID_TABS.get(section, GRID_GROUPINGS)
        self.grouping = self._chosen.get(section, keys[0])
        self._make_tabs(keys)

    def grouping_noun(self) -> str:
        """"album", "artist", "category"... (for the status bar)."""
        return self.NOUNS[self.grouping]

    def all_label(self) -> str:
        return f"All {self.TAB_TITLES[self.grouping]}"

    def set_tracks(self, tracks: list[dict]):
        self._tracks = tracks
        self.model.set_groups(grid_groups(tracks, self.grouping))

    def set_grouping(self, grouping: str):
        self.grouping = grouping
        if grouping in self._tab_buttons:
            self._tab_buttons[grouping].setChecked(True)
        self.set_tracks(self._tracks)
        self.grouping_changed.emit()

    def _on_context_menu(self, pos):
        index = self.list.indexAt(pos)
        if not index.isValid():
            return
        self.list.setCurrentIndex(index)
        group = index.data(GroupRole)
        self.group_selected.emit(group)
        self.menu_requested.emit(group, self.list.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, event):
        # A click on the hover play button plays instead of selecting.
        if obj is self.list.viewport() and event.type() == QEvent.Type.MouseButtonPress \
                and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            index = self.list.indexAt(pos)
            if index.isValid() and play_button_rect(self.list.visualRect(index)).contains(pos):
                self.group_play.emit(index.data(GroupRole))
                return True
        if obj is self.list.viewport() and event.type() == QEvent.Type.Leave:
            self.list.viewport().update()   # hide the play button
        return super().eventFilter(obj, event)
