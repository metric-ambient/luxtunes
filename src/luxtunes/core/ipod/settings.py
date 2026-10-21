"""Each iPod's sync settings: what the device page's tabs are set to.

Kept in the library's `devices` table (as JSON), under the iPod's FireWire
GUID, so they're remembered between plug-ins.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field, fields

from luxtunes.core.library import now_utc

MOVIE_RULES = ["all", "1 most recent", "3 most recent", "5 most recent", "10 most recent",
               "all unwatched", "1 most recent unwatched", "3 most recent unwatched",
               "5 most recent unwatched", "10 most recent unwatched",
               "1 least recent unwatched", "3 least recent unwatched",
               "5 least recent unwatched", "10 least recent unwatched"]
EPISODE_RULES = ["all", "all unwatched", "1 newest", "3 newest", "5 newest", "10 newest",
                 "1 oldest unwatched", "3 oldest unwatched", "5 oldest unwatched",
                 "10 oldest unwatched"]
PODCAST_RULES = ["all", "all unplayed", "1 most recent", "3 most recent", "5 most recent",
                 "10 most recent", "1 least recent unplayed", "3 least recent unplayed"]
PODCAST_SCOPES = ["all podcasts", "selected podcasts"]
BIT_RATES = [128, 192, 256]


@dataclass
class SyncSettings:
    # Summary tab
    only_checked: bool = False          # "Sync only checked songs and videos"
    convert: bool = False               # "Convert higher bit rate songs to ... AAC"
    convert_kbps: int = 128
    manual: bool = False                # "Manually manage music and videos"
    disk_use: bool = False              # "Enable disk use"
    # Music tab
    sync_music: bool = True
    music_mode: str = "all"             # "all" (entire library) or "selected"
    include_music_videos: bool = True
    autofill: bool = False              # "Automatically fill free space with songs"
    playlists: list = field(default_factory=list)   # persistent ids
    artists: list = field(default_factory=list)
    albums: list = field(default_factory=list)      # "Album Artist\x1fAlbum"
    genres: list = field(default_factory=list)
    # Movies, TV Shows, Podcasts and Books are opt-in on a new device: only
    # music syncs until you tick them (saved devices keep their own choices).
    # Movies tab
    sync_movies: bool = False
    movies_auto: bool = True
    movies_rule: str = "all"
    movies: list = field(default_factory=list)      # persistent ids, picked by hand
    # TV Shows tab
    sync_tv: bool = False
    tv_auto: bool = True
    tv_rule: str = "all unwatched"
    # Podcasts tab: "Automatically include <rule> episodes of <scope>", plus
    # shows and single episodes ticked by hand
    sync_podcasts: bool = False
    podcasts_auto: bool = True
    podcasts_rule: str = "all unplayed"
    podcasts_scope: str = "all podcasts"           # or "selected podcasts"
    podcast_shows: list = field(default_factory=list)       # show names
    podcast_episodes: list = field(default_factory=list)    # persistent ids
    # Books tab
    sync_books: bool = False
    books_mode: str = "all"                        # "all" or "selected"
    books: list = field(default_factory=list)      # book keys ("Author\x1fBook")
    # Games tab (none yet)
    sync_games: bool = True
    # Photos tab
    sync_photos: bool = False
    photos_folder: str = field(default_factory=lambda: os.path.expanduser("~/Pictures"))
    photos_mode: str = "all"            # "all" folders or "selected"
    photo_folders: list = field(default_factory=list)
    full_resolution: bool = False
    # Android devices: which storage the LuxTunes folder goes on (its name, e.g.
    # "Internal shared storage" or the SD card's; empty = the phone's own), and
    # whether that's the phone's own ("internal") or a removable card, so the
    # choice holds even if the card shows up under another name
    android_storage: str = ""
    android_storage_kind: str = ""
    # Android devices: take off media that's no longer chosen (only ever from
    # the LuxTunes folder). Off: it stays until this is turned on.
    android_delete_unselected: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str | None) -> "SyncSettings":
        settings = cls()
        try:
            stored = json.loads(text) if text else {}
        except ValueError:
            stored = {}
        known = {f.name: f for f in fields(cls)}
        for key, value in stored.items():
            if key in known:
                setattr(settings, key, value)
        return settings


@dataclass
class DeviceRecord:
    id: int
    key: str
    name: str | None
    pending_name: str | None
    settings: SyncSettings
    stats_imported: str | None
    last_sync: str | None
    is_new: bool = False


def load_device(conn: sqlite3.Connection, key: str, name: str = "", model: str = "",
                serial: str = "") -> DeviceRecord:
    """This iPod's record, created (with default settings) the first time it's seen."""
    row = conn.execute("SELECT * FROM devices WHERE device_key = ?", (key,)).fetchone()
    is_new = row is None
    if is_new:
        with conn:
            conn.execute("INSERT INTO devices (device_key, name, model, serial, settings_json) "
                         "VALUES (?, ?, ?, ?, ?)", (key, name, model, serial, SyncSettings().to_json()))
        row = conn.execute("SELECT * FROM devices WHERE device_key = ?", (key,)).fetchone()
    elif name and name != row["name"] and not row["pending_name"]:
        with conn:
            conn.execute("UPDATE devices SET name = ? WHERE id = ?", (name, row["id"]))
    shown = row["pending_name"] or name or row["name"]      # a rename waiting for the next sync wins
    return DeviceRecord(row["id"], key, shown, row["pending_name"],
                        SyncSettings.from_json(row["settings_json"]),
                        row["stats_imported"], row["last_sync"], is_new)


def save_settings(conn: sqlite3.Connection, device_id: int, settings: SyncSettings) -> None:
    with conn:
        conn.execute("UPDATE devices SET settings_json = ? WHERE id = ?",
                     (settings.to_json(), device_id))


def rename_device(conn: sqlite3.Connection, device_id: int, name: str) -> None:
    """Renamed in the sidebar: shown right away, written to the iPod at the next sync."""
    name = name.strip()
    if name:
        with conn:
            conn.execute("UPDATE devices SET pending_name = ?, name = ? WHERE id = ?",
                         (name, name, device_id))


def mark_synced(conn: sqlite3.Connection, device_id: int, name: str | None = None) -> None:
    with conn:
        conn.execute("UPDATE devices SET last_sync = ?, pending_name = NULL, "
                     "name = COALESCE(?, name) WHERE id = ?", (now_utc(), name, device_id))


def set_stats_marker(conn: sqlite3.Connection, device_id: int, marker: str | None) -> None:
    with conn:
        conn.execute("UPDATE devices SET stats_imported = ? WHERE id = ?", (marker, device_id))
