"""Import an iTunes library from "iTunes Music Library.xml".

iTunes rewrites that XML every time the library changes. It's an Apple
"property list" (plist): one big dictionary with a "Tracks" dictionary
(keyed by Track ID) and a "Playlists" list. Python's built-in plistlib reads
it straight into dicts, lists, ints, strings and datetimes.

Running an import twice is safe: tracks and playlists are matched by their
iTunes Persistent ID and updated in place.
"""

from __future__ import annotations

import json
import os
import plistlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from luxtunes.core.library import UNSUPPORTED_KINDS
from luxtunes.core.paths import resolve_case_insensitive

# iTunes XML key  ->  column in the `tracks` table.
TRACK_FIELDS = {
    "Persistent ID": "persistent_id",
    "Name": "name",
    "Artist": "artist",
    "Album Artist": "album_artist",
    "Album": "album",
    "Composer": "composer",
    "Genre": "genre",
    "Grouping": "grouping",
    "Comments": "comments",
    "Year": "year",
    "Release Date": "release_date",
    "Track Number": "track_number",
    "Track Count": "track_count",
    "Disc Number": "disc_number",
    "Disc Count": "disc_count",
    "BPM": "bpm",
    "Compilation": "compilation",
    "Explicit": "explicit",
    "Part Of Gapless Album": "part_of_gapless_album",
    "Sort Name": "sort_name",
    "Sort Artist": "sort_artist",
    "Sort Album Artist": "sort_album_artist",
    "Sort Album": "sort_album",
    "Sort Composer": "sort_composer",
    "Rating": "rating",
    "Rating Computed": "rating_computed",
    "Album Rating": "album_rating",
    "Album Rating Computed": "album_rating_computed",
    "Play Count": "play_count",
    "Play Date UTC": "play_date",
    "Skip Count": "skip_count",
    "Skip Date": "skip_date",
    "Date Added": "date_added",
    "Date Modified": "date_modified",
    "Kind": "kind",
    "Size": "size",
    "Total Time": "total_time",
    "Bit Rate": "bit_rate",
    "Sample Rate": "sample_rate",
    "Volume Adjustment": "volume_adjustment",
    "Start Time": "start_time",
    "Stop Time": "stop_time",
    "Artwork Count": "artwork_count",
    "Purchased": "purchased",
    "Track ID": "itunes_track_id",
    "Series": "show",
    "Season": "season",
    "Episode Order": "episode_number",
    "Episode": "episode_id",
}

# Keys we read but deliberately don't store anywhere.
IGNORED_KEYS = {
    "Location",              # handled separately (converted to a Linux path)
    "Disabled",              # handled separately (-> enabled = 0)
    "Play Date",             # same as "Play Date UTC", in Mac local-time seconds
    "Track Type",            # always "File" for local files
    "File Folder Count",     # iTunes-internal bookkeeping
    "Library Folder Count",
    "Has Video", "Movie", "TV Show", "HD", "Video Width", "Video Height",  # -> media_kind
    "Podcast",                                                              # -> media_kind
}


@dataclass
class ImportResult:
    tracks: int = 0
    playlists: int = 0
    missing_files: list[str] = field(default_factory=list)
    unknown_keys: set[str] = field(default_factory=set)


class PathMapper:
    """Turns iTunes' Windows/Mac file URLs into paths on this computer.

    iTunes stores locations like:
        file://localhost/D:/Music/iTunes/iTunes%20Media/Music/10%20Years/...
    With from_prefix "D:/Music/iTunes/iTunes Media/" and
    to_prefix "~/Music/LuxTunes/LuxTunes Media/", that becomes:
        /home/you/Music/LuxTunes/LuxTunes Media/Music/10 Years/...
    """

    def __init__(self, from_prefix: str, to_prefix: str | os.PathLike):
        self.from_prefix = _url_to_path_text(from_prefix).rstrip("/") + "/"
        self.to_prefix = Path(os.path.expanduser(str(to_prefix)))

    def map(self, location_url: str) -> str:
        path = _url_to_path_text(location_url)
        # Windows paths are case-insensitive, so compare the prefix that way.
        if path.lower().startswith(self.from_prefix.lower()):
            relative = path[len(self.from_prefix):]
            return str(resolve_case_insensitive(self.to_prefix / relative))
        return path  # outside the iTunes Media folder: keep as-is, flagged missing later


def _url_to_path_text(url: str) -> str:
    """'file://localhost/D:/a%20b/c.mp3' -> 'D:/a b/c.mp3' (also accepts plain paths)."""
    if url.startswith("file://localhost/"):
        url = url[len("file://localhost/"):]
    elif url.startswith("file://"):
        url = url[len("file://"):]
    path = unquote(url).replace("\\", "/")
    # Mac paths keep their leading "/", Windows paths start with a drive letter.
    return path


def _to_db_value(value):
    """Convert plist values into something SQLite can store."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, datetime):
        # plistlib returns naive datetimes that are actually UTC.
        return value.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def _json_default(value):
    if isinstance(value, datetime):
        return _to_db_value(value)
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(type(value))


def read_library(xml_path: str | os.PathLike) -> dict:
    with open(xml_path, "rb") as f:
        return plistlib.load(f)


def import_library(
    conn: sqlite3.Connection,
    xml_path: str | os.PathLike,
    media_folder: str | os.PathLike,
    itunes_media_folder: str | None = None,
) -> ImportResult:
    """Import every track and playlist from an iTunes XML file.

    media_folder: where the "iTunes Media" folder's contents now live on this
        computer, e.g. "~/Music/LuxTunes/LuxTunes Media".
    itunes_media_folder: the old location, as iTunes saw it. Defaults to the
        "Music Folder" recorded in the XML, e.g. "D:/Music/iTunes/iTunes Media/".
    """
    library = read_library(xml_path)
    mapper = PathMapper(itunes_media_folder or library["Music Folder"], media_folder)
    result = ImportResult()

    with conn:  # one transaction: all or nothing
        track_id_map = _import_tracks(conn, library.get("Tracks", {}), mapper, result)
        _import_playlists(conn, library.get("Playlists", []), track_id_map, result)
        for key, value in {
            "itunes_library_persistent_id": library.get("Library Persistent ID"),
            "itunes_application_version": library.get("Application Version"),
            "itunes_music_folder": library.get("Music Folder"),
            "media_folder": str(mapper.to_prefix),
            "imported_at": _to_db_value(datetime.now(timezone.utc).replace(tzinfo=None)),
        }.items():
            conn.execute(
                "INSERT INTO library_info (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    return result


def _media_kind(itunes_track: dict) -> str:
    """music / movie / tvshow / podcast / audiobook, from what the XML says."""
    if itunes_track.get("TV Show"):
        return "tvshow"
    if itunes_track.get("Podcast"):
        return "podcast"
    if itunes_track.get("Has Video") or itunes_track.get("Movie"):
        return "movie"
    kind = (itunes_track.get("Kind") or "").lower()
    location = (itunes_track.get("Location") or "").lower()
    if "book" in kind or "audible" in kind or location.endswith((".m4b", ".aa", ".aax")):
        return "audiobook"
    return "music"


def _import_tracks(conn, tracks: dict, mapper: PathMapper, result: ImportResult) -> dict[int, int]:
    """Insert/update tracks. Returns {iTunes Track ID: LuxTunes track id}."""
    id_map: dict[int, int] = {}
    for itunes_track in tracks.values():
        row = {}
        extra = {}
        for key, value in itunes_track.items():
            if key in TRACK_FIELDS:
                row[TRACK_FIELDS[key]] = _to_db_value(value)
            elif key not in IGNORED_KEYS:
                extra[key] = value
                result.unknown_keys.add(key)

        row["media_kind"] = _media_kind(itunes_track)
        row["enabled"] = 0 if itunes_track.get("Disabled") else 1
        row["extra_json"] = json.dumps(extra, default=_json_default) if extra else None

        location = itunes_track.get("Location")
        if location:
            row["location"] = mapper.map(location)
            if not os.path.exists(row["location"]):
                result.missing_files.append(row["location"])

        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "persistent_id")
        conn.execute(
            f"INSERT INTO tracks ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(persistent_id) DO UPDATE SET {updates}",
            [row[c] for c in columns],
        )
        db_id = conn.execute(
            "SELECT id FROM tracks WHERE persistent_id = ?", (row["persistent_id"],)
        ).fetchone()[0]
        id_map[itunes_track["Track ID"]] = db_id
        result.tracks += 1
    return id_map


def _import_playlists(conn, playlists: list, track_id_map: dict[int, int], result: ImportResult):
    for position, pl in enumerate(playlists):
        if pl.get("Distinguished Kind") in UNSUPPORTED_KINDS:
            continue  # Purchased (no store in LuxTunes) and iTunes DJ
        row = {
            "persistent_id": pl["Playlist Persistent ID"],
            "name": pl.get("Name", "Untitled Playlist"),
            "parent_persistent_id": pl.get("Parent Persistent ID"),
            "is_folder": int(bool(pl.get("Folder"))),
            "is_master": int(bool(pl.get("Master"))),
            "distinguished_kind": pl.get("Distinguished Kind"),
            "is_smart": int("Smart Criteria" in pl),
            "smart_info": pl.get("Smart Info"),
            "smart_criteria": pl.get("Smart Criteria"),
            "position": position,
        }
        columns = list(row)
        updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "persistent_id")
        conn.execute(
            f"INSERT INTO playlists ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT(persistent_id) DO UPDATE SET {updates}",
            [row[c] for c in columns],
        )
        playlist_id = conn.execute(
            "SELECT id FROM playlists WHERE persistent_id = ?", (row["persistent_id"],)
        ).fetchone()[0]

        # Replace the playlist's contents with what the XML says.
        # (For smart playlists this is a snapshot of their current contents.)
        conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (playlist_id,))
        items = [
            (playlist_id, i, track_id_map[item["Track ID"]])
            for i, item in enumerate(pl.get("Playlist Items", []))
            if item["Track ID"] in track_id_map
        ]
        conn.executemany(
            "INSERT INTO playlist_items (playlist_id, position, track_id) VALUES (?, ?, ?)",
            items,
        )
        result.playlists += 1
