"""The LuxTunes main window:

    +--------------------------------------------------------------------+
    | |<<  >  >>|  volume  |      [ LCD display ]      | views | search  |
    +--------------+-----------------------------------------------------+
    | LIBRARY      |  ✓ Name   Time  Artist  Album  Genre  Rating ...    |
    |   Music      |                                                     |
    | PLAYLISTS    |                song list                            |
    |   ...        |                                                     |
    |--------------|                                                     |
    | Selected Item|                                                     |
    | [album art]  |                                                     |
    | + ⇄ ↻    ▾   |                                                     |
    +--------------+-----------------------------------------------------+
    |               5,000 songs, 14.2 days, 35.00 GB                     |
    +--------------------------------------------------------------------+
"""

from __future__ import annotations

import logging
import os
import random
from collections import Counter
import sqlite3
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPalette, QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QCheckBox,
    QDialog,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QStyle,
    QButtonGroup,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from luxtunes import __version__
from luxtunes.core import (add_files, artwork, cd, convert, encoders, equalizer, library, organize,
                           smart, tag_sync, trash)
from luxtunes.core import edits
from luxtunes.core.tags import UNCHANGED, TagError
from luxtunes.core.paths import find_file, nearest_existing_folder
from luxtunes.importer.itunes_xml import import_library
from luxtunes.ui import formatting, theme
from luxtunes.ui.player import REPEAT_ALL, REPEAT_OFF, REPEAT_ONE, Player
from luxtunes.core.albums import albums_in_order
from luxtunes.ui.about import AboutDialog
from luxtunes.ui.album_view import TrackTableView
from luxtunes.ui.art_cache import ART_CACHE
from luxtunes.ui.artwork_window import ArtworkWindow
from luxtunes.ui.mini_player import MiniPlayer
from luxtunes.ui.sleep_timer import SleepTimer, SleepTimerDialog, clock, left_text
from luxtunes.ui.cover_flow import CoverFlow
from luxtunes.ui.cd_page import CdPage
from luxtunes.ui.device_page import DevicePage
from luxtunes.ui.video_panel import VideoPanel
from luxtunes.core.android import device as android_device
from luxtunes.core.android import sync as android_sync
from luxtunes.core.ipod import device as ipod_device
from luxtunes.core.ipod import settings as ipod_settings
from luxtunes.ui.equalizer_window import EqualizerWindow
from luxtunes.ui.get_info import InfoDialog, MultiInfoDialog
from luxtunes.ui.grid_view import GridView
from luxtunes.ui.view_icons import view_icons
from luxtunes.ui.playlist_window import PlaylistWindow
from luxtunes.ui import album_view
from luxtunes.ui.preferences import PreferencesDialog, appearance
from luxtunes.ui.view_options import ViewOptionsDialog
from luxtunes.ui.track_model import (
    ALBUM_GROUP_COLUMN,
    ALBUM_RATING_COLUMN,
    ALBUM_TRACK_COLUMN,
    ARTIST_COLUMN,
    FIXED_COLUMNS,
    NAME_COLUMN,
    COLUMNS,
    ENABLED_COLUMN,
    RATING_COLUMN,
    TrackTableModel,
)
from luxtunes.ui.widgets import (
    SOURCE_MOVIES,
    SOURCE_MUSIC,
    SOURCE_UP_NEXT,
    playlist_icon,
    ArtworkPane,
    JumpSlider,
    NowPlayingDisplay,
    RatingDelegate,
    Sidebar,
)

VIDEO_KINDS = ("movie", "tvshow")
log = logging.getLogger(__name__)

# Bump if COLUMNS changes, so saved column layouts from older versions are ignored.
HEADER_LAYOUT_VERSION = 3  # 2: all columns added; 3: Album List columns

VIEW_MODES = ("list", "album", "grid", "coverflow")
VIEW_TIPS = ("List", "Album List", "Grid", "Cover Flow")
# Columns shown in a fresh Album List layout, in order.
ALBUM_LAYOUT = ("album_group", "album_track", "enabled", "name", "total_time",
                "artist", "genre", "rating", "play_count")


class MainWindow(QMainWindow):
    def __init__(self, conn: sqlite3.Connection):
        super().__init__()
        self.conn = conn
        self.settings = QSettings("LuxTunes", "LuxTunes")
        self.setWindowTitle("LuxTunes")
        self.setMinimumSize(800, 450)

        self.model = TrackTableModel(self)
        self.player = Player(self)
        self.sleep_timer = SleepTimer(self.player, self)
        self.sleep_timer.changed.connect(self._show_sleep_timer)
        if equalizer.has_saved(conn):
            self.eq_settings = equalizer.load(conn)
        else:                           # kept in the app's settings by earlier test builds
            self.eq_settings = equalizer.Settings.from_json(self.settings.value("equalizer"))
        self.player.set_equalizer(self.eq_settings)
        self._eq_window = None
        self._eq_save_timer = QTimer(self)
        self._eq_save_timer.setSingleShot(True)
        self._eq_save_timer.setInterval(500)            # once a slider stops moving
        self._eq_save_timer.timeout.connect(
            lambda: equalizer.save(self.conn, self.eq_settings))
        # Movies and TV shows play inside the main window, in place of the
        # sidebar and song list (built in _build_ui).
        self.video_panel = VideoPanel()
        self.player.set_video_output(self.video_panel.sink)
        self.video_panel.play_clicked.connect(self.player.toggle)       # pause / resume
        self.video_panel.seek_requested.connect(self.player.seek)
        self.video_panel.next_clicked.connect(self.player.next)
        self.video_panel.previous_clicked.connect(self.player.previous)
        self.video_panel.close_requested.connect(self.player.stop)
        self.video_panel.full_screen_requested.connect(self._toggle_video_full_screen)
        self.video_panel.volume_changed.connect(lambda value: self.volume.setValue(value))
        self._video_full_screen = False
        self._was_maximized = False
        self._art_mode = "selected"      # or "playing"
        self._art_window: ArtworkWindow | None = None
        self._mini_player: MiniPlayer | None = None
        self._loudness_worker = None             # Sound Check measuring songs
        # Edit → Cut / Copy / Paste: the songs on LuxTunes' own clipboard.
        self._song_clipboard: dict | None = None
        # Edit → Undo / Redo: (label, undo, redo), most recent last.
        self._undo_stack: list[tuple[str, object, object]] = []
        self._redo_stack: list[tuple[str, object, object]] = []
        self._playing_source = None      # the list the current song was started from
        self.view_mode = "list"
        self._table_layout = "list"      # which column layout the song table has now
        self._header_states: dict[str, object] = {"list": None, "album": None}
        self._drilled = False            # a Grid tile is open
        self._syncing = False            # Cover Flow <-> song list update in progress
        self._views_refresh_pending = False
        self._playlist_windows: list[PlaylistWindow] = []
        self._current_source = None
        self._grid_group = None          # the Grid tile last clicked
        self._info_changed = self._kind_changed = False   # set while Get Info is open
        self._dup_ids: set[int] | None = None   # File → Display Duplicates is on
        self._showing_missing = False           # File → Display Missing is on
        self._broken_ids: set[int] = set()      # songs that failed to play this session
        self._resort_pending = False            # songs were edited: re-sort the list soon
        self._ipods: dict[str, ipod_device.IPod] = {}   # plugged-in iPods by key
        self._ipod_mounts: set[str] = set()
        self._phones: dict[str, android_device.Phone] = {}   # Android devices by key
        self._phone_names: set[str] = set()
        self._phone_tries: dict[str, int] = {}  # phone address -> times we tried to open it
        self._mount_tries: dict[str, int] = {}  # iPod partition -> times we tried to mount it
        self._mount_ejected: set[str] = set()   # ejected here: left alone until replugged
        self._sync_worker = None                # the iPod sync running in the background
        self._closing_after_sync = False        # closing was asked for during a sync
        self._discs: dict[str, cd.Disc] = {}    # audio CDs in the drives, by "cd:/dev/sr0"
        self._cd_unreadable: set[str] = set()   # drives we couldn't open (told you once)
        self._lookups: dict[str, object] = {}   # MusicBrainz lookups running, by disc key
        self._cd_pending: dict[str, str] = {}   # what to do once a new CD's names are in
        self._asking_import = False             # "Import this CD?" is showing
        self._import_worker = None              # the CD import running in the background
        self._add_worker = None                 # File → Add to Library running in the background
        self._add_playlist = None               # the playlist shown when it started
        self._import_key: str | None = None
        self._import_eject = False
        self._import_failures: list[str] = []
        self._song_lookup = None                # Get Track Info Online… running in the background
        self._smart_refresh_pending = False     # a live smart playlist on show may need updating
        self._update_worker = None              # Help → Check for Updates… in the background
        self._recent: list[int] = []            # Controls → Play Recent (newest first)

        self._build_ui()
        self._build_menus()
        self._connect()
        self._restore_settings()
        self.reload_library()
        # Song edits whose files couldn't be updated last time (file missing,
        # drive not mounted, ...): try again shortly after the window appears.
        QTimer.singleShot(2000, self._write_pending_tags)
        # Look for iPods now and every 2 seconds (just a glance at what's mounted).
        self._device_timer = QTimer(self)
        self._device_timer.timeout.connect(self._scan_devices)
        self._device_timer.timeout.connect(self._update_device_actions)
        self._device_timer.start(2000)
        QTimer.singleShot(0, self._scan_devices)
        self._update_device_actions()
        QTimer.singleShot(800, self._offer_desktop_integration)

    # ======================================================================
    # Building the window
    # ======================================================================

    def _build_ui(self):
        style = self.style()
        icon = lambda sp: style.standardIcon(sp)

        # ---- Toolbar ------------------------------------------------------
        toolbar = QWidget()
        toolbar.setObjectName("toolbar")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(6)

        self.prev_button = self._tool_button("transport", icon(QStyle.StandardPixmap.SP_MediaSeekBackward), "Previous")
        self.play_button = self._tool_button("playButton", icon(QStyle.StandardPixmap.SP_MediaPlay), "Play")
        self.next_button = self._tool_button("transport", icon(QStyle.StandardPixmap.SP_MediaSeekForward), "Next")
        self._play_icon = icon(QStyle.StandardPixmap.SP_MediaPlay)
        self._pause_icon = icon(QStyle.StandardPixmap.SP_MediaPause)
        self._stop_icon = icon(QStyle.StandardPixmap.SP_MediaStop)
        for button in (self.prev_button, self.play_button, self.next_button):
            bar.addWidget(button)
        bar.addSpacing(14)

        quiet = QLabel()
        quiet.setPixmap(icon(QStyle.StandardPixmap.SP_MediaVolumeMuted).pixmap(14, 14))
        bar.addWidget(quiet)
        self.volume = JumpSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(100)
        bar.addWidget(self.volume)
        loud = QLabel()
        loud.setPixmap(icon(QStyle.StandardPixmap.SP_MediaVolume).pixmap(14, 14))
        bar.addWidget(loud)
        # The sleep timer, while it runs: the time left; click to turn it off.
        self.sleep_button = self._tool_button("sleepButton", None, "", "☾")
        self.sleep_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.sleep_button.clicked.connect(self._sleep_timer_off)
        self.sleep_button.hide()
        bar.addWidget(self.sleep_button)
        bar.addSpacing(20)

        self.lcd = NowPlayingDisplay()
        bar.addWidget(self.lcd, 1)
        bar.addSpacing(20)

        # View buttons: List, Album List, Grid, Cover Flow (joined).
        self.view_group = QButtonGroup(self)
        self.view_buttons = []
        views_box = QHBoxLayout()
        views_box.setSpacing(0)
        for i, (mode, tip) in enumerate(zip(VIEW_MODES, VIEW_TIPS)):
            button = self._tool_button("viewButton", None, tip)
            button.setCheckable(True)
            button.setProperty("segment", "first" if i == 0 else "last" if i == 3 else "middle")
            button.clicked.connect(lambda checked=False, m=mode: self._set_view_mode(m))
            self.view_group.addButton(button)
            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(True)      # hiding them doesn't shift the display
            button.setSizePolicy(policy)
            self.view_buttons.append(button)
            views_box.addWidget(button)
        self.view_buttons[0].setChecked(True)
        bar.addLayout(views_box)
        bar.addSpacing(20)

        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search Music")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(200)
        bar.addWidget(self.search)

        # ---- Left panel: sidebar + album art + small buttons --------------
        left = QWidget()
        left.setObjectName("leftPanel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.sidebar = Sidebar()
        left_layout.addWidget(self.sidebar, 1)

        self.art = ArtworkPane()
        left_layout.addWidget(self.art)

        buttons = QWidget()
        buttons.setObjectName("sidebarButtons")
        row = QHBoxLayout(buttons)
        row.setContentsMargins(6, 3, 6, 3)
        row.setSpacing(6)
        self.new_playlist_button = self._tool_button("sidebarButton", None, "New playlist", "+")
        self.shuffle_button = self._tool_button("sidebarButton", None, "Shuffle", "⇄")
        self.shuffle_button.setCheckable(True)
        self.repeat_button = self._tool_button("sidebarButton", None, "Repeat: off", "↻")
        self.repeat_button.setCheckable(True)
        self.art_toggle = self._tool_button("sidebarButton", None, "Show or hide artwork", "▾")
        for button in (self.new_playlist_button, self.shuffle_button, self.repeat_button):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.art_toggle)
        left_layout.addWidget(buttons)

        # ---- Song list -----------------------------------------------------
        self.table = TrackTableView()
        self.table.setObjectName("tracks")
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(RATING_COLUMN, RatingDelegate(self.table))
        self.table.setItemDelegateForColumn(
            ALBUM_RATING_COLUMN, RatingDelegate(self.table, key="album_rating", editable=False))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed)
        # Songs can be dragged onto playlists (and Up Next) in the sidebar.
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(19)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionsMovable(True)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for i, column in enumerate(COLUMNS):
            self.table.setColumnWidth(i, column.width)
            self.table.setColumnHidden(i, not column.shown)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_menu)
        header.column_dropped_off.connect(self._on_column_dropped_off)
        header.can_hide = lambda section: section not in FIXED_COLUMNS
        header.setSectionResizeMode(ENABLED_COLUMN, QHeaderView.ResizeMode.Fixed)
        header.setSortIndicator(ARTIST_COLUMN, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)

        # ---- The other views --------------------------------------------------
        self.cover_flow = CoverFlow()
        self.cover_flow.hide()
        self.songs_split = QSplitter(Qt.Orientation.Vertical)   # Cover Flow above the list
        self.songs_split.setObjectName("songsSplit")
        self.songs_split.addWidget(self.cover_flow)
        self.songs_split.addWidget(self.table)
        self.songs_split.setStretchFactor(1, 1)
        self.songs_split.setCollapsible(1, False)
        self.songs_split.setSizes([320, 400])

        # Shown above the songs of an opened Grid tile: "◀ All Albums   SUPERBLOOM".
        self.drill_bar = QWidget()
        self.drill_bar.setObjectName("drillBar")
        drill = QHBoxLayout(self.drill_bar)
        drill.setContentsMargins(4, 2, 4, 2)
        self.drill_back = self._tool_button("drillBack", None, "Back to the grid", "◀ All Albums")
        self.drill_title = QLabel()
        self.drill_title.setObjectName("drillTitle")
        self.drill_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drill.addWidget(self.drill_back)
        drill.addWidget(self.drill_title, 1)
        drill.addSpacing(self.drill_back.sizeHint().width())   # keeps the title centred
        self.drill_bar.hide()

        songs_page = QWidget()
        songs_layout = QVBoxLayout(songs_page)
        songs_layout.setContentsMargins(0, 0, 0, 0)
        songs_layout.setSpacing(0)
        songs_layout.addWidget(self.drill_bar)
        songs_layout.addWidget(self.songs_split, 1)

        self.grid = GridView()
        self.views = QStackedWidget()
        self.views.addWidget(songs_page)    # 0: List / Album List / Cover Flow
        self.views.addWidget(self.grid)     # 1: Grid
        self.device_page = DevicePage()
        self.views.addWidget(self.device_page)   # 2: an iPod's sync settings
        self.cd_page = CdPage()
        self.views.addWidget(self.cd_page)       # 3: an audio CD

        # File → Display Duplicates: "Displaying Duplicates  [Display All]" along the bottom.
        self.dup_bar = QWidget()
        self.dup_bar.setObjectName("dupBar")
        bar = QVBoxLayout(self.dup_bar)
        bar.setContentsMargins(4, 6, 4, 6)
        bar.setSpacing(3)
        self.filter_label = dup_label = QLabel("Displaying Duplicates")
        dup_label.setObjectName("dupLabel")
        dup_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dup_button = QToolButton()
        self.dup_button.setObjectName("dupButton")
        self.dup_button.setText("Display All")
        bar.addWidget(dup_label)
        bar.addWidget(self.dup_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.dup_bar.hide()
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.views, 1)
        right_layout.addWidget(self.dup_bar)

        # ---- Put it together ------------------------------------------------
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(left)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, False)
        self.splitter.setHandleWidth(1)
        self.splitter.setSizes([215, 1000])
        left.setMinimumWidth(150)
        # The sidebar can't grow past the biggest the album art is allowed to be.
        left.setMaximumWidth(max(150, self.art.screen_limit()))
        self.left_panel = left

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        # The body is the sidebar + list, or a playing movie / TV show.
        self.body = QStackedWidget()
        self.body.addWidget(self.splitter)          # 0
        self.body.addWidget(self.video_panel)       # 1
        layout.addWidget(self.body, 1)
        self.toolbar = toolbar
        self.setCentralWidget(central)

        self.summary_label = QLabel()
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.statusBar().addWidget(self.summary_label, 1)
        self.statusBar().setSizeGripEnabled(True)

    @staticmethod
    def _tool_button(name, icon=None, tooltip="", text="") -> QToolButton:
        button = QToolButton()
        button.setObjectName(name)
        if icon is not None:
            button.setIcon(icon)
        if text:
            button.setText(text)
        button.setToolTip(tooltip)
        button.setAutoRaise(False)
        return button

    def _build_menus(self):
        menus = self.menuBar()

        file_menu = menus.addMenu("&File")
        self._action(file_menu, "New Playlist", self._new_playlist, QKeySequence.StandardKey.New)
        self.from_selection_action = self._action(
            file_menu, "New Playlist from Selection", self._new_playlist_from_selection, "Ctrl+Shift+N")
        self._action(file_menu, "New Smart Playlist…", self._new_smart_playlist, "Ctrl+Alt+N")
        self._action(file_menu, "New Playlist Folder", self._new_folder)
        self.edit_smart_action = self._action(file_menu, "Edit Smart Playlist…",
                                              lambda: self._edit_smart_playlist(self._current_smart_id()))
        file_menu.addSeparator()
        self._action(file_menu, "Add File to Library…", self._add_file, "Ctrl+O")
        self._action(file_menu, "Add Folder to Library…", self._add_folder)
        library_menu = file_menu.addMenu("Library")
        self._action(library_menu, "Organize Library…", self._organize_library)
        self._action(library_menu, "Import iTunes Library…", self.import_itunes_library, "Ctrl+Shift+O")
        library_menu.addSeparator()
        self._action(library_menu, "Restore from Backup…", self._restore_backup)
        file_menu.addSeparator()
        self.info_action = self._action(file_menu, "Get Info", self._get_info, "Ctrl+I")
        self.file_rating_menu = file_menu.addMenu("Rating")
        self.reveal_action = self._action(file_menu, "Show in File Manager",
                                          self._reveal_selected, "Ctrl+Shift+R")
        self.duplicates_action = self._action(file_menu, "Display Duplicates", self._display_duplicates)
        self.missing_action = self._action(file_menu, "Display Missing", self._display_missing)
        file_menu.addSeparator()
        self.sync_action = self._action(file_menu, "Sync Device", self._sync_device)
        file_menu.addSeparator()
        quit_action = self._action(file_menu, "Quit", self.close)
        quit_action.setShortcuts([QKeySequence(QKeySequence.StandardKey.Quit), QKeySequence("Ctrl+W")])
        file_menu.aboutToShow.connect(self._update_file_menu)
        file_menu.aboutToHide.connect(self._file_menu_closed)

        edit_menu = menus.addMenu("&Edit")
        self.undo_action = self._action(edit_menu, "Undo", self._undo, "Ctrl+Z")
        self.redo_action = self._action(edit_menu, "Redo", self._redo, "Ctrl+Shift+Z")
        edit_menu.addSeparator()
        self.cut_action = self._action(edit_menu, "Cut", lambda: self._copy_songs(cut=True), "Ctrl+X")
        self.copy_action = self._action(edit_menu, "Copy", self._copy_songs, "Ctrl+C")
        self.paste_action = self._action(edit_menu, "Paste", self._paste_songs, "Ctrl+V")
        self.delete_action = self._action(edit_menu, "Delete", self._delete_selection)
        self._action(edit_menu, "Select All", self._select_all, "Ctrl+A")
        self._action(edit_menu, "Select None", self.table_clear_selection, "Ctrl+Shift+A")
        edit_menu.addSeparator()
        self._action(edit_menu, "Preferences…", self._show_preferences, "Ctrl+,")
        edit_menu.aboutToShow.connect(self._update_edit_menu)
        edit_menu.aboutToHide.connect(self._edit_menu_closed)

        view_menu = menus.addMenu("&View")
        group = QActionGroup(self)
        self.view_actions = {}
        for n, (mode, label) in enumerate(zip(VIEW_MODES, ("as List", "as Album List", "as Grid",
                                                           "as Cover Flow"))):
            action = self._action(view_menu, label, lambda checked=False, m=mode: self._set_view_mode(m),
                                  f"Ctrl+Shift+{n + 3}")
            action.setCheckable(True)
            group.addAction(action)
            self.view_actions[mode] = action
        self._action(view_menu, "View Options…", self._show_view_options, "Ctrl+J")
        view_menu.addSeparator()
        self.show_art_action = self._action(view_menu, "Show Artwork", self._toggle_artwork, "Ctrl+G")
        self.show_art_action.setCheckable(True)
        self.equalizer_action = self._action(view_menu, "Show Equalizer", self._toggle_equalizer,
                                             "Ctrl+T")
        view_menu.aboutToShow.connect(lambda: self.equalizer_action.setText(
            "Hide Equalizer" if self._eq_window is not None and self._eq_window.isVisible()
            else "Show Equalizer"))
        view_menu.addSeparator()
        self._action(view_menu, "Pop-Out Player", self._open_art_window)
        self._action(view_menu, "Mini Player", self._open_mini_player, "Ctrl+Shift+M")
        view_menu.addSeparator()
        self.dark_action = self._action(view_menu, "Dark Mode", self._toggle_dark_mode)
        self.dark_action.setCheckable(True)

        controls = menus.addMenu("&Controls")
        self.play_action = self._action(controls, "Play / Pause", self._on_play_clicked)  # Space: see _connect
        self.next_action = self._action(controls, "Next", self.player.next, "Ctrl+Right")
        self.previous_action = self._action(controls, "Previous", self.player.previous, "Ctrl+Left")
        self.current_song_action = self._action(controls, "Go to Current Song",
                                                self._go_to_current_song, "Ctrl+L")
        self.sleep_action = self._action(controls, "Sleep Timer…", self._sleep_timer_dialog,
                                         "Ctrl+Shift+S")
        self.sleep_off_action = self._action(controls, "Turn Off Sleep Timer",
                                             self._sleep_timer_off)
        self.sleep_off_action.setVisible(False)
        controls.aboutToShow.connect(self._update_controls_menu)
        self.recent_menu = controls.addMenu("Play Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        controls.addSeparator()
        self.eject_action = self._action(controls, "Eject Disc", self._eject_current_device, "Ctrl+E")
        controls.addSeparator()
        self.shuffle_action = self._action(controls, "Shuffle", None)
        self.shuffle_action.setCheckable(True)
        repeat_menu = controls.addMenu("Repeat")
        group = QActionGroup(self)
        self.repeat_actions = {}
        for mode, label in ((REPEAT_OFF, "Off"), (REPEAT_ALL, "All"), (REPEAT_ONE, "One")):
            action = self._action(repeat_menu, label, lambda checked=False, m=mode: self._set_repeat(m))
            action.setCheckable(True)
            group.addAction(action)
            self.repeat_actions[mode] = action
        self.repeat_actions[REPEAT_OFF].setChecked(True)

        help_menu = menus.addMenu("&Help")
        self._action(help_menu, "LuxTunes Help", self._show_help, "F1")
        help_menu.addSeparator()
        self._action(help_menu, "Check for Updates…", self._check_for_updates)
        from luxtunes.core import updater
        if updater.running_appimage():
            self._action(help_menu, "Remove LuxTunes from This Computer…", self._remove_appimage)
        help_menu.addSeparator()
        self._action(help_menu, "About LuxTunes", self._about)

    def _action(self, menu, text, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if slot is not None:
            action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _connect(self):
        self.sidebar.source_selected.connect(self.show_source)
        self.sidebar.tracks_dropped.connect(self._on_tracks_dropped)
        self.search.textChanged.connect(self._on_search)
        # Double-clicking the stars rates the song (a quick second click); it doesn't play it.
        self.table.doubleClicked.connect(
            lambda index: index.column() == RATING_COLUMN or self._play_row(index.row()))
        enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self.table)
        enter.setContext(Qt.ShortcutContext.WidgetShortcut)  # only when the song list has focus
        enter.activated.connect(self._play_selected)
        space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        space.activated.connect(self._on_play_clicked)
        delete = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.table)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self._delete_selection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_song_menu)
        self.table.selectionModel().currentRowChanged.connect(self._on_current_row_changed)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_summary())

        self.play_button.clicked.connect(self._on_play_clicked)
        self.next_button.clicked.connect(self.player.next)
        self.prev_button.clicked.connect(self.player.previous)
        self.volume.valueChanged.connect(self._on_volume)
        self.lcd.seek_requested.connect(self.player.seek)

        self.shuffle_button.toggled.connect(self._set_shuffle)
        self.shuffle_action.toggled.connect(self._set_shuffle)
        self.repeat_button.clicked.connect(self._cycle_repeat)
        self.art_toggle.clicked.connect(lambda: self._toggle_artwork(not self.art.isVisible()))
        self.art.header_clicked.connect(self._flip_art_mode)
        self.art.image_clicked.connect(self._open_art_window)
        self.new_playlist_button.clicked.connect(self._new_playlist)
        self.sidebar.playlist_renamed.connect(self._on_playlist_renamed)
        self.sidebar.menu_requested.connect(self._show_sidebar_menu)
        self.sidebar.delete_requested.connect(self._delete_source)
        self.model.field_edited.connect(self._on_field_edited)

        # Keep Cover Flow / Grid in step with the song list's contents and order.
        self.model.modelReset.connect(self._schedule_views_refresh)
        self.model.layoutChanged.connect(self._schedule_views_refresh)
        self.cover_flow.album_centered.connect(self._on_cover_flow_album)
        self.cover_flow.album_activated.connect(lambda album: self._play_tracks(album["tracks"]))
        self.grid.group_selected.connect(self._on_grid_selected)
        self.grid.group_opened.connect(self._open_grid_group)
        self.grid.group_play.connect(lambda group: self._play_tracks(group["tracks"]))
        self.grid.menu_requested.connect(self._show_grid_menu)
        self.drill_back.clicked.connect(self._close_grid_group)
        self.grid.grouping_changed.connect(self._update_summary)

        self.player.track_started.connect(self._on_track_started)
        self.player.track_finished.connect(self._on_track_finished)
        self.player.track_skipped.connect(self._on_track_skipped)
        self.player.playing_changed.connect(self._on_playing_changed)
        self.player.position_changed.connect(self._on_position)
        self.player.stopped.connect(self._on_stopped)
        self.player.track_started.connect(lambda *_: self._update_device_actions())
        self.player.stopped.connect(lambda *_: self._update_device_actions())
        self._setup_media_keys()
        self.player.file_missing.connect(self._on_file_missing)
        self.player.track_unavailable.connect(lambda t: self.model.set_missing(t["id"], True))
        self.player.relocated.connect(self._save_location)
        self.player.queue_changed.connect(self._on_queue_changed)
        self.player.bookmark_changed.connect(self._on_bookmark)
        self.dup_button.clicked.connect(self._display_all)
        self.sidebar.device_renamed.connect(self._on_device_renamed)
        self.sidebar.eject_requested.connect(self._eject_device)
        self.device_page.sync_requested.connect(self._sync_device)
        self.lcd.stop_requested.connect(self._stop_task)
        self.cd_page.import_clicked.connect(self._cd_import_clicked)
        self.cd_page.settings_clicked.connect(self._edit_import_settings)
        self.cd_page.lookup_clicked.connect(
            lambda: self.cd_page.disc and self._offer_cd_lookup(self.cd_page.disc.key,
                                                                then_continue=False))
        self.views.currentChanged.connect(self._update_view_buttons)
        self.cd_page.edited.connect(self._on_cd_edited)
        self.player.problem.connect(self._on_play_problem)

        self.model.rating_edited.connect(lambda tid, r: library.set_rating(self.conn, tid, r))
        self.model.enabled_edited.connect(lambda tid, on: library.set_enabled(self.conn, tid, on))

    # ======================================================================
    # Library & sources
    # ======================================================================

    def reload_library(self):
        playlists = library.load_playlists(self.conn)
        look = appearance(self.settings)            # Preferences → General → Sources
        has_movies = look["show_movies"]            # shown even while empty
        has_tv = look["show_tv_shows"]
        devices = [(key, self._device_name(ipod)) for key, ipod in self._players.items()]
        devices += [(key, disc.name) for key, disc in self._discs.items()]
        self.sidebar.populate(playlists, has_movies, theme.section_color(self.dark_action.isChecked()),
                              len(self.player.up_next), devices, has_tv,
                              look["show_podcasts"], look["show_audiobooks"])
        if not self.sidebar.select_source(self._current_source or SOURCE_MUSIC):
            self.sidebar.select_source(SOURCE_MUSIC)
        if library.count_tracks(self.conn, "music") == 0:
            self.statusBar().showMessage(
                "Your library is empty. Use File → Import iTunes Library… to get started."
            )

    def show_source(self, source):
        self._current_source = source
        self._end_duplicates()
        if source[0] == "device":
            self._show_device(source[1])
            return
        if self._showing_device_page():     # back from an iPod's or a CD's page
            self.views.setCurrentIndex(1 if self.view_mode == "grid" else 0)
            self.search.setEnabled(True)
        if self._drilled:
            self._leave_drill(show_grid=self.view_mode == "grid")
        kind, value = source
        if kind == "library":
            tracks = library.load_tracks(self.conn, media_kind=value)
            sort_column = ARTIST_COLUMN
            self.search.setPlaceholderText({"music": "Search Music", "movie": "Search Movies",
                                            "tvshow": "Search TV Shows",
                                            "podcast": "Search Podcasts",
                                            "audiobook": "Search Audiobooks"}.get(value, "Search"))
            self.grid.set_section(value)
        elif kind == "queue":
            tracks = []
            for i, track in enumerate(self.player.up_next):
                row = dict(track)            # a copy: the same song can be queued twice
                row["_queue_index"] = i
                tracks.append(row)
            sort_column = -1  # queue order
            self.search.setPlaceholderText("Search Up Next")
            self.grid.set_section("music")
        else:
            smart.refresh(self.conn, value)            # a live smart playlist: up to date now
            tracks = library.load_tracks(self.conn, playlist_id=value)
            sort_column = -1  # playlist order
            self.search.setPlaceholderText("Search Playlist")
            self.grid.set_section("music")
        self.model.set_tracks(tracks, sort_column)
        wanted = self._remembered_view(self._view_key(source))
        if wanted != self.view_mode:
            self._set_view_mode(wanted, remember=False)     # this section's own view
        header = self.table.horizontalHeader()
        header.blockSignals(True)   # don't make the view re-sort; the model already did
        header.setSortIndicator(sort_column, Qt.SortOrder.AscendingOrder)
        header.blockSignals(False)
        header.viewport().update()
        self.model.set_now_playing(self.player.current["id"] if self.player.current else None)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.table.scrollToTop()
        self._update_summary()
        self._update_play_button()

    def _on_search(self, text):
        self.model.set_filter(text)
        self._update_summary()

    def _update_summary(self):
        if self.view_mode == "grid" and not self._drilled:
            # Grid counts tiles: "3 albums, 54.8 minutes, 127.7 MB".
            groups = self.grid.model.groups
            tracks = [t for g in groups for t in g["tracks"]]
            self.summary_label.setText(formatting.library_summary(
                len(groups), sum(t.get("total_time") or 0 for t in tracks),
                sum(t.get("size") or 0 for t in tracks), noun=self.grid.grouping_noun()))
            return
        self.summary_label.setText(self.model.summary(self._selected_rows()))

    def import_itunes_library(self):
        xml_path, _ = QFileDialog.getOpenFileName(
            self, "Choose your iTunes Music Library.xml", os.path.expanduser("~"),
            "iTunes library (*.xml)",
        )
        if not xml_path:
            return
        media = QFileDialog.getExistingDirectory(
            self,
            "Choose the folder that holds your copied iTunes Media (it contains a 'Music' folder)",
            os.path.expanduser("~/Music/LuxTunes/LuxTunes Media"),
        )
        if not media:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = import_library(self.conn, xml_path, media)
        except Exception as error:  # show any failure instead of crashing
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Import failed", str(error))
            return
        QApplication.restoreOverrideCursor()
        self.reload_library()
        missing = len(result.missing_files)
        QMessageBox.information(
            self, "Import complete",
            f"Imported {result.tracks:,} songs and {result.playlists} playlists."
            + (f"\n\n{missing:,} files could not be found on disk." if missing else ""),
        )

    # ======================================================================
    # Playback
    # ======================================================================

    def _play_row(self, row: int):
        if not 0 <= row < self.model.rowCount():
            return
        if self._current_source == SOURCE_UP_NEXT:
            self.player.play_from_up_next(self.model.track_at(row)["_queue_index"])
        else:
            self._playing_source = self._current_source
            self.player.play_queue(self.model.visible_tracks(), row)

    def _play_selected(self):
        index = self.table.currentIndex()
        self._play_row(index.row() if index.isValid() else 0)

    def _stop_mode(self) -> bool:
        """Show Stop (not Pause) while you look at a different list
        from the one that's playing."""
        return (self.player.is_playing and self._playing_source is not None
                and self._current_source not in (self._playing_source, SOURCE_UP_NEXT))

    def _update_play_button(self):
        if self._stop_mode():
            icon = self._stop_icon
        else:
            icon = self._pause_icon if self.player.is_playing else self._play_icon
        self.play_button.setIcon(icon)

    # ---- Media keys (Play/Pause, Next, Previous, Stop, Mute) ------------------------------

    def _setup_media_keys(self):
        """The keyboard's media keys. On a desktop like Cinnamon they're caught by
        the desktop and passed on over MPRIS (ui/mpris.py), which also works
        while LuxTunes is in the background. Only without that (no desktop bus)
        does the window handle them itself, so one press never acts twice."""
        self._volume_before_mute = None
        self._last_media_action = ("", 0.0)
        self._mpris = None
        try:
            from luxtunes.ui.mpris import Mpris
            self._mpris = Mpris({
                "play_pause": lambda: self._media_action("play_pause"),
                "stop": lambda: self._media_action("stop"),
                "next": lambda: self._media_action("next"),
                "previous": lambda: self._media_action("previous"),
                "raise": self._bring_to_front, "quit": self.close,
                "current": lambda: self.player.current,
                "playing": lambda: self.player.is_playing,
                "volume": self.volume.value}, self)
        except Exception as error:           # media keys are a nicety; never stop the app
            log.warning("media keys unavailable: %s", error)
            self._mpris = None
        keys = [(Qt.Key.Key_VolumeMute, self._toggle_mute)]
        if self._mpris is None or not self._mpris.active:
            keys += [(Qt.Key.Key_MediaTogglePlayPause, lambda: self._media_action("play_pause")),
                     (Qt.Key.Key_MediaPlay, lambda: self._media_action("play_pause")),
                     (Qt.Key.Key_MediaPause, lambda: self._media_action("play_pause")),
                     (Qt.Key.Key_MediaNext, lambda: self._media_action("next")),
                     (Qt.Key.Key_MediaPrevious, lambda: self._media_action("previous")),
                     (Qt.Key.Key_MediaStop, lambda: self._media_action("stop"))]
        for key, slot in keys:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(slot)
        if self._mpris is not None:
            for signal in (self.player.track_started, self.player.playing_changed,
                           self.player.stopped):
                signal.connect(lambda *_: self._mpris and self._mpris.changed())

    def _media_action(self, action: str):
        """One media key press. The same key arriving twice within a moment (by
        two routes) only counts once."""
        now = time.monotonic()
        last, when = self._last_media_action
        if action == last and now - when < 0.3:
            log.info("media key %s: ignored a second copy", action)
            return
        self._last_media_action = (action, now)
        log.info("media key: %s", action)
        if action == "play_pause":
            self._media_play_pause()
        elif action == "next":
            self.player.next()
        elif action == "previous":
            self.player.previous()
        elif action == "stop":
            self.player.stop()

    def _media_play_pause(self):
        """Play/Pause key: pause or resume, or start the list on screen."""
        if self.player.current is not None:
            self.player.toggle()
        else:
            self._start_in_current_list()

    def _toggle_mute(self):
        if self._volume_before_mute is None:
            self._volume_before_mute = self.volume.value()
            self.volume.setValue(0)
        else:
            self.volume.setValue(self._volume_before_mute or 50)
            self._volume_before_mute = None

    def _bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_play_clicked(self):
        if self._stop_mode():
            self.player.stop()                 # Stop: everything stops
        elif self.player.current is not None:
            self.player.toggle()               # Pause / resume
        else:
            self._start_in_current_list()

    def _start_in_current_list(self):
        """Play from the list on screen: the selected song if there is one,
        otherwise a random song (Shuffle on) or the top of the list."""
        count = self.model.rowCount()
        if count == 0:
            return
        index = self.table.currentIndex()
        if index.isValid() and self.table.selectionModel().isRowSelected(index.row()):
            row = index.row()
        elif self.player.shuffle:
            row = random.randrange(count)
        else:
            row = 0
        self._play_row(row)

    def _on_file_missing(self, track: dict):
        """Say the file is gone and offer to find it."""
        self.model.set_missing(track["id"], True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("LuxTunes")
        box.setText(
            f"The song “{track.get('name')}” could not be used because the original "
            "file could not be found. Would you like to locate it?"
        )
        box.setInformativeText(f"LuxTunes looked for it at:\n{track.get('location') or '(no location)'}")
        locate = box.addButton("Locate", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(locate)
        box.exec()
        if box.clickedButton() is not locate:
            return

        if not self._locate_track(track):
            return
        # Play it now, from the row it's shown in.
        row = self.model.row_of(track["id"], self.table.currentIndex().row())
        if row >= 0:
            self._play_row(row)

    def _locate_track(self, track: dict) -> bool:
        """Ask where a missing song's file is, save it, then offer to look for
        the library's other missing files in the same place.
        Returns False if you cancelled."""
        located = self._locate_and_search(track)
        if located and self._showing_missing:
            self._refresh_missing_view()
        return located

    def _locate_and_search(self, track: dict) -> bool:
        from luxtunes.core import relink
        path, _ = QFileDialog.getOpenFileName(
            self, f"Locate “{track.get('name')}”",
            self.settings.value("locate_folder") or nearest_existing_folder(track.get("location")),
            "Music and videos (*.mp3 *.m4a *.m4b *.aac *.flac *.wav *.aif *.aiff *.ogg *.opus "
            "*.m4v *.mp4 *.mov);;All files (*)",
        )
        if not path:
            return False
        self.settings.setValue("locate_folder", os.path.dirname(path))
        old_location = track.get("location") or ""
        self._save_location(track, path)

        missing = [t for t in library.load_tracks(self.conn)
                   if t["id"] != track["id"] and find_file(t.get("location")) is None]
        if not missing:
            return True
        same_album = sum(1 for t in missing if t.get("album") and t.get("album") == track.get("album"))
        detail = (f"{same_album:,} other song{'s' if same_album != 1 else ''} from “{track.get('album')}” "
                  f"{'are' if same_album != 1 else 'is'} also missing. " if same_album else "")
        answer = QMessageBox.question(
            self, "LuxTunes",
            f"{detail}{len(missing):,} item{'s' if len(missing) != 1 else ''} in your library "
            f"can't be found.\n\nWould you like to search for the remaining files in this location?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return True
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            found = relink.find_others(old_location, path, missing)
            for track_id, new_path in found.items():
                library.set_location(self.conn, track_id, new_path)
                self.model.update_track(track_id, {"location": new_path})
                self.model.set_missing(track_id, False)
                self.player.refresh_track(track_id, {"location": new_path})
        finally:
            QApplication.restoreOverrideCursor()
        if found:
            QMessageBox.information(
                self, "LuxTunes",
                f"Found {len(found):,} of the {len(missing):,} missing items. "
                + ("" if len(found) == len(missing) else
                   "Use File → Display Missing to see the ones that are still missing."))
        else:
            QMessageBox.information(self, "LuxTunes",
                                    "None of the other missing files were found in this location.")
        return True

    def _refresh_missing_view(self):
        """Display Missing is showing: rebuild it after files were found."""
        self._showing_missing = False
        if not self.library_problems():
            self._end_duplicates()
            self._current_source = None
            if not self.sidebar.select_source(SOURCE_MUSIC):
                self.show_source(SOURCE_MUSIC)
            self.statusBar().showMessage("Every item in your library now has its file.", 6000)
            return
        self._display_missing()

    def _locate_selected(self, track: dict):
        self._locate_track(track)

    def _save_location(self, track: dict, path: str):
        library.set_location(self.conn, track["id"], path)
        self.model.update_track(track["id"], {"location": path})
        self.model.set_missing(track["id"], False)

    def _on_track_started(self, track: dict):
        self._remember_recent(track["id"])
        if self._eq_window is not None:
            self._eq_window.show_song(track)
        self.model.set_missing(track["id"], False)  # it's back, if it was ever missing
        self.lcd.show_track(track)
        self.model.set_now_playing(track["id"])
        # The art shows whatever happened last: a song starting, or you clicking one.
        self._art_mode = "playing"
        self._refresh_artwork()
        self._update_play_button()
        if self.player.shuffle and self._current_source == self._playing_source:
            # The view follows a shuffle (scrolls to the song)
            # but your selection stays where it was.
            row = self.model.row_of(track["id"])
            if row >= 0:
                self.table.scrollTo(self.model.index(row, NAME_COLUMN),
                                    QAbstractItemView.ScrollHint.PositionAtCenter)
        if self._art_window is not None:
            self._art_window.show_track(track, self._artwork_for(track))
            self._art_window.set_lyrics(self._lyrics_for(track))
        if self._mini_player is not None:
            self._mini_player.show_track(track)
        # Movies and TV shows play in the video window; music closes it.
        if track.get("media_kind") in VIDEO_KINDS:
            self._show_video(track)
        else:
            self._hide_video()

    def _on_playing_changed(self, playing: bool):
        self._update_play_button()
        self.video_panel.set_playing(playing)
        if self._art_window is not None:
            self._art_window.set_playing(playing)
        if self._mini_player is not None:
            self._mini_player.set_playing(playing)

    def _on_position(self, position: int, duration: int):
        self.lcd.update_position(position, duration)
        if self.showing_video():
            self.video_panel.update_position(position, duration)
        if self._art_window is not None:
            self._art_window.update_position(position, duration)
        if self._mini_player is not None:
            self._mini_player.update_position(position, duration)

    def _on_track_finished(self, track: dict):
        self.model.update_track(track["id"], library.record_play(self.conn, track["id"]))
        self._schedule_smart_refresh()

    def _on_track_skipped(self, track: dict):
        self.model.update_track(track["id"], library.record_skip(self.conn, track["id"]))
        self._schedule_smart_refresh()

    def _schedule_smart_refresh(self):
        """A play or an edit can change what a live smart playlist holds (e.g.
        "Unplayed"): update the one on show shortly (once for a batch)."""
        if not self._smart_refresh_pending:
            self._smart_refresh_pending = True
            QTimer.singleShot(400, self._refresh_shown_smart_playlist)

    def _refresh_shown_smart_playlist(self):
        self._smart_refresh_pending = False
        source = self._current_source
        if not source or source[0] != "playlist" or self._dup_ids is not None or self._showing_missing:
            return
        playlist = self._playlist_by_id(source[1])
        if playlist and playlist["is_smart"] and smart.refresh(self.conn, source[1]):
            self._reload_list_in_place()

    def _reload_list_in_place(self):
        """Reload the songs on show, keeping the sort, search and scroll position."""
        source = self._current_source
        scroll = self.table.verticalScrollBar().value()
        search = self.search.text()
        sort = self.table.horizontalHeader().sortIndicatorSection()
        order = self.table.horizontalHeader().sortIndicatorOrder()
        self.show_source(source)
        if sort >= 0 and sort != self.table.horizontalHeader().sortIndicatorSection():
            self.table.sortByColumn(sort, order)
        if search:
            self.search.setText(search)
        self.table.verticalScrollBar().setValue(scroll)

    def _on_stopped(self):
        self._hide_video()
        self.lcd.clear()
        self.model.set_now_playing(None)
        self._playing_source = None
        self._art_mode = "selected"
        self._refresh_artwork()
        self._update_play_button()

    def _on_volume(self, value: int):
        self.player.set_volume(value)
        self.settings.setValue("volume", value)
        if self._art_window is not None:
            self._art_window.set_volume(value)
        if self._mini_player is not None:
            self._mini_player.set_volume(value)
        self.video_panel.set_volume(value)

    # ---- Movies and TV shows ------------------------------------------------------

    def showing_video(self) -> bool:
        return self.body.currentWidget() is self.video_panel

    def _show_video(self, track: dict):
        self.video_panel.set_volume(self.volume.value())
        self.body.setCurrentWidget(self.video_panel)
        self.video_panel.show_track(track)

    def _hide_video(self):
        """Back to the sidebar and list (the video ended, was closed, or music started)."""
        if self._video_full_screen:
            self._toggle_video_full_screen()
        if self.showing_video():
            self.body.setCurrentWidget(self.splitter)
            self.table.setFocus()

    def _toggle_video_full_screen(self):
        """Full screen hides the menus and the top and bottom bars too."""
        full = not self._video_full_screen
        if full and not self.showing_video():
            return
        self._video_full_screen = full
        for bar in (self.menuBar(), self.toolbar, self.statusBar()):
            bar.setVisible(not full)
        if full:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
        elif self._was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
        self.video_panel.set_full_screen(full)

    def _set_shuffle(self, on: bool):
        self.player.shuffle = on
        self.settings.setValue("shuffle", on)         # remembered for next time
        for widget in (self.shuffle_button, self.shuffle_action):
            if widget.isChecked() != on:
                widget.setChecked(on)

    def _cycle_repeat(self):
        order = [REPEAT_OFF, REPEAT_ALL, REPEAT_ONE]
        self._set_repeat(order[(order.index(self.player.repeat) + 1) % 3])

    def _set_repeat(self, mode: str):
        self.player.repeat = mode
        self.settings.setValue("repeat", mode)        # remembered for next time
        self.repeat_actions[mode].setChecked(True)
        self.repeat_button.setText("↻¹" if mode == REPEAT_ONE else "↻")
        self.repeat_button.setCheckable(True)
        self.repeat_button.setChecked(mode != REPEAT_OFF)
        self.repeat_button.setToolTip(f"Repeat: {mode}")

    # ---- The four views: List, Album List, Grid, Cover Flow ----------------------

    def _refresh_view_icons(self):
        color = theme.text_color(self.dark_action.isChecked())
        for button, icon in zip(self.view_buttons, view_icons(color)):
            button.setIcon(icon)

    # Each Library section remembers its own view (Music: List; Movies, TV
    # Shows, Podcasts and Audiobooks: Grid); playlists and Up Next share one.
    VIEW_DEFAULTS = {"music": "list", "playlists": "list"}

    def _view_key(self, source=None) -> str:
        source = source or self._current_source or SOURCE_MUSIC
        return source[1] if source[0] == "library" else "playlists"

    def _remembered_view(self, key: str) -> str:
        mode = self.settings.value(f"view_mode/{key}", self.VIEW_DEFAULTS.get(key, "grid"))
        return mode if mode in VIEW_MODES else self.VIEW_DEFAULTS.get(key, "grid")

    def _set_view_mode(self, mode: str, remember: bool = True):
        self.view_mode = mode
        if remember:
            self.settings.setValue(f"view_mode/{self._view_key()}", mode)
        self.view_buttons[VIEW_MODES.index(mode)].setChecked(True)
        self.view_actions[mode].setChecked(True)
        if self._showing_device_page():
            return      # an iPod's or CD's page is showing; the new view applies when you leave it
        if self._drilled:
            self._leave_drill(show_grid=False)
        if mode == "grid":
            self.views.setCurrentIndex(1)
        else:
            self.views.setCurrentIndex(0)
            self.cover_flow.setVisible(mode == "coverflow")
            self._apply_table_layout("album" if mode == "album" else "list")
        self._refresh_views()
        if mode == "coverflow":
            index = self.table.currentIndex()
            self.cover_flow.center_on_track(self.model.track_at(index.row()) if index.isValid() else None)
            self.cover_flow.setFocus()

    def _apply_table_layout(self, layout: str):
        """Switch the song table between the List and Album List column sets.
        Each keeps its own columns, widths and sort."""
        if layout == self._table_layout:
            return
        header = self.table.horizontalHeader()
        self._header_states[self._table_layout] = header.saveState()
        self._table_layout = layout
        saved = self._header_states.get(layout)
        if saved is not None:
            header.restoreState(saved)
        elif layout == "album":
            self._default_album_layout()
        else:
            for i, column in enumerate(COLUMNS):
                self.table.setColumnHidden(i, not column.shown)
        for i in (ALBUM_GROUP_COLUMN, ALBUM_TRACK_COLUMN):   # album columns only in Album List
            self.table.setColumnHidden(i, layout != "album")
        self.table.setColumnHidden(ENABLED_COLUMN, not appearance(self.settings)["show_list_checkboxes"])
        self.table.set_album_mode(layout == "album")

    def _default_album_layout(self):
        header = self.table.horizontalHeader()
        keys = [c.key for c in COLUMNS]
        for i, column in enumerate(COLUMNS):
            self.table.setColumnHidden(i, column.key not in ALBUM_LAYOUT)
        for position, key in enumerate(ALBUM_LAYOUT):
            header.moveSection(header.visualIndex(keys.index(key)), position)
        self.table.setColumnWidth(ALBUM_GROUP_COLUMN, COLUMNS[ALBUM_GROUP_COLUMN].width)
        header.setSortIndicator(ALBUM_GROUP_COLUMN, Qt.SortOrder.AscendingOrder)

    def _schedule_views_refresh(self):
        # Several model changes can arrive together; refresh once afterwards.
        if not self._views_refresh_pending:
            self._views_refresh_pending = True
            QTimer.singleShot(0, self._refresh_views)

    def _refresh_views(self):
        self._views_refresh_pending = False
        if self.view_mode == "coverflow" and self.cover_flow.isVisible():
            self.cover_flow.set_albums(albums_in_order(self.model.visible_tracks()))
        if self.view_mode == "grid" and not self._drilled:
            self.grid.set_tracks(self.model.visible_tracks())
        self._update_summary()

    def _play_tracks(self, tracks: list[dict]):
        """Play a whole album / artist / genre from the top (Grid, Cover Flow)."""
        if tracks:
            self._playing_source = self._current_source
            self.player.play_queue(list(tracks), 0)

    def _select_track(self, track: dict, scroll_hint=QAbstractItemView.ScrollHint.PositionAtTop):
        row = self.model.row_of(track["id"])
        if row >= 0:
            self._syncing = True
            self.table.selectRow(row)
            self.table.scrollTo(self.model.index(row, NAME_COLUMN), scroll_hint)
            self._syncing = False

    def _on_cover_flow_album(self, album: dict):
        # Cover Flow moved to an album: highlight its first song in the list below.
        self._select_track(album["first_track"])

    def _on_grid_selected(self, group: dict):
        self._grid_group = group
        self._select_track(group["tracks"][0])   # the art pane shows it

    def _open_grid_group(self, group: dict):
        """Double-clicked a Grid tile: show just its songs, Album List style."""
        self._drilled = True
        self.model.set_id_filter({t["id"] for t in group["tracks"]})
        self.drill_back.setText(f"◀ {self.grid.all_label()}")
        self.drill_title.setText(group["title"])
        self.drill_bar.show()
        self.cover_flow.hide()
        self.views.setCurrentIndex(0)
        self._apply_table_layout("album")
        self.table.scrollToTop()

    def _close_grid_group(self):
        self._leave_drill(show_grid=True)

    def _leave_drill(self, show_grid: bool):
        self._drilled = False
        self.drill_bar.hide()
        self.model.set_id_filter(self._dup_ids)     # back to all (or all duplicates)
        if show_grid:
            self.views.setCurrentIndex(1)
            self._refresh_views()

    # ---- Go to Current Song (Ctrl+L) -------------------------------------------

    def _go_to_current_song(self):
        """Jump to the list the song was started from, select it, and flash it."""
        track = self.player.current
        if track is None:
            return
        source = self._playing_source or SOURCE_MUSIC
        if self._current_source != source:
            if not self.sidebar.select_source(source):
                self.sidebar.select_source(SOURCE_MUSIC)
        row = self.model.row_of(track["id"])
        if row < 0 and self.search.text():
            self.search.clear()                  # a search was hiding it
            row = self.model.row_of(track["id"])
        if row < 0 and self._current_source != SOURCE_MUSIC:
            self.sidebar.select_source(SOURCE_MUSIC)  # e.g. played from Up Next
            row = self.model.row_of(track["id"])
        if row < 0:
            return
        self.table.scrollTo(self.model.index(row, NAME_COLUMN),
                            QAbstractItemView.ScrollHint.PositionAtCenter)
        self.table.setFocus()
        self._flash_row(row)

    def _flash_row(self, row: int, times: int = 3, interval_ms: int = 110):
        """Blink the row's highlight a few times so you can spot it, ending selected."""
        steps = [False, True] * times
        for n, selected in enumerate(steps):
            QTimer.singleShot(interval_ms * n, lambda sel=selected: (
                self.table.selectRow(row) if sel else self.table.clearSelection()))

    # ---- Editing song details in the list ---------------------------------------

    def _on_field_edited(self, track_id: int, key: str, value):
        try:
            saved = library.update_track_fields(self.conn, track_id, {key: value})
        except ValueError:
            title = next(c.title for c in COLUMNS if c.key == key)
            QMessageBox.warning(self, "LuxTunes", f"“{value}” isn't a valid {title}.")
            return
        error = self._update_file(track_id)
        self._after_edit(track_id)
        if error:
            self.statusBar().showMessage(
                f"Saved in the library, but the file couldn't be updated: {error}.", 8000)
        self._update_summary()

    def _after_edit(self, track_id: int):
        """Show a song's saved details everywhere: list, display, player."""
        track = library.load_track(self.conn, track_id)
        if track is None:
            return
        self.model.update_track(track_id, track)
        for window in self._playlist_windows:
            window.model.update_track(track_id, track)
        if not self._resort_pending:        # once, after a whole batch of edits
            self._resort_pending = True
            QTimer.singleShot(0, self._resort_after_edit)
        self._schedule_smart_refresh()
        self.player.refresh_track(track_id, track)   # e.g. the file's new location
        current = self.player.current
        if current is not None and current["id"] == track_id:
            self.lcd.show_track(current)        # the display shows the new name
            self.player.apply_volume()          # a new Volume Adjustment applies right away
            self.player.apply_equalizer()       # and a new Equalizer Preset
            if self._eq_window is not None:
                self._eq_window.show_song(current)

    def _resort_after_edit(self):
        """Edited songs jump to their new place in the sorted list,
        staying selected, and the list scrolls to follow them."""
        self._resort_pending = False
        self.model.resort()
        for window in self._playlist_windows:
            window.model.resort()
        current = self.table.currentIndex()
        if current.isValid():
            self.table.scrollTo(current, QAbstractItemView.ScrollHint.EnsureVisible)

    # ---- Get Info (Ctrl+I) ------------------------------------------------------

    def _info_targets(self) -> tuple[list[dict], int]:
        """The songs Get Info is for, plus (for one song) its row in the list."""
        if self.view_mode == "grid" and not self._drilled:
            group = self._grid_group
            return (list(group["tracks"]), -1) if group else ([], -1)
        rows = self._selected_rows()
        if len(rows) == 1:
            return [self.model.track_at(rows[0])], rows[0]
        return [self.model.track_at(r) for r in rows], -1

    def _get_info(self):
        tracks, row = self._info_targets()
        tracks = [t for t in tracks if t is not None]
        if not tracks:
            return
        self._info_changed = self._kind_changed = False
        if len(tracks) == 1:
            if row < 0:                 # e.g. a one-song album in Grid view
                dialog = InfoDialog(self.conn, tracks, 0, self._apply_info, self,
                                    presets=self.eq_settings.names())
            else:                       # Previous/Next walk through the list
                dialog = InfoDialog(self.conn, self.model.visible_tracks(), row, self._apply_info,
                                    self, presets=self.eq_settings.names())
        else:
            if not self._confirm_multi_info():
                return
            dialog = MultiInfoDialog(self.conn, tracks, self._apply_info, self,
                                     presets=self.eq_settings.names())
        dialog.exec()
        if self._kind_changed:
            self.reload_library()            # moved between Music and Movies
        elif self._info_changed:
            self._update_summary()
            self._refresh_artwork()
            self._schedule_views_refresh()   # album names/art may have changed

    def _confirm_multi_info(self) -> bool:
        """The warning before editing several songs at once."""
        if not self.settings.value("confirm_multi_info", True, type=bool):
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("LuxTunes")
        box.setText("Are you sure you want to edit information for multiple items?")
        check = QCheckBox("Do not ask me again")
        box.setCheckBox(check)
        edit = box.addButton("Edit Items", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(edit)
        box.exec()
        if box.clickedButton() is not edit:
            return False
        if check.isChecked():
            self.settings.setValue("confirm_multi_info", False)
        return True

    def _apply_info(self, track_id: int, fields: dict, rating, artworks=UNCHANGED) -> str | None:
        """Save one song's Get Info changes, then write them into its file.
        Returns None, or why the file couldn't be updated (the edit stays
        pending and is retried later). Raises ValueError for a bad value."""
        if fields:
            library.update_track_fields(self.conn, track_id, fields)
            self._kind_changed |= "media_kind" in fields
        if rating is not None:
            library.set_rating(self.conn, track_id, rating)
        error = self._update_file(track_id, artworks)
        self._after_edit(track_id)
        self._info_changed = True
        return error

    def _update_file(self, track_id: int, artworks=UNCHANGED) -> str | None:
        """After an edit: write the changes into the file's tags, then (with
        "Keep LuxTunes Media folder organized" on) move/rename the file to
        match. Returns None, or what went wrong."""
        error = tag_sync.write_pending(self.conn, track_id, artworks)
        if error:
            error = f"{error} (LuxTunes will try writing the tags again next time it starts)"
        elif artworks is not UNCHANGED:
            library.set_artwork_count(self.conn, track_id, len(artworks))
            track = library.load_track(self.conn, track_id)
            ART_CACHE.forget(track and track.get("location"))
        move_error = organize.organize_track(self.conn, track_id)
        if move_error:
            move_error = f"couldn't move it to its new folder ({move_error})"
        return "; ".join(e for e in (error, move_error) if e) or None

    # ---- Get Track Info Online… (right-click) ------------------------------------

    def _get_track_info_online(self, tracks: list[dict]):
        """Look the selected songs up on MusicBrainz (after you say yes), then
        show every suggested change for you to tick before anything is saved."""
        tracks = [{k: v for k, v in t.items() if not k.startswith("_")} for t in tracks if t]
        if not tracks or self._song_lookup is not None:
            return
        from luxtunes.core import musicbrainz
        albums, loose = musicbrainz.group_by_album(tracks)
        lookups = len(albums) + len(loose)
        count = len(tracks)
        approved = self._approve_internet(
            "Get Track Info Online",
            f"Look up {'“' + tracks[0].get('name', '') + '”' if count == 1 else f'{count:,} songs'} "
            "on the internet?",
            "LuxTunes will send the song names, artists and album names to MusicBrainz, a free "
            "online music database, and download suggested corrections, plus missing album covers "
            "from the Cover Art Archive. You'll see every change before anything is saved."
            + (f"\n\nThis takes about {max(1, lookups * 2 // 60)} minute(s): MusicBrainz allows "
               "about one question a second." if lookups > 20 else ""),
            "Look Up Online")
        if not approved:
            return
        from luxtunes.ui.cd_worker import SongLookupWorker
        worker = SongLookupWorker(tracks, approved=True, parent=self)
        self._song_lookup = worker
        progress = QProgressDialog("Looking up…", "Stop", 0, lookups, self)
        progress.setWindowTitle("Get Track Info Online")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.canceled.connect(worker.stop)

        def on_progress(done, total, text):
            progress.setMaximum(max(total, 1))
            progress.setValue(min(done, total))
            progress.setLabelText(text + "…" if text != "Done" else text)

        def on_done(result):
            progress.close()
            self._review_track_info(result)

        def on_failed(message):
            progress.close()
            QMessageBox.warning(self, "Get Track Info Online", f"The lookup didn't work:\n\n{message}")

        def on_finished():
            self._song_lookup = None
            worker.deleteLater()

        worker.progress.connect(on_progress)
        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        worker.finished.connect(on_finished)
        worker.start()

    def _review_track_info(self, result):
        from luxtunes.ui.track_info_dialog import TrackInfoDialog
        if not result.albums:
            text = "MusicBrainz had nothing new for these songs."
            if result.problems:
                text += "\n\n" + "\n".join(result.problems[:5])
            QMessageBox.information(self, "Get Track Info Online", text)
            return
        dialog = TrackInfoDialog(result, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        fields, artwork = dialog.chosen()
        problems = []
        changed = sorted(set(fields) | set(artwork))
        for track_id in changed:
            try:
                error = self._apply_info(track_id, fields.get(track_id, {}), None,
                                         [artwork[track_id]] if track_id in artwork else UNCHANGED)
            except ValueError as bad:
                error = str(bad)
            if error:
                track = library.load_track(self.conn, track_id) or {}
                problems.append(f"{track.get('name', track_id)}: {error}")
        self._refresh_artwork()
        self.statusBar().showMessage(f"Updated {len(changed)} song{'s' if len(changed) != 1 else ''}.",
                                     6000)
        if problems:
            QMessageBox.warning(self, "Get Track Info Online",
                                "Some files couldn't be updated:\n\n" + "\n".join(problems[:20]))

    # ---- Preferences and Organize Library ---------------------------------------

    def _show_preferences(self):
        dialog = PreferencesDialog(self.conn, self, self.settings)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_appearance()
            new_folder = str(organize.media_folder(self.conn))
            if os.path.normpath(new_folder) != os.path.normpath(dialog.old_folder):
                self._relocate_media_folder(dialog.old_folder, new_folder)

    def _relocate_media_folder(self, old: str, new: str):
        """The media folder moved: songs in the old folder point to the new one
        (those whose files are there)."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = organize.relocate_media_folder(self.conn, old, new)
        finally:
            QApplication.restoreOverrideCursor()
        if not result.updated and not result.not_found:
            return                                   # no songs lived in the old folder
        if result.updated:
            self.reload_library()
            self.player.update_locations(dict(self.conn.execute("SELECT id, location FROM tracks")))
        lines = []
        if result.updated:
            lines.append(f"{result.updated:,} songs now play from “{new}”.")
        if result.not_found:
            lines.append(f"{result.not_found:,} songs from “{old}” weren't found in the new "
                         "folder, so they still point to the old one. Copy them over and choose "
                         "the folder again, or use File → Display Missing to find them.")
        QMessageBox.information(self, "LuxTunes Media Folder", "\n\n".join(lines))

    def _apply_appearance(self):
        """Preferences → General: text sizes, icons, checkboxes, Grid colour,
        which sources show, and the library's name in the title bar."""
        look = appearance(self.settings)
        theme.apply_theme(QApplication.instance(), self.dark_action.isChecked(), look["grid_dark"])
        base = QApplication.font()
        bigger = 2 if look["source_text"] == "large" else 0
        font = QFont(base)
        font.setPointSizeF(base.pointSizeF() + bigger)
        self.sidebar.setFont(font)
        self.sidebar.setIconSize(QSize(16 + bigger * 2, 16 + bigger * 2))
        self.sidebar.show_icons = look["show_source_icons"]
        large_list = look["list_text"] == "large"
        font = QFont(base)
        font.setPointSizeF(base.pointSizeF() + (2 if large_list else 0))
        self.table.setFont(font)
        album_view.ROW_HEIGHT = 23 if large_list else 19
        self.table.verticalHeader().setDefaultSectionSize(album_view.ROW_HEIGHT)
        self.table.setColumnHidden(ENABLED_COLUMN, not look["show_list_checkboxes"])
        if self._table_layout == "album":
            self.table.refresh_album_layout()
        for part in [self.grid, *self.grid.findChildren(QWidget)]:   # repaint in the new colours
            part.update()
        self._refresh_view_icons()
        self.setWindowTitle(f"LuxTunes — {library.library_name(self.conn)}")
        self._apply_playback_prefs(look)
        self.reload_library()

    def _apply_playback_prefs(self, look: dict):
        """Preferences → Playback: crossfade, gapless, Sound Check."""
        self.player.crossfade_seconds = look["crossfade_seconds"] if look["crossfade"] else 0
        self.player.gapless = look["gapless"]
        self.player.sound_check = look["sound_check"]
        self.player.apply_volume()
        if look["sound_check"]:
            QTimer.singleShot(3000, self._measure_loudness)   # after the window is up

    def _measure_loudness(self):
        """Sound Check: give songs without a loudness correction one, in the background."""
        if self._loudness_worker is not None or not appearance(self.settings)["sound_check"]:
            return
        missing = self.conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE loudness IS NULL AND "
            "media_kind IN ('music', 'podcast', 'audiobook')").fetchone()[0]
        if not missing:
            return
        from luxtunes.ui.loudness_worker import LoudnessWorker
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        if not db_path:
            return
        worker = LoudnessWorker(db_path, self)
        self._loudness_worker = worker
        worker.progress.connect(lambda done, total, name: self.lcd.update_count(
            "loudness", f"“{name}”", done, total))
        worker.measured.connect(self._on_loudness_measured)
        worker.done.connect(self._on_loudness_done)
        worker.finished.connect(worker.deleteLater)
        self.lcd.start_task("loudness", "Sound Check", "Checking song volumes…",
                            "Stop (carries on next time)")
        worker.start()

    def _on_loudness_measured(self, track_id: int, gain: float):
        self.model.update_track(track_id, {"loudness": gain})
        self.player.refresh_track(track_id, {"loudness": gain})
        current = self.player.current
        if current is not None and current.get("id") == track_id:
            self.player.apply_volume()

    def _on_loudness_done(self, count: int, stopped: bool, no_ffmpeg: bool):
        self._loudness_worker = None
        if stopped:
            message = f"Sound Check paused after {count:,} songs; it carries on next time."
        else:
            message = f"Sound Check is ready ({count:,} songs checked)."
        self._lcd_finished("loudness", message)
        if no_ffmpeg:
            QMessageBox.information(
                self, "Sound Check",
                "Some songs have no volume information in their tags, and measuring them "
                "needs FFmpeg, which isn't installed. Install it with:\n\n"
                "    sudo apt install ffmpeg\n\nSound Check carries on with them next time.")

    def _restore_backup(self):
        """File → Library → Restore from Backup…: pick one of the weekly backups
        (the chooser opens in the Backups folder) and put it back. The library as
        it is now is kept beside the backups first."""
        from luxtunes.core import backup
        busy = [what for what, worker in (("a sync", self._sync_worker),
                                          ("adding files", self._add_worker),
                                          ("Sound Check", self._loudness_worker)) if worker]
        if busy:
            QMessageBox.information(self, "Restore from Backup",
                                    f"Wait until {busy[0]} has finished, then try again.")
            return
        folder = backup.folder_for(self.conn)
        if folder is None:
            return
        folder.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Restore from Backup", str(folder),
                                              "LuxTunes libraries (*.db)")
        if not path:
            return
        try:
            songs = backup.check(Path(path))
        except backup.RestoreError as error:
            QMessageBox.warning(self, "Restore from Backup", str(error))
            return
        if Path(path).resolve() == backup.library_path(self.conn).resolve():
            return                                   # that's the library itself
        answer = QMessageBox.warning(
            self, "Restore from Backup",
            f"Replace your library with “{Path(path).name}” ({songs:,} items)?\n\n"
            "Ratings, play counts, playlists and edits made since that backup will be "
            "replaced by the backup's. Your library as it is now is kept in the Backups "
            "folder first, so you can go back to it the same way. Your music files aren't "
            "touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.player.stop()
        self.player.up_next = []
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._song_clipboard = None
        try:
            kept = backup.restore(self.conn, Path(path))
        except (backup.RestoreError, sqlite3.Error, OSError) as error:
            QMessageBox.warning(self, "Restore from Backup", f"The backup couldn't be restored: "
                                f"{error}. Your library wasn't changed.")
            return
        self.eq_settings.copy_from(equalizer.load(self.conn))
        if self._eq_window is not None:
            self._eq_window.refresh()
        self._current_source = SOURCE_MUSIC
        self.reload_library()
        self.show_source(SOURCE_MUSIC)
        self.statusBar().showMessage(
            f"Restored from “{Path(path).name}”. Your library before the restore is kept as "
            f"“{kept.name}”.", 12000)

    def _organize_library(self):
        """File → Library → Organize Library…: put every file in the LuxTunes
        Media folder where "Keep … organized" would have put it."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            plan = organize.plan_library(self.conn)
        finally:
            QApplication.restoreOverrideCursor()
        if not plan:
            QMessageBox.information(self, "Organize Library",
                                    "Your LuxTunes Media folder is already organized.")
            return
        root = organize.media_folder(self.conn)
        box = QMessageBox(self)
        box.setWindowTitle("Organize Library")
        box.setIcon(QMessageBox.Icon.Question)
        count = len(plan)
        box.setText(f"{count:,} file{'s' if count != 1 else ''} in your LuxTunes Media folder "
                    f"{'are' if count != 1 else 'is'} not where LuxTunes would put "
                    f"{'them' if count != 1 else 'it'}. Rename and move "
                    f"{'them' if count != 1 else 'it'} into "
                    "the right artist and album folders?")
        box.setInformativeText("Click Show Details to see every change.")
        details = [f"{os.path.relpath(m.source, root)}\n    → {os.path.relpath(m.target, root)}"
                   for m in plan[:1000]]
        if count > 1000:
            details.append(f"… and {count - 1000:,} more")
        box.setDetailedText("\n".join(details))
        go = box.addButton("Organize", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(go)
        box.exec()
        if box.clickedButton() is not go:
            return

        progress = QProgressDialog("Organizing files…", "Stop", 0, count, self)
        progress.setWindowTitle("Organize Library")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)

        def step(done, total):
            progress.setValue(done)
            QApplication.processEvents()
            return not progress.wasCanceled()

        moved, problems = organize.organize_library(self.conn, [m.track_id for m in plan], step)
        progress.setValue(count)
        for track_id in moved:
            self._after_edit(track_id)
        message = f"Moved {len(moved):,} file{'s' if len(moved) != 1 else ''}."
        if problems:
            message += f" {len(problems):,} couldn't be moved:\n\n" + "\n".join(problems[:20])
        QMessageBox.information(self, "Organize Library", message)

    def _write_pending_tags(self):
        written, failed = tag_sync.write_all_pending(self.conn)
        if written or failed:
            message = f"Updated {written} file(s) with your earlier edits."
            if failed:
                message += f" {failed} file(s) still can't be updated (missing or read-only)."
            self.statusBar().showMessage(message, 8000)

    def _on_bookmark(self, track: dict, position: int):
        library.save_bookmark(self.conn, track["id"], position)
        self.model.update_track(track["id"], {"bookmark_time": position})

    # ---- Playlist right-click menu ----------------------------------------------

    def _playlist_by_id(self, playlist_id: int) -> dict | None:
        return next((p for p in library.load_playlists(self.conn) if p["id"] == playlist_id), None)

    def _show_sidebar_menu(self, source, global_pos):
        menu = QMenu(self)
        if source == SOURCE_UP_NEXT:
            clear = menu.addAction("Clear Up Next", lambda: self.player.remove_from_up_next(
                list(range(len(self.player.up_next)))))
            clear.setEnabled(bool(self.player.up_next))
        elif source[0] == "device" and source[1].startswith("cd:"):
            key = source[1]
            busy = self._import_key == key
            importing = menu.addAction("Stop Importing" if busy else "Import CD",
                                       lambda: (self._stop_import() if busy else self._start_import(key)))
            importing.setEnabled(busy or self._import_worker is None)
            names = menu.addAction("Get Track Names",
                                   lambda: self._offer_cd_lookup(key, then_continue=False))
            names.setEnabled(not busy and key not in self._lookups)
            menu.addSeparator()
            eject = menu.addAction("Eject", lambda: self._eject_device(key))
            eject.setEnabled(not busy)
        elif source[0] == "device":
            syncing = self._sync_worker is not None
            sync = menu.addAction("Sync", lambda: self._sync_source(source))
            sync.setEnabled(not (syncing and self._sync_ipod.key == source[1]))
            menu.addAction("Rename", lambda: self.sidebar.rename_source(source))
            menu.addSeparator()
            eject = menu.addAction("Eject", lambda: self._eject_device(source[1]))
            eject.setEnabled(not (syncing and self._sync_ipod.key == source[1]))
        elif source[0] == "playlist":
            playlist = self._playlist_by_id(source[1])
            if playlist is None or playlist["is_folder"]:
                return
            menu.addAction("Open in New Window", lambda: self._open_playlist_window(playlist))
            if playlist["is_smart"]:
                menu.addAction("Edit Smart Playlist", lambda: self._edit_smart_playlist(playlist["id"]))
            menu.addSeparator()
            menu.addAction("Export…", lambda: self._export_playlist(playlist))
            burn = menu.addAction("Burn Playlist to Disc")
            burn.setEnabled(False)                     # LuxTunes can't burn discs
            burn.setToolTip("LuxTunes can't burn discs yet.")
            order = menu.addAction("Copy To Play Order", lambda: self._copy_to_play_order(playlist))
            # Only useful for the playlist on show, sorted by a column other than its own order.
            order.setEnabled(self._current_source == source and self.model.sorted_by_column)
            menu.addSeparator()
            menu.addAction("Duplicate", lambda: self._duplicate_playlist(playlist))
            menu.addAction("Delete", lambda: self._delete_source(source))
        else:
            return
        menu.exec(global_pos)

    def _open_playlist_window(self, playlist: dict):
        tracks = library.load_tracks(self.conn, playlist_id=playlist["id"])
        window = PlaylistWindow(playlist["name"], tracks,
                                self.table.horizontalHeader().saveState(), self)
        window.play_requested.connect(lambda t, r, pid=playlist["id"]: self._play_from_window(pid, t, r))
        window.destroyed.connect(lambda *_: self._playlist_windows.remove(window)
                                 if window in self._playlist_windows else None)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._playlist_windows.append(window)
        window.show()

    def _play_from_window(self, playlist_id: int, tracks: list, row: int):
        self._playing_source = ("playlist", playlist_id)
        self.player.play_queue(tracks, row)

    def _duplicate_playlist(self, playlist: dict):
        library.duplicate_playlist(self.conn, playlist["id"])
        self.reload_library()

    def _delete_source(self, source):
        """Delete a playlist (Delete key or right-click), after asking."""
        if source[0] != "playlist":
            return
        playlist = self._playlist_by_id(source[1])
        if playlist is None:
            return
        if self.settings.value("confirm_delete_playlist", True, type=bool):
            box = QMessageBox(self)
            box.setWindowTitle("LuxTunes")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(f"Are you sure you want to delete the playlist “{playlist['name']}”?")
            box.setInformativeText("The songs stay in your library.")
            dont_ask = QCheckBox("Do not ask me again")
            box.setCheckBox(dont_ask)
            delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(delete)
            box.exec()
            if box.clickedButton() is not delete:
                return
            if dont_ask.isChecked():
                self.settings.setValue("confirm_delete_playlist", False)
        library.delete_playlist(self.conn, playlist["id"])
        if self._playing_source == source:
            self._playing_source = None        # keeps playing; Ctrl+L goes to Music
        if self._current_source == source:
            self._current_source = SOURCE_MUSIC
        self.reload_library()

    # ---- Equalizer ---------------------------------------------------------------------

    def _toggle_equalizer(self):
        """View > Show Equalizer (Ctrl+T); hides it when it's already showing."""
        if self._eq_window is not None and self._eq_window.isVisible():
            self._eq_window.hide()
        else:
            self._show_equalizer()

    def _show_equalizer(self):
        if self._eq_window is None:
            self._eq_window = EqualizerWindow(self.eq_settings, self)
            self._eq_window.changed.connect(self._equalizer_changed)
            self._eq_window.preset_renamed.connect(self._equalizer_preset_renamed)
            self._eq_window.preset_deleted.connect(lambda name: self._equalizer_preset_renamed(name, None))
        self._eq_window.show_song(self.player.current)
        self._eq_window.show()
        self._eq_window.raise_()
        self._eq_window.activateWindow()

    def _equalizer_changed(self):
        self.player.apply_equalizer()
        self._eq_save_timer.start()

    def _equalizer_preset_renamed(self, old: str, new: str | None):
        """Songs with this preset follow the rename (or lose the deleted preset)."""
        for track_id in library.rename_equalizer_preset(self.conn, old, new):
            self._after_edit(track_id)

    # ---- Pop-out artwork player ------------------------------------------------

    def _artwork_for(self, track: dict | None) -> bytes | None:
        path = find_file(track.get("location")) if track else None
        return artwork.read_embedded_artwork(path) if path else None

    def _lyrics_for(self, track: dict | None) -> str:
        """The song's lyrics: from the library, or else from the file's tags."""
        if not track:
            return ""
        if track.get("lyrics"):
            return track["lyrics"]
        path = find_file(track.get("location"))
        if not path:
            return ""
        try:
            from luxtunes.core.tags import read_details
            return read_details(path).get("lyrics") or ""
        except (TagError, OSError):
            return ""

    def _open_art_window(self):
        """Clicking the album art opens (or brings back) the pop-out player.
        It shows the playing song; if nothing is playing, the selected one."""
        if self._art_window is None:
            w = ArtworkWindow(self)
            w.play_clicked.connect(self._on_play_clicked)
            w.next_clicked.connect(self.player.next)
            w.previous_clicked.connect(self.player.previous)
            w.seek_requested.connect(self.player.seek)
            w.volume_changed.connect(self.volume.setValue)
            w.set_lyrics_shown(self.settings.value("popout_lyrics", False, type=bool))
            w.lyrics_toggled.connect(lambda shown: self.settings.setValue("popout_lyrics", shown))
            self._art_window = w
        w = self._art_window
        track = self.player.current or self.model.track_at(self.table.currentIndex().row())
        w.show_track(track, self._artwork_for(track))
        w.set_lyrics(self._lyrics_for(track))
        w.set_volume(self.volume.value())
        w.set_playing(self.player.is_playing)
        if not w.isVisible():
            w.open_at_art_size()
        w.show()
        w.raise_()
        w.activateWindow()

    def _open_mini_player(self):
        """Controls > Mini Player: the main window is minimized and a small
        player takes its place; closing that brings the window back."""
        if self._mini_player is None:
            w = MiniPlayer()                 # its own window: it stays when this one is minimized
            w.play_clicked.connect(self._on_play_clicked)
            w.next_clicked.connect(self.player.next)
            w.previous_clicked.connect(self.player.previous)
            w.seek_requested.connect(self.player.seek)
            w.volume_changed.connect(self.volume.setValue)
            w.restore_requested.connect(self._close_mini_player)
            self._mini_player = w
        w = self._mini_player
        w.show_track(self.player.current)
        w.set_volume(self.volume.value())
        w.set_playing(self.player.is_playing)
        w.update_position(*self._mini_position())
        w.show()
        w.raise_()
        w.activateWindow()
        self._full_screen_before_mini = self.isFullScreen()
        self.showMinimized()

    def _mini_position(self) -> tuple[int, int]:
        player = self.player._player
        return (player.position(), player.duration()) if self.player.current else (0, 0)

    def _close_mini_player(self):
        """The mini player was closed (or ⤢): back to the window, filling the
        screen (full screen again if it was). Not showNormal(): that brings
        back the window's last "normal" size, e.g. snapped to half the screen."""
        w, self._mini_player = self._mini_player, None
        if w is not None:
            w.deleteLater()
        if getattr(self, "_full_screen_before_mini", False):
            self.showFullScreen()
        else:
            self.showMaximized()
        self.raise_()
        self.activateWindow()

    # ---- New / renamed playlists -----------------------------------------------

    # ---- Smart playlists ------------------------------------------------------------

    def _current_smart_id(self) -> int | None:
        source = self._current_source
        if source and source[0] == "playlist":
            playlist = self._playlist_by_id(source[1])
            if playlist and playlist["is_smart"] and not playlist["distinguished_kind"]:
                return playlist["id"]
        return None

    def _rule_playlists(self, exclude: int | None = None) -> list[tuple[str, str]]:
        """What a "Playlist is …" rule can name: (persistent id, name)."""
        return [(p["persistent_id"].upper(), p["name"]) for p in library.load_playlists(self.conn)
                if p["id"] != exclude and not p["is_master"] and p["persistent_id"]
                and p["distinguished_kind"] not in library.UNSUPPORTED_KINDS]

    def _new_smart_playlist(self):
        """File → New Smart Playlist…: the rules first, then its name."""
        from luxtunes.ui.smart_playlist_dialog import SmartPlaylistDialog
        dialog = SmartPlaylistDialog(smart.default_playlist(), "Smart Playlist",
                                     self._rule_playlists(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        playlist_id = smart.create(self.conn, None, dialog.playlist())
        self._current_source = ("playlist", playlist_id)
        self.reload_library()
        self.sidebar.rename_source(("playlist", playlist_id))

    def _edit_smart_playlist(self, playlist_id: int | None):
        from luxtunes.ui.smart_playlist_dialog import SmartPlaylistDialog
        if playlist_id is None:
            return
        current = smart.load(self.conn, playlist_id)
        playlist = self._playlist_by_id(playlist_id)
        if current is None or playlist is None:
            QMessageBox.warning(self, "Edit Smart Playlist", "This smart playlist's rules couldn't "
                                "be read, so it can't be edited.")
            return
        dialog = SmartPlaylistDialog(current, playlist["name"], self._rule_playlists(playlist_id), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        smart.save(self.conn, playlist_id, dialog.playlist())
        if self._current_source == ("playlist", playlist_id):
            self.show_source(self._current_source)

    def _export_playlist(self, playlist: dict):
        """Export…: the playlist as an M3U8/M3U playlist file or a tab-separated text file."""
        if playlist["is_smart"]:
            smart.refresh(self.conn, playlist["id"])
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export Playlist", str(Path.home() / f"{playlist['name']}.m3u8"),
            "M3U8 playlist (*.m3u8);;M3U playlist (*.m3u);;Text file (*.txt)")
        if not path:
            return
        tracks = library.load_tracks(self.conn, playlist_id=playlist["id"])
        try:
            library.export_playlist(tracks, path, "txt" if chosen.startswith("Text") else
                                    "m3u" if chosen.startswith("M3U playlist") else "m3u8")
        except OSError as error:
            QMessageBox.warning(self, "Export", f"The playlist couldn't be saved:\n\n{error}")
            return
        self.statusBar().showMessage(f"Exported “{playlist['name']}” ({len(tracks)} songs).", 6000)

    def _copy_to_play_order(self, playlist: dict):
        """Make the list's current sort its own order (the "#" order)."""
        if self._current_source != ("playlist", playlist["id"]):
            return
        library.set_playlist_order(self.conn, playlist["id"],
                                   [t["id"] for t in self.model.all_in_view_order()])
        self.show_source(self._current_source)

    def _new_playlist(self):
        """Make "untitled playlist" and start editing its name."""
        playlist_id = library.create_playlist(self.conn)
        self._current_source = ("playlist", playlist_id)
        self.reload_library()
        self.sidebar.rename_source(("playlist", playlist_id))

    def _on_playlist_renamed(self, playlist_id: int, name: str):
        library.rename_playlist(self.conn, playlist_id, name)
        # Rebuild the sidebar (to re-sort A-Z) once the name editor has closed.
        QTimer.singleShot(0, self.reload_library)

    # ---- Column header menu ----------------------------------------------------

    def _show_header_menu(self, pos):
        header = self.table.horizontalHeader()
        section = header.logicalIndexAt(pos)
        menu = QMenu(self)
        auto = menu.addAction("Auto Size Column", lambda: self._auto_size(section))
        auto.setEnabled(section >= 0 and section != ENABLED_COLUMN)
        menu.addAction("Auto Size All Columns", self._auto_size_all)
        menu.addSeparator()
        for i, column in sorted(enumerate(COLUMNS), key=lambda c: c[1].title.casefold()):
            if i in FIXED_COLUMNS:
                continue
            action = menu.addAction(column.title)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(i))
            action.toggled.connect(lambda on, i=i: self._set_column_shown(i, on))
        menu.exec(header.mapToGlobal(pos))

    def _on_column_dropped_off(self, index: int):
        """A column header was dragged down into the songs: hide that column
        (like unchecking it in the header menu). Fixed columns just snap back."""
        if index not in FIXED_COLUMNS:
            self._set_column_shown(index, False)

    def _set_column_shown(self, index: int, shown: bool):
        self.table.setColumnHidden(index, not shown)
        if shown and self.table.columnWidth(index) < 20:
            self.table.setColumnWidth(index, COLUMNS[index].width)

    def _auto_size(self, index: int):
        if index >= 0 and index != ENABLED_COLUMN:
            self.table.resizeColumnToContents(index)

    def _auto_size_all(self):
        for i in range(len(COLUMNS)):
            if not self.table.isColumnHidden(i):
                self._auto_size(i)

    def _on_queue_changed(self):
        self.sidebar.set_queue_length(len(self.player.up_next))
        if self._current_source == SOURCE_UP_NEXT:
            self.show_source(SOURCE_UP_NEXT)

    # ======================================================================
    # Right-click menu on songs
    # ======================================================================

    def _selected_rows(self) -> list[int]:
        return sorted(i.row() for i in self.table.selectionModel().selectedRows())

    def _selected_tracks(self) -> list[dict]:
        return [self.model.track_at(r) for r in self._selected_rows()]

    def _current_playlist(self) -> dict | None:
        """The playlist being shown, if the current source is a playlist."""
        if not self._current_source or self._current_source[0] != "playlist":
            return None
        return next((p for p in library.load_playlists(self.conn)
                     if p["id"] == self._current_source[1]), None)

    def _show_song_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        tracks = self._selected_tracks()
        if not tracks:
            return
        clicked = self.model.track_at(index.row())
        menu = self._song_menu(tracks, clicked, clicked.get("name") or "",
                               lambda: self._play_row(index.row()))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _show_grid_menu(self, group: dict, global_pos):
        """Right-click a Grid tile: the song menu, for all of its songs."""
        tracks = [t for t in group.get("tracks") or [] if t]
        if not tracks:
            return
        self._grid_group = group                  # Get Info works on this tile
        menu = self._song_menu(tracks, tracks[0], group.get("title") or "",
                               lambda: self._play_tracks(tracks))
        menu.exec(global_pos)

    def _song_menu(self, tracks: list[dict], clicked: dict, play_label: str, play) -> QMenu:
        """The right-click menu for some songs (a selection, or a Grid tile's)."""
        menu = QMenu(self)
        menu.addAction(f"Play “{play_label}”", play)
        if self._current_source == SOURCE_UP_NEXT:
            menu.addAction("Remove from Queue", lambda: self._delete_tracks(tracks))
        else:
            menu.addAction("Add to Queue", lambda: self.player.add_to_up_next(
                [{k: v for k, v in t.items() if not k.startswith("_")} for t in tracks]))
        menu.addSeparator()

        menu.addAction("Get Info", self._get_info)
        menu.addAction("Get Track Info Online…", lambda: self._get_track_info_online(tracks))
        paste = menu.addAction("Paste Artwork", lambda: self._paste_artwork(tracks))
        mime = QApplication.clipboard().mimeData()
        paste.setEnabled(bool(mime) and (mime.hasImage() or mime.hasUrls()))
        menu.addMenu(self._rating_menu(menu, tracks))
        reveal = menu.addAction("Show in File Manager", lambda: self._show_in_file_manager(clicked))
        reveal.setEnabled(find_file(clicked.get("location")) is not None)
        if find_file(clicked.get("location")) is None:
            menu.addAction("Locate File…", lambda: self._locate_selected(clicked))
        menu.addSeparator()

        menu.addAction("Reset Plays", lambda: self._reset_plays(tracks))
        flacs = [t for t in tracks if convert.can_convert(t)]
        if flacs:
            menu.addAction("Create MP3 Version", lambda: self._create_mp3_versions(flacs))
        videos = [t for t in tracks if convert.can_make_ipod_video(t)]
        if videos:
            menu.addAction("Create iPod Version", lambda: self._create_ipod_versions(videos))
        menu.addSeparator()

        menu.addMenu(self._add_to_playlist_menu(menu, tracks))
        menu.addMenu(self._show_in_playlist_menu(menu, clicked))
        menu.addSeparator()

        delete = menu.addAction("Delete", lambda: self._delete_tracks(tracks))
        playlist = self._current_playlist()
        delete.setEnabled(not (playlist and playlist["is_smart"]))  # smart lists fill themselves
        all_unchecked = all(not t.get("enabled", 1) for t in tracks)
        menu.addAction("Check Selection" if all_unchecked else "Uncheck Selection",
                       lambda: self._set_checked(tracks, all_unchecked))
        return menu

    def _paste_artwork(self, tracks: list[dict]):
        """Right-click → Paste Artwork: the copied picture becomes the artwork
        of every selected song (written into each file, replacing its art)."""
        from luxtunes.ui.get_info import images_from_mime
        images = images_from_mime(QApplication.clipboard().mimeData())
        tracks = [t for t in tracks if t.get("id") is not None]
        if not images or not tracks:
            QMessageBox.information(self, "Paste Artwork",
                                    "Copy a picture (or a picture file) first, then paste it.")
            return
        if len(tracks) > 1 and QMessageBox.question(
                self, "Paste Artwork",
                f"Replace the artwork of these {len(tracks):,} songs with the copied picture?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return
        problems = []
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for track in tracks:
                error = self._apply_info(track["id"], {}, None, [images[0]])
                if error:
                    problems.append(f"{track.get('name') or track['id']}: {error}")
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_artwork()         # (the list and Grid refresh after each edit)
        self.statusBar().showMessage(
            f"Artwork pasted into {len(tracks) - len(problems):,} "
            f"song{'s' if len(tracks) - len(problems) != 1 else ''}.", 6000)
        if problems:
            QMessageBox.warning(self, "Paste Artwork", "Some songs couldn't be changed:\n\n"
                                + "\n".join(problems[:20]))

    def _rating_menu(self, parent, tracks) -> QMenu:
        menu = QMenu("Rating", parent)
        self._fill_rating_menu(menu, tracks)
        return menu

    def _fill_rating_menu(self, menu: QMenu, tracks):
        menu.clear()
        ratings = {(t.get("rating") or 0) if not t.get("rating_computed") else 0 for t in tracks}
        current = ratings.pop() if len(ratings) == 1 else None
        for stars in range(6):
            action = menu.addAction("None" if stars == 0 else "★" * stars,
                                    lambda s=stars: self._rate(tracks, s * 20))
            action.setCheckable(True)
            action.setChecked(current is not None and round(current / 20) == stars)

    def _rate(self, tracks, rating: int):
        for t in tracks:
            self.model.update_track(t["id"], library.set_rating(self.conn, t["id"], rating))

    def _add_to_playlist_menu(self, parent, tracks) -> QMenu:
        menu = QMenu("Add to Playlist", parent)
        playlists = [p for p in library.load_playlists(self.conn) if library.is_user_playlist(p)]
        for p in playlists:
            menu.addAction(playlist_icon(p), p["name"],
                           lambda pid=p["id"], name=p["name"]: self._add_to_playlist(pid, name, tracks))
        menu.setEnabled(bool(playlists))
        return menu

    def _add_to_playlist(self, playlist_id: int, name: str, tracks, verb: str = "Copy",
                         also=None, also_playlists=()) -> bool:
        """Add songs to a playlist (asking about duplicates), undoably. also():
        more changes made in the same step (e.g. removing the songs from the
        playlist they were cut from), in also_playlists."""
        ids = [t["id"] for t in tracks]
        already = library.playlist_track_ids(self.conn, playlist_id)
        if any(i in already for i in ids):
            box = QMessageBox(self)
            box.setWindowTitle("LuxTunes")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText("There are duplicates being added to the playlist. "
                        "Would you like to add the duplicates or skip them?")
            skip = box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
            add = box.addButton("Add", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(add)
            box.exec()
            if box.clickedButton() is skip:
                tracks = [t for t in tracks if t["id"] not in already]
            elif box.clickedButton() is not add:
                return False
        if not tracks:
            return False

        def change():
            library.add_to_playlist(self.conn, playlist_id, [t["id"] for t in tracks])
            if also is not None:
                also()

        self._record_playlist_edit(self._edit_label(verb, tracks),
                                   [playlist_id, *also_playlists], change)
        count = len(tracks)
        self.statusBar().showMessage(
            f"Added {count} song{'s' if count != 1 else ''} to “{name}”", 4000)
        if self._current_source == ("playlist", playlist_id) or \
                self._current_source in [("playlist", p) for p in also_playlists]:
            self.show_source(self._current_source)
        return True

    def _show_help(self):
        """Help → LuxTunes Help (F1): the short guides."""
        if getattr(self, "_help_window", None) is None:
            from luxtunes.ui.help_window import HelpWindow
            self._help_window = HelpWindow(self)
        self._help_window.show()
        self._help_window.raise_()
        self._help_window.activateWindow()

    # ---- Edit: Cut / Copy / Paste, Select All / None --------------------------------

    def _song_list_has_focus(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is None or focus is self.table or self.table.isAncestorOf(focus) \
            or not isinstance(focus, QLineEdit)

    def _copy_songs(self, cut: bool = False):
        """Edit → Copy / Cut: the selected songs go on LuxTunes' clipboard (and
        their names on the system clipboard, to paste as text elsewhere)."""
        tracks = [t for t in self._selected_tracks() if t]
        if not tracks or self._showing_device_page():
            return
        source = self._current_source
        self._song_clipboard = {
            "ids": [t["id"] for t in tracks], "cut": cut, "source": source,
            "positions": [t.get("playlist_position") for t in tracks]}
        QApplication.clipboard().setText("\n".join(
            " - ".join(p for p in (t.get("name"), t.get("artist")) if p) for t in tracks))
        count = len(tracks)
        self.statusBar().showMessage(
            f"{'Cut' if cut else 'Copied'} {count:,} item{'s' if count != 1 else ''}", 3000)

    def _paste_songs(self):
        """Edit → Paste into what's shown: a playlist, Up Next, or a Library
        section (songs already in that section, smart playlists and folders:
        nothing happens)."""
        clip = self._song_clipboard
        if not clip or self._current_source is None:
            return
        tracks = library.load_tracks_by_ids(self.conn, clip["ids"])
        if not tracks:
            return
        done = self._put_songs(self._current_source, tracks, move=clip["cut"],
                               from_source=clip["source"],
                               cut_entries=list(zip(clip["positions"], clip["ids"])))
        if done and clip["cut"]:
            self._song_clipboard = None            # a cut is pasted once

    def _put_songs(self, target, tracks: list[dict], move: bool, from_source=None,
                   cut_entries=()) -> bool:
        """Songs pasted or dropped on `target`. move: cut (or dragged).
        cut_entries: (position, track id) in the playlist they were cut from."""
        if target == SOURCE_UP_NEXT:
            self.player.add_to_up_next(tracks)
            return True
        if target[0] == "library":
            if target[1] not in edits.PASTE_SECTIONS:
                return False
            return self._put_in_section(target[1], tracks, move)
        if target[0] != "playlist":
            return False
        playlist = next((p for p in library.load_playlists(self.conn) if p["id"] == target[1]), None)
        if playlist is None or not library.is_user_playlist(playlist):
            return False                          # smart playlists and folders: nothing
        cut_from = from_source[1] if move and from_source and from_source[0] == "playlist" \
            and from_source != target else None
        if cut_from is None:
            return self._add_to_playlist(playlist["id"], playlist["name"], tracks)
        wanted = [(p, track_id) for p, track_id in cut_entries if p is not None]

        def take_out():
            # Only entries still holding the song that was cut (the playlist may
            # have changed since).
            order = edits.playlist_order(self.conn, cut_from)
            library.remove_from_playlist(self.conn, cut_from, [
                p for p, track_id in wanted if p < len(order) and order[p] == track_id])

        return self._add_to_playlist(playlist["id"], playlist["name"], tracks, verb="Move",
                                     also=take_out, also_playlists=[cut_from])

    def _put_in_section(self, kind: str, tracks: list[dict], move: bool) -> bool:
        """Into a Library section (Music, Podcasts, Audiobooks, Movies, TV Shows).
        Cut (or drag): the song
        itself changes media kind (its file moves to that folder). Copy: a copy
        of the file with the new kind, beside the original in the library."""
        problems, going = [], []
        for t in tracks:
            verdict = edits.can_go(t, kind)
            if verdict == edits.OK:
                going.append(t)
            elif verdict != edits.SAME:
                problems.append(verdict)
        title = edits.SECTION_NAMES[kind]
        if going:
            if move:
                self._move_to_section(kind, going, problems)
            else:
                self._copy_to_section(kind, going, problems)
        if problems:
            QMessageBox.warning(self, title, "\n\n".join(problems[:10]) + (
                f"\n\n… and {len(problems) - 10:,} more." if len(problems) > 10 else ""))
        return bool(going)

    def _set_kinds(self, kinds: dict[int, str], problems: list[str]):
        for track_id, kind in kinds.items():
            try:
                error = self._apply_info(track_id, {"media_kind": kind}, None)
            except ValueError as error_value:
                error = str(error_value)
            if error:
                name = (library.load_track(self.conn, track_id) or {}).get("name") or "A song"
                problems.append(f"“{name}” moved to {edits.SECTION_NAMES[kind]}, but its file "
                                f"couldn't be moved: {error}.")
        self._refresh_after_edit()

    def _move_to_section(self, kind: str, tracks: list[dict], problems: list[str]):
        before = {t["id"]: t.get("media_kind") or "music" for t in tracks}
        after = {t["id"]: kind for t in tracks}
        self._set_kinds(after, problems)
        self._push_undo(self._edit_label("Move", tracks),
                        lambda: self._set_kinds(before, []), lambda: self._set_kinds(after, []))
        self.statusBar().showMessage(
            f"Moved {len(tracks):,} item{'s' if len(tracks) != 1 else ''} to "
            f"{edits.SECTION_NAMES[kind]}", 4000)

    def _make_copies(self, kind: str, originals: list[int], made: list[int], problems: list[str]):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for track_id in originals:
                try:
                    made.append(edits.duplicate_as(self.conn, track_id, kind))
                except (OSError, KeyError) as error:
                    problems.append(str(error) if isinstance(error, FileNotFoundError)
                                    else f"A copy couldn't be made: {error}")
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_after_edit()

    def _remove_copies(self, made: list[int]):
        """Undo a pasted copy: its library entry, and the file LuxTunes made for it."""
        copies = library.load_tracks_by_ids(self.conn, made)
        self.player.forget_tracks(set(made))
        failures = []
        for t in copies:
            failure = trash.move_to_trash(t.get("location"))
            if failure is not None and failure[0] != trash.MISSING:
                failures.append((t, *failure))
        library.delete_tracks(self.conn, list(made))
        made.clear()
        self._refresh_after_edit()
        if failures:
            self._report_trash_failures(failures, "Undo",
                                        "The copies were taken out of your library, but some "
                                        "of their files couldn't be moved to the Trash.")

    def _copy_to_section(self, kind: str, tracks: list[dict], problems: list[str]):
        originals = [t["id"] for t in tracks]
        made: list[int] = []
        self._make_copies(kind, originals, made, problems)
        if made:
            self._push_undo(self._edit_label("Copy", tracks), lambda: self._remove_copies(made),
                            lambda: self._make_copies(kind, originals, made, []))
            self.statusBar().showMessage(
                f"Copied {len(made):,} item{'s' if len(made) != 1 else ''} to "
                f"{edits.SECTION_NAMES[kind]}", 4000)

    def _select_all(self):
        if self._song_list_has_focus():
            self.table.selectAll()

    def table_clear_selection(self):
        self.table.clearSelection()

    # ---- Edit: Undo / Redo -----------------------------------------------------------

    UNDO_LIMIT = 50

    @staticmethod
    def _edit_label(verb: str, tracks: list[dict]) -> str:
        """'Copy “Praise Him”' or 'Copy 3 Items'."""
        if len(tracks) == 1:
            return f"{verb} “{tracks[0].get('name') or 'Untitled'}”"
        return f"{verb} {len(tracks):,} Items"

    def _push_undo(self, label: str, undo, redo):
        self._undo_stack.append((label, undo, redo))
        del self._undo_stack[:-self.UNDO_LIMIT]
        self._redo_stack.clear()

    def _record_playlist_edit(self, label: str, playlist_ids: list[int], change):
        """Make a change to playlists so Undo can put their songs back as they were."""
        playlist_ids = list(dict.fromkeys(playlist_ids))
        before = {p: edits.playlist_order(self.conn, p) for p in playlist_ids}
        change()
        after = {p: edits.playlist_order(self.conn, p) for p in playlist_ids}
        self._push_undo(label, lambda: self._restore_playlists(before),
                        lambda: self._restore_playlists(after))

    def _restore_playlists(self, orders: dict[int, list[int]]):
        for playlist_id, order in orders.items():
            edits.set_playlist_order(self.conn, playlist_id, order)
        self._schedule_smart_refresh()
        if self._current_source and self._current_source[0] == "playlist" \
                and self._current_source[1] in orders:
            self.show_source(self._current_source)

    def _refresh_after_edit(self):
        """Songs changed sections or came and went: redraw the sidebar and list."""
        self.reload_library()
        if self._current_source and self._current_source[0] != "device":
            self.show_source(self._current_source)

    def _undo(self):
        if not self._undo_stack:
            return
        label, undo, redo = self._undo_stack.pop()
        undo()
        self._redo_stack.append((label, undo, redo))
        self._refresh_after_edit()
        self.statusBar().showMessage(f"Undid {label}", 3000)

    def _redo(self):
        if not self._redo_stack:
            return
        label, undo, redo = self._redo_stack.pop()
        redo()
        self._undo_stack.append((label, undo, redo))
        self._refresh_after_edit()
        self.statusBar().showMessage(f"Redid {label}", 3000)

    def _update_edit_menu(self):
        """Undo/Redo say what they'd do; items that can't do anything are greyed."""
        self.undo_action.setText(f"Undo {self._undo_stack[-1][0]}" if self._undo_stack else "Undo")
        self.redo_action.setText(f"Redo {self._redo_stack[-1][0]}" if self._redo_stack else "Redo")
        self.undo_action.setEnabled(bool(self._undo_stack))
        self.redo_action.setEnabled(bool(self._redo_stack))
        songs = bool(self._selected_rows()) and not self._showing_device_page()
        for action in (self.cut_action, self.copy_action, self.delete_action):
            action.setEnabled(songs)
        self.paste_action.setEnabled(self._paste_possible())

    def _edit_menu_closed(self):
        # The shortcuts work whenever there's something to do; each one checks.
        for action in (self.undo_action, self.redo_action, self.cut_action, self.copy_action,
                       self.paste_action, self.delete_action):
            action.setEnabled(True)

    def _paste_possible(self) -> bool:
        source = self._current_source
        if not self._song_clipboard or source is None:
            return False
        if source == SOURCE_UP_NEXT:
            return True
        if source[0] == "library":
            return source[1] in edits.PASTE_SECTIONS
        if source[0] == "playlist":
            playlist = self._current_playlist()
            return playlist is not None and library.is_user_playlist(playlist)
        return False

    def _on_tracks_dropped(self, target, track_ids: list[int]):
        """Songs were dragged from the song list onto a sidebar item."""
        tracks = library.load_tracks_by_ids(self.conn, track_ids)
        if not tracks:
            return
        # Dragging onto a Library section moves the songs there, like Cut and Paste.
        self._put_songs(target, tracks, move=True)

    def _show_in_playlist_menu(self, parent, track) -> QMenu:
        menu = QMenu("Show in Playlist", parent)
        playlists = library.playlists_containing(self.conn, track["id"])
        for p in playlists:
            menu.addAction(playlist_icon(p), p["name"],
                           lambda pid=p["id"]: self._jump_to_playlist(pid, track["id"]))
        menu.setEnabled(bool(playlists))
        return menu

    def _jump_to_playlist(self, playlist_id: int, track_id: int):
        """Select the playlist in the sidebar, then highlight the song in it."""
        self.sidebar.select_source(("playlist", playlist_id))
        row = self.model.row_of(track_id)
        if row >= 0:
            self.table.selectRow(row)
            self.table.scrollTo(self.model.index(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)

    def _show_in_file_manager(self, track):
        path = find_file(track.get("location"))
        if path is None:
            return
        # Ask the file manager (Nemo on Mint) to open the folder with the file selected.
        uri = QUrl.fromLocalFile(path).toString()
        try:
            shown = subprocess.run(
                ["dbus-send", "--session", "--type=method_call",
                 "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                 "org.freedesktop.FileManager1.ShowItems", f"array:string:{uri}", "string:"],
                timeout=5,
            ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            shown = False
        if not shown:  # fall back to just opening the folder
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def _reset_plays(self, tracks):
        # Ask first, so a slip of the mouse can't wipe play counts.
        if len(tracks) == 1:
            what = f"the song “{tracks[0].get('name')}”"
        else:
            what = f"the {len(tracks)} selected songs"
        box = QMessageBox(self)
        box.setWindowTitle("LuxTunes")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Are you sure you want to reset the number of plays and skips of {what}?")
        reset = box.addButton("Reset", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(reset)
        box.exec()
        if box.clickedButton() is not reset:
            return
        fields = library.reset_plays(self.conn, [t["id"] for t in tracks])
        for t in tracks:
            self.model.update_track(t["id"], fields)

    def _set_checked(self, tracks, checked: bool):
        for t in tracks:
            self.model.update_track(t["id"], library.set_enabled(self.conn, t["id"], checked))

    def _delete_selection(self):
        self._delete_tracks(self._selected_tracks())

    def _delete_tracks(self, tracks: list[dict]):
        tracks = [t for t in tracks if t]
        if not tracks:
            return
        count = len(tracks)
        noun = "song" if count == 1 else "songs"

        if self._current_source == SOURCE_UP_NEXT:
            self.player.remove_from_up_next([t["_queue_index"] for t in tracks])
            return

        playlist = self._current_playlist()
        if playlist is not None:
            if playlist["is_smart"]:
                return
            answer = QMessageBox.question(
                self, "LuxTunes",
                f"Are you sure you want to remove the selected {noun} from the list?")
            if answer == QMessageBox.StandardButton.Yes:
                self._record_playlist_edit(
                    self._edit_label("Delete", tracks), [playlist["id"]],
                    lambda: library.remove_from_playlist(
                        self.conn, playlist["id"], [t["playlist_position"] for t in tracks]))
                self.show_source(self._current_source)
            return

        # Deleting from the library itself: confirm, then ask about the files.
        answer = QMessageBox.question(
            self, "LuxTunes",
            f"Are you sure you want to delete the selected {noun} from your LuxTunes library?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        box = QMessageBox(self)
        box.setWindowTitle("LuxTunes")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Do you want to move the selected {noun} to the Trash, "
                    "or keep the files in your LuxTunes Media folder?")
        to_trash = box.addButton("Move to Trash", QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Keep Files", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() not in (to_trash, keep):
            return

        ids = {t["id"] for t in tracks}
        self.player.forget_tracks(ids)
        snapshot = edits.snapshot_tracks(self.conn, list(ids))
        failures = []
        if box.clickedButton() is to_trash:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                for t in tracks:
                    failure = trash.move_to_trash(t.get("location"))
                    if failure is not None:
                        failures.append((t, *failure))
            finally:
                QApplication.restoreOverrideCursor()
        library.delete_tracks(self.conn, list(ids))
        if box.clickedButton() is keep:
            # Only the library entries went: Undo can bring them back.
            self._push_undo(self._edit_label("Delete", tracks),
                            lambda: edits.restore_tracks(self.conn, snapshot),
                            lambda: library.delete_tracks(self.conn, list(ids)))
        else:
            # Files went to the Trash (or are gone): that can't be undone here,
            # and earlier steps may refer to these songs.
            self._undo_stack.clear()
            self._redo_stack.clear()
        self.show_source(self._current_source)
        if failures:
            self._report_trash_failures(
                failures, "LuxTunes",
                f"{len(failures):,} of the {count:,} deleted {noun} still "
                f"{'has its file' if len(failures) == 1 else 'have their files'} on disk. "
                "They're no longer in your library.")

    def _report_trash_failures(self, failures: list, title: str, summary: str) -> list[dict]:
        """Say which files couldn't go to the Trash, and why. Files on a drive
        without a usable Trash can be deleted for good instead, if you say so.
        Returns the songs whose files were deleted that way."""
        lines = [f"• {t.get('name') or 'Untitled'}: {trash.explain(t.get('location'), kind, message)}"
                 for t, kind, message in failures]
        no_trash = [t for t, kind, _ in failures if kind == trash.NO_TRASH]
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(summary)
        box.setInformativeText("\n".join(lines[:12]) + (f"\n… and {len(lines) - 12:,} more "
                                                        "(Show Details)." if len(lines) > 12 else ""))
        if len(lines) > 12:
            box.setDetailedText("\n".join(lines))
        delete = None
        if no_trash:
            delete = box.addButton(f"Delete {len(no_trash):,} File{'s' if len(no_trash) != 1 else ''} "
                                   "Permanently…", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if delete is None or box.clickedButton() is not delete:
            return []
        if QMessageBox.warning(
                self, title,
                f"Delete {len(no_trash):,} file{'s' if len(no_trash) != 1 else ''} for good? "
                "They don't go to the Trash and can't be recovered.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return []
        deleted, problems = [], []
        for t in no_trash:
            error = trash.delete_permanently(t.get("location"))
            if error is None:
                deleted.append(t)
            else:
                problems.append(f"• {t.get('name') or 'Untitled'}: {error}")
        if problems:
            QMessageBox.warning(self, title, "These files couldn't be deleted:\n\n"
                                + "\n".join(problems[:12]))
        return deleted

    # ======================================================================
    # iPods (the DEVICES section)
    # ======================================================================

    def _showing_device_page(self) -> bool:
        return self.views.currentWidget() in (self.device_page, self.cd_page)

    def _update_view_buttons(self, *_):
        """List / Album List / Grid / Cover Flow do nothing on an iPod's or a
        CD's page, so they're hidden there (and their menu items greyed out)."""
        on_songs = not self._showing_device_page()
        for button in self.view_buttons:
            button.setVisible(on_songs)
        for action in self.view_actions.values():
            action.setEnabled(on_songs)

    def _scan_devices(self):
        """Notice iPods being plugged in or removed. Cheap: it only reads the
        list of mounted drives, and reads an iPod's details once, when it appears."""
        self._scan_cds()
        self._auto_mount()
        phones_changed = self._scan_phones()
        mounted = {mountpoint: (dev, fs) for dev, mountpoint, fs in ipod_device.mounted_filesystems()
                   if not mountpoint.startswith(("/proc", "/sys", "/dev"))}
        ipod_mounts = {m for m in mounted if m in self._ipod_mounts or ipod_device.is_ipod(m)}
        if ipod_mounts == self._ipod_mounts:
            if phones_changed:
                self._forget_missing_source()
                self.reload_library()
                self._update_device_actions()
            return
        self._ipod_mounts = ipod_mounts
        known = {ipod.mountpoint: key for key, ipod in self._ipods.items()}
        for mountpoint, key in known.items():
            if mountpoint not in ipod_mounts:       # unplugged / ejected
                del self._ipods[key]
        for mountpoint in ipod_mounts - set(known):
            dev, fs = mounted[mountpoint]
            try:
                ipod = ipod_device.read_ipod(mountpoint, dev, fs)
            except OSError:
                continue
            self._ipods[ipod.key] = ipod
            ipod_settings.load_device(self.conn, ipod.key, ipod.name, ipod.model_number, ipod.serial)
            self.statusBar().showMessage(f"“{ipod.name}” is connected.", 5000)
        self._forget_missing_source()
        self.reload_library()
        self._update_device_actions()       # Sync and Eject right away, not at the next check

    def _forget_missing_source(self):
        if self._current_source and self._current_source[0] == "device" \
                and not self._current_source[1].startswith("cd:") \
                and self._current_source[1] not in self._players:
            self._current_source = SOURCE_MUSIC

    @property
    def _players(self) -> dict:
        """iPods and phones, by key."""
        return {**self._ipods, **self._phones}

    def _scan_phones(self) -> bool:
        """Notice Android devices (unlocked, in File transfer mode). True if the
        list changed. A phone's storage shows up only once it's unlocked, so a
        phone without one is looked at again each time."""
        self._auto_mount_phones()
        names = android_device.mounted_names()
        waiting = any(not p.storages for p in self._phones.values())
        if names == self._phone_names and not waiting:
            return False
        self._phone_names = names
        before = {k: sorted(p.storages) for k, p in self._phones.items()}
        phones = {p.key: p for p in android_device.find_phones()}
        if self._sync_worker is not None and self._sync_ipod.key in self._phones \
                and self._sync_ipod.key in phones:
            phones[self._sync_ipod.key] = self._sync_ipod      # keep the one syncing
        for key, phone in phones.items():
            if key not in self._phones:
                android_sync.load_record(self.conn, phone)
                self.statusBar().showMessage(f"“{self._device_name(phone)}” is connected.", 5000)
        self._phones = phones
        return before != {k: sorted(p.storages) for k, p in phones.items()}

    def _auto_mount_phones(self):
        """Open a phone the desktop knows about but didn't open by itself, a few
        times (it may still be asking whether to allow access)."""
        waiting = android_device.unmounted_phones()
        for uri in list(self._phone_tries):
            if uri not in waiting:
                del self._phone_tries[uri]       # unplugged (or open): start afresh next time
        for uri in waiting:
            tries = self._phone_tries.get(uri, 0)
            if tries < 0 or tries >= self.MOUNT_TRIES:
                continue                         # ejected here, or given up
            self._phone_tries[uri] = tries + 1
            android_device.mount(uri)

    MOUNT_TRIES = 5

    def _auto_mount(self):
        """Mount a plugged-in iPod that the desktop didn't mount by itself (like
        clicking it in the file manager). Right after it's plugged in the system
        may still be busy with it, so it's tried a few times, one device check
        (2 s) apart."""
        if not ipod_device.apple_usb_connected():
            self._mount_tries.clear()
            self._mount_ejected.clear()
            return
        for partition in ipod_device.unmounted_ipod_partitions():
            tries = self._mount_tries.get(partition, 0)
            if partition in self._mount_ejected or tries >= self.MOUNT_TRIES:
                continue
            self._mount_tries[partition] = tries + 1
            mountpoint, error = ipod_device.mount(partition)
            if error is None:
                # Mounted: if it's later unmounted elsewhere, leave it that way.
                self._mount_tries[partition] = self.MOUNT_TRIES
            elif tries + 1 == self.MOUNT_TRIES:
                self.statusBar().showMessage(
                    f"Couldn't open the iPod: {error}. Try clicking it in the file manager.", 20000)

    def _device_name(self, ipod) -> str:
        if ipod.key.startswith("android:"):
            return android_sync.load_record(self.conn, ipod).name or ipod.name
        record = ipod_settings.load_device(self.conn, ipod.key, ipod.name)
        return record.name or ipod.name

    def _record(self, device):
        if device.key.startswith("android:"):
            return android_sync.load_record(self.conn, device)
        return ipod_settings.load_device(self.conn, device.key, device.name, device.model_number,
                                         device.serial)

    def _show_device(self, key: str):
        if key.startswith("cd:"):
            self._show_cd(key)
            return
        ipod = self._players.get(key)
        if ipod is None:
            self.sidebar.select_source(SOURCE_MUSIC)
            return
        record = self._record(ipod)
        if self._drilled:
            self._leave_drill(show_grid=False)
        self.cover_flow.hide()
        self.device_page.set_device(self.conn, ipod, record)
        self.views.setCurrentWidget(self.device_page)
        self.search.setEnabled(False)
        self.summary_label.setText(f"{record.name} — {ipod.model_name or 'iPod'}")
        self._update_device_actions()

    def _on_device_renamed(self, key: str, name: str):
        ipod = self._players.get(key)
        if ipod is None or not name.strip():
            QTimer.singleShot(0, self.reload_library)
            return
        record = self._record(ipod)
        ipod_settings.rename_device(self.conn, record.id, name)
        if key in self._ipods:
            self.statusBar().showMessage("The new name is written to the iPod at the next sync.",
                                         6000)
        QTimer.singleShot(0, self.reload_library)

    def _current_device(self):
        """The iPod or phone on show, or the only one plugged in."""
        players = self._players
        if self._current_source and self._current_source[0] == "device":
            return players.get(self._current_source[1])
        return next(iter(players.values()), None) if len(players) == 1 else None

    def _sync_source(self, source):
        """Sidebar → Sync: sync that iPod (the one right-clicked, not the one on show)."""
        self._sync_device(self._players.get(source[1]))

    def _sync_device(self, ipod=None):
        """Sync the iPod or phone in the background; progress shows in the Now Playing display."""
        ipod = ipod or self._current_device()
        if ipod is None:
            return
        if self._sync_worker is not None:
            # One sync at a time: libgpod and the library's play counts are
            # handled by one sync, start to finish.
            busy = self._sync_name
            if self._sync_ipod.key == ipod.key:
                text = f"“{busy}” is already syncing."
            else:
                text = (f"“{busy}” has to finish syncing before you can sync "
                        f"“{self._device_name(ipod)}”.")
            QMessageBox.information(self, "Sync", text)
            return
        from luxtunes.core.ipod import gpod
        phone = ipod.key.startswith("android:")
        if phone and not ipod.storages:
            QMessageBox.information(
                self, "Sync", f"Can't see “{self._device_name(ipod)}”'s storage yet. Unlock "
                "it, and in its USB notification choose “File transfer”.")
            return
        if not phone and not gpod.available():
            QMessageBox.warning(
                self, "Sync",
                "Syncing iPods needs libgpod, which isn't installed.\n\n"
                "Install it from a terminal with:\n    sudo apt install libgpod-common")
            return
        from luxtunes.ui.sync_worker import SyncWorker
        record = self._record(ipod)
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        worker = SyncWorker(db_path, ipod, ipod.key, self)
        self._sync_worker = worker
        self._sync_ipod = ipod
        self._update_device_actions()
        self._sync_first_time = record.last_sync is None
        self._sync_name = record.name or ipod.name
        worker.progress.connect(self._on_sync_progress)
        worker.confirm_needed.connect(self._confirm_sync)
        worker.playlists_confirm_needed.connect(self._confirm_phone_deletions)
        worker.succeeded.connect(self._sync_finished)
        worker.failed.connect(self._sync_failed)
        worker.finished.connect(self._sync_thread_done)
        self.lcd.start_task("sync", f"Syncing “{self._sync_name}”", "Preparing to sync…",
                            "Stop syncing")
        self.device_page.set_syncing(ipod.key)
        self.sidebar.set_syncing(ipod.key)
        worker.start()

    def _on_sync_progress(self, text: str, done: int, total: int):
        self.lcd.update_count("sync", text, done, total)

    def _lcd_finished(self, task: str, message: str, seconds: int = 6):
        """A sync or import ended: the display says how. If another task is on
        show, the message also goes to the status bar."""
        if self.lcd.showing_task != task:
            self.statusBar().showMessage(message, 8000)
        self.lcd.finish_task(task, message, seconds)

    def _stop_task(self, key: str):
        """The ✕ in the display: stops the task on show."""
        if key == "import":
            self._stop_import()
        elif key == "sync":
            self._stop_sync()
        elif key == "add":
            self._stop_adding()
        elif key == "loudness" and self._loudness_worker is not None:
            self.lcd.set_stopping("loudness", "Stopping…")
            self._loudness_worker.stop()

    def _stop_sync(self):
        if self._sync_worker is not None:
            self.lcd.set_stopping("sync", "Stopping after the current song…")
            self._sync_worker.stop()

    def _confirm_sync(self, adding: int, removing: int, removals=()):
        """Asked by the sync before it changes the iPod. Only the first LuxTunes
        sync that takes songs off an iPod (or a very big removal) needs a yes.
        Show Details lists each song that would come off, and why."""
        worker = self._sync_worker
        if worker is None:
            return
        if removing == 0 or (not self._sync_first_time and removing < 100):
            worker.answer(True)
            return
        from luxtunes.ui.sync_confirm_dialog import SyncConfirmDialog
        dialog = SyncConfirmDialog(self._sync_name, adding, list(removals), self)
        worker.answer(dialog.exec() == QDialog.DialogCode.Accepted)

    def _confirm_phone_deletions(self, names):
        """The phone deleted some playlists: delete them here too? Asked every
        time (a tap on the phone mustn't remove a long-kept playlist unseen).
        No keeps them, and they go back to the phone."""
        worker = self._sync_worker
        if worker is None:
            return
        shown = "\n".join(f"• {n}" for n in names[:15]) + ("\n…" if len(names) > 15 else "")
        box = QMessageBox(QMessageBox.Icon.Question, "Sync",
                          f"These playlists were deleted on “{self._sync_name}”:\n\n{shown}\n\n"
                          "Delete them from your library too? Their songs stay in the library. "
                          "Keep them, and they go back to the phone.", parent=self)
        delete = box.addButton("Delete Playlists", QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton("Keep Playlists", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.exec()
        worker.answer(box.clickedButton() is delete)

    def _sync_finished(self, report):
        ipod = self._sync_ipod
        if report.cancelled and not (report.added or report.removed):
            self._lcd_finished("sync", "Sync cancelled.", 3)
            return
        # Plays and ratings from the iPod show up in the song list right away.
        if report.stats:
            for track_id in report.stats.changed:
                self._after_edit(track_id)
        phone = ipod.key.startswith("android:")
        if phone:
            refreshed = ipod
        else:
            try:
                refreshed = ipod_device.read_ipod(ipod.mountpoint, ipod.device, ipod.filesystem)
            except OSError:
                refreshed = ipod
            self._ipods[ipod.key] = refreshed
        self.reload_library()
        if report.cancelled:
            self._lcd_finished("sync", f"Sync stopped: {report.added:,} copied, {report.removed:,} removed.")
        else:
            self._lcd_finished("sync", f"{'Phone' if phone else 'iPod'} sync is complete. "
                                 f"{report.added:,} added, {report.removed:,} removed.")
            if appearance(self.settings)["sync_done_sound"]:
                from luxtunes.core import sounds
                sounds.play_done_sound()            # the desktop's "done" sound
        if getattr(report, "equalizer_from_phone", False):
            # The phone's equalizer is LuxTunes' now: show and play it.
            self.eq_settings.copy_from(equalizer.load(self.conn))
            self.player.apply_equalizer()
            if self._eq_window is not None:
                self._eq_window.refresh()
        changes = getattr(report, "phone_playlists", None)
        if changes is not None and (changes.created or changes.updated or changes.deleted):
            parts = [f"{len(v)} {w}" for v, w in ((changes.created, "new"),
                                                  (changes.updated, "changed"),
                                                  (changes.deleted, "deleted")) if v]
            self.statusBar().showMessage(f"Playlists from “{self._sync_name}”: "
                                         + ", ".join(parts) + ".", 8000)
        if report.stats and report.stats.plays:
            self.statusBar().showMessage(
                f"{report.stats.plays:,} plays were read from “{self._sync_name}”.", 8000)
        if report.problems and not self._closing_after_sync:
            QMessageBox.warning(self, "Sync", "Some items couldn't be synced:\n\n"
                                + "\n".join(report.problems[:20]))

    def _sync_failed(self, message: str):
        self._lcd_finished("sync", "The sync didn't finish.", 4)
        if self._closing_after_sync:
            log.warning("sync didn't finish while closing: %s", message)
            return
        QMessageBox.warning(self, "Sync", f"“{self._sync_name}” couldn't be synced:\n\n{message}")

    def _sync_thread_done(self):
        if self._sync_worker is not None:
            self._sync_worker.deleteLater()
        self._sync_worker = None
        self.device_page.set_syncing(None)
        self.sidebar.set_syncing(None)
        self._update_device_actions()
        if self._closing_after_sync:
            QTimer.singleShot(0, self.close)      # stopped safely: now close

    def _eject_current_device(self):
        if self._current_source and self._current_source[0] == "device" \
                and self._current_source[1].startswith("cd:"):
            self._eject_device(self._current_source[1])
            return
        if len(self._discs) == 1 and not self._players:
            self._eject_device(next(iter(self._discs)))
            return
        ipod = self._current_device()
        if ipod is not None:
            self._eject_device(ipod.key)

    def _eject_device(self, key: str):
        if key.startswith("cd:"):
            self._eject_cd(key)
            return
        ipod = self._players.get(key)
        if ipod is None:
            return
        if self._sync_worker is not None and self._sync_ipod.key == key:
            QMessageBox.information(self, "Eject", f"“{self._device_name(ipod)}” is syncing. "
                                    "Stop the sync (✕ in the display) or let it finish, then eject.")
            return
        if key.startswith("android:"):
            self._eject_phone(ipod)
            return
        self._try_eject(ipod, 1)

    def _eject_phone(self, phone):
        name = self._device_name(phone)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            error = android_device.eject(phone)
        except (OSError, subprocess.SubprocessError) as problem:
            error = str(problem)
        finally:
            QApplication.restoreOverrideCursor()
        if error:
            QMessageBox.warning(self, "Eject", f"“{name}” couldn't be ejected.\n\n{error}")
            return
        self._phone_tries[phone.uri] = -1        # don't open it again until it's replugged
        self.statusBar().showMessage(f"“{name}” can now be disconnected.", 6000)
        self._scan_devices()

    EJECT_TRIES, EJECT_WAIT_MS = 5, 1500

    def _try_eject(self, ipod, attempt: int):
        """Eject, and if something still has the iPod open (often the system
        finishing up just after a sync), wait a moment and try again."""
        if self._ipods.get(ipod.key) is not ipod:
            return                                  # gone meanwhile (ejected elsewhere)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            error = ipod_device.eject(ipod)
        finally:
            QApplication.restoreOverrideCursor()
        if error and ipod_device.is_busy_error(error) and attempt < self.EJECT_TRIES:
            self.statusBar().showMessage(f"Waiting for “{ipod.name}” to be free…")
            QTimer.singleShot(self.EJECT_WAIT_MS, lambda: self._try_eject(ipod, attempt + 1))
            return
        self.statusBar().clearMessage()
        if error:
            QMessageBox.warning(self, "Eject", self._eject_problem(ipod, error))
            return
        self._mount_ejected.add(ipod.device)    # don't mount it again until it's replugged
        self.statusBar().showMessage(f"“{ipod.name}” can now be disconnected.", 6000)
        self._scan_devices()

    def _eject_problem(self, ipod, error: str) -> str:
        text = f"“{ipod.name}” couldn't be ejected."
        if not ipod_device.is_busy_error(error):
            return f"{text}\n\n{error}"
        users = ipod_device.users_of(ipod.mountpoint)
        if not users:
            return (f"{text} The system says it's still in use, but no program of yours has "
                    "files open on it. Wait a few seconds and eject again.\n\n" + error)
        lines = []
        for pid, name, path in users:
            who = "LuxTunes" if pid == os.getpid() else name
            lines.append(f"• {who} (process {pid}): {path}")
        return (f"{text} These programs are still using it:\n\n" + "\n".join(lines)
                + "\n\nClose them (for a file manager, close its windows showing the iPod) "
                "and eject again.")

    # ======================================================================
    # Audio CDs: Import CD
    # ======================================================================

    def _scan_cds(self):
        """Notice audio CDs going in and out of the drives (a quick question to
        each drive), then look up a new disc's names in the background."""
        changed = False
        for device in cd.drives():
            key = f"cd:{device}"
            present = cd.audio_disc_present(device)
            if not present and key in self._discs and key != self._import_key:
                del self._discs[key]                     # ejected (or taken out)
                self._cd_pending.pop(key, None)
                self._cd_unreadable.discard(device)
                changed = True
                continue
            if not present and not os.access(device, os.R_OK):
                if device not in self._cd_unreadable:
                    self._cd_unreadable.add(device)
                    self.statusBar().showMessage(
                        f"LuxTunes can't read the CD drive {device}. Add yourself to the "
                        "“cdrom” group (sudo usermod -aG cdrom $USER), then log out and back in.",
                        15000)
                continue
            if present and key not in self._discs:
                try:
                    disc = cd.blank_disc(device, cd.read_toc(device))
                except cd.CdError as error:
                    if device not in self._cd_unreadable:
                        self._cd_unreadable.add(device)
                        self.statusBar().showMessage(str(error), 8000)
                    continue
                self._cd_unreadable.discard(device)
                self._discs[key] = disc
                action = cd.insert_action(self.conn)
                if action != "nothing":             # "Do Nothing": it just appears in the sidebar
                    self._cd_pending[key] = action
                    self._current_source = ("device", key)      # show it straight away
                    # Going online needs your OK: ask once the CD is showing.
                    QTimer.singleShot(0, lambda k=key: self._offer_cd_lookup(k))
                changed = True
        if changed:
            if self._current_source and self._current_source[0] == "device" \
                    and self._current_source[1].startswith("cd:") \
                    and self._current_source[1] not in self._discs:
                self._current_source = SOURCE_MUSIC
            self.reload_library()

    def _approve_internet(self, title: str, question: str, details: str, go: str,
                          dont_ask_again=None) -> bool:
        """Ask before anything goes online. Nothing does without a yes here.
        With dont_ask_again (a function), the question has a "Don't ask again"
        box; ticking it calls that function (the question stops coming up by
        itself; it never turns into going online without asking)."""
        if not self.isVisible():
            return False
        box = QMessageBox(QMessageBox.Icon.Question, title, question, parent=self)
        box.setInformativeText(details)
        yes = box.addButton(go, QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Don't Look Up", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        check = None
        if dont_ask_again is not None:
            check = QCheckBox("Don't ask again when a CD is inserted")
            box.setCheckBox(check)
        box.exec()
        if check is not None and check.isChecked():
            dont_ask_again()
        return box.clickedButton() is yes

    def _offer_cd_lookup(self, key: str, then_continue: bool = True):
        """Ask to look up a CD's names online; afterwards (or if you say no) do
        what "When you insert a CD" says."""
        disc = self._discs.get(key)
        if disc is None or key in self._lookups:
            return
        automatic = then_continue                 # asked because a CD was found, not a button
        if automatic and not cd.ask_lookup(self.conn):
            self._run_cd_pending(key)              # turned off in Preferences (or "Don't ask again")
            return
        approved = self._approve_internet(
            "Get Track Names", f"Look up the names of the songs on “{disc.name}” on the internet?",
            "LuxTunes will send the CD's layout (how long each track is, not your music) to "
            "MusicBrainz, a free online music database, and download the song names, and the "
            "album cover from the Cover Art Archive.", "Look Up Online",
            dont_ask_again=(lambda: cd.set_ask_lookup(self.conn, False)) if automatic else None)
        if approved and key in self._discs:
            self._start_lookup(self._discs[key], approved=True)
        elif then_continue:
            self._run_cd_pending(key)

    def _start_lookup(self, disc: cd.Disc, approved: bool):
        from luxtunes.ui.cd_worker import LookupWorker
        worker = LookupWorker(disc, approved, self)
        self._lookups[disc.key] = worker
        worker.found.connect(self._on_cd_looked_up)
        worker.finished.connect(worker.deleteLater)
        if self.cd_page.disc is not None and self.cd_page.disc.key == disc.key:
            self.cd_page.set_status("Looking up track names…")
            self.cd_page.lookup_button.setEnabled(False)
        worker.start()

    def _on_cd_looked_up(self, key: str, found, problem: str):
        self._lookups.pop(key, None)
        disc = self._discs.get(key)
        if disc is None:
            return                                   # ejected meanwhile
        if found is not None and found.toc.disc_id() == disc.toc.disc_id():
            for new, old in zip(found.tracks, disc.tracks):
                new.checked = old.checked
            self._discs[key] = disc = found
            if not found.looked_up:
                problem = "This CD wasn't found in the MusicBrainz database."
        if problem:
            self.statusBar().showMessage(f"Track names: {problem}", 8000)
        self.reload_library()                         # the sidebar shows the album's name
        if self._current_source == ("device", key):
            self._show_cd(key)
        self._run_cd_pending(key)

    def _run_cd_pending(self, key: str):
        """What "When you insert a CD" says to do, once (after the names lookup)."""
        if self.cd_page.disc is not None and self.cd_page.disc.key == key \
                and key not in self._lookups and self._import_key != key:
            self.cd_page.set_status("")
        action = self._cd_pending.pop(key, None)
        if action == "ask":
            self._ask_import(key)
        elif action in ("import", "import_eject"):
            self._start_import(key, eject_after=action == "import_eject")

    def _ask_import(self, key: str):
        disc = self._discs.get(key)
        if disc is None or self._import_worker is not None or self._asking_import \
                or not self.isVisible():
            return
        self._asking_import = True           # one question at a time
        try:
            self._ask_import_now(key, disc)
        finally:
            self._asking_import = False

    def _ask_import_now(self, key: str, disc: cd.Disc):
        box = QMessageBox(QMessageBox.Icon.Question, "LuxTunes",
                          f"Would you like to import the CD “{disc.name}” into your LuxTunes library?",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        dont_ask = QCheckBox("Do not ask me again")
        box.setCheckBox(dont_ask)
        yes = box.exec() == QMessageBox.StandardButton.Yes
        if dont_ask.isChecked():      # your answer becomes the setting
            cd.set_insert_action(self.conn, "import" if yes else "show")
        if yes and key in self._discs:
            self._start_import(key)

    def _show_cd(self, key: str):
        disc = self._discs.get(key)
        if disc is None:
            self.sidebar.select_source(SOURCE_MUSIC)
            return
        if self._drilled:
            self._leave_drill(show_grid=False)
        self.cover_flow.hide()
        self.cd_page.set_disc(disc)
        self.cd_page.set_importing(self._import_key == key)
        self.cd_page.lookup_button.setEnabled(key not in self._lookups and self._import_key != key)
        if key in self._lookups:
            self.cd_page.set_status("Looking up track names…")
        elif self._import_key == key:
            self.cd_page.set_status("Importing…")
        else:
            self.cd_page.set_status("")
        self.views.setCurrentWidget(self.cd_page)
        self.search.setEnabled(False)
        total = sum(t.total_time for t in disc.tracks)
        count = len(disc.tracks)
        self.summary_label.setText(f"{count} song{'s' if count != 1 else ''}, "
                                   f"{formatting.format_duration(total)} total time")

    def _on_cd_edited(self):
        """Your corrections show in the sidebar too (the album name)."""
        disc = self.cd_page.disc
        if disc is not None:
            QTimer.singleShot(0, self.reload_library)

    def _edit_import_settings(self):
        from luxtunes.ui.import_settings import ImportSettingsDialog
        dialog = ImportSettingsDialog(encoders.load(self.conn), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            encoders.save(self.conn, dialog.result_settings())

    def _cd_import_clicked(self):
        disc = self.cd_page.disc
        if disc is None:
            return
        if self._import_key == disc.key:
            self._stop_import()
        elif self._import_worker is not None:
            QMessageBox.information(self, "Import CD", "Another CD is being imported. "
                                    "Wait for it to finish, then import this one.")
        else:
            self._start_import(disc.key)

    def _start_import(self, key: str, eject_after: bool = False):
        disc = self._discs.get(key)
        if disc is None or self._import_worker is not None:
            return
        missing = [name for name, found in (("cdparanoia", cd.cdparanoia_path()),
                                            ("ffmpeg", convert.ffmpeg_path())) if not found]
        if missing:
            QMessageBox.warning(
                self, "Import CD",
                "Importing CDs needs " + " and ".join(missing) + ", which "
                + ("isn't" if len(missing) == 1 else "aren't") + " installed.\n\n"
                "Install from a terminal with:\n    sudo apt install " + " ".join(missing))
            return
        numbers = [t.number for t in disc.tracks if t.checked]
        if not numbers:
            QMessageBox.information(self, "Import CD", "No songs are checked. Check the songs "
                                    "you want to import.")
            return
        from luxtunes.ui.cd_worker import ImportWorker
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        worker = ImportWorker(db_path, disc, numbers, self)
        self._import_worker = worker
        self._import_key = key
        self._import_eject = eject_after
        self._update_device_actions()
        self._import_failures = []
        worker.track_started.connect(self._on_import_track_started)
        worker.progress.connect(self._on_import_progress)
        worker.track_done.connect(self._on_import_track_done)
        worker.track_failed.connect(self._on_import_track_failed)
        worker.all_done.connect(self._on_import_done)
        worker.finished.connect(worker.deleteLater)
        first = next(t.name for t in disc.tracks if t.checked)
        self.lcd.start_task("import", f"Importing “{first}”", "Starting…", "Stop importing")
        self.sidebar.set_busy(key, True)
        if self.cd_page.disc is not None and self.cd_page.disc.key == key:
            for number in numbers:
                self.cd_page.set_track_state(number, None)
            self.cd_page.set_importing(True)
            self.cd_page.set_status("Importing…")
        worker.start()

    def _stop_import(self):
        if self._import_worker is not None:
            self.lcd.set_stopping("import", "Stopping…")
            self._import_worker.stop()

    def _import_page_shows(self) -> bool:
        return self.cd_page.disc is not None and self.cd_page.disc.key == self._import_key

    def _on_import_track_started(self, number: int, name: str):
        if self._import_page_shows():
            self.cd_page.set_track_state(number, "importing")
        self.lcd.update_task("import", f"Importing “{name}”", "Time remaining: calculating…", 0.0)

    def _on_import_progress(self, number: int, name: str, fraction: float, remaining: float,
                            speed: float):
        left = formatting.format_duration(int(remaining * 1000))
        self.lcd.update_task("import", f"Importing “{name}”",
                             f"Time remaining: {left} ({speed:.1f}x)", fraction)

    def _on_import_track_done(self, number: int, track_id: int):
        if self._import_page_shows():
            self.cd_page.set_track_state(number, "done")

    def _on_import_track_failed(self, number: int, message: str):
        if self._import_page_shows():
            self.cd_page.set_track_state(number, "failed" if message else None)
        if message:
            self._import_failures.append(f"Track {number}: {message}" if number else message)

    def _on_import_done(self, imported: int, stopped: bool):
        key = self._import_key
        failures = self._import_failures
        self._import_worker = None
        self._import_key = None
        self.sidebar.set_busy(key, False)
        self._update_device_actions()
        disc = self._discs.get(key)
        if self.cd_page.disc is not None and self.cd_page.disc.key == key:
            self.cd_page.set_importing(False)
            self.cd_page.set_status(f"Imported {imported} song{'s' if imported != 1 else ''}."
                                    if imported else "")
        songs = f"{imported} song{'s' if imported != 1 else ''}"
        if stopped:
            message = f"Import stopped: {songs} imported."
        elif failures:
            message = f"Imported {songs}; {len(failures)} couldn't be imported."
        else:
            message = f"Import complete: {songs} added to your library."
        self._lcd_finished("import", message)
        if not stopped and imported and appearance(self.settings)["import_done_sound"]:
            from luxtunes.core import sounds
            sounds.play_done_sound()            # the desktop's "done" sound
        self.reload_library()
        if failures:
            QMessageBox.warning(self, "Import CD", "Some songs couldn't be imported:\n\n"
                                + "\n".join(failures[:20]))
        elif self._import_eject and not stopped and disc is not None:
            self._eject_cd(key)

    def _eject_cd(self, key: str):
        disc = self._discs.get(key)
        if disc is None:
            return
        if self._import_key == key:
            QMessageBox.information(self, "Eject", "This CD is being imported. Stop the import "
                                    "(✕ in the display) or let it finish, then eject.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            error = cd.eject(disc.device)
        finally:
            QApplication.restoreOverrideCursor()
        if error:
            QMessageBox.warning(self, "Eject", f"The CD couldn't be ejected:\n\n{error}")
            return
        del self._discs[key]
        self._cd_pending.pop(key, None)
        if self._current_source == ("device", key):
            self._current_source = SOURCE_MUSIC
        self.reload_library()

    # ======================================================================
    # Menu bar: File, View and Controls items
    # ======================================================================

    def _update_file_menu(self):
        """Grey out the song items when no song is selected."""
        tracks = [t for t in self._selected_tracks() if t is not None]
        for action in (self.info_action, self.reveal_action, self.from_selection_action):
            action.setEnabled(bool(tracks))
        self.file_rating_menu.setEnabled(bool(tracks))
        self._fill_rating_menu(self.file_rating_menu, tracks)
        self.duplicates_action.setEnabled(self._dup_ids is None and not self._showing_missing)
        self.missing_action.setEnabled(not self._showing_missing)
        if tracks:
            self.reveal_action.setEnabled(find_file(tracks[0].get("location")) is not None)
        self.edit_smart_action.setEnabled(self._current_smart_id() is not None)
        self._update_device_actions()

    # ---- Sleep timer ---------------------------------------------------------------

    def _sleep_timer_dialog(self):
        """Controls → Sleep Timer…: choose how long; the window shows when it stops."""
        dialog = SleepTimerDialog(self.sleep_timer.minutes, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sleep_timer.start(dialog.minutes)
            self.statusBar().showMessage(
                f"Sleep timer: the music stops at {clock(self.sleep_timer.end)}.", 5000)

    def _sleep_timer_off(self):
        if self.sleep_timer.active:
            self.sleep_timer.stop()
            self.statusBar().showMessage("Sleep timer turned off.", 4000)

    def _show_sleep_timer(self):
        timer = self.sleep_timer
        self.sleep_button.setVisible(timer.active)
        self.sleep_off_action.setVisible(timer.active)
        if timer.active:
            self.sleep_button.setText(f"☾ {left_text(timer.seconds_left())}")
            self.sleep_button.setToolTip(f"Sleep timer: the music stops at {clock(timer.end)}. "
                                         "Click to turn it off.")
            self.sleep_action.setText(f"Sleep Timer… (stops at {clock(timer.end)})")
        else:
            self.sleep_action.setText("Sleep Timer…")

    def _update_controls_menu(self):
        self.recent_menu.setEnabled(bool(self._recent))
        self._update_device_actions()

    def _update_device_actions(self):
        """Keep Sync, Eject and the playback items greyed out whenever they can't
        be used (in the menus and for their shortcuts). Runs on every device
        check, when playback starts or stops, and when a sync or import ends."""
        try:
            ipod = self._current_device() or next(iter(self._players.values()), None)
        except Exception:
            ipod = None
        self.sync_action.setText(f"Sync “{self._device_name(ipod)}”" if ipod else "Sync Device")
        syncing_this = self._sync_worker is not None and ipod is not None \
            and self._sync_ipod.key == ipod.key
        self.sync_action.setEnabled(ipod is not None and not syncing_this)
        busy_ipod = self._sync_ipod.key if self._sync_worker is not None else None
        ejectable = [k for k in self._players if k != busy_ipod] + \
                    [k for k in self._discs if k != self._import_key]
        self.eject_action.setEnabled(bool(ejectable))
        disc = any(k.startswith("cd:") for k in ejectable)
        self.eject_action.setText("Eject Disc" if disc or not ejectable else
                                  "Eject Phone" if ejectable[0].startswith("android:") else
                                  "Eject iPod")
        playing = self.player.current is not None
        for action in (self.next_action, self.previous_action, self.current_song_action):
            action.setEnabled(playing)
        on_list = self.views.currentWidget() not in (self.device_page, self.cd_page)
        self.play_action.setEnabled(playing or (on_list and self.model.rowCount() > 0))

    def _file_menu_closed(self):
        # Re-enable after the menu closes so the keyboard shortcuts always work
        # (each one checks for a selection itself).
        for action in (self.info_action, self.reveal_action, self.from_selection_action,
                       self.duplicates_action, self.missing_action, self.edit_smart_action):
            action.setEnabled(True)

    def _reveal_selected(self):
        tracks = [t for t in self._selected_tracks() if t is not None]
        if tracks and find_file(tracks[0].get("location")):
            self._show_in_file_manager(tracks[0])

    def _new_playlist_from_selection(self):
        """A new playlist holding the selected songs, named "Artist - Album" if
        they're all from one album, then its name editor opens."""
        tracks = [t for t in self._selected_tracks() if t is not None]
        if not tracks:
            return
        playlist_id = library.create_playlist(self.conn, library.playlist_name_for(tracks))
        library.add_to_playlist(self.conn, playlist_id, [t["id"] for t in tracks])
        self._current_source = ("playlist", playlist_id)
        self.reload_library()
        self.sidebar.rename_source(("playlist", playlist_id))

    def _new_folder(self):
        folder_id = library.create_folder(self.conn)
        self._current_source = ("playlist", folder_id)
        self.reload_library()
        self.sidebar.rename_source(("playlist", folder_id))

    # ---- Add File / Add Folder ----------------------------------------------------

    def _add_file(self):
        patterns = " ".join(f"*{e}" for e in sorted(add_files.SUPPORTED))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add To Library", self.settings.value("add_folder", os.path.expanduser("~")),
            f"Music and videos ({patterns});;All files (*)")
        if paths:
            self.settings.setValue("add_folder", os.path.dirname(paths[0]))
            self._add_paths(paths)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Add To Library", self.settings.value("add_folder", os.path.expanduser("~")))
        if folder:
            self.settings.setValue("add_folder", os.path.dirname(folder))
            self._add_paths([folder])

    def _add_paths(self, paths: list[str]):
        """Add files and folders in the background; progress (and the file it's
        on) shows in the Now Playing display, with ✕ to stop. What's added so
        far is kept, and adding the same folder again carries on from there."""
        if self._add_worker is not None:
            QMessageBox.information(self, "Add To Library",
                                    "LuxTunes is still adding files. Wait for it to finish, "
                                    "or stop it with ✕ in the display.")
            return
        from luxtunes.ui.add_worker import AddWorker
        # Adding while one of your playlists is shown adds to it too.
        playlist = self._current_playlist()
        self._add_playlist = playlist["id"] if playlist and library.is_user_playlist(playlist) \
            else None
        db_path = self.conn.execute("PRAGMA database_list").fetchone()[2]
        worker = AddWorker(db_path, paths, self)
        self._add_worker = worker
        worker.searching.connect(
            lambda count: self.lcd.update_task("add", None, f"Looking for music… {count:,} found",
                                               None))
        worker.progress.connect(self._on_add_progress)
        worker.done.connect(self._on_add_done)
        worker.finished.connect(worker.deleteLater)
        self.lcd.start_task("add", "Adding to Library", "Looking for music…", "Stop adding")
        worker.start()

    def _on_add_progress(self, done: int, total: int, path: str):
        from luxtunes.ui.add_worker import AddWorker
        self.lcd.update_count("add", f"“{AddWorker.short_name(path)}”", done, total)

    def _stop_adding(self):
        if self._add_worker is not None:
            self.lcd.set_stopping("add", "Stopping…")
            self._add_worker.stop()

    def _on_add_done(self, result):
        self._add_worker = None
        if self._add_playlist is not None and result.added:
            library.add_to_playlist(self.conn, self._add_playlist, result.added)
        self._add_playlist = None
        self.reload_library()
        self.show_source(self._current_source or SOURCE_MUSIC)
        added = len(result.added)
        items = f"{added:,} item{'s' if added != 1 else ''}"
        if result.stopped:
            message = f"Adding stopped: {items} added. Add the folder again to carry on."
        elif added:
            message = f"Added {items} to your library."
        elif result.already and not result.failed:
            message = "Those items are already in your library."
        else:
            message = "Nothing was added."
        self._lcd_finished("add", message)
        self.statusBar().showMessage(message, 8000)
        if result.failed:
            QMessageBox.warning(
                self, "Add To Library",
                f"{len(result.failed):,} file(s) couldn't be added:\n\n"
                + "\n".join(result.failed[:20])
                + ("\n…" if len(result.failed) > 20 else ""))

    # ---- Create MP3 Version (FLAC → 320 kbps MP3) ------------------------------------

    def _create_mp3_versions(self, tracks: list[dict]):
        ffmpeg = convert.ffmpeg_path()
        if ffmpeg is None:
            QMessageBox.warning(
                self, "Create MP3 Version",
                "Creating MP3 versions needs FFmpeg, which isn't installed.\n\n"
                "Install it from a terminal with:\n    sudo apt install ffmpeg\n\n"
                "then try again.")
            return
        progress = QProgressDialog("", "Stop", 0, len(tracks), self)
        progress.setWindowTitle("Create MP3 Version")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        made, problems, converted = 0, [], []
        for done, track in enumerate(tracks):
            progress.setValue(done)
            progress.setLabelText(f"Converting “{track.get('name')}”…")
            source = find_file(track.get("location"))
            if source is None:
                problems.append(f"{track.get('name')}: file not found")
                continue
            source_track = library.load_track(self.conn, track["id"]) or track
            target = str(convert.mp3_target(source))
            process = subprocess.Popen(
                convert.command(ffmpeg, source, target, source_track.get("sample_rate")),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            while process.poll() is None:        # keep the window responsive meanwhile
                QApplication.processEvents()
                if progress.wasCanceled():
                    process.kill()
                    process.wait()
                    break
                time.sleep(0.03)
            if progress.wasCanceled():
                convert.remove_partial(target)
                break
            error = process.stderr.read().decode(errors="replace").strip()
            if process.returncode != 0:
                convert.remove_partial(target)
                problems.append(f"{track.get('name')}: {error.splitlines()[-1] if error else 'FFmpeg failed'}")
                continue
            try:
                converted.append((source_track, convert.finish(self.conn, source_track, source,
                                                               target)))
                made += 1
            except (OSError, ValueError, TagError) as problem:
                problems.append(f"{track.get('name')}: {problem}")
        progress.setValue(len(tracks))
        self._delete_originals(converted, "Create MP3 Version")
        if made:
            self.reload_library()
            self.show_source(self._current_source or SOURCE_MUSIC)
            self.statusBar().showMessage(
                f"Created {made} MP3 version{'s' if made != 1 else ''}.", 6000)
        if problems:
            QMessageBox.warning(self, "Create MP3 Version",
                                "Some files couldn't be converted:\n\n" + "\n".join(problems[:20]))

    def _create_ipod_versions(self, tracks: list[dict]):
        """Right-click → Create iPod Version: an .m4v copy of each movie or
        episode that the iPod can play, added to the library beside it."""
        ffmpeg = convert.ffmpeg_path()
        if ffmpeg is None:
            QMessageBox.warning(
                self, "Create iPod Version",
                "Creating iPod versions needs FFmpeg, which isn't installed.\n\n"
                "Install it from a terminal with:\n    sudo apt install ffmpeg\n\n"
                "then try again.")
            return
        import threading
        progress = QProgressDialog("", "Stop", 0, 100 * len(tracks), self)
        progress.setWindowTitle("Create iPod Version")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        made, problems, converted = 0, [], []
        for done, track in enumerate(tracks):
            source = find_file(track.get("location"))
            if source is None:
                problems.append(f"{track.get('name')}: file not found")
                continue
            source_track = library.load_track(self.conn, track["id"]) or track
            target = str(convert.ipod_video_target(source))
            process = subprocess.Popen(convert.ipod_video_command(ffmpeg, source, target),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            position = [0]                       # ms converted so far, from FFmpeg's -progress

            def read_progress(out=process.stdout, at=position):
                for line in out:
                    if line.startswith("out_time_ms="):
                        try:
                            at[0] = int(line.split("=", 1)[1]) // 1000
                        except ValueError:
                            pass

            threading.Thread(target=read_progress, daemon=True).start()
            length = source_track.get("total_time") or 0
            label = f"Converting “{track.get('name')}”"
            if len(tracks) > 1:
                label += f" ({done + 1} of {len(tracks)})"
            while process.poll() is None:            # the window stays responsive
                share = min(99, position[0] * 100 // length) if length else 0
                progress.setLabelText(f"{label}… {share}%" if length else f"{label}…")
                progress.setValue(100 * done + share)
                QApplication.processEvents()
                if progress.wasCanceled():
                    process.kill()
                    process.wait()
                    break
                time.sleep(0.05)
            if progress.wasCanceled():
                convert.remove_partial(target)
                break
            error = process.stderr.read().strip()
            if process.returncode != 0:
                convert.remove_partial(target)
                problems.append(f"{track.get('name')}: "
                                f"{error.splitlines()[-1] if error else 'FFmpeg failed'}")
                continue
            try:
                converted.append((source_track, convert.finish_video(self.conn, source_track,
                                                                     target)))
                made += 1
            except (OSError, ValueError, TagError) as problem:
                problems.append(f"{track.get('name')}: {problem}")
        progress.setValue(100 * len(tracks))
        self._delete_originals(converted, "Create iPod Version")
        if made:
            self.reload_library()
            self.show_source(self._current_source or SOURCE_MUSIC)
            self.statusBar().showMessage(
                f"Created {made} iPod version{'s' if made != 1 else ''}.", 6000)
        if problems:
            QMessageBox.warning(self, "Create iPod Version",
                                "Some videos couldn't be converted:\n\n" + "\n".join(problems[:20]))

    def _delete_originals(self, converted: list[tuple[dict, int]], title: str):
        """Preferences → Advanced → Delete original after conversion: each
        original's file goes to the Trash and its converted copy takes its
        place in the library. If the file can't go to the Trash, the original
        stays (both are kept) and you're told why."""
        if not converted or not convert.delete_original(self.conn):
            return
        copies = {original["id"]: copy_id for original, copy_id in converted}
        kept = []
        for original, copy_id in converted:
            failure = trash.move_to_trash(original.get("location"))
            if failure is None:
                self.player.forget_tracks({original["id"]})
                convert.replace_original(self.conn, original["id"], copy_id)
            else:
                kept.append((original, *failure))
        if kept:
            deleted = self._report_trash_failures(
                kept, title,
                f"The converted copies were made, but {len(kept):,} original"
                f"{'s' if len(kept) != 1 else ''} couldn't be deleted, so "
                f"{'they stay' if len(kept) != 1 else 'it stays'} in your library too.")
            for original in deleted:
                self.player.forget_tracks({original["id"]})
                convert.replace_original(self.conn, original["id"], copies[original["id"]])

    # ---- Display Duplicates -------------------------------------------------------

    @staticmethod
    def _duplicate_key(track: dict) -> tuple:
        return ((track.get("name") or "").strip().casefold(),
                (track.get("artist") or "").strip().casefold())

    def _display_duplicates(self):
        """Show only songs that share their name and artist with another song
        in this list, until Display All is clicked (or another list is chosen)."""
        if self._dup_ids is not None or self._showing_missing:
            return
        if self._drilled:
            self._leave_drill(show_grid=self.view_mode == "grid")
        self.filter_label.setText("Displaying Duplicates")
        tracks = self.model.all_tracks()
        counts = Counter(self._duplicate_key(t) for t in tracks)
        self._dup_ids = {t["id"] for t in tracks if counts[self._duplicate_key(t)] > 1}
        self.model.set_id_filter(self._dup_ids)
        if self.view_mode != "grid":
            self.table.sortByColumn(NAME_COLUMN, Qt.SortOrder.AscendingOrder)   # pairs side by side
        self.dup_bar.show()
        self._update_summary()

    def _display_all(self):
        if self._showing_missing:
            self._end_duplicates()
            self._current_source = None
            if not self.sidebar.select_source(SOURCE_MUSIC):
                self.show_source(SOURCE_MUSIC)
            return
        if self._dup_ids is None:
            return
        self._end_duplicates()
        if self._drilled:
            self._leave_drill(show_grid=self.view_mode == "grid")
        else:
            self.model.set_id_filter(None)
        self._update_summary()

    def _end_duplicates(self):
        """Turn off Display Duplicates / Display Missing (hides the banner)."""
        self._dup_ids = None
        self._showing_missing = False
        self.dup_bar.hide()

    # ---- Display Missing ------------------------------------------------------------

    def _on_play_problem(self, track: dict, message: str):
        self._broken_ids.add(track["id"])
        self.statusBar().showMessage(f"Couldn't play “{track.get('name')}”: {message}", 5000)

    def library_problems(self) -> dict[int, str]:
        """Every song or movie with a problem, and what it is: its file can't be
        found, its edits couldn't be saved into the file, or it wouldn't play."""
        problems = {}
        for track in library.load_tracks(self.conn):
            if find_file(track.get("location")) is None:
                problems[track["id"]] = "File not found"
            elif track["id"] in self._broken_ids:
                problems[track["id"]] = "Couldn't be played"
            elif track.get("tags_dirty"):
                problems[track["id"]] = "Edits not saved to the file"
        return problems

    def _display_missing(self):
        """Show every song and movie whose file is missing or has a problem,
        with the banner at the bottom; Display All goes back to Music."""
        if self._showing_missing:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            problems = self.library_problems()
        finally:
            QApplication.restoreOverrideCursor()
        if not problems:
            QMessageBox.information(self, "Display Missing",
                                    "Every song and movie in your library has its file, "
                                    "and none have problems.")
            return
        if self._drilled:
            self._leave_drill(show_grid=False)
        if self._showing_device_page():
            self.views.setCurrentIndex(1 if self.view_mode == "grid" else 0)
            self.search.setEnabled(True)
        self._end_duplicates()
        tracks = library.load_tracks_by_ids(self.conn, list(problems))
        for track in tracks:
            track["_problem"] = problems[track["id"]]
        self.sidebar.blockSignals(True)
        self.sidebar.clearSelection()
        self.sidebar.blockSignals(False)
        self._current_source = ("view", "missing")
        self.model.set_tracks(tracks, ARTIST_COLUMN)
        for track_id, problem in problems.items():
            if problem == "File not found":
                self.model.set_missing(track_id, True)          # the "!" marker
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search.setPlaceholderText("Search Missing")
        self._showing_missing = True
        self.filter_label.setText("Displaying Missing")
        self.dup_bar.show()
        missing = sum(1 for p in problems.values() if p == "File not found")
        others = len(problems) - missing
        message = f"{missing:,} item{'s' if missing != 1 else ''} with no file"
        if others:
            message += f", {others:,} with other problems"
        self.statusBar().showMessage(message + ". Hover over a song's name to see what's wrong.", 10000)
        self._update_summary()

    # ---- View Options (Ctrl+J) ----------------------------------------------------

    def _show_view_options(self):
        item = self.sidebar.currentItem()
        name = item.text(0) if item is not None else "Music"
        icon = item.icon(0) if item is not None else None
        shown = {i for i in range(len(COLUMNS)) if not self.table.isColumnHidden(i)}
        dialog = ViewOptionsDialog(name, icon, shown, self)
        if dialog.exec():
            chosen = dialog.shown_columns()
            for index in dialog.boxes:
                if (index in chosen) != (index in shown):
                    self._set_column_shown(index, index in chosen)

    # ---- Play Recent ----------------------------------------------------------------

    def _remember_recent(self, track_id: int):
        self._recent = [track_id] + [i for i in self._recent if i != track_id][:9]
        self.settings.setValue("recent_tracks", self._recent)

    def _media_icon(self, movie: bool) -> QIcon:
        """A small music note or film strip, drawn in the menu's text colour."""
        color = self.palette().color(QPalette.ColorRole.Text)
        pixmap = QPixmap(16, 16)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if movie:
                painter.setPen(color)
                painter.setBrush(color)
                painter.drawRect(2, 2, 11, 11)
                painter.setBrush(self.palette().color(QPalette.ColorRole.Base))
                for y in (3, 6, 9, 12):
                    painter.drawRect(3, y, 1, 1)
                    painter.drawRect(11, y, 1, 1)
            else:
                font = painter.font()
                font.setPixelSize(15)
                painter.setFont(font)
                painter.setPen(color)
                painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "♫")
        finally:
            painter.end()
        return QIcon(pixmap)

    def _fill_recent_menu(self):
        self.recent_menu.clear()
        tracks = library.load_tracks_by_ids(self.conn, self._recent)
        if not tracks:
            self.recent_menu.addAction("No Recent Songs").setEnabled(False)
            return
        icons = {False: self._media_icon(False), True: self._media_icon(True)}
        for track in tracks:
            self.recent_menu.addAction(icons[track.get("media_kind") in VIDEO_KINDS],
                                       track.get("name") or "Untitled",
                                       lambda t=track: self._play_recent(t))

    def _play_recent(self, track: dict):
        """Play a recent song: from the list on screen if it's in it (so play
        carries on from there), otherwise on its own."""
        row = self.model.row_of(track["id"])
        if row >= 0 and self._current_source != SOURCE_UP_NEXT:
            self._play_row(row)
        else:
            self._playing_source = None
            self.player.play_queue([track], 0)

    # ======================================================================
    # Artwork & appearance
    # ======================================================================

    def _refresh_artwork(self):
        if not self.art.isVisible():
            return
        selected = self.model.track_at(self.table.currentIndex().row())
        if self.player.current is not None and (self._art_mode == "playing" or selected is None):
            track = self.player.current
            self.art.set_title("Now Playing")
        else:
            track = selected
            self.art.set_title("Selected Item")
        if track is None:
            self.art.set_image(None)
        elif find_file(track.get("location")) is None:
            self.art.set_image(None, "Artwork Not\nAvailable")  # the file is missing
        else:
            self.art.set_image(artwork.read_embedded_artwork(find_file(track.get("location"))))

    def _on_current_row_changed(self, current, previous):
        if current.isValid():            # you clicked (or moved to) a song
            self._art_mode = "selected"
            if self.cover_flow.isVisible() and not self._syncing:
                self.cover_flow.center_on_track(self.model.track_at(current.row()))
        self._refresh_artwork()

    def _flip_art_mode(self):
        self._art_mode = "playing" if self._art_mode == "selected" else "selected"
        self._refresh_artwork()

    def _toggle_artwork(self, visible: bool):
        self.art.setVisible(visible)
        self.art_toggle.setText("▾" if visible else "▴")
        if self.show_art_action.isChecked() != visible:
            self.show_art_action.setChecked(visible)
        self.settings.setValue("show_artwork", visible)
        self._refresh_artwork()

    def _toggle_dark_mode(self, dark: bool):
        theme.apply_theme(QApplication.instance(), dark, appearance(self.settings)["grid_dark"])
        self.settings.setValue("dark_mode", dark)
        self._refresh_view_icons()
        self.reload_library()  # re-colour the sidebar section titles

    # ---- Help → Check for Updates… --------------------------------------------------

    def _check_for_updates(self):
        """Ask GitHub for the newest LuxTunes (after a yes), then offer to install it."""
        from luxtunes.core import updater
        if self._update_worker is not None:
            return
        approved = self._approve_internet(
            "Check for Updates", "Check GitHub for a newer version of LuxTunes?",
            f"LuxTunes will ask GitHub ({updater.RELEASES_PAGE.removeprefix('https://')}) for its "
            "latest release. Nothing about you or your library is sent. If there's a new version, "
            "you choose whether to install it.", "Check Now")
        if not approved:
            return
        from luxtunes.ui.update_worker import CheckWorker
        worker = CheckWorker(approved=True, parent=self)
        self._update_worker = worker
        self.statusBar().showMessage("Checking for updates…")
        worker.found.connect(self._on_update_found)
        worker.failed.connect(lambda message: self._update_problem("Check for Updates", message))
        worker.finished.connect(self._update_worker_done)
        worker.start()

    def _update_worker_done(self):
        if self._update_worker is not None:
            self._update_worker.deleteLater()
        self._update_worker = None

    def _update_problem(self, title: str, message: str):
        self.statusBar().clearMessage()
        QMessageBox.warning(self, title, message)

    def _on_update_found(self, release):
        from luxtunes.core import updater
        self.statusBar().clearMessage()
        if not updater.is_newer(release.version):
            QMessageBox.information(self, "Check for Updates",
                                    f"You have the latest version of LuxTunes ({__version__}).")
            return
        notes = release.notes[:1500] + ("…" if len(release.notes) > 1500 else "")
        if updater.kind() == "source":
            QMessageBox.information(
                self, "Check for Updates",
                f"LuxTunes {release.version} is available (you have {__version__}).\n\n"
                "This copy runs from its source folder, so update it there with git pull.")
            return
        asset = release.asset()
        if asset is None:
            QMessageBox.information(
                self, "Check for Updates",
                f"LuxTunes {release.version} is available, but its download is still being "
                "built. Try again in a few minutes.")
            return
        box = QMessageBox(QMessageBox.Icon.Information, "Check for Updates",
                          f"LuxTunes {release.version} is available. You have {__version__}.",
                          parent=self)
        size = f"{asset.size / 1024 / 1024:.1f} MB" if asset.size else "the new version"
        box.setInformativeText(f"Installing downloads {size} from GitHub. The new version "
                               "starts the next time LuxTunes opens.")
        if notes:
            box.setDetailedText(notes)
        install = box.addButton("Install Update", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is install:          # your yes to the download
            self._install_update(release)

    def _install_update(self, release):
        from luxtunes.ui.update_worker import InstallWorker
        worker = InstallWorker(release, approved=True, parent=self)
        self._update_worker = worker
        progress = QProgressDialog(f"Downloading LuxTunes {release.version}…", None, 0, 0, self)
        progress.setWindowTitle("Updating LuxTunes")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        def on_progress(done, total):
            if total:
                progress.setMaximum(total)
                progress.setValue(min(done, total))

        def on_installing():
            progress.setMaximum(0)
            progress.setLabelText(f"Installing LuxTunes {release.version}…")

        def on_done():
            progress.close()
            self._offer_restart(release.version)

        def on_failed(message):
            progress.close()
            self._update_problem("Updating LuxTunes", message)

        worker.progress.connect(on_progress)
        worker.installing.connect(on_installing)
        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        worker.finished.connect(self._update_worker_done)
        worker.start()

    def _offer_restart(self, version: str):
        from luxtunes.core import updater
        answer = QMessageBox.question(
            self, "Updating LuxTunes",
            f"LuxTunes {version} is installed. Restart LuxTunes now to use it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            command = updater.launcher()
            from PySide6.QtCore import QProcess
            self._stop_single_instance()
            if QProcess.startDetached(command[0], command[1:]):
                self.close()

    # ---- The AppImage and the applications menu ---------------------------------------

    def _offer_desktop_integration(self):
        """First run of the AppImage: offer to keep it in ~/Applications with a menu entry."""
        from luxtunes.core import desktop_integration, updater
        appimage = updater.running_appimage()
        if self.settings.value("appimage/dont_ask", False, type=bool) \
                or not desktop_integration.should_offer(appimage):
            return
        box = QMessageBox(QMessageBox.Icon.Question, "LuxTunes",
                          "Add LuxTunes to your applications menu?", parent=self)
        box.setInformativeText(
            "LuxTunes will keep itself in the Applications folder in your home folder and "
            "appear in the menu (Sound & Video) with its icon. Updates install there too.")
        add = box.addButton("Add to Menu", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        never = box.addButton("Don't Ask Again", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        if box.clickedButton() is never:
            self.settings.setValue("appimage/dont_ask", True)
        if box.clickedButton() is not add:
            return
        try:
            target = desktop_integration.integrate(appimage)
        except OSError as error:
            QMessageBox.warning(self, "LuxTunes", f"Couldn't add LuxTunes to the menu: {error}")
            return
        if target.resolve() == appimage.resolve():
            QMessageBox.information(self, "LuxTunes", "LuxTunes is now in your applications menu.")
            return
        QMessageBox.information(
            self, "LuxTunes",
            "LuxTunes is now in your applications menu. It will restart from its new place; "
            f"you can delete the file you downloaded ({appimage.name} in {appimage.parent}).")
        from PySide6.QtCore import QProcess
        self._stop_single_instance()
        if QProcess.startDetached(str(target), []):
            self.close()

    def _remove_appimage(self):
        """Help → Remove LuxTunes from This Computer…: the menu entry and ~/Applications copy."""
        from luxtunes.core import desktop_integration
        answer = QMessageBox.question(
            self, "Remove LuxTunes",
            "Remove LuxTunes from the applications menu and delete it from your Applications "
            "folder?\n\nYour library, playlists, settings and music files are kept, so they're "
            "all there if you install LuxTunes again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            desktop_integration.remove()
        except OSError as error:
            QMessageBox.warning(self, "Remove LuxTunes", f"Couldn't remove everything: {error}")
            return
        QMessageBox.information(self, "Remove LuxTunes", "LuxTunes has been removed. "
                                "It will close now.")
        self.close()

    def _stop_single_instance(self):
        """Before restarting: let the new LuxTunes start instead of just
        bringing this window to the front (see ui/app.py), and hand it the
        media keys."""
        if self._mpris is not None:
            self._mpris.close()
        from PySide6.QtNetwork import QLocalServer
        server = self.findChild(QLocalServer)
        if server is not None:
            server.close()

    def _about(self):
        AboutDialog(self.conn, self).exec()

    # ======================================================================
    # Remembering window layout between runs
    # ======================================================================

    def _restore_settings(self):
        s = self.settings
        if s.value("geometry") is not None:
            self.restoreGeometry(s.value("geometry"))
        else:
            self.resize(1280, 800)
        if s.value("splitter") is not None:
            self.splitter.restoreState(s.value("splitter"))
        if s.value("header_version", 0, type=int) == HEADER_LAYOUT_VERSION:
            self._header_states["list"] = s.value("header_list")
            self._header_states["album"] = s.value("header_album")
            if self._header_states["list"] is not None:
                self.table.horizontalHeader().restoreState(self._header_states["list"])
        if s.value("songs_split") is not None:
            self.songs_split.restoreState(s.value("songs_split"))

        dark = s.value("dark_mode", False, type=bool)
        self.dark_action.setChecked(dark)
        self._apply_appearance()            # also sets the theme

        show_art = s.value("show_artwork", True, type=bool)
        self.show_art_action.setChecked(show_art)
        self.art.setVisible(show_art)
        self.art_toggle.setText("▾" if show_art else "▴")

        recent = s.value("recent_tracks", []) or []
        if isinstance(recent, str):         # QSettings returns one item as a plain string
            recent = [recent]
        self._recent = [int(i) for i in recent if str(i).isdigit()][:10]

        volume = s.value("volume", 80, type=int)
        self.volume.setValue(volume)
        self.player.set_volume(volume)

        # Shuffle and Repeat as you left them.
        self._set_shuffle(s.value("shuffle", False, type=bool))
        repeat = s.value("repeat", REPEAT_OFF)
        self._set_repeat(repeat if repeat in (REPEAT_OFF, REPEAT_ALL, REPEAT_ONE) else REPEAT_OFF)

        self._refresh_view_icons()
        self._set_view_mode(self._remembered_view("music"), remember=False)

    def closeEvent(self, event):
        if self._sync_worker is not None:
            # Never close in the middle of a sync: ask, then stop it safely and
            # close once it has (see _sync_thread_done).
            event.ignore()
            if self._closing_after_sync:
                self.statusBar().showMessage(
                    "LuxTunes will close as soon as the sync has stopped safely.", 8000)
                return
            answer = QMessageBox.warning(
                self, "Sync in Progress",
                f"A sync with “{self._sync_name}” is ongoing. Closing LuxTunes during a sync "
                "could result in sync progress loss or other irregularities.\n\n"
                "Are you sure you want to close LuxTunes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes or self._sync_worker is None:
                if self._sync_worker is None and answer == QMessageBox.StandardButton.Yes:
                    QTimer.singleShot(0, self.close)        # it ended while you were asked
                return
            # The quickest safe stop: the copy in progress is cut short (its
            # half-copied file removed), then what's there is saved.
            self._closing_after_sync = True
            self.lcd.set_stopping("sync", "Stopping safely, then closing…")
            self.statusBar().showMessage(
                "Stopping the sync safely; LuxTunes closes when it's done.")
            self._sync_worker.stop()
            return
        self._device_timer.stop()            # no more looking for iPods and CDs
        if self._mpris is not None:
            self._mpris.close()              # media keys go to other players now
        self._cd_pending.clear()             # and no "Import this CD?" after closing
        if self._import_worker is not None:
            # Stop the CD import (the half-imported song's file is removed).
            worker = self._import_worker
            worker.stop()
            worker.wait(30_000)
        if self._add_worker is not None:
            # Stop adding (a half-copied file is removed; what's added is kept).
            worker = self._add_worker
            worker.stop()
            worker.wait(30_000)
        if self._song_lookup is not None:
            self._song_lookup.stop()
            self._song_lookup.wait(20_000)
        if self._update_worker is not None:
            self._update_worker.wait()       # never stop an update half-installed
        if self._loudness_worker is not None:
            self._loudness_worker.stop()
            self._loudness_worker.wait(15_000)   # finishes the song it's measuring
        for lookup in list(self._lookups.values()):
            lookup.wait(20_000)          # a MusicBrainz lookup gives up within ~15 s
        s = self.settings
        s.setValue("geometry", self.saveGeometry())
        s.setValue("splitter", self.splitter.saveState())
        self._header_states[self._table_layout] = self.table.horizontalHeader().saveState()
        s.setValue("header_list", self._header_states["list"])
        s.setValue("header_album", self._header_states["album"])
        s.setValue("header_version", HEADER_LAYOUT_VERSION)
        s.setValue("songs_split", self.songs_split.saveState())
        equalizer.save(self.conn, self.eq_settings)
        if self._mini_player is not None:
            self._mini_player.restore_requested.disconnect()
            self._mini_player.close()
        self.player.shutdown()
        super().closeEvent(event)
