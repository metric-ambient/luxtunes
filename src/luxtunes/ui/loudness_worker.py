"""Sound Check's background work: each song without a loudness correction
gets one (from its tags, or measured with FFmpeg), one at a time, with its own
connection to the library. Stoppable; what's done is kept, and the next start
carries on with the rest."""

from __future__ import annotations

import os
import shutil
import threading

from PySide6.QtCore import QThread, Signal

from luxtunes.core import db, loudness
from luxtunes.core.paths import find_file

# Songs measured as unmeasurable (silent, unreadable) get this, so they aren't
# tried again at every start.
NOTHING = 0.0


class LoudnessWorker(QThread):
    progress = Signal(int, int, str)          # done, total, song name
    measured = Signal(int, float)             # track id, correction (dB)
    done = Signal(int, bool, bool)            # how many, stopped, FFmpeg missing

    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        conn = db.connect(self.db_path)
        count, no_ffmpeg = 0, False
        try:
            rows = conn.execute(
                "SELECT id, name, location FROM tracks WHERE loudness IS NULL AND "
                "media_kind IN ('music', 'podcast', 'audiobook') ORDER BY id").fetchall()
            for done, (track_id, name, location) in enumerate(rows):
                if self._stop.is_set():
                    break
                self.progress.emit(done, len(rows), name or os.path.basename(location or ""))
                path = find_file(location)
                if path is None:
                    continue                          # missing: maybe later
                gain = loudness.from_tags(path)
                if gain is None:
                    if shutil.which("ffmpeg") is None:
                        no_ffmpeg = True
                        continue
                    gain = loudness.measure(path)
                    if gain is None:
                        gain = NOTHING
                with conn:
                    conn.execute("UPDATE tracks SET loudness = ? WHERE id = ?", (gain, track_id))
                count += 1
                self.measured.emit(track_id, gain)
        finally:
            conn.close()
            self.done.emit(count, self._stop.is_set(), no_ffmpeg)
