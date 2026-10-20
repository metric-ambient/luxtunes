"""File → Add to Library, in the background, so LuxTunes stays usable while a
big folder is added (and you can see which file it's on).

The add gets its own connection to the library (SQLite connections can't be
shared between threads); each file is saved as soon as it's added.
"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QThread, Signal

from luxtunes.core import add_files, db


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
