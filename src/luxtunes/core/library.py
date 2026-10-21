"""Everyday reads and writes on the library: loading tracks and playlists,
recording plays and skips, rating songs.

The UI calls these functions instead of writing SQL itself, so the rules
(e.g. "a play is counted when a song finishes") live in one place.
"""

from __future__ import annotations

import os
import json
import sqlite3
from datetime import datetime, timezone

# Built-in playlists in an imported iTunes library, identified by "Distinguished Kind".
KIND_MOVIES = 2
KIND_TV_SHOWS = 3
KIND_MUSIC = 4
KIND_PURCHASED = 19
KIND_ITUNES_DJ = 22
LIBRARY_SECTION_KINDS = {KIND_MOVIES, KIND_TV_SHOWS, KIND_MUSIC}
# Imported lists LuxTunes doesn't use (no store, no DJ). Not imported.
UNSUPPORTED_KINDS = {KIND_PURCHASED, KIND_ITUNES_DJ}
# Kinds 200+ are imported default smart playlists (My Top Rated, Recently Played, ...),
# which behave like ordinary smart playlists.


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_tracks(
    conn: sqlite3.Connection,
    *,
    media_kind: str | None = None,
    playlist_id: int | None = None,
) -> list[dict]:
    """Tracks for one sidebar source, as plain dicts.

    With playlist_id, tracks come back in playlist order (duplicates included)
    and each has a "playlist_position". Otherwise, every track of media_kind.
    """
    if playlist_id is not None:
        rows = conn.execute(
            """SELECT t.*, pi.position AS playlist_position
               FROM playlist_items pi JOIN tracks t ON t.id = pi.track_id
               WHERE pi.playlist_id = ? ORDER BY pi.position""",
            (playlist_id,),
        )
    elif media_kind is not None:
        rows = conn.execute("SELECT * FROM tracks WHERE media_kind = ?", (media_kind,))
    else:
        rows = conn.execute("SELECT * FROM tracks")
    return [dict(r) for r in rows]


def load_playlists(conn: sqlite3.Connection) -> list[dict]:
    """All playlists except the hidden master "Library" list, in sidebar order:
    like iTunes, smart playlists first, then folders, then playlists, each A-Z."""
    rows = conn.execute(
        """SELECT * FROM playlists WHERE is_master = 0
           ORDER BY is_smart DESC, is_folder DESC, name COLLATE NOCASE"""
    )
    return [dict(r) for r in rows]


def create_playlist(conn: sqlite3.Connection, name: str | None = None) -> int:
    """Make a new empty playlist and return its id. With no name, picks
    "untitled playlist", "untitled playlist 2", ...."""
    from luxtunes.core.db import new_persistent_id

    if name is None:
        taken = {r[0] for r in conn.execute("SELECT name FROM playlists")}
        name, n = "untitled playlist", 2
        while name in taken:
            name, n = f"untitled playlist {n}", n + 1
    with conn:
        cursor = conn.execute(
            "INSERT INTO playlists (persistent_id, name, position) VALUES (?, ?, "
            "(SELECT COALESCE(MAX(position) + 1, 0) FROM playlists))",
            (new_persistent_id(), name),
        )
    return cursor.lastrowid


def rename_playlist(conn: sqlite3.Connection, playlist_id: int, name: str) -> None:
    name = name.strip()
    if not name:
        return  # an empty name is ignored; the old one stays
    with conn:
        conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, playlist_id))


def count_tracks(conn: sqlite3.Connection, media_kind: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM tracks WHERE media_kind = ?", (media_kind,)
    ).fetchone()[0]


def record_play(conn: sqlite3.Connection, track_id: int) -> dict:
    """A song played to the end: +1 play, last played = now. Returns the new values."""
    played = now_utc()
    with conn:
        conn.execute(
            "UPDATE tracks SET play_count = play_count + 1, play_date = ? WHERE id = ?",
            (played, track_id),
        )
    row = conn.execute("SELECT play_count FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if row is None:  # the song was deleted while it was playing
        return {}
    count = row[0]
    return {"play_count": count, "play_date": played}


def record_skip(conn: sqlite3.Connection, track_id: int) -> dict:
    """The user skipped a song early: +1 skip, last skipped = now."""
    skipped = now_utc()
    with conn:
        conn.execute(
            "UPDATE tracks SET skip_count = skip_count + 1, skip_date = ? WHERE id = ?",
            (skipped, track_id),
        )
    row = conn.execute("SELECT skip_count FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if row is None:  # the song was deleted while it was playing
        return {}
    count = row[0]
    return {"skip_count": count, "skip_date": skipped}


def set_rating(conn: sqlite3.Connection, track_id: int, rating: int) -> dict:
    """Set a song's rating (0-100). A rating you set yourself is never 'computed'."""
    rating = max(0, min(100, int(rating)))
    with conn:
        conn.execute(
            "UPDATE tracks SET rating = ?, rating_computed = 0 WHERE id = ?",
            (rating, track_id),
        )
    return {"rating": rating, "rating_computed": 0}


def set_location(conn: sqlite3.Connection, track_id: int, location: str) -> dict:
    """Point a track at a (new) file on disk, e.g. after you locate a missing file."""
    with conn:
        conn.execute("UPDATE tracks SET location = ? WHERE id = ?", (location, track_id))
    return {"location": location}


def set_enabled(conn: sqlite3.Connection, track_id: int, enabled: bool) -> dict:
    with conn:
        conn.execute("UPDATE tracks SET enabled = ? WHERE id = ?", (int(enabled), track_id))
    return {"enabled": int(enabled)}


# ---- Bulk edits from the song list's right-click menu ----------------------

def reset_plays(conn: sqlite3.Connection, track_ids: list[int]) -> dict:
    """Reset Plays: zero the play AND skip counts, and forget the
    last played / last skipped dates."""
    fields = {"play_count": 0, "play_date": None, "skip_count": 0, "skip_date": None}
    with conn:
        conn.executemany(
            "UPDATE tracks SET play_count = 0, play_date = NULL,"
            " skip_count = 0, skip_date = NULL WHERE id = ?",
            [(i,) for i in track_ids],
        )
    return fields


def delete_tracks(conn: sqlite3.Connection, track_ids: list[int]) -> None:
    """Remove songs from the library (and from every playlist). Files are untouched."""
    with conn:
        conn.executemany("DELETE FROM tracks WHERE id = ?", [(i,) for i in track_ids])
        # Close the gaps left in playlists.
        for (playlist_id,) in conn.execute("SELECT id FROM playlists").fetchall():
            _renumber(conn, playlist_id)


# ---- Playlists -------------------------------------------------------------

def is_user_playlist(p: dict) -> bool:
    """A normal playlist you can add songs to (not smart, not a folder, not built-in)."""
    return not (p["is_smart"] or p["is_folder"] or p["is_master"] or p["distinguished_kind"])


def add_to_playlist(conn: sqlite3.Connection, playlist_id: int, track_ids: list[int]) -> None:
    """Append songs to the end of a playlist (duplicates allowed)."""
    with conn:
        start = conn.execute(
            "SELECT COALESCE(MAX(position) + 1, 0) FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO playlist_items (playlist_id, position, track_id) VALUES (?, ?, ?)",
            [(playlist_id, start + i, tid) for i, tid in enumerate(track_ids)],
        )


def remove_from_playlist(conn: sqlite3.Connection, playlist_id: int, positions: list[int]) -> None:
    """Remove entries (by their position in the playlist), then close the gaps."""
    with conn:
        conn.executemany(
            "DELETE FROM playlist_items WHERE playlist_id = ? AND position = ?",
            [(playlist_id, p) for p in positions],
        )
        _renumber(conn, playlist_id)


def _renumber(conn: sqlite3.Connection, playlist_id: int) -> None:
    rows = conn.execute(
        "SELECT position, track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
    conn.executemany(
        "INSERT INTO playlist_items (playlist_id, position, track_id) VALUES (?, ?, ?)",
        [(playlist_id, i, r[1]) for i, r in enumerate(rows)],
    )


def playlists_containing(conn: sqlite3.Connection, track_id: int) -> list[dict]:
    """Playlists a song appears in, for 'Show in Playlist' (built-in lists excluded)."""
    rows = conn.execute(
        """SELECT DISTINCT p.* FROM playlists p
           JOIN playlist_items pi ON pi.playlist_id = p.id
           WHERE pi.track_id = ? AND p.is_master = 0
             AND (p.distinguished_kind IS NULL OR p.distinguished_kind >= 200)
           ORDER BY p.position, p.name""",
        (track_id,),
    )
    return [dict(r) for r in rows]


def playlist_track_ids(conn: sqlite3.Connection, playlist_id: int) -> set[int]:
    """Ids of the songs already in a playlist (to warn about duplicates)."""
    rows = conn.execute(
        "SELECT DISTINCT track_id FROM playlist_items WHERE playlist_id = ?", (playlist_id,)
    )
    return {r[0] for r in rows}


def load_tracks_by_ids(conn: sqlite3.Connection, track_ids: list[int]) -> list[dict]:
    """Tracks for the given ids, in the same order (missing ids are skipped)."""
    by_id = {}
    for start in range(0, len(track_ids), 500):  # SQLite limits how many ?s one query can have
        chunk = track_ids[start:start + 500]
        rows = conn.execute(
            f"SELECT * FROM tracks WHERE id IN ({','.join('?' * len(chunk))})", chunk
        )
        by_id.update({r["id"]: dict(r) for r in rows})
    return [by_id[i] for i in track_ids if i in by_id]


# ---- Editing song details ----------------------------------------------------

# Fields you can edit in the song list (click a selected song's cell) and
# their type. These are the ones also written into the file's tags.
EDITABLE_TEXT_FIELDS = {
    "name", "artist", "album_artist", "album", "genre", "composer", "grouping",
    "comments", "sort_name", "sort_artist", "sort_album_artist", "sort_album",
    "sort_composer", "lyrics",
}
EDITABLE_NUMBER_FIELDS = {"year", "bpm", "track_number", "track_count", "disc_number", "disc_count"}
# Get Info's Options tab: settings kept in the library only (never in the file).
EDITABLE_FLAG_FIELDS = {"compilation", "bookmarkable", "skip_when_shuffling", "part_of_gapless_album"}
EDITABLE_TIME_FIELDS = {"start_time", "stop_time"}          # milliseconds, or None
EDITABLE_OTHER_FIELDS = {"volume_adjustment", "media_kind", "equalizer"}
EDITABLE_FIELDS = (EDITABLE_TEXT_FIELDS | EDITABLE_NUMBER_FIELDS | EDITABLE_FLAG_FIELDS
                   | EDITABLE_TIME_FIELDS | EDITABLE_OTHER_FIELDS)
MEDIA_KINDS = ("music", "movie", "tvshow", "podcast", "audiobook")
AUDIO_KINDS = ("music", "podcast", "audiobook")
AUDIOBOOK_EXTENSIONS = (".m4b", ".aa", ".aax")


def kind_defaults(kind: str) -> dict:
    """Podcasts and audiobooks remember where you stopped and stay out of
    shuffle (the defaults a new one gets; Get Info can change them)."""
    if kind in ("podcast", "audiobook"):
        return {"bookmarkable": 1, "skip_when_shuffling": 1}
    return {}


def clean_field_value(key: str, value):
    """Turn what was typed into what gets stored (None = empty).
    Raises ValueError for e.g. a Year that isn't a number, KeyError for a
    field that can't be edited."""
    if key in EDITABLE_FLAG_FIELDS:
        return int(bool(value))
    if key in EDITABLE_TIME_FIELDS:
        return None if value in (None, "") else max(0, int(value))
    if key == "volume_adjustment":
        return max(-100, min(100, int(value or 0)))
    if key == "equalizer":
        name = " ".join(str(value or "").split())          # a preset name; empty = None
        return name or None
    if key == "media_kind":
        if value not in MEDIA_KINDS:
            raise ValueError(f"unknown media kind {value!r}")
        return value
    text = "" if value is None else str(value).strip()
    if key in EDITABLE_NUMBER_FIELDS:
        if not text:
            return None
        number = int(text)          # ValueError if not a whole number
        if number < 0:
            raise ValueError(f"{key} can't be negative")
        return number
    if key == "lyrics":
        return str(value).replace("\r\n", "\n") if value else None   # keep line breaks
    if key in EDITABLE_TEXT_FIELDS:
        return text or None
    raise KeyError(f"{key} can't be edited")


SORTABLE_FIELDS = ("name", "artist", "album_artist", "album", "composer")


def update_track_fields(conn: sqlite3.Connection, track_id: int, fields: dict) -> dict:
    """Save edited song details. Fields that live in the file's tags are also
    remembered in `dirty_fields`, so they get written into the file (now, or
    later if the file can't be written right now). Returns the saved values."""
    from luxtunes.core.tags import TAG_FIELDS

    clean = {k: clean_field_value(k, v) for k, v in fields.items()}
    if not clean:
        return {}
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if row is None:
        return {}
    if "media_kind" in clean and clean["media_kind"] != row["media_kind"]:
        # Becoming a podcast or audiobook: remember position, skip in shuffle
        # (unless this edit sets those too).
        for key, value in kind_defaults(clean["media_kind"]).items():
            clean.setdefault(key, value)
    # Renaming e.g. the Artist makes its old Sort Artist wrong ("The 1980" would
    # still sort as "1975"), so a sort field is cleared when its field changes,
    # unless you set the sort field in the same edit.
    for key in SORTABLE_FIELDS:
        sort_key = f"sort_{key}"
        if key in clean and sort_key not in clean and clean[key] != row[key] and row[sort_key]:
            clean[sort_key] = None
    tag_keys = {k for k in clean if k in TAG_FIELDS or k == "lyrics"}
    if tag_keys:
        pending = set(json.loads(row["dirty_fields"])) if row["dirty_fields"] else set()
        clean["dirty_fields"] = json.dumps(sorted(pending | tag_keys))
        clean["tags_dirty"] = 1
    clean["date_modified"] = now_utc()
    assignments = ", ".join(f"{k} = ?" for k in clean)
    with conn:
        conn.execute(f"UPDATE tracks SET {assignments} WHERE id = ?", [*clean.values(), track_id])
    return clean


def pending_tag_fields(conn: sqlite3.Connection, track_id: int) -> set[str]:
    """Which tag fields still need writing into this song's file."""
    row = conn.execute("SELECT tags_dirty, dirty_fields FROM tracks WHERE id = ?",
                       (track_id,)).fetchone()
    if row is None or not row[0]:
        return set()
    if row[1]:
        return set(json.loads(row[1]))
    # Edited before LuxTunes tracked which fields changed: write the fields
    # that could be edited in the list back then.
    return {"name", "artist", "album_artist", "album", "genre", "composer", "grouping",
            "comments", "sort_name", "sort_artist", "sort_album_artist", "sort_album",
            "sort_composer", "year", "bpm"}


def tracks_with_pending_tags(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT id FROM tracks WHERE tags_dirty = 1")]


def mark_tags_written(conn: sqlite3.Connection, track_id: int, size: int | None) -> dict:
    fields = {"tags_dirty": 0, "dirty_fields": None}
    if size is not None:
        fields["size"] = size
    with conn:
        conn.execute("UPDATE tracks SET tags_dirty = 0, dirty_fields = NULL, size = COALESCE(?, size) "
                     "WHERE id = ?", (size, track_id))
    return fields


def set_artwork_count(conn: sqlite3.Connection, track_id: int, count: int) -> None:
    with conn:
        conn.execute("UPDATE tracks SET artwork_count = ? WHERE id = ?", (count, track_id))


def save_bookmark(conn: sqlite3.Connection, track_id: int, position_ms: int) -> None:
    """Remember where a song was stopped ("Remember playback position")."""
    with conn:
        conn.execute("UPDATE tracks SET bookmark_time = ? WHERE id = ?", (position_ms, track_id))


def all_genres(conn: sqlite3.Connection) -> list[str]:
    """Every genre in the library, A-Z (for Get Info's Genre list)."""
    rows = conn.execute("SELECT DISTINCT genre FROM tracks WHERE genre IS NOT NULL AND genre != ''")
    return sorted((r[0] for r in rows), key=str.casefold)


def load_track(conn: sqlite3.Connection, track_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return dict(row) if row else None


def rename_equalizer_preset(conn: sqlite3.Connection, old: str, new: str | None) -> list[int]:
    """A preset was renamed (or deleted: new is None): update the songs using it.
    Returns their ids."""
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM tracks WHERE equalizer = ? COLLATE NOCASE", (old,))]
    if ids:
        with conn:
            conn.execute("UPDATE tracks SET equalizer = ? WHERE equalizer = ? COLLATE NOCASE",
                         (new, old))
    return ids


def delete_playlist(conn: sqlite3.Connection, playlist_id: int) -> None:
    """Delete a playlist (its songs stay in the library). Deleting a folder
    deletes the playlists and folders inside it too."""
    doomed, todo = [], [playlist_id]
    while todo:
        current = todo.pop()
        doomed.append(current)
        todo.extend(r[0] for r in conn.execute(
            "SELECT c.id FROM playlists c JOIN playlists p "
            "ON c.parent_persistent_id = p.persistent_id WHERE p.id = ?", (current,)))
    with conn:
        conn.executemany("DELETE FROM playlists WHERE id = ?", [(i,) for i in doomed])


def create_folder(conn: sqlite3.Connection) -> int:
    """File → New Playlist Folder: an empty "untitled folder" (2, 3, ...)."""
    from luxtunes.core.db import new_persistent_id

    taken = {r[0] for r in conn.execute("SELECT name FROM playlists")}
    name, n = "untitled folder", 2
    while name in taken:
        name, n = f"untitled folder {n}", n + 1
    with conn:
        cursor = conn.execute(
            "INSERT INTO playlists (persistent_id, name, is_folder, position) VALUES (?, ?, 1, "
            "(SELECT COALESCE(MAX(position) + 1, 0) FROM playlists))",
            (new_persistent_id(), name))
    return cursor.lastrowid


def playlist_name_for(tracks: list[dict]) -> str | None:
    """New Playlist from Selection's name: "Artist - Album" when every song is
    from the same album, otherwise None ("untitled playlist")."""
    albums = {(t.get("album") or "").strip() for t in tracks}
    if len(albums) != 1 or "" in albums:
        return None
    artists = {(t.get("album_artist") or t.get("artist") or "").strip() for t in tracks}
    album = albums.pop()
    artist = artists.pop() if len(artists) == 1 else ""
    return f"{artist} - {album}" if artist else album


def duplicate_playlist(conn: sqlite3.Connection, playlist_id: int) -> int:
    """Copy a playlist and its songs as "<name> copy" (a smart playlist's copy
    has the same rules). Returns the new playlist's id."""
    name = conn.execute("SELECT name FROM playlists WHERE id = ?", (playlist_id,)).fetchone()[0]
    taken = {r[0] for r in conn.execute("SELECT name FROM playlists")}
    new_name, n = f"{name} copy", 2
    while new_name in taken:
        new_name, n = f"{name} copy {n}", n + 1
    new_id = create_playlist(conn, new_name)
    smart_row = conn.execute("SELECT is_smart, smart_info, smart_criteria FROM playlists WHERE id = ?",
                             (playlist_id,)).fetchone()
    if smart_row[0]:                         # a smart playlist's copy is smart too
        with conn:
            conn.execute("UPDATE playlists SET is_smart = 1, smart_info = ?, smart_criteria = ? "
                         "WHERE id = ?", (smart_row[1], smart_row[2], new_id))
    track_ids = [r[0] for r in conn.execute(
        "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position", (playlist_id,))]
    add_to_playlist(conn, new_id, track_ids)
    return new_id


def set_playlist_order(conn: sqlite3.Connection, playlist_id: int, track_ids: list[int]) -> None:
    """Copy To Play Order: the playlist's songs in this order (songs not
    listed keep their place after them)."""
    current = [r[0] for r in conn.execute(
        "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position", (playlist_id,))]
    remaining = list(current)
    order = []
    for track_id in track_ids:
        if track_id in remaining:              # each listed once per time it's in the playlist
            remaining.remove(track_id)
            order.append(track_id)
    order += remaining
    with conn:
        conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        conn.executemany("INSERT INTO playlist_items (playlist_id, position, track_id) VALUES (?, ?, ?)",
                         [(playlist_id, i, t) for i, t in enumerate(order)])


def export_playlist(tracks: list[dict], path: str, kind: str) -> None:
    """Export…: "m3u8" (UTF-8), "m3u" (Latin-1, what old players expect) or
    "txt" (tab-separated: Name, Artist, Album, Genre, Time, Location)."""
    if kind == "txt":
        lines = ["Name\tArtist\tAlbum\tGenre\tTime\tLocation"]
        for t in tracks:
            seconds = (t.get("total_time") or 0) // 1000
            lines.append("\t".join(str(v or "").replace("\t", " ") for v in (
                t.get("name"), t.get("artist"), t.get("album"), t.get("genre"),
                f"{seconds // 60}:{seconds % 60:02d}", t.get("location"))))
        text, encoding = "\n".join(lines) + "\n", "utf-8"
    else:
        lines = ["#EXTM3U"]
        for t in tracks:
            if not t.get("location"):
                continue
            seconds = (t.get("total_time") or 0) // 1000
            title = " - ".join(x for x in (t.get("artist"), t.get("name")) if x)
            lines += [f"#EXTINF:{seconds},{title}", t["location"]]
        text = "\n".join(lines) + "\n"
        encoding = "utf-8" if kind == "m3u8" else "latin-1"
    with open(path, "w", encoding=encoding, errors="replace") as f:
        f.write(text)


def default_library_name() -> str:
    """"Alex's Library": your first name from the system account, else the username."""
    import getpass
    import pwd
    try:
        user = pwd.getpwuid(os.getuid())
        name = (user.pw_gecos.split(",")[0].split() or [""])[0] or user.pw_name
    except (KeyError, OSError):
        name = getpass.getuser()
    return f"{name}’s Library"


def library_name(conn: sqlite3.Connection) -> str:
    """Preferences → General → Library Name (shown in the window's title)."""
    row = conn.execute("SELECT value FROM library_info WHERE key = 'library_name'").fetchone()
    return row[0] if row and row[0] else default_library_name()


def set_library_name(conn: sqlite3.Connection, name: str) -> None:
    with conn:
        conn.execute("INSERT INTO library_info (key, value) VALUES ('library_name', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (name.strip(),))
