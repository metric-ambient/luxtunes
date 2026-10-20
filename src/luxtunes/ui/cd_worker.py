"""Background work for audio CDs, so LuxTunes stays usable: looking up the
disc's names on MusicBrainz, and importing (ripping + encoding) its tracks.
The import gets its own connection to the library (SQLite connections can't
be shared between threads) and reports progress with signals; the main
window shows it in the Now Playing display.
"""

from __future__ import annotations

import copy
import threading

from PySide6.QtCore import QThread, Signal

from luxtunes.core import cd, db, encoders


class LookupWorker(QThread):
    found = Signal(str, object, str)          # disc key, Disc with names (or None), problem

    def __init__(self, disc: cd.Disc, approved: bool, parent=None):
        super().__init__(parent)
        self.disc = copy.deepcopy(disc)
        self.approved = approved              # you said yes to going online

    def run(self):
        try:
            self.found.emit(self.disc.key, cd.lookup(self.disc, approved=self.approved), "")
        except cd.CdError as error:
            self.found.emit(self.disc.key, None, str(error))
        except Exception as error:            # never let a crash in the thread go unreported
            self.found.emit(self.disc.key, None, f"{type(error).__name__}: {error}")


class ImportWorker(QThread):
    track_started = Signal(int, str)                      # track number, name
    progress = Signal(int, str, float, float, float)      # number, name, fraction, seconds left, speed
    track_done = Signal(int, int)                         # track number, new song id
    track_failed = Signal(int, str)                       # track number, why
    all_done = Signal(int, bool)                          # songs imported, stopped by you

    def __init__(self, db_path: str, disc: cd.Disc, numbers: list[int], parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.disc = copy.deepcopy(disc)        # your corrections at the moment you pressed Import
        self.numbers = numbers
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        conn = None
        imported, stopped = 0, False
        try:
            conn = db.connect(self.db_path)
            settings = encoders.load(conn)
            for track in self.disc.tracks:
                if track.number not in self.numbers:
                    continue
                if self._stop.is_set():
                    stopped = True
                    break
                self.track_started.emit(track.number, track.name)
                report = lambda p, t=track: self.progress.emit(t.number, t.name, p.fraction,
                                                               p.remaining, p.speed)
                try:
                    track_id = cd.import_track(conn, self.disc, track, settings, report, self._stop)
                except cd.ImportStopped:
                    stopped = True
                    self.track_failed.emit(track.number, "")
                    break
                except (cd.CdError, OSError) as error:
                    self.track_failed.emit(track.number, str(error))
                    continue
                except Exception as error:
                    self.track_failed.emit(track.number, f"{type(error).__name__}: {error}")
                    continue
                imported += 1
                self.track_done.emit(track.number, track_id)
        except Exception as error:
            self.track_failed.emit(0, f"{type(error).__name__}: {error}")
        finally:
            if conn is not None:
                conn.close()
            self.all_done.emit(imported, stopped)


class SongLookupWorker(QThread):
    """Right-click → Get Track Info Online…, in the background."""
    progress = Signal(int, int, str)          # albums done, albums in all, what now
    done = Signal(object)                     # musicbrainz.LookupResult
    failed = Signal(str)

    def __init__(self, tracks: list[dict], approved: bool, parent=None):
        super().__init__(parent)
        self.tracks = copy.deepcopy(tracks)
        self.approved = approved              # you said yes to going online
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        from luxtunes.core import musicbrainz
        try:
            result = musicbrainz.lookup_songs(self.tracks, approved=self.approved,
                                              progress=self.progress.emit, stop=self._stop)
            self.done.emit(result)
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}" if not isinstance(error, cd.CdError)
                             else str(error))
