"""Edit menu work that changes the library: pasting songs into the Library
sections (Podcasts, Audiobooks, Movies, TV Shows), and the snapshots Undo
uses to put things back.

Pasting a copy into a section makes a new file with that media kind (in its
folder) and a new library entry; the original stays where it was. A cut, or
dragging a song onto a section, changes the original's media kind instead.
Video files only go to Movies and TV Shows, other files only to the audio
sections. A song already in a section isn't pasted there again (so a song in
Music can't be pasted into Music), but songs can always be moved back.

No Qt in here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from luxtunes.core import add_files, library, organize
from luxtunes.core.db import new_persistent_id
from luxtunes.core.paths import find_file

VIDEO_SECTIONS = ("movie", "tvshow")
AUDIO_SECTIONS = ("music", "podcast", "audiobook")
# Sections that can take pasted or dropped songs (songs already there: nothing).
PASTE_SECTIONS = VIDEO_SECTIONS + AUDIO_SECTIONS
SECTION_NAMES = {"music": "Music", "movie": "Movies", "tvshow": "TV Shows",
                 "podcast": "Podcasts", "audiobook": "Audiobooks"}

SAME, OK = "same", "ok"


def is_video_file(track: dict) -> bool:
    return Path(track.get("location") or "").suffix.lower() in add_files.VIDEO_EXTENSIONS


def can_go(track: dict, kind: str) -> str:
    """OK, SAME (already in that section: nothing to do), or why it can't go there."""
    if (track.get("media_kind") or "music") == kind:
        return SAME
    name = track.get("name") or "Untitled"
    if kind in VIDEO_SECTIONS and not is_video_file(track):
        return (f"“{name}” isn't a video file, so it can't go in {SECTION_NAMES[kind]}. "
                "Only movies and TV shows go there.")
    # Music also keeps music videos; Podcasts and Audiobooks take no videos.
    if kind in ("podcast", "audiobook") and is_video_file(track):
        return (f"“{name}” is a video file, so it can't go in {SECTION_NAMES[kind]}. "
                "Videos go in Movies or TV Shows (or Music, for music videos).")
    return OK


# Not carried over to a pasted copy: it's a new item in the library.
NOT_COPIED = ("id", "persistent_id", "location", "play_count", "play_date", "skip_count",
              "skip_date", "bookmark_time", "itunes_track_id", "tags_dirty", "dirty_fields")


def duplicate_as(conn: sqlite3.Connection, track_id: int, kind: str) -> int:
    """Paste a copy into another section: a copy of the file with the new media
    kind, in that kind's folder of the LuxTunes Media folder, and a new library
    entry for it. Returns the new id. Raises FileNotFoundError / OSError."""
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if row is None:
        raise KeyError(track_id)
    source = find_file(row["location"])
    if source is None:
        raise FileNotFoundError(f"“{row['name']}” couldn't be found at “{row['location']}”")
    fields = {k: row[k] for k in row.keys() if k not in NOT_COPIED}
    fields["media_kind"] = kind
    fields.update(library.kind_defaults(kind))
    target = organize.target_path(organize.media_folder(conn), fields, source)
    if target == Path(source):
        target = target.with_name(f"{target.stem} 1{target.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    add_files.copy_file(source, target)
    fields.update(location=str(target), persistent_id=new_persistent_id(),
                  date_added=library.now_utc(), date_modified=library.now_utc(),
                  play_count=0, skip_count=0)
    with conn:
        cursor = conn.execute(f"INSERT INTO tracks ({', '.join(fields)}) VALUES "
                              f"({', '.join('?' for _ in fields)})", list(fields.values()))
    return cursor.lastrowid


# ---- Snapshots for Undo ----------------------------------------------------------------

def playlist_order(conn: sqlite3.Connection, playlist_id: int) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position",
        (playlist_id,))]


def set_playlist_order(conn: sqlite3.Connection, playlist_id: int, track_ids: list[int]) -> None:
    """A playlist's songs as given (songs no longer in the library are left out)."""
    existing = {r[0] for r in conn.execute("SELECT id FROM tracks")}
    if conn.execute("SELECT 1 FROM playlists WHERE id = ?", (playlist_id,)).fetchone() is None:
        return
    with conn:
        conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        conn.executemany(
            "INSERT INTO playlist_items (playlist_id, position, track_id) VALUES (?, ?, ?)",
            [(playlist_id, i, t) for i, t in enumerate(t for t in track_ids if t in existing)])


def snapshot_tracks(conn: sqlite3.Connection, track_ids: list[int]) -> dict:
    """Everything needed to bring deleted library entries back: their rows and
    the order of every playlist they were in."""
    ids = list(track_ids)
    rows = [dict(r) for r in library.load_tracks_by_ids(conn, ids)]
    playlists = {r[0] for i in ids for r in conn.execute(
        "SELECT DISTINCT playlist_id FROM playlist_items WHERE track_id = ?", (i,))}
    return {"rows": rows, "playlists": {p: playlist_order(conn, p) for p in playlists}}


def restore_tracks(conn: sqlite3.Connection, snapshot: dict) -> None:
    """Put deleted library entries back (same ids), and their playlists' order."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    with conn:
        for row in snapshot["rows"]:
            row = {k: v for k, v in row.items() if k in columns}
            conn.execute(f"INSERT OR IGNORE INTO tracks ({', '.join(row)}) VALUES "
                         f"({', '.join('?' for _ in row)})", list(row.values()))
    for playlist_id, order in snapshot["playlists"].items():
        set_playlist_order(conn, playlist_id, order)
