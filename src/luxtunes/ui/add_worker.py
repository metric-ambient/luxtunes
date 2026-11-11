"""File → Add to Library, in the background, so LuxTunes stays usable while a
big folder is added (and you can see which file it's on).

The add gets its own connection to the library (SQLite connections can't be
shared between threads); each file is saved as soon as it's added. Adding
an iPod's songs (synced with another computer) works the same way.
"""

from __future__ import annotations

import logging
import os
import threading

from PySide6.QtCore import QThread, Signal

from luxtunes.core import add_files, db

log = logging.getLogger(__name__)


class AddWorker(QThread):
    searching = Signal(int)                 # files found so far
    progress = Signal(int, int, str)        # done, total, file being added
    done = Signal(object)                   # add_files.AddResult

    def __init__(self, db_path: str, paths: list[str], parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.paths = paths
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        conn = None
        result = add_files.AddResult()
        try:
            conn = db.connect(self.db_path)
            result = add_files.add_paths(
                conn, self.paths, stop=self._stop,
                progress=lambda done, total, path: self.progress.emit(done, total, path),
                found_so_far=self.searching.emit)
        except Exception as error:          # never let a crash in the thread go unreported
            result.failed.append(f"{type(error).__name__}: {error}")
        finally:
            if conn is not None:
                conn.close()
            self.done.emit(result)

    @staticmethod
    def short_name(path: str) -> str:
        """"Artist/Album/01 Song.mp3" for the display: enough to find the file."""
        parts = os.path.normpath(path).split(os.sep)
        return "/".join(parts[-3:])


class IPodCheckWorker(QThread):
    """Reads an iPod's song list and finds the songs that aren't in the library."""
    found = Signal(str, object, int)        # device key, [IPodSong], protected songs left out

    def __init__(self, db_path: str, ipod, model_hint: str | None = None, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.ipod = ipod
        self.model_hint = model_hint

    def run(self):
        from luxtunes.core.ipod import import_songs, sync
        conn = None
        songs, protected = [], 0
        try:
            extended = sync.database_options(self.ipod)["sysinfo_extended"]   # nano 6G/7G
            everything = import_songs.read_ipod(self.ipod.mountpoint, self.model_hint, extended)
            conn = db.connect(self.db_path)
            songs = import_songs.new_songs(conn, everything)
            protected = import_songs.protected_count(everything)
        except Exception as error:          # an unreadable iPod: just nothing to offer
            log.warning("couldn't read the iPod's songs: %s", error)
        finally:
            if conn is not None:
                conn.close()
            self.found.emit(self.ipod.key, songs, protected)


class IPodImportWorker(QThread):
    """Copies an iPod's songs into the library (see core/ipod/import_songs.py)."""
    progress = Signal(int, int, str)        # done, total, song name
    done = Signal(object)                   # import_songs.ImportResult

    def __init__(self, db_path: str, songs: list, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.songs = songs
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        from luxtunes.core.ipod import import_songs
        conn = None
        result = import_songs.ImportResult()
        try:
            conn = db.connect(self.db_path)
            result = import_songs.import_songs(
                conn, self.songs, stop=self._stop,
                progress=lambda done, total, name: self.progress.emit(done, total, name))
        except Exception as error:          # never let a crash in the thread go unreported
            result.failed.append(f"{type(error).__name__}: {error}")
        finally:
            if conn is not None:
                conn.close()
            self.done.emit(result)
