"""Runs an iPod or phone sync in the background, so LuxTunes stays usable meanwhile.

The sync gets its own connection to the library (SQLite connections can't be
shared between threads). It reports progress with signals; the main window
shows them in the Now Playing display. Before changing anything on the iPod
it may ask the main window to confirm (confirm_needed), and waits for the
answer.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from luxtunes.core import db
from luxtunes.core.ipod import gpod, settings as ipod_settings, sync as ipod_sync


class SyncWorker(QThread):
    progress = Signal(str, int, int)          # text, done, total
    confirm_needed = Signal(int, int, object)  # songs to add, to remove, [Removal]
    playlists_confirm_needed = Signal(object)  # names of playlists the phone deleted
    succeeded = Signal(object)                # SyncReport
    failed = Signal(str)

    def __init__(self, db_path: str, ipod, device_key: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.ipod = ipod
        self.device_key = device_key
        self._stop = threading.Event()
        self._answered = threading.Event()
        self._answer = False

    # Called from the main window ---------------------------------------------------------
    def stop(self):
        """Stop as soon as it's safe (the songs copied so far are kept)."""
        self._stop.set()
        self._answer_with(False)

    def answer(self, ok: bool):
        self._answer_with(ok)

    def _answer_with(self, ok: bool):
        self._answer = ok
        self._answered.set()

    # Runs in the background thread --------------------------------------------------------
    def _progress(self, text, done, total):
        self.progress.emit(text, done, total)
        return not self._stop.is_set()

    def _confirm_playlists(self, names):
        self._answered.clear()
        self.playlists_confirm_needed.emit(list(names))
        self._answered.wait()
        return self._answer and not self._stop.is_set()

    def _confirm(self, adding, removing, removals=()):
        self._answered.clear()
        self.confirm_needed.emit(adding, removing, list(removals))
        self._answered.wait()
        return self._answer and not self._stop.is_set()

    def run(self):
        conn = None
        try:
            conn = db.connect(self.db_path)
            if self.device_key.startswith("android:"):
                from luxtunes.core.android import sync as android_sync
                record = android_sync.load_record(conn, self.ipod)
                report = android_sync.sync(conn, self.ipod, record, self._progress, self._confirm,
                                           self._confirm_playlists)
            else:
                record = ipod_settings.load_device(conn, self.device_key, self.ipod.name,
                                                   self.ipod.model_number, self.ipod.serial)
                report = ipod_sync.sync(conn, self.ipod, record, self._progress, self._confirm)
            self.succeeded.emit(report)
        except ipod_sync.SyncCancelled:
            report = ipod_sync.SyncReport(cancelled=True)
            self.succeeded.emit(report)
        except (gpod.GpodError, OSError) as error:
            self.failed.emit(str(error))
        except Exception as error:          # never let a crash in the thread go unreported
            self.failed.emit(f"{type(error).__name__}: {error}")
        finally:
            if conn is not None:
                conn.close()
