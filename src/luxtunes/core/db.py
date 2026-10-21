"""The LuxTunes library database.

Everything LuxTunes knows about your music lives in one SQLite file. The UI,
the importer and the sync engines all read and write through this module.

Conventions used throughout:
  * Dates are stored as ISO-8601 text in UTC, e.g. "2024-11-05T05:19:44Z".
    Text in this format sorts correctly, and is readable in any SQLite viewer.
  * Ratings use a 0-100 scale (20 = 1 star ... 100 = 5 stars). The iPod
    uses the same scale, so no conversion is needed when syncing.
  * Every track and playlist has a `persistent_id`: a 16-character hex string.
    Tracks imported from an iTunes library keep their Persistent ID, so the
    imported library and the iPod can always be matched up.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from pathlib import Path

# Bump this when the schema changes, and add a step to _migrate().
SCHEMA_VERSION = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS library_info (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    id                    INTEGER PRIMARY KEY,
    persistent_id         TEXT NOT NULL UNIQUE,

    -- What the song is
    name                  TEXT,
    artist                TEXT,
    album_artist          TEXT,
    album                 TEXT,
    composer              TEXT,
    genre                 TEXT,
    grouping              TEXT,
    comments              TEXT,
    lyrics                TEXT,       -- read from the file's tags, not the XML
    year                  INTEGER,
    release_date          TEXT,
    track_number          INTEGER,
    track_count           INTEGER,
    disc_number           INTEGER,
    disc_count            INTEGER,
    bpm                   INTEGER,
    compilation           INTEGER NOT NULL DEFAULT 0,
    explicit              INTEGER NOT NULL DEFAULT 0,
    part_of_gapless_album INTEGER NOT NULL DEFAULT 0,

    -- Sorting overrides ("Sort Artist" etc. in Get Info)
    sort_name             TEXT,
    sort_artist           TEXT,
    sort_album_artist     TEXT,
    sort_album            TEXT,
    sort_composer         TEXT,

    -- Your listening history
    rating                INTEGER,    -- 0-100
    rating_computed       INTEGER NOT NULL DEFAULT 0,  -- 1 = inherited from album rating
    album_rating          INTEGER,
    album_rating_computed INTEGER NOT NULL DEFAULT 0,
    play_count            INTEGER NOT NULL DEFAULT 0,
    play_date             TEXT,       -- last played
    skip_count            INTEGER NOT NULL DEFAULT 0,
    skip_date             TEXT,       -- last skipped
    date_added            TEXT,
    date_modified         TEXT,

    -- The file itself
    location              TEXT,       -- absolute path on this computer
    kind                  TEXT,       -- e.g. "MPEG audio file"
    media_kind            TEXT NOT NULL DEFAULT 'music',  -- music / movie
    size                  INTEGER,    -- bytes
    total_time            INTEGER,    -- milliseconds
    bit_rate              INTEGER,    -- kbps
    sample_rate           INTEGER,    -- Hz
    volume_adjustment     INTEGER,
    start_time            INTEGER,    -- ms
    stop_time             INTEGER,    -- ms
    artwork_count         INTEGER NOT NULL DEFAULT 0,
    purchased             INTEGER NOT NULL DEFAULT 0,

    -- Where it came from
    itunes_track_id       INTEGER,    -- Track ID in the iTunes XML (not stable; for reference only)
    extra_json            TEXT        -- any imported fields we don't have a column for yet
);

CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks (album_artist, artist, album);
CREATE INDEX IF NOT EXISTS idx_tracks_location ON tracks (location);

CREATE TABLE IF NOT EXISTS playlists (
    id                    INTEGER PRIMARY KEY,
    persistent_id         TEXT NOT NULL UNIQUE,
    name                  TEXT NOT NULL,
    parent_persistent_id  TEXT,       -- for playlists inside a folder
    is_folder             INTEGER NOT NULL DEFAULT 0,
    is_master             INTEGER NOT NULL DEFAULT 0,  -- the hidden "Library" playlist
    distinguished_kind    INTEGER,    -- imported built-in playlists (Music, Movies, ...)
    is_smart              INTEGER NOT NULL DEFAULT 0,
    smart_info            BLOB,       -- encoded smart-playlist rules (see core/smart.py)
    smart_criteria        BLOB,
    position              INTEGER     -- order in the sidebar
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id  INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    track_id     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    PRIMARY KEY (playlist_id, position)
);

CREATE INDEX IF NOT EXISTS idx_playlist_items_track ON playlist_items (track_id);
"""


def default_db_path() -> Path:
    """Where the library lives unless told otherwise.

    Follows the Linux convention (XDG): ~/.local/share/luxtunes/library.db
    """
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "luxtunes" / "library.db"


def new_persistent_id() -> str:
    """A fresh random 64-bit ID (16 hex digits), for tracks added in LuxTunes."""
    return secrets.token_hex(8).upper()


def connect(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open (and if needed, create) the library database."""
    path = Path(path) if path else default_db_path()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row          # rows behave like dicts: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # safer if LuxTunes crashes mid-write
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"This library was created by a newer LuxTunes (schema {version}); "
            f"this version only understands schema {SCHEMA_VERSION}."
        )
    if version < 1:
        conn.executescript(SCHEMA)
    if version < 2:
        # The checkbox next to each song. Unchecked songs are skipped during
        # playback and (later) not synced, exactly.
        conn.execute("ALTER TABLE tracks ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    if version < 3:
        # LuxTunes has no store and no DJ: drop those imported built-in lists
        # (Purchased = 19, iTunes DJ = 22) if an earlier import brought them in.
        conn.execute("DELETE FROM playlists WHERE distinguished_kind IN (19, 22)")
    if version < 4:
        # 1 = the library has metadata edits not yet written into the music file's
        # tags. The tag writer (Get Info update) writes them and clears the flag.
        conn.execute("ALTER TABLE tracks ADD COLUMN tags_dirty INTEGER NOT NULL DEFAULT 0")
    if version < 5:
        # Get Info: which tag fields still need writing into the file (a JSON
        # list), and the Options tab's "Remember playback position" (with the
        # saved position) and "Skip when shuffling".
        conn.execute("ALTER TABLE tracks ADD COLUMN dirty_fields TEXT")
        conn.execute("ALTER TABLE tracks ADD COLUMN bookmarkable INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE tracks ADD COLUMN bookmark_time INTEGER")
        conn.execute("ALTER TABLE tracks ADD COLUMN skip_when_shuffling INTEGER NOT NULL DEFAULT 0")
    if version < 6:
        # iPods (and later other devices) LuxTunes has seen, with each one's
        # sync settings (the Summary/Music/Movies/... tabs) as JSON.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                id             INTEGER PRIMARY KEY,
                device_key     TEXT NOT NULL UNIQUE,   -- FireWire GUID or serial number
                name           TEXT,
                model          TEXT,
                serial         TEXT,
                settings_json  TEXT,
                pending_name   TEXT,                   -- renamed in LuxTunes, written at next sync
                stats_imported TEXT,                   -- marker of the last Play Counts file read
                last_sync      TEXT
            )""")
    if version < 7:
        # TV shows: their own media kind plus show / season / episode, and
        # the fields the import stored for them (kept in extra_json until now).
        conn.execute("ALTER TABLE tracks ADD COLUMN show TEXT")
        conn.execute("ALTER TABLE tracks ADD COLUMN season INTEGER")
        conn.execute("ALTER TABLE tracks ADD COLUMN episode_number INTEGER")
        conn.execute("ALTER TABLE tracks ADD COLUMN episode_id TEXT")
        import json
        rows = conn.execute("SELECT id, extra_json FROM tracks WHERE extra_json LIKE '%TV Show%' "
                            "OR extra_json LIKE '%Series%'").fetchall()
        for track_id, extra_json in rows:
            try:
                extra = json.loads(extra_json)
            except (TypeError, ValueError):
                continue
            fields = {"show": extra.pop("Series", None), "season": extra.pop("Season", None),
                      "episode_number": extra.pop("Episode Order", None),
                      "episode_id": extra.pop("Episode", None)}
            if extra.pop("TV Show", False):
                fields["media_kind"] = "tvshow"
            fields = {k: v for k, v in fields.items() if v is not None}
            fields["extra_json"] = json.dumps(extra) if extra else None
            assignments = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE tracks SET {assignments} WHERE id = ?", [*fields.values(), track_id])
    if version < 8:
        # Get Info's Equalizer Preset: the name of the song's own preset.
        conn.execute("ALTER TABLE tracks ADD COLUMN equalizer TEXT")
    # Future schema changes go here:
    # if version < 9:
    #     conn.execute("ALTER TABLE tracks ADD COLUMN ...")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def stats(conn: sqlite3.Connection) -> dict:
    """A quick summary of what's in the library."""
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "tracks": one("SELECT COUNT(*) FROM tracks"),
        "music": one("SELECT COUNT(*) FROM tracks WHERE media_kind = 'music'"),
        "rated": one("SELECT COUNT(*) FROM tracks WHERE rating > 0 AND rating_computed = 0"),
        "total_plays": one("SELECT COALESCE(SUM(play_count), 0) FROM tracks"),
        "playlists": one("SELECT COUNT(*) FROM playlists WHERE is_master = 0"),
        "smart_playlists": one("SELECT COUNT(*) FROM playlists WHERE is_smart = 1"),
    }
