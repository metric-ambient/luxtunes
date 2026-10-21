"""The data behind the song list.

Qt's QTableView asks this model "what goes in row 5, column 3?" and the model
answers. It holds every track of the current source (the library or a
playlist) in memory, and handles searching and sorting itself, which is
fast even for tens of thousands of songs.
"""

from __future__ import annotations

import json
from typing import NamedTuple

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QApplication, QStyle

from luxtunes.core.library import EDITABLE_NUMBER_FIELDS, EDITABLE_TEXT_FIELDS
from luxtunes.ui import formatting, theme

EDITABLE_KEYS = EDITABLE_TEXT_FIELDS | EDITABLE_NUMBER_FIELDS


class Column(NamedTuple):
    key: str
    title: str
    width: int
    align_right: bool = False
    shown: bool = False        # visible by default?


# Every column on offer (right-click a column header to show/hide).
# Some have no data in LuxTunes yet (podcast fields, ...); they
# exist for parity and simply stay empty. Order = order of a fresh layout.
COLUMNS = [
    Column("enabled", "✓", 26, shown=True),
    Column("name", "Name", 300, shown=True),
    Column("total_time", "Time", 55, True, shown=True),
    Column("artist", "Artist", 190, shown=True),
    Column("album", "Album", 250, shown=True),
    Column("genre", "Genre", 90, shown=True),
    Column("rating", "Rating", 82, shown=True),
    Column("kind", "Kind", 110, shown=True),
    Column("bit_rate", "Bit Rate", 75, True, shown=True),
    Column("year", "Year", 50, True, shown=True),
    Column("play_count", "Plays", 50, True, shown=True),
    Column("date_added", "Date Added", 135, shown=True),
    # Hidden by default:
    Column("album_artist", "Album Artist", 160),
    Column("album_rating", "Album Rating", 90),
    Column("bpm", "Beats Per Minute", 60, True),
    Column("category", "Category", 100),
    Column("comments", "Comments", 160),
    Column("composer", "Composer", 140),
    Column("date_modified", "Date Modified", 135),
    Column("description", "Description", 160),
    Column("disc_number", "Disc Number", 70, True),
    Column("episode_id", "Episode ID", 80),
    Column("episode_number", "Episode Number", 70, True),
    Column("equalizer", "Equalizer", 90),
    Column("grouping", "Grouping", 120),
    Column("play_date", "Last Played", 135),
    Column("skip_date", "Last Skipped", 135),
    Column("purchase_date", "Purchase Date", 135),
    Column("release_date", "Release Date", 135),
    Column("sample_rate", "Sample Rate", 85, True),
    Column("season", "Season", 55, True),
    Column("show", "Show", 120),
    Column("size", "Size", 70, True),
    Column("skip_count", "Skips", 50, True),
    Column("sort_album", "Sort Album", 160),
    Column("sort_album_artist", "Sort Album Artist", 160),
    Column("sort_artist", "Sort Artist", 160),
    Column("sort_composer", "Sort Composer", 140),
    Column("sort_name", "Sort Name", 160),
    Column("sort_show", "Sort Show", 120),
    Column("track_number", "Track Number", 75, True),
    # Only shown in Album List view (always first there):
    Column("album_group", "Album by Artist", 240),
    Column("album_track", "#", 32, True),
]
COLUMN_INDEX = {c.key: i for i, c in enumerate(COLUMNS)}
ENABLED_COLUMN = COLUMN_INDEX["enabled"]
NAME_COLUMN = COLUMN_INDEX["name"]
ARTIST_COLUMN = COLUMN_INDEX["artist"]
RATING_COLUMN = COLUMN_INDEX["rating"]
ALBUM_RATING_COLUMN = COLUMN_INDEX["album_rating"]
ALBUM_GROUP_COLUMN = COLUMN_INDEX["album_group"]
ALBUM_TRACK_COLUMN = COLUMN_INDEX["album_track"]
ALBUM_VIEW_COLUMNS = {ALBUM_GROUP_COLUMN, ALBUM_TRACK_COLUMN}
# Columns that can't be hidden (or shown) from the header menu.
FIXED_COLUMNS = {ENABLED_COLUMN, NAME_COLUMN} | ALBUM_VIEW_COLUMNS
# Room for the sort arrow: a right-aligned header title would otherwise sit
# underneath it. Added (as spaces) only to the column that is sorted.
SORT_ARROW_PAD = "\u2002\u2002\u2002"

RIGHT_ALIGNED = (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter).value

# Drag-and-drop format: a JSON list of track ids, in the order shown.
TRACK_IDS_MIME = "application/x-luxtunes-track-ids"


def track_ids_from_mime(data: QMimeData) -> list[int]:
    if not data.hasFormat(TRACK_IDS_MIME):
        return []
    return json.loads(bytes(data.data(TRACK_IDS_MIME)).decode("utf8"))


# Ask for this role to get the whole track dict for a row.
TrackRole = Qt.ItemDataRole.UserRole


class TrackTableModel(QAbstractTableModel):
    rating_edited = Signal(int, int)        # track id, new rating (0-100)
    field_edited = Signal(int, str, object)  # track id, field, typed value (saved by the window)
    enabled_edited = Signal(int, bool)      # track id, checked?

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all: list[dict] = []          # every track in the current source
        self._rows: list[dict] = []         # after search filter + sort
        self._words: list[str] = []
        self._id_filter: set[int] | None = None   # Grid view "open an album": only these songs
        self._sort_column = ARTIST_COLUMN
        self._sort_order = Qt.SortOrder.AscendingOrder
        self._now_playing_id: int | None = None
        self._missing_ids: set[int] = set()   # songs we tried to play but couldn't find
        self._speaker_icon = None
        self._missing_icon = None

    # ---- Loading -----------------------------------------------------------

    def set_tracks(self, tracks: list[dict], sort_column: int, order=Qt.SortOrder.AscendingOrder):
        """Show a new set of tracks. sort_column -1 means 'original order'."""
        self.beginResetModel()
        self._words = []  # a new list always starts unfiltered (the search box is cleared too)
        self._id_filter = None
        for position, track in enumerate(tracks):
            track["_order"] = position
            track["_search"] = formatting.search_text(track)
        self._all = tracks
        self._sort_column = sort_column
        self._sort_order = order
        self._apply()
        self.endResetModel()

    def set_id_filter(self, track_ids: set[int] | None):
        """Show only these songs (None = all). Used when a Grid tile is opened."""
        self.beginResetModel()
        self._id_filter = track_ids
        self._apply()
        self.endResetModel()

    def set_filter(self, text: str):
        self.beginResetModel()
        self._words = text.casefold().split()
        self._apply()
        self.endResetModel()

    def _apply(self):
        rows = [t for t in self._all
                if formatting.matches(t["_search"], self._words)
                and (self._id_filter is None or t["id"] in self._id_filter)]
        if self._sort_column < 0:
            key = lambda t: t["_order"]
        else:
            key = formatting.sort_key(COLUMNS[self._sort_column].key)
        rows.sort(key=key, reverse=self._sort_order == Qt.SortOrder.DescendingOrder)
        self._rows = rows

    def resort(self):
        """Re-apply the sort (and search) after songs were edited, keeping the
        selection on the same songs wherever they move to."""
        self.layoutAboutToBeChanged.emit()
        persistent = self.persistentIndexList()
        old = [(self._rows[i.row()] if 0 <= i.row() < len(self._rows) else None, i.column())
               for i in persistent]
        self._apply()
        where = {id(track): row for row, track in enumerate(self._rows)}
        new = [self.index(where[id(track)], column) if track is not None and id(track) in where
               else QModelIndex() for track, column in old]
        self.changePersistentIndexList(persistent, new)
        self.layoutChanged.emit()

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder):
        """Called by the table view when a column header is clicked."""
        self.layoutAboutToBeChanged.emit()
        self._sort_column = column
        self._sort_order = order
        self._apply()
        self.layoutChanged.emit()
        # The sorted column's title gained/lost its arrow padding.
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(COLUMNS) - 1)

    # ---- Reading -----------------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        column = COLUMNS[section]
        if role == Qt.ItemDataRole.DisplayRole:
            if column.align_right and section == self._sort_column:
                return column.title + SORT_ARROW_PAD
            return column.title
        if role == Qt.ItemDataRole.TextAlignmentRole and column.align_right:
            return RIGHT_ALIGNED
        return None

    def flags(self, index):
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if index.column() == ENABLED_COLUMN:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        elif COLUMNS[index.column()].key in EDITABLE_KEYS:
            flags |= Qt.ItemFlag.ItemIsEditable   # click a selected song's cell to edit
        return flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        track = self._rows[index.row()]
        column = COLUMNS[index.column()]

        if role == Qt.ItemDataRole.DisplayRole:
            if column.key in ("enabled", "rating", "album_group"):
                return None  # drawn as a checkbox / stars / album cell instead
            return formatting.display_value(track, column.key)
        if role == Qt.ItemDataRole.EditRole and column.key in EDITABLE_KEYS:
            value = track.get(column.key)
            return "" if value is None else str(value)
        if role == TrackRole:
            return track
        if role == Qt.ItemDataRole.CheckStateRole and column.key == "enabled":
            return Qt.CheckState.Checked if track.get("enabled", 1) else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.TextAlignmentRole and column.align_right:
            return RIGHT_ALIGNED
        if role == Qt.ItemDataRole.BackgroundRole and track["id"] == self._now_playing_id \
                and self._now_playing_id is not None:
            return theme.now_playing_color()        # a grey bar; the selection's blue covers it
        if role == Qt.ItemDataRole.DecorationRole and column.key == "name":
            if track["id"] == self._now_playing_id:
                if self._speaker_icon is None:
                    self._speaker_icon = QApplication.style().standardIcon(
                        QStyle.StandardPixmap.SP_MediaVolume
                    )
                return self._speaker_icon
            if track["id"] in self._missing_ids:  # "!" for a missing file
                if self._missing_icon is None:
                    self._missing_icon = QApplication.style().standardIcon(
                        QStyle.StandardPixmap.SP_MessageBoxWarning
                    )
                return self._missing_icon
        if role == Qt.ItemDataRole.ToolTipRole and column.key == "name" and track.get("_problem"):
            location = track.get("location") or "no location"
            return f"{track['_problem']}\n{location}"      # Display Missing: what's wrong
        return None

    # ---- Editing (checkbox and stars) --------------------------------------

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        track = self._rows[index.row()]
        key = COLUMNS[index.column()].key
        if key == "enabled" and role == Qt.ItemDataRole.CheckStateRole:
            checked = getattr(value, "value", value) == Qt.CheckState.Checked.value
            self.update_track(track["id"], {"enabled": int(checked)})
            self.enabled_edited.emit(track["id"], checked)
            return True
        if key == "rating" and role == Qt.ItemDataRole.EditRole:
            rating = int(value)
            self.update_track(track["id"], {"rating": rating, "rating_computed": 0})
            self.rating_edited.emit(track["id"], rating)
            return True
        if key in EDITABLE_KEYS and role == Qt.ItemDataRole.EditRole:
            if str(value).strip() != (self.data(index, Qt.ItemDataRole.EditRole) or "").strip():
                self.field_edited.emit(track["id"], key, value)  # the window validates & saves
            return True
        return False

    # ---- Helpers for the main window ---------------------------------------

    def track_at(self, row: int) -> dict | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def all_tracks(self) -> list[dict]:
        """Every song in the current list, ignoring the search and any filter."""
        return list(self._all)

    def visible_tracks(self) -> list[dict]:
        return list(self._rows)

    @property
    def sorted_by_column(self) -> bool:
        """Sorted by a column (not the list's own order)."""
        return self._sort_column >= 0

    def all_in_view_order(self) -> list[dict]:
        """Every song in the list (search ignored), in the order the view sorts them."""
        key = formatting.sort_key(COLUMNS[self._sort_column].key) if self._sort_column >= 0 \
            else (lambda t: t["_order"])
        return sorted(self._all, key=key, reverse=self._sort_order == Qt.SortOrder.DescendingOrder)

    def update_track(self, track_id: int, fields: dict):
        """Change a track's values (e.g. after a play) and refresh its rows."""
        search_changed = any(k in formatting.SEARCH_FIELDS for k in fields)
        for track in self._all:
            if track["id"] == track_id:
                track.update(fields)
                if search_changed:
                    track["_search"] = formatting.search_text(track)
        for row, track in enumerate(self._rows):
            if track["id"] == track_id:
                self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def row_of(self, track_id: int, prefer_row: int = -1) -> int:
        """The row showing a track (prefer_row wins if it shows that track)."""
        if 0 <= prefer_row < len(self._rows) and self._rows[prefer_row]["id"] == track_id:
            return prefer_row
        return next((i for i, t in enumerate(self._rows) if t["id"] == track_id), -1)

    def set_missing(self, track_id: int, missing: bool):
        if missing:
            self._missing_ids.add(track_id)
        else:
            self._missing_ids.discard(track_id)
        self.update_track(track_id, {})

    def set_now_playing(self, track_id: int | None):
        self._now_playing_id = track_id
        if self._rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._rows) - 1, len(COLUMNS) - 1),
                [Qt.ItemDataRole.DecorationRole, Qt.ItemDataRole.BackgroundRole],
            )

    def summary(self, selected_rows: list[int] | None = None) -> str:
        """Totals for the list, or for the selection if more than one song is selected."""
        if selected_rows and len(selected_rows) > 1:
            tracks = [self._rows[r] for r in selected_rows if 0 <= r < len(self._rows)]
            of_total = len(self._rows)
        else:
            tracks, of_total = self._rows, None
        return formatting.library_summary(
            len(tracks),
            sum(t.get("total_time") or 0 for t in tracks),
            sum(t.get("size") or 0 for t in tracks),
            of_total,
        )

    # ---- Dragging songs out (onto sidebar playlists) -----------------------

    def mimeTypes(self):
        return [TRACK_IDS_MIME]

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes if i.isValid()})
        data = QMimeData()
        ids = [self._rows[r]["id"] for r in rows]
        data.setData(TRACK_IDS_MIME, json.dumps(ids).encode("utf8"))
        return data
