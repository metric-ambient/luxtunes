"""Syncing the library to an Android device: plain files any music player can
use, plus a small database for players that want everything LuxTunes knows.

On the phone's chosen storage (its own, or an SD card):

    LuxTunes/
        LuxTunes Media/       the files, filed like the computer's media folder:
            Music/<Artist>/<Album>/<disc-track Name>.mp3   (+ cover.jpg per album)
            Movies/  TV Shows/  Podcasts/  Audiobooks/
        Playlists/<name>.m3u8 one per synced playlist (paths relative to it)
        library.db            SQLite: every song's details, ratings, plays,
                              lyrics, playlists (see docs/android-library-format.md)
        changes.json          written by a player on the phone: plays, skips and
                              ratings since the last sync, playlist changes and
                              songs added there; added to the library here, then
                              emptied
        .luxtunes-sync.json   what LuxTunes put there

Nothing outside the LuxTunes folder is ever written or deleted (see
contained()), and the library's own files are never changed. With "Delete
media not in this selection" on, media no longer chosen is taken off. The
same sync settings as an iPod's choose what goes (all music or selected
playlists/artists/albums/genres, movies, TV shows, podcasts, audiobooks),
and "Convert higher bit rate songs" works the same.
No Qt in here.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import sqlite3
import struct
import tempfile
import time
from pathlib import Path

from luxtunes import __version__
from luxtunes.core import artwork as artwork_reader
from luxtunes.core import library, organize
from luxtunes.core.android import changes as phone_changes
from luxtunes.core.android.changes import CHANGES
from luxtunes.core.android.device import Phone, storage_kind
from luxtunes.core.db import get_setting, set_setting
from luxtunes.core.ipod import plan as ipod_plan
from luxtunes.core.ipod import settings as ipod_settings
from luxtunes.core.ipod import stats
from luxtunes.core.ipod.sync import Removal, SyncCancelled, SyncReport, _convert_for_ipod
from luxtunes.core.paths import find_file

log = logging.getLogger(__name__)

FOLDER = "LuxTunes"
MEDIA = "LuxTunes Media"
PLAYLISTS = "Playlists"
DATABASE = "library.db"
MANIFEST = ".luxtunes-sync.json"
SAVE_EVERY = 100            # songs between saves of the record during a sync
FORMAT_VERSION = 2          # 2: smart rules, reference playlists, playlists back


# ---- The library's own id (so a phone knows which library it came from) ------------------

def load_record(conn: sqlite3.Connection, phone: Phone) -> ipod_settings.DeviceRecord:
    """The phone's settings record. Its name comes from the phone only the first
    time; after that a rename in the sidebar is kept."""
    known = conn.execute("SELECT 1 FROM devices WHERE device_key = ?", (phone.key,)).fetchone()
    if known:
        return ipod_settings.load_device(conn, phone.key)
    return ipod_settings.load_device(conn, phone.key, phone.name, phone.model_name, phone.serial)


def library_id(conn: sqlite3.Connection) -> str:
    known = get_setting(conn, "library_id")
    if known:
        return known
    new = secrets.token_hex(8).upper()
    set_setting(conn, "library_id", new)
    return new


# ---- What's on the phone ---------------------------------------------------------------

def read_manifest(root: Path) -> dict:
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("files", {})
    data.setdefault("covers", [])
    data.setdefault("playlists", [])
    return data


class OutsideFolder(OSError):
    """A path that isn't inside the LuxTunes folder: never written or deleted."""


def contained(root: Path, relative) -> Path:
    """root/relative, but only if it's inside the LuxTunes folder (after following
    any links, so a link can't lead elsewhere). Every write and delete on the
    phone goes through here: nothing outside that folder is ever touched."""
    if Path(root).name != FOLDER:
        raise OutsideFolder(f"{root} isn't a LuxTunes folder")
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, relative))
    if target == base or os.path.commonpath([base, target]) != base:
        raise OutsideFolder(f"{relative}: not inside the LuxTunes folder, left alone")
    return Path(target)


def _delete(root: Path, relative) -> None:
    """Remove a file inside the LuxTunes folder (gone already is fine)."""
    path = contained(root, relative)
    if path.is_dir():
        raise OutsideFolder(f"{relative} is a folder, left alone")
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _write(root: Path, relative, data: bytes) -> None:
    """Write a file in the LuxTunes folder. Phones can't change a file in
    place, so an old one is removed first."""
    path = contained(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    _delete(root, relative)
    with open(path, "wb") as out:
        out.write(data)


CHUNK = 4 * 1024 * 1024


def _copy(source: str, root: Path, relative, stop=None) -> None:
    """Copy a file into the LuxTunes folder. With `stop` (a threading.Event),
    a piece at a time, so Stop (or closing LuxTunes) doesn't wait for a whole
    movie: the half-copied file is removed and SyncCancelled raised."""
    target = contained(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    _delete(root, relative)
    try:
        if stop is None:
            shutil.copyfile(source, target)
        else:
            with open(source, "rb") as src, open(target, "wb") as dst:
                while True:
                    if stop.is_set():
                        raise SyncCancelled()
                    block = src.read(CHUNK)
                    if not block:
                        break
                    dst.write(block)
    except BaseException:
        try:
            _delete(root, relative)          # no half-copied song left behind
        except OSError:
            pass
        raise


def _remove_empty_folders(root: Path, relative: str, stop: str = MEDIA) -> None:
    """Remove the folders above a removed file while they're empty, up to (not
    including) root/stop."""
    stop_path = Path(root) / stop
    folder = Path(root) / Path(relative).parent
    while folder != stop_path and stop_path in folder.parents:
        try:
            contained(root, folder.relative_to(root)).rmdir()     # only works when empty
        except OSError:
            return
        folder = folder.parent


def existing_files(root: Path) -> set[str]:
    """Paths (relative to the LuxTunes folder) of the files in its media folder."""
    found = set()
    media = root / MEDIA
    for folder, _, files in os.walk(media):
        for name in files:
            found.add(str((Path(folder) / name).relative_to(root)))
    return found


# ---- What the phone sends back --------------------------------------------------------------

def _empty_changes(root: Path, merged: phone_changes.PhoneChanges) -> None:
    """Right after merging: empty changes.json, so nothing counts twice, keeping
    anything the phone added since it was read."""
    if merged.text is None:
        if not (root / CHANGES).exists():
            # None yet: an empty one, ready for a player on the phone to fill.
            _write(root, CHANGES, json.dumps(phone_changes.emptied(merged, None)).encode())
        return                                    # (an unreadable file is left alone)
    try:
        now = (root / CHANGES).read_text(encoding="utf-8")
    except OSError:
        now = None
    _write(root, CHANGES, json.dumps(phone_changes.emptied(merged, now)).encode("utf-8"))


def _log_phone_changes(conn, root: Path, merged) -> None:
    """What the phone sent, for the terminal (run LuxTunes from one to see it)."""
    if merged.text is None:
        log.info("no %s on the phone (%s): nothing to bring back", CHANGES, root / CHANGES)
        return
    entries = sum(1 for _ in merged.tracks)
    known = sum(1 for t in merged.tracks if conn.execute(
        "SELECT 1 FROM tracks WHERE persistent_id = ?",
        (stats.persistent_id(t.dbid),)).fetchone())
    same = merged.library_id == library_id(conn)
    log.info("%s on the phone: %d song entries (%d found in this library), %d playlist "
             "changes, library_id %s", CHANGES, entries, known, len(merged.playlists),
             "matches" if same else f"{merged.library_id!r} doesn't match {library_id(conn)!r}")


def _import_phone_songs(conn, root: Path, merged, manifest: dict, record, report,
                        tell) -> None:
    """Songs added on the phone (format v2 "added_tracks"): each file is copied
    into the library as Add to Library would, under the phone's id, and
    recorded as if this sync had put it there, so it isn't copied again or
    offered for removal. Runs before the plays and playlists, which may use
    these ids."""
    if not merged.added:
        return
    if merged.library_id != library_id(conn):
        report.problems.append("Songs added on the phone were added for a different LuxTunes "
                               "library, so they weren't brought in.")
        return
    from luxtunes.core import add_files
    from luxtunes.core.tags import TagError
    files = manifest["files"]
    media_root = organize.media_folder(conn)
    known = None
    for i, added in enumerate(merged.added):
        label = added.details.get("name") or Path(added.path).stem
        tell("Adding songs from the phone…", i, len(merged.added), can_stop=False)
        try:
            source = contained(root, added.path)
        except OutsideFolder:
            report.problems.append(f"“{label}” added on the phone: not inside the LuxTunes "
                                   "folder, so it wasn't brought in.")
            continue
        if not source.is_file():
            continue                                  # deleted on the phone before this sync
        row = conn.execute("SELECT id, name, location FROM tracks WHERE upper(persistent_id) = ?",
                           (added.id,)).fetchone()
        if row is not None:
            if (files.get(added.id) or {}).get("path") == added.path:
                continue                              # brought in by an earlier sync
            if not _same_size(row[2], source):
                report.problems.append(f"“{label}” added on the phone wasn't brought in: its id "
                                       f"is already used by “{row[1]}” in your library.")
                continue
            track_id = row[0]                         # brought in by a sync that was cut off
        else:
            if known is None:
                known = add_files._known_locations(conn)
            try:
                track_id = add_files.add_file(
                    conn, str(source), True, media_root, known, details={"media_kind": "music"},
                    persistent_id=added.id, fallback=added.details, date_added=added.date_added)
            except add_files.AlreadyAdded:
                report.problems.append(f"“{label}” added on the phone is already in your "
                                       "library, so it wasn't added again.")
                continue
            except (TagError, OSError, ValueError) as error:
                log.warning("couldn't bring in %s from the phone: %s", added.path, error)
                report.problems.append(f"“{label}” added on the phone couldn't be brought in: "
                                       f"{getattr(error, 'strerror', None) or error}")
                continue
            report.imported.append(track_id)
        track = conn.execute("SELECT name, artist, album, location FROM tracks WHERE id = ?",
                             (track_id,)).fetchone()
        try:
            st = os.stat(track[3])
            on_phone = source.stat().st_size
        except (OSError, TypeError):
            continue
        files[added.id] = {"path": added.path, "size": on_phone, "source_size": st.st_size,
                           "source_mtime": st.st_mtime_ns, "name": track[0],
                           "artist": track[1], "album": track[2]}
    in_library = {r[0] for r in conn.execute("SELECT persistent_id FROM tracks")}
    brought = [pid for pid in files if pid in {a.id for a in merged.added}]
    settings = record.settings
    if brought and settings.sync_music and settings.music_mode == "selected":
        settings.phone_songs = [p for p in dict.fromkeys(settings.phone_songs + brought)
                                if p in in_library]
        ipod_settings.save_settings(conn, record.id, settings)
    elif brought and not settings.sync_music:
        report.problems.append("Songs added on the phone were added to your library, but Sync "
                               "Music is off for this phone, so they won't stay on it.")
    _save_manifest(root, manifest, conn)
    log.info("songs added on the phone: %d brought in", len(report.imported))


def _phone_value(key: str, value):
    """A value from the phone's Get Info as Get Info here would take it
    (ValueError if it can't be used)."""
    if key in phone_changes.EDITED_TEXT:
        if not isinstance(value, str):
            raise ValueError("not text")
        if key == "name" and not value.strip():
            raise ValueError("a song needs a name")
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("not a whole number")
    if key == "compilation":
        if value not in (0, 1):
            raise ValueError("not 0 or 1")
        return value
    return "" if value == 0 else value            # 0: none


def _apply_phone_edits(conn, merged, report) -> None:
    """Song details changed in the phone's Get Info (format v2 "edited_tracks"),
    saved exactly as Get Info here saves them: sort names follow, the tags are
    marked to be written into the file, date_modified is set. The phone's value
    wins. Runs after songs added on the phone, which may have been edited too."""
    if not merged.edited:
        return
    if merged.library_id != library_id(conn):
        report.problems.append("Song details edited on the phone were for a different "
                               "LuxTunes library, so they weren't applied.")
        return
    for edit in merged.edited:
        row = conn.execute("SELECT id, name FROM tracks WHERE upper(persistent_id) = ?",
                           (edit.id,)).fetchone()
        if row is None:
            report.problems.append(f"Details edited on the phone for a song that isn't in "
                                   f"your library ({edit.id}) were skipped.")
            continue
        fields = {}
        for key, value in edit.fields.items():
            try:
                fields[key] = library.clean_field_value(key, _phone_value(key, value))
            except (ValueError, KeyError) as error:
                report.problems.append(f"“{row[1]}”: the {key.replace('_', ' ')} edited on the "
                                       f"phone wasn't used ({error}).")
        # update_track_fields cleans again; values already clean stay the same.
        if fields and library.update_track_fields(conn, row[0], fields):
            report.edited.append(row[0])
    log.info("song details edited on the phone: %d songs updated", len(report.edited))


def _same_size(location: str | None, source: Path) -> bool:
    try:
        return bool(location) and os.path.getsize(location) == source.stat().st_size
    except OSError:
        return False


def _apply_phone_equalizer(conn, merged, report) -> None:
    """The equalizer as changed on the phone becomes LuxTunes' (the phone's
    change wins: it's the newer one in practice)."""
    if merged.equalizer is None:
        return
    if merged.library_id != library_id(conn):
        report.problems.append("Equalizer changes on the phone were made for a different "
                               "LuxTunes library, so they weren't applied.")
        return
    if phone_changes.apply_equalizer(conn, merged):
        report.equalizer_from_phone = True
        log.info("equalizer taken from the phone (%d presets)", len(merged.equalizer.presets))


def _apply_phone_playlists(conn, root: Path, merged, manifest: dict, record, report,
                           confirm_playlists) -> None:
    """Playlists made, changed or deleted on the phone (format v2)."""
    if not merged.playlists:
        return
    if merged.library_id != library_id(conn):
        report.problems.append("Playlist changes on the phone were made for a different "
                               "LuxTunes library, so they weren't applied.")
        return
    sent = {p.upper() for p in manifest.get("sent_playlists") or []}
    on_phone = {pid.upper() for pid in manifest["files"]}
    doomed = phone_changes.deletions(conn, merged, sent)
    allow = True
    if doomed and confirm_playlists is not None:
        allow = bool(confirm_playlists(doomed))
    result = phone_changes.apply_playlists(conn, merged, sent, on_phone, allow, report.problems)
    report.phone_playlists = result
    settings = record.settings
    if result.new_ids:
        if not settings.sync_music:
            report.problems.append(
                "Playlists made on the phone were added to your library, but Sync Music is "
                "off for this phone, so they won't go back to it: " + ", ".join(result.created))
        elif settings.music_mode != "all":
            # A new playlist must stay on the phone that made it.
            settings.playlists = list(dict.fromkeys(settings.playlists + result.new_ids))
            ipod_settings.save_settings(conn, record.id, settings)
    log.info("phone playlists: %d created, %d changed, %d deleted, %d kept", len(result.created),
             len(result.updated), len(result.deleted), len(result.kept))


# ---- Where each song goes ------------------------------------------------------------------

def phone_path(track: dict, extension: str, taken: set[str]) -> str:
    """"LuxTunes Media/Music/Artist/Album/01 Name.mp3" (relative to the LuxTunes folder)."""
    parts = organize.relative_parts(track)
    stem = "/".join([MEDIA, *parts])
    path = stem + extension
    n = 2
    while path.casefold() in taken:
        path = f"{stem} {n}{extension}"
        n += 1
    taken.add(path.casefold())
    return path


class PhoneNames:
    """Phone storage ignores the case of names, but the computer's view of it
    doesn't: making "Crown The Empire" next to an existing "Crown the Empire"
    fails with an input/output error. So each part of a path is spelled the
    way it already is on the phone (listings are read once and remembered)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._folders: dict[str, dict[str, str]] = {}

    def _names(self, parent: str) -> dict[str, str]:
        if parent not in self._folders:
            try:
                names = os.listdir(self.root / parent) if parent else os.listdir(self.root)
            except OSError:
                names = []
            self._folders[parent] = {n.casefold(): n for n in names}
        return self._folders[parent]

    def resolve(self, relative: str) -> str:
        done: list[str] = []
        for part in relative.split("/"):
            done.append(self._names("/".join(done)).get(part.casefold(), part))
        return "/".join(done)

    def add(self, relative: str) -> None:
        """A file was written there (its folders exist now too)."""
        parts = relative.split("/")
        for i, part in enumerate(parts):
            self._names("/".join(parts[:i])).setdefault(part.casefold(), part)


def transfer_kbps(song: dict, settings) -> int | None:
    """Android plays FLAC, Ogg and the rest, so only "Convert higher bit rate
    songs" ever converts."""
    if not settings.convert:
        return None
    return ipod_plan.transfer_kbps(song, settings)


# ---- The database for players on the phone ------------------------------------------------

TRACK_COLUMNS = (
    "media_kind", "name", "artist", "album_artist", "album", "genre", "composer", "grouping",
    "year", "track_number", "track_count", "disc_number", "disc_count", "total_time", "bit_rate",
    "sample_rate", "size", "rating", "rating_computed", "album_rating", "play_count", "play_date",
    "skip_count", "skip_date", "date_added", "compilation", "comments", "lyrics", "bookmarkable",
    "bookmark_time", "skip_when_shuffling", "start_time", "stop_time", "volume_adjustment",
    "show", "season", "episode_number", "sort_name", "sort_artist", "sort_album",
    "sort_album_artist", "sort_composer", "enabled",
    # v2: everything else a smart playlist rule can test (smart.FIELDS)
    "bpm", "kind", "date_modified", "purchased",
    # LuxTunes 0.7.0: the song's own equalizer preset (a name in equalizer_presets)
    "equalizer",
    # LuxTunes 0.8.0: Sound Check's loudness correction in dB (NULL: not known yet)
    "loudness")

# The equalizer's bands as equalizer_presets columns.
EQ_COLUMNS = ("band_32", "band_64", "band_125", "band_250", "band_500", "band_1k", "band_2k",
              "band_4k", "band_8k", "band_16k")

SCHEMA = """
CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE tracks (id TEXT PRIMARY KEY, path TEXT NOT NULL, artwork TEXT, {columns});
CREATE TABLE playlists (id TEXT PRIMARY KEY, name TEXT NOT NULL, is_smart INTEGER,
                        position INTEGER, file TEXT, smart_info BLOB, smart_criteria BLOB);
CREATE TABLE playlist_items (playlist_id TEXT, position INTEGER, track_id TEXT);
CREATE INDEX playlist_items_by_list ON playlist_items (playlist_id, position);
CREATE TABLE reference_playlists (id TEXT PRIMARY KEY, name TEXT NOT NULL, is_smart INTEGER,
                                  is_folder INTEGER);
CREATE TABLE reference_items (playlist_id TEXT, track_id TEXT);
CREATE TABLE equalizer_presets (name TEXT PRIMARY KEY, position INTEGER, preamp REAL,
                                {eq_columns});
"""


def _write_equalizer(out, conn) -> None:
    """The equalizer: on/off and the chosen preset in info, every preset (and
    the Manual values, position NULL) in equalizer_presets."""
    from luxtunes.core import equalizer
    settings = equalizer.load(conn)
    # equalizer_from_phone: this LuxTunes takes the phone's equalizer changes
    # (changes.json "equalizer"), so the phone can drop its own copy.
    out.executemany("INSERT INTO info VALUES (?, ?)",
                    [("equalizer_on", "1" if settings.on else "0"),
                     ("equalizer_preset", settings.current.name),
                     ("equalizer_from_phone", "1")])
    rows = [(p.name, i, p.preamp, *p.bands) for i, p in enumerate(settings.presets)]
    rows.append((equalizer.MANUAL, None, settings.manual.preamp, *settings.manual.bands))
    marks = ", ".join("?" for _ in range(3 + len(EQ_COLUMNS)))
    out.executemany(f"INSERT INTO equalizer_presets VALUES ({marks})", rows)


def _referenced(group, found: set[str]) -> set[str]:
    """Persistent ids that "Playlist is …" rules in this group point to."""
    from luxtunes.core import smart
    for rule in group.rules:
        if isinstance(rule, smart.Group):
            _referenced(rule, found)
        elif rule.known and rule.field == 0x28 and rule.value:
            found.add(str(rule.value).upper())
    return found


def _write_references(out, conn, playlists: list[dict], placed: dict) -> None:
    """Playlists that synced smart playlists' rules point to but that aren't
    synced themselves: their songs (those on the phone), for evaluating the
    rules there. Not shown as playlists."""
    from luxtunes.core import smart
    synced = {p["persistent_id"].upper() for p in playlists}
    wanted: set[str] = set()
    for p in playlists:
        if p.get("is_smart"):
            row = conn.execute("SELECT smart_info, smart_criteria FROM playlists WHERE "
                               "persistent_id = ?", (p["persistent_id"],)).fetchone()
            try:
                rules = smart.decode(row[0], row[1]).rules if row else None
            except (smart.SmartFormatError, struct.error):
                rules = None
            if rules is not None:
                _referenced(rules, wanted)
    by_id = {r[0]: r[1] for r in conn.execute("SELECT id, persistent_id FROM tracks")}
    now = time.time()
    for pid in sorted(wanted - synced):
        row = conn.execute("SELECT name, is_smart, is_folder FROM playlists WHERE "
                           "upper(persistent_id) = ?", (pid,)).fetchone()
        if row is None:
            continue
        out.execute("INSERT INTO reference_playlists VALUES (?, ?, ?, ?)",
                    (pid, row[0], int(row[1] or 0), int(row[2] or 0)))
        members = smart.playlist_members(conn, pid, now)
        out.executemany("INSERT INTO reference_items VALUES (?, ?)",
                        [(pid, by_id[t]) for t in sorted(members)
                         if by_id.get(t) in placed])


def build_database(path: str, conn: sqlite3.Connection, placed: dict, covers: dict,
                   playlists: list[dict], lists_files: dict) -> None:
    """placed: persistent id -> phone path; covers: album folder -> cover path."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    columns = [c for c in TRACK_COLUMNS if c in existing]
    out = sqlite3.connect(path)
    try:
        out.executescript(SCHEMA.format(columns=", ".join(columns),
                                        eq_columns=", ".join(f"{c} REAL" for c in EQ_COLUMNS)))
        info = {"format_version": str(FORMAT_VERSION), "library_id": library_id(conn),
                "library_name": library.library_name(conn), "luxtunes_version": __version__,
                "synced_at": library.now_utc(),
                # This LuxTunes takes songs added on the phone (changes.json
                # "added_tracks") and details edited there ("edited_tracks").
                "phone_imports": "1", "phone_edits": "1"}
        out.executemany("INSERT INTO info VALUES (?, ?)", info.items())
        by_pid = {t["persistent_id"]: t for t in library.load_tracks(conn)}
        rows = []
        for pid, path_on_phone in placed.items():
            track = by_pid.get(pid)
            if track is None:
                continue
            cover = covers.get(str(Path(path_on_phone).parent))
            rows.append((pid, path_on_phone, cover, *(track.get(c) for c in columns)))
        marks = ", ".join("?" for _ in range(3 + len(columns)))
        out.executemany(f"INSERT INTO tracks VALUES ({marks})", rows)
        for position, entry in enumerate(playlists):
            blobs = conn.execute("SELECT smart_info, smart_criteria FROM playlists WHERE "
                                 "persistent_id = ? AND is_smart", (entry["persistent_id"],)
                                 ).fetchone() or (None, None)
            out.execute("INSERT INTO playlists VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (entry["persistent_id"], entry["name"], int(entry.get("is_smart") or 0),
                         position, lists_files.get(entry["persistent_id"]), blobs[0], blobs[1]))
            ids = [t["persistent_id"] for t in library.load_tracks_by_ids(conn, entry["track_ids"])]
            out.executemany("INSERT INTO playlist_items VALUES (?, ?, ?)",
                            [(entry["persistent_id"], i, pid) for i, pid in enumerate(ids)
                             if pid in placed])
        _write_references(out, conn, playlists, placed)
        _write_equalizer(out, conn)
        out.commit()
    finally:
        out.close()


def playlist_text(entry: dict, tracks: list[dict], placed: dict) -> str:
    """An .m3u8 playlist; paths are relative to the Playlists folder."""
    lines = ["#EXTM3U", f"#PLAYLIST:{entry['name']}"]
    for t in tracks:
        path = placed.get(t["persistent_id"])
        if not path:
            continue
        seconds = round((t.get("total_time") or 0) / 1000)
        who = t.get("artist") or ""
        lines.append(f"#EXTINF:{seconds},{who + ' - ' if who else ''}{t.get('name') or ''}")
        lines.append("../" + path)
    return "\n".join(lines) + "\n"


# ---- The sync ---------------------------------------------------------------------------

def sync(conn: sqlite3.Connection, phone: Phone, record: ipod_settings.DeviceRecord,
         progress=None, confirm=None, confirm_playlists=None, stop=None) -> SyncReport:
    """Sync this phone. progress(text, done, total) may return False to stop
    copying; confirm(adding, removing, removals) is asked before anything on the
    phone changes; confirm_playlists(names) before deleting playlists the phone
    deleted (no: they're kept, and go back to the phone). stop: a threading.Event
    that also cuts a long copy short. Raises OSError if the phone can't be
    written to."""
    report = SyncReport()
    settings = record.settings

    def tell(text, done=0, total=0, can_stop=True):
        if progress is not None and progress(text, done, total) is False and can_stop:
            raise SyncCancelled()

    storage = phone.storage(settings.android_storage, settings.android_storage_kind)
    if settings.android_storage_kind == "removable" and storage is not None and \
            device_storage_kind(phone, storage) != "removable":
        raise OSError(f"The SD card chosen for this phone (“{settings.android_storage}”) "
                      "isn't there. Put it back, or choose another storage on the phone's page.")
    if storage is None:
        raise OSError("Can't see the phone's storage. Unlock the phone and choose "
                      "“File transfer” in its USB notification.")
    root = Path(storage) / FOLDER
    tell("Reading the phone…")
    (root / MEDIA).mkdir(parents=True, exist_ok=True)     # there even before the first song
    manifest = read_manifest(root)
    files: dict = manifest["files"]

    # 1. What a player on the phone sends back: songs added there, details
    # edited there, plays, skips and ratings, then its playlist changes; then
    # changes.json is emptied straight away.
    merged = phone_changes.read(root)
    report.problems += merged.problems
    _log_phone_changes(conn, root, merged)
    _import_phone_songs(conn, root, merged, manifest, record, report, tell)
    _apply_phone_edits(conn, merged, report)
    report.stats = stats.merge(conn, merged.tracks)
    log.info("from the phone: %d plays, %d skips, %d ratings added to the library",
             report.stats.plays, report.stats.skips, report.stats.ratings)
    _apply_phone_playlists(conn, root, merged, manifest, record, report, confirm_playlists)
    _apply_phone_equalizer(conn, merged, report)
    _empty_changes(root, merged)

    # 2. What should be there.
    from luxtunes.core import smart
    smart.refresh_all(conn)
    clean = settings.android_delete_unselected
    on_phone = sum(int(f.get("size") or 0) for f in files.values())
    room = ipod_plan_space(storage, on_phone)
    the_plan = ipod_plan.build_plan(conn, settings, free_space=room, kbps_for=transfer_kbps,
                                    phone=True)
    wanted = {t["persistent_id"]: t for t in the_plan.tracks if t.get("persistent_id")}
    if not clean and room is not None:
        # What's kept but no longer chosen still takes up room.
        kept = sum(int(f.get("size") or 0) for pid, f in files.items() if pid not in wanted)
        if kept:
            the_plan = ipod_plan.build_plan(conn, settings, free_space=room - kept,
                                            kbps_for=transfer_kbps, phone=True)
            wanted = {t["persistent_id"]: t for t in the_plan.tracks if t.get("persistent_id")}
    report.problems += [f"{t.get('name')}: file not found" for t in the_plan.missing]
    present = existing_files(root)

    def changed(pid: str, track: dict) -> bool:
        entry = files.get(pid)
        if entry is None or entry.get("path") not in present:
            return True
        source = find_file(track.get("location"))
        try:
            st = os.stat(source) if source else None
        except OSError:
            st = None
        return st is not None and (st.st_size != entry.get("source_size")
                                   or st.st_mtime_ns != entry.get("source_mtime"))

    # With "Delete media not in this selection" on: whatever was synced before
    # but isn't chosen now, and stray files in LuxTunes Media that no sync put
    # there. Off: those stay. Either way, nothing outside the LuxTunes folder.
    to_remove = [pid for pid in files if pid not in wanted] if clean else []
    ours = {f["path"].casefold() for f in files.values()} | \
        {c.casefold() for c in manifest.get("covers") or []}
    strays = sorted(p for p in present if p.casefold() not in ours
                    and not Path(p).name.startswith(".")) if clean else []
    to_copy = [t for pid, t in wanted.items() if changed(pid, t)]
    in_library = {r[0] for r in conn.execute("SELECT persistent_id FROM tracks")}
    removals = [Removal(files[pid].get("name") or "", files[pid].get("artist") or "",
                        files[pid].get("album") or "",
                        "Not in your sync settings" if pid in in_library
                        else "Not in your LuxTunes library")
                for pid in to_remove]
    removals += [Removal(Path(p).name, "", str(Path(p).parent.relative_to(MEDIA)),
                         "Not synced by LuxTunes") for p in strays]
    if confirm is not None and not confirm(len(to_copy), len(removals), removals):
        # Nothing on the phone changes, but its database still gets brought up
        # to date with what's there (the phone's own changes are in already).
        report.cancelled = True
        _finish(conn, root, phone, record, report, manifest, the_plan, tell, stopped=True,
                on_phone=present)
        return report

    # 3. Take off what's no longer wanted (only ever inside the LuxTunes folder).
    gone = [(files[pid]["path"], pid, files[pid].get("name")) for pid in to_remove] + \
           [(p, None, Path(p).name) for p in strays]
    stuck: list[str] = []
    for i, (relative, pid, name) in enumerate(gone):
        tell("Removing media from the phone…", i, len(gone), can_stop=False)
        try:
            _delete(root, relative)
        except OutsideFolder as error:
            report.problems.append(f"{name}: {error}")
            if pid is not None:
                del files[pid]               # a bad entry in the record: forget it
            continue
        except OSError as error:
            if pid is None:
                stuck.append(name)           # a leftover file the phone won't let go of
                log.warning("couldn't remove leftover %s from the phone: %s", relative, error)
            else:
                report.problems.append(f"{name}: {error.strerror or error}")
            continue
        _remove_empty_folders(root, relative)
        if pid is not None:
            del files[pid]
        report.removed += 1
    if stuck:
        report.problems.append(
            f"{len(stuck):,} leftover file{'s' if len(stuck) != 1 else ''} on the phone couldn't "
            "be removed (" + ", ".join(stuck[:5]) + (", …" if len(stuck) > 5 else "") + "). "
            "These are usually left by an earlier copy that failed. Your songs aren't affected. "
            "To clear them, unplug the phone, restart it, and sync again.")

    # 4. Copy new and changed songs.
    taken = {f["path"].casefold() for f in files.values()}      # incl. kept ones
    fresh: set[str] = set()                   # album folders that got songs this sync
    new_covers: set[str] = set()              # covers written this sync
    names = PhoneNames(root)
    scratch = tempfile.TemporaryDirectory(prefix="luxtunes-android-")
    try:
        for i, track in enumerate(to_copy):
            pid = track["persistent_id"]
            source = find_file(track.get("location"))
            if source is None:
                report.problems.append(f"{track.get('name')}: file not found")
                continue
            kbps = transfer_kbps(track, settings)
            sending = source
            if kbps is not None:
                tell(f"Converting “{track.get('name')}”", i, len(to_copy))
                sending = _convert_for_ipod(source, kbps, scratch.name, report, track)
                if sending is None:
                    continue
                _tag_converted(sending, source, track)
            tell(f"Copying “{track.get('name')}”", i, len(to_copy))
            old = files.get(pid)
            if old:
                taken.discard(old["path"].casefold())
            relative = names.resolve(phone_path(track, Path(sending).suffix.lower(), taken))
            try:
                _copy(sending, root, relative, stop)
            except OSError as error:
                report.problems.append(f"{track.get('name')}: {error.strerror or error}")
                if old and old["path"].casefold() != relative.casefold():
                    taken.add(old["path"].casefold())     # the old copy stays for now
                else:
                    files.pop(pid, None)
                continue
            finally:
                if sending != source:
                    try:
                        os.remove(sending)
                    except OSError:
                        pass
            st = os.stat(source)
            files[pid] = {"path": relative, "size": os.path.getsize(root / relative)
                          if kbps is not None else st.st_size,
                          "source_size": st.st_size, "source_mtime": st.st_mtime_ns,
                          "name": track.get("name"), "artist": track.get("artist"),
                          "album": track.get("album")}
            names.add(relative)
            if old and old["path"].casefold() != relative.casefold():
                # Moved (e.g. its album was renamed): the old copy goes only
                # now that the new one is there.
                try:
                    _delete(root, old["path"])
                    _remove_empty_folders(root, old["path"])
                except OSError:
                    pass
            report.added += 1
            # The album's cover goes with its first song, so it's there even if
            # the sync is stopped later.
            fresh.add(str(Path(relative).parent))
            _add_cover(root, relative, track, source, manifest, report, new_covers)
            if report.added % SAVE_EVERY == 0:
                # Now and then, the record of what's here (if LuxTunes is cut
                # off, the next sync knows these songs are already there).
                _save_manifest(root, manifest, conn)
    except SyncCancelled:
        report.cancelled = True
    finally:
        scratch.cleanup()
        _save_manifest(root, manifest, conn)          # what's there now, even if stopped

    # 5–7. Covers, playlists and the database, also after Stop: they describe
    # exactly what's on the phone now, so a player can use the songs copied so far.
    _finish(conn, root, phone, record, report, manifest, the_plan, tell,
            stopped=report.cancelled, fresh=fresh, on_phone=present | new_covers)
    return report


def _tag_converted(converted: str, source: str, track: dict) -> None:
    """A converted copy has no tags (FFmpeg drops them): write the library's
    details, lyrics and the original's artwork into it, so players on the
    phone show them. The library's own file is never touched."""
    from luxtunes.core import tags as tag_io
    fields = {k: track.get(k) for k in tag_io.TAG_FIELDS}
    art = artwork_reader.read_embedded_artwork(source)
    try:
        tag_io.write_tags(converted, fields,
                          lyrics=track.get("lyrics") or tag_io.UNCHANGED,
                          artworks=[art] if art else tag_io.UNCHANGED)
    except (tag_io.TagError, OSError, ValueError) as error:
        log.warning("couldn't tag the converted copy of %s: %s", source, error)


def _cover_name(folder: str, art: bytes) -> str:
    name = "cover.png" if art[:4] == b"\x89PNG" else "cover.jpg"
    return f"{folder}/{name}"


def _add_cover(root: Path, relative: str, track: dict, source: str, manifest: dict,
               report: SyncReport, new_covers: set | None = None) -> None:
    """cover.jpg for this song's album folder, if it has none yet."""
    if (track.get("media_kind") or "music") not in library.AUDIO_KINDS:
        return
    folder = str(Path(relative).parent)
    covers = manifest.setdefault("covers", [])
    if any(str(Path(c).parent) == folder for c in covers):
        return
    art = artwork_reader.read_embedded_artwork(source)
    if not art:
        return                       # another song of the album may have one (see _write_covers)
    cover = _cover_name(folder, art)
    try:
        _write(root, cover, art)
        covers.append(cover)
        if new_covers is not None:
            new_covers.add(cover)
    except OSError as error:
        report.problems.append(f"Cover for {folder}: {error.strerror or error}")


def _finish(conn, root: Path, phone: Phone, record, report: SyncReport, manifest: dict,
            the_plan, tell, stopped: bool, fresh: set[str] = frozenset(),
            on_phone: set[str] | None = None) -> None:
    placed = {pid: f["path"] for pid, f in manifest["files"].items()}

    # 5. Album covers (for players that look for cover.jpg next to the songs).
    # After Stop, only the covers already there (no artwork is read, to stop quickly).
    by_pid = {t["persistent_id"]: t for t in library.load_tracks(conn) if t.get("persistent_id")}
    covers = _write_covers(root, placed, by_pid, manifest, report, read_new=not stopped,
                           fresh=fresh, on_phone=on_phone,
                           tell=lambda done, total: tell("Adding album covers…", done, total,
                                                         can_stop=False))

    # 6. Playlists as .m3u8 files.
    tell("Writing playlists…", can_stop=False)
    lists_files = _write_playlists(conn, root, the_plan.playlists, placed, manifest)
    manifest["sent_playlists"] = [p["persistent_id"] for p in the_plan.playlists]
    report.playlists = len(lists_files)

    # 7. The database for players that want everything.
    tell("Writing the library database…", can_stop=False)
    with tempfile.TemporaryDirectory(prefix="luxtunes-android-db-") as folder:
        local = os.path.join(folder, DATABASE)
        build_database(local, conn, placed, covers, the_plan.playlists, lists_files)
        _copy(local, root, DATABASE)
    _save_manifest(root, manifest, conn)
    if not stopped:
        ipod_settings.mark_synced(conn, record.id)      # a rename stays in LuxTunes
    log.info("%s %s: +%d -%d, %d playlists", "stopped syncing" if stopped else "synced",
             phone.name, report.added, report.removed, report.playlists)


def device_storage_kind(phone: Phone, folder: str) -> str:
    name = next((n for n, f in phone.storages.items() if f == folder), "")
    return storage_kind(name)


def media_bytes(storage: str | None) -> int:
    """How much LuxTunes has put on this storage (from its own record there)."""
    if not storage:
        return 0
    files = read_manifest(Path(storage) / FOLDER)["files"]
    return sum(int(f.get("size") or 0) for f in files.values())


def ipod_plan_space(storage: str, on_phone: int) -> int | None:
    """Room for songs: free space plus what LuxTunes already put there."""
    try:
        return shutil.disk_usage(storage).free + on_phone
    except OSError:
        return None


def _save_manifest(root: Path, manifest: dict, conn) -> None:
    manifest["version"] = FORMAT_VERSION
    manifest["library_id"] = library_id(conn)
    _write(root, MANIFEST, json.dumps(manifest, indent=1).encode("utf-8"))


def _write_covers(root: Path, placed: dict, tracks: dict, manifest: dict,
                  report: SyncReport, read_new: bool = True, fresh=frozenset(),
                  on_phone: set[str] | None = None, tell=None) -> dict:
    """One cover file per album folder, from the artwork of the first of its
    songs that has some. read_new False: only keep the covers already there.
    Folders where no song had artwork are remembered (no_cover) and only looked
    at again when one of their songs was copied (fresh).

    on_phone: the files known to be on the phone (read once at the start of
    the sync, plus covers written since). Asking the phone about each cover
    instead takes a second or more per album over USB: many minutes for a big
    library."""
    covers = {}
    old = {str(Path(c).parent): c for c in manifest.get("covers") or []}
    no_cover = set(manifest.get("no_cover") or [])
    there = {p.casefold() for p in on_phone} if on_phone is not None else None

    def exists(relative: str) -> bool:
        if there is not None:
            return relative.casefold() in there
        return (root / relative).exists()

    by_folder: dict[str, list[str]] = {}
    for pid, path in sorted(placed.items(), key=lambda item: item[1]):
        by_folder.setdefault(str(Path(path).parent), []).append(pid)
    still_none = set()
    for done, (folder, pids) in enumerate(sorted(by_folder.items())):
        if tell is not None and done % 25 == 0:
            tell(done, len(by_folder))
        songs = [tracks[p] for p in pids if p in tracks and
                 (tracks[p].get("media_kind") or "music") in library.AUDIO_KINDS]
        if not songs:
            continue
        existing = old.get(folder)
        if existing and exists(existing):
            covers[folder] = existing
            continue
        if not read_new or (folder in no_cover and folder not in fresh):
            if folder in no_cover:
                still_none.add(folder)
            continue
        art = None
        for song in songs:
            art = artwork_reader.read_embedded_artwork(find_file(song.get("location")))
            if art:
                break
        if not art:
            still_none.add(folder)
            continue
        relative = _cover_name(folder, art)
        try:
            _write(root, relative, art)
            covers[folder] = relative
        except OSError as error:
            report.problems.append(f"Cover for {folder}: {error.strerror or error}")
    for gone in set(old.values()) - set(covers.values()):
        try:
            _delete(root, gone)
            _remove_empty_folders(root, gone)
        except OSError:
            pass
    manifest["covers"] = sorted(covers.values())
    manifest["no_cover"] = sorted(still_none)
    return covers


def _write_playlists(conn, root: Path, playlists: list[dict], placed: dict,
                     manifest: dict) -> dict:
    """Returns playlist persistent id -> its file (relative to the LuxTunes folder).
    A playlist file that hasn't changed since the last sync isn't written again
    (each write to the phone takes a moment)."""
    import hashlib
    written = {}
    names = set()
    old_hashes = manifest.get("playlist_hashes") or {}
    hashes = {}
    for entry in playlists:
        name = organize.safe_name(entry["name"] or "Playlist", organize.FOLDER_MAX, True)
        while name.casefold() in names:
            name += " 2"
        names.add(name.casefold())
        relative = f"{PLAYLISTS}/{name}.m3u8"
        tracks = library.load_tracks_by_ids(conn, entry["track_ids"])
        data = playlist_text(entry, tracks, placed).encode("utf-8")
        digest = hashlib.sha1(data).hexdigest()
        if old_hashes.get(relative) != digest or not (root / relative).exists():
            _write(root, relative, data)
        hashes[relative] = digest
        written[entry["persistent_id"]] = relative
    for gone in set(manifest.get("playlists") or []) - set(written.values()):
        try:
            _delete(root, gone)
        except OSError:
            pass
    manifest["playlists"] = sorted(written.values())
    manifest["playlist_hashes"] = hashes
    return written
