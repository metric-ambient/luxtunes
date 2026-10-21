"""Edit → Preferences (Ctrl+,).

General: the library's name, which sources show in the sidebar, text sizes,
the Grid view's colour, source icons and list checkboxes, and what happens
when you insert a CD (plus Import Settings…) and the sounds when an import
or an iPod sync ends.
Advanced: the LuxTunes Media folder and keeping it organized.

Looking up CD names or album artwork online is never automatic: LuxTunes
asks every time (see MainWindow._approve_internet). The only setting is
whether it asks by itself when a CD is inserted. Checking for updates only
happens when you choose Help → Check for Updates…, after a yes.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core import add_files, cd, convert, encoders, library, organize

# Look-and-feel choices, kept in the app's settings (QSettings), with their defaults.
APPEARANCE = {
    "show_movies": True,            # Sources: Movies
    "show_tv_shows": True,          # Sources: TV Shows
    "show_podcasts": True,          # Sources: Podcasts
    "show_audiobooks": True,        # Sources: Audiobooks
    "source_text": "small",         # sidebar text: small / large
    "list_text": "small",           # song list text: small / large
    "grid_dark": True,              # Grid View: dark / light
    "show_source_icons": True,
    "show_list_checkboxes": True,
    "import_done_sound": True,      # play the desktop's notification sound after a CD import
    "sync_done_sound": True,        # ... and after an iPod sync
}


def appearance(settings: QSettings) -> dict:
    """The look-and-feel choices (defaults for anything not set yet)."""
    out = {}
    for key, default in APPEARANCE.items():
        value = settings.value(f"prefs/{key}", default)
        if isinstance(default, bool):
            value = value if isinstance(value, bool) else str(value).lower() in ("true", "1")
        out[key] = value
    return out


def _line() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


class PreferencesDialog(QDialog):
    def __init__(self, conn, parent=None, settings: QSettings | None = None):
        super().__init__(parent)
        self.conn = conn
        self.settings = settings if settings is not None else QSettings("LuxTunes", "LuxTunes")
        self.setWindowTitle("Preferences")
        self.resize(540, 460)
        self._folder = str(organize.media_folder(conn))
        self.old_folder = self._folder           # to point songs at a moved folder after OK
        self._import_settings = encoders.load(conn)

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._advanced_tab(), "Advanced")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

    def _general_tab(self) -> QWidget:
        page = QWidget()
        look = appearance(self.settings)
        self.library_name = QLineEdit(library.library_name(self.conn))
        name_form = QFormLayout()
        name_form.addRow("Library Name:", self.library_name)

        self.show_movies = QCheckBox("Movies")
        self.show_movies.setChecked(look["show_movies"])
        self.show_tv_shows = QCheckBox("TV Shows")
        self.show_tv_shows.setChecked(look["show_tv_shows"])
        self.show_podcasts = QCheckBox("Podcasts")
        self.show_podcasts.setChecked(look["show_podcasts"])
        self.show_audiobooks = QCheckBox("Audiobooks")
        self.show_audiobooks.setChecked(look["show_audiobooks"])
        sources = QHBoxLayout()
        for box in (self.show_movies, self.show_tv_shows, self.show_podcasts, self.show_audiobooks):
            sources.addWidget(box)
        sources.addStretch(1)
        name_form.addRow("Sources:", sources)

        def size_combo(value):
            combo = QComboBox()
            combo.addItem("Small", "small")
            combo.addItem("Large", "large")
            combo.setCurrentIndex(1 if value == "large" else 0)
            return combo

        self.source_text = size_combo(look["source_text"])
        self.list_text = size_combo(look["list_text"])
        self.grid_view = QComboBox()
        self.grid_view.addItem("Dark", True)
        self.grid_view.addItem("Light", False)
        self.grid_view.setCurrentIndex(0 if look["grid_dark"] else 1)
        self.source_icons = QCheckBox("Show source icons")
        self.source_icons.setChecked(look["show_source_icons"])
        self.list_checkboxes = QCheckBox("Show list checkboxes")
        self.list_checkboxes.setChecked(look["show_list_checkboxes"])
        grid = QGridLayout()
        grid.addWidget(QLabel("Source Text:"), 0, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.source_text, 0, 1)
        grid.addWidget(self.source_icons, 0, 2)
        grid.addWidget(QLabel("List Text:"), 1, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.list_text, 1, 1)
        grid.addWidget(self.list_checkboxes, 1, 2)
        grid.addWidget(QLabel("Grid View:"), 2, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.grid_view, 2, 1)
        grid.setColumnStretch(3, 1)

        self.insert_action = QComboBox()
        for value, label in cd.INSERT_ACTIONS:
            self.insert_action.addItem(label, value)
        self.insert_action.setCurrentIndex(max(0, self.insert_action.findData(cd.insert_action(self.conn))))
        settings_button = QPushButton("Import Settings…")
        settings_button.clicked.connect(self._edit_import_settings)
        row = QHBoxLayout()
        row.addWidget(QLabel("When you insert a CD:"))
        row.addWidget(self.insert_action)
        row.addWidget(settings_button)
        row.addStretch(1)
        self.import_sound = QCheckBox("Play a sound when a CD finishes importing")
        self.import_sound.setChecked(look["import_done_sound"])
        self.sync_sound = QCheckBox("Play a sound when an iPod finishes syncing")
        self.sync_sound.setChecked(look["sync_done_sound"])
        self.ask_lookup = QCheckBox("Ask to look up track names online when a CD is inserted")
        self.ask_lookup.setChecked(cd.ask_lookup(self.conn))
        online = QLabel("Looking up CD track names or artwork online always asks first. With "
                        "this off, use Get Track Names on the CD's page, or Get Track Info "
                        "Online… on imported songs.")
        online.setWordWrap(True)
        online.setEnabled(False)
        layout = QVBoxLayout(page)
        layout.addLayout(name_form)
        layout.addWidget(_line())
        layout.addLayout(grid)
        layout.addWidget(_line())
        layout.addLayout(row)
        layout.addWidget(self.import_sound)
        layout.addWidget(self.sync_sound)
        layout.addWidget(self.ask_lookup)
        layout.addWidget(online)
        layout.addStretch(1)
        return page

    def _edit_import_settings(self):
        from luxtunes.ui.import_settings import ImportSettingsDialog
        dialog = ImportSettingsDialog(self._import_settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._import_settings = dialog.result_settings()

    def _advanced_tab(self) -> QWidget:
        page = QWidget()
        box = QGroupBox("LuxTunes Media folder location")
        self.folder_label = QLabel(self._folder)
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        change = QPushButton("Change…")
        change.clicked.connect(self._change_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder_label, 1)
        row.addWidget(change, 0, Qt.AlignmentFlag.AlignTop)
        box.setLayout(row)

        self.keep_organized = QCheckBox("Keep LuxTunes Media folder organized")
        self.keep_organized.setChecked(organize.is_enabled(self.conn))
        note = QLabel("Places files into album and artist folders, and names the files based "
                      "on the disc number, track number, and the song title.")
        note.setWordWrap(True)
        note.setContentsMargins(24, 0, 0, 0)

        self.copy_files = QCheckBox("Copy files to LuxTunes Media folder when adding to library")
        self.copy_files.setChecked(add_files.copy_when_adding(self.conn))

        self.delete_original = QCheckBox("Delete original after conversion")
        self.delete_original.setChecked(convert.delete_original(self.conn))
        delete_note = QLabel("After Create MP3 Version or Create iPod Version works, the "
                             "original file (FLAC, AVI…) goes to the Trash and the new version "
                             "takes its place in your library, keeping its plays, rating and "
                             "playlists.")
        delete_note.setWordWrap(True)
        delete_note.setContentsMargins(24, 0, 0, 0)

        layout = QVBoxLayout(page)
        layout.addWidget(box)
        layout.addWidget(self.keep_organized)
        layout.addWidget(note)
        layout.addWidget(self.copy_files)
        layout.addWidget(self.delete_original)
        layout.addWidget(delete_note)
        layout.addStretch(1)
        return page

    def _change_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Change LuxTunes Media Folder Location",
                                                  self._folder)
        if folder:
            self._folder = folder
            self.folder_label.setText(folder)

    def _save(self):
        organize.set_enabled(self.conn, self.keep_organized.isChecked())
        organize.set_media_folder(self.conn, self._folder)
        add_files.set_copy_when_adding(self.conn, self.copy_files.isChecked())
        convert.set_delete_original(self.conn, self.delete_original.isChecked())
        cd.set_insert_action(self.conn, self.insert_action.currentData())
        cd.set_ask_lookup(self.conn, self.ask_lookup.isChecked())
        encoders.save(self.conn, self._import_settings)
        if self.library_name.text().strip():
            library.set_library_name(self.conn, self.library_name.text())
        s = self.settings
        s.setValue("prefs/show_movies", self.show_movies.isChecked())
        s.setValue("prefs/show_tv_shows", self.show_tv_shows.isChecked())
        s.setValue("prefs/show_podcasts", self.show_podcasts.isChecked())
        s.setValue("prefs/show_audiobooks", self.show_audiobooks.isChecked())
        s.setValue("prefs/import_done_sound", self.import_sound.isChecked())
        s.setValue("prefs/sync_done_sound", self.sync_sound.isChecked())
        s.setValue("prefs/source_text", self.source_text.currentData())
        s.setValue("prefs/list_text", self.list_text.currentData())
        s.setValue("prefs/grid_dark", bool(self.grid_view.currentData()))
        s.setValue("prefs/show_source_icons", self.source_icons.isChecked())
        s.setValue("prefs/show_list_checkboxes", self.list_checkboxes.isChecked())
        self.accept()
