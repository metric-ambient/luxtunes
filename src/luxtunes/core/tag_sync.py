"""Copy library edits into the music files' tags.

Edits are saved in the library first (update_track_fields remembers which
tag fields changed). write_pending() then writes exactly those fields into
the file, and clears the "pending" mark only if that worked. If a file is
missing or read-only, the edit stays pending and is retried later (LuxTunes
retries all pending files at startup).
"""

from __future__ import annotations

import logging
import os
import sqlite3

from luxtunes.core import library
from luxtunes.core.paths import find_file
from luxtunes.core.tags import TAG_FIELDS, UNCHANGED, TagError, write_tags

log = logging.getLogger(__name__)
PAIRS = (("track_number", "track_count"), ("disc_number", "disc_count"))


def write_pending(conn: sqlite3.Connection, track_id: int, artworks=UNCHANGED) -> str | None:
    """Write this song's pending tag edits (and new artwork, if given) into its file.
    Returns None on success, or a short reason it couldn't be written."""
    track = library.load_track(conn, track_id)
    if track is None:
        return "song not found"
    pending = library.pending_tag_fields(conn, track_id)
    if not pending and artworks is UNCHANGED:
        return None
    path = find_file(track.get("location"))
    if path is None:
        return "file not found"

    fields = {key: track.get(key) for key in pending if key in TAG_FIELDS}
    for first, second in PAIRS:     # numbers are written as "3/12", so send both halves
        if first in fields or second in fields:
            fields[first], fields[second] = track.get(first), track.get(second)
    lyrics = (track.get("lyrics") or "") if "lyrics" in pending else UNCHANGED
    try:
        write_tags(path, fields, lyrics=lyrics, artworks=artworks)
    except TagError as error:
        log.warning("couldn't write tags for %r: %s", track.get("name"), error)
        return str(error)
    library.mark_tags_written(conn, track_id, os.path.getsize(path))
    log.info("wrote tags for %r (%s)", track.get("name"),
             ", ".join(sorted(fields) + (["lyrics"] if lyrics is not UNCHANGED else [])
                       + (["artwork"] if artworks is not UNCHANGED else [])))
    return None


def write_all_pending(conn: sqlite3.Connection) -> tuple[int, int]:
    """Retry every song with unwritten edits. Returns (written, still_pending)."""
    written = failed = 0
    for track_id in library.tracks_with_pending_tags(conn):
        if write_pending(conn, track_id) is None:
            written += 1
        else:
            failed += 1
    return written, failed
