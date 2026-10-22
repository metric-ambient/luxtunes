"""Writing Get Info edits into files, in the background.

Saving the details is instant (the library), but writing them into the files
can take long: a video's tags sit in front of gigabytes of picture, so adding
artwork can mean rewriting the whole file, and on a USB drive that's slow for
dozens of episodes. So the files are written here, one after another, with the
library's own connection for this thread, while LuxTunes stays usable. The
display shows the progress; closing LuxTunes waits for the file being written.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QThread, Signal

from luxtunes.core import db, library, organize, tag_sync
from luxtunes.core.tags import UNCHANGED


class TagWriteWorker(QThread):
    progress = Signal(int, int, str)          # done, total, song name
    item_done = Signal(int, str)              # track id, problem ("" = fine)
    done = Signal(int, list)                  # how many written, problems

    def __init__(self, db_path: str, jobs: list[tuple[int, object]], parent=None):
        """jobs: (track id, artworks or UNCHANGED)."""
        super().__init__(parent)
        self.db_path = db_path
        self.jobs = jobs

    def run(self):
        conn = db.connect(self.db_path)
        written, problems = 0, []
        try:
            for done, (track_id, artworks) in enumerate(self.jobs):
                track = library.load_track(conn, track_id) or {}
                name = track.get("name") or os.path.basename(track.get("location") or "")
                self.progress.emit(done, len(self.jobs), name)
                try:
                    error = tag_sync.write_pending(conn, track_id, artworks)
                    if not error and artworks is not UNCHANGED:
                        library.set_artwork_count(conn, track_id, len(artworks))
                    move_error = organize.organize_track(conn, track_id)
                except Exception as failure:          # never lose the rest of the queue
                    error, move_error = f"{type(failure).__name__}: {failure}", None
                if move_error:
                    move_error = f"couldn't move it to its new folder ({move_error})"
                problem = "; ".join(e for e in (error, move_error) if e)
                if problem:
                    problems.append(f"{name}: {problem}")
                else:
                    written += 1
                self.item_done.emit(track_id, problem)
        finally:
            conn.close()
            self.done.emit(written, problems)
