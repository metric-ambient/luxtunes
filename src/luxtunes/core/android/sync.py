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
                              ratings since the last sync; added to the library
                              here, then emptied
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
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from luxtunes import __version__
from luxtunes.core import artwork as artwork_reader
from luxtunes.core import library, organize
from luxtunes.core.android.device import Phone
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
CHANGES = "changes.json"
MANIFEST = ".luxtunes-sync.json"
FORMAT_VERSION = 1


# ---- The library's own id (so a phone knows which library it came from) ------------------

def load_record(conn: sqlite3.Connection, phone: Phone) -> ipod_settings.DeviceRecord:
    """The phone's settings record. Its name comes from the phone only the first
    time; after that a rename in the sidebar is kept."""
    known = conn.execute("SELECT 1 FROM devices WHERE device_key = ?", (phone.key,)).fetchone()
    if known:
        return ipod_settings.load_device(conn, phone.key)
    return ipod_settings.load_device(conn, phone.key, phone.name, phone.model_name, phone.serial)


def library_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM library_info WHERE key = 'library_id'").fetchone()
    if row:
        return row[0]
    new = secrets.token_hex(8).upper()
    with conn:
        conn.execute("INSERT INTO library_info (key, value) VALUES ('library_id', ?)", (new,))
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


def _copy(source: str, root: Path, relative) -> None:
    target = contained(root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    _delete(root, relative)
    try:
        shutil.copyfile(source, target)
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


# ---- Plays, skips and ratings from a player on the phone --------------------------------------

def _unix(text) -> int:
    if not text:
        return 0
    try:
        return int(datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def read_changes(root: Path) -> list[stats.IPodTrackStats]:
    """changes.json, in the same shape the iPod's play counts take, so the
    same rules apply (plays and skips only ever added; ratings only when
    changed on the phone)."""
    try:
        data = json.loads((root / CHANGES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = []
    for entry in data.get("tracks") or []:
        try:
            dbid = int(entry["id"], 16)
        except (KeyError, TypeError, ValueError):
            continue
        rated = "rating" in entry
        items.append(stats.IPodTrackStats(
            dbid=dbid, recent_plays=max(0, int(entry.get("plays") or 0)),
            recent_skips=max(0, int(entry.get("skips") or 0)),
            last_played=_unix(entry.get("last_played")), last_skipped=_unix(entry.get("last_skipped")),
            rating=int(entry.get("rating") or 0) if rated else 0,
            rating_at_sync=-1 if rated else 0))
    return items


def clear_changes(root: Path) -> None:
    """After the changes are in the library: an empty file, so they can't count twice."""
    if (root / CHANGES).exists():
        _write(root, CHANGES, json.dumps({"version": FORMAT_VERSION, "tracks": []}).encode())


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
    "sort_album_artist", "sort_composer", "enabled")

SCHEMA = """
CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE tracks (id TEXT PRIMARY KEY, path TEXT NOT NULL, artwork TEXT, {columns});
CREATE TABLE playlists (id TEXT PRIMARY KEY, name TEXT NOT NULL, is_smart INTEGER,
                        position INTEGER, file TEXT);
CREATE TABLE playlist_items (playlist_id TEXT, position INTEGER, track_id TEXT);
CREATE INDEX playlist_items_by_list ON playlist_items (playlist_id, position);
"""


def build_database(path: str, conn: sqlite3.Connection, placed: dict, covers: dict,
                   playlists: list[dict], lists_files: dict) -> None:
    """placed: persistent id -> phone path; covers: album folder -> cover path."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tracks)")}
    columns = [c for c in TRACK_COLUMNS if c in existing]
    out = sqlite3.connect(path)
    try:
        out.executescript(SCHEMA.format(columns=", ".join(columns)))
        info = {"format_version": str(FORMAT_VERSION), "library_id": library_id(conn),
                "library_name": library.library_name(conn), "luxtunes_version": __version__,
                "synced_at": library.now_utc()}
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
            out.execute("INSERT INTO playlists VALUES (?, ?, ?, ?, ?)",
                        (entry["persistent_id"], entry["name"], int(entry.get("is_smart") or 0),
                         position, lists_files.get(entry["persistent_id"])))
            ids = [t["persistent_id"] for t in library.load_tracks_by_ids(conn, entry["track_ids"])]
            out.executemany("INSERT INTO playlist_items VALUES (?, ?, ?)",
                            [(entry["persistent_id"], i, pid) for i, pid in enumerate(ids)
                             if pid in placed])
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
         progress=None, confirm=None) -> SyncReport:
    """Sync this phone. progress(text, done, total) may return False to stop
    copying; confirm(adding, removing, removals) is asked before anything on the
    phone changes. Raises OSError if the phone can't be written to."""
    report = SyncReport()
    settings = record.settings

    def tell(text, done=0, total=0, can_stop=True):
        if progress is not None and progress(text, done, total) is False and can_stop:
            raise SyncCancelled()

    storage = phone.storage(settings.android_storage)
    if storage is None:
        raise OSError("Can't see the phone's storage. Unlock the phone and choose "
                      "“File transfer” in its USB notification.")
    root = Path(storage) / FOLDER
    tell("Reading the phone…")
    (root / MEDIA).mkdir(parents=True, exist_ok=True)     # there even before the first song
    manifest = read_manifest(root)
    files: dict = manifest["files"]

    # 1. Plays, skips and ratings from a player on the phone.
    report.stats = stats.merge(conn, read_changes(root))

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
        report.cancelled = True
        return report

    # 3. Take off what's no longer wanted (only ever inside the LuxTunes folder).
    gone = [(files[pid]["path"], pid, files[pid].get("name")) for pid in to_remove] + \
           [(p, None, Path(p).name) for p in strays]
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
            report.problems.append(f"{name}: {error.strerror or error}")
            continue
        _remove_empty_folders(root, relative)
        if pid is not None:
            del files[pid]
        report.removed += 1
    clear_changes(root)                   # (the changes are in the library now)

    # 4. Copy new and changed songs.
    taken = {f["path"].casefold() for f in files.values()}      # incl. kept ones
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
            tell(f"Copying “{track.get('name')}”", i, len(to_copy))
            old = files.get(pid)
            if old:
                taken.discard(old["path"].casefold())
                try:
                    _delete(root, old["path"])
                except OSError:
                    pass
            relative = phone_path(track, Path(sending).suffix.lower(), taken)
            try:
                _copy(sending, root, relative)
            except OSError as error:
                report.problems.append(f"{track.get('name')}: {error.strerror or error}")
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
            report.added += 1
    except SyncCancelled:
        report.cancelled = True
    finally:
        scratch.cleanup()
        _save_manifest(root, manifest, conn)          # what's there now, even if stopped

    placed = {pid: f["path"] for pid, f in files.items()}
    if report.cancelled:
        return report

    # 5. Album covers (for players that look for cover.jpg next to the songs).
    tell("Adding album covers…", can_stop=False)
    by_pid = {t["persistent_id"]: t for t in library.load_tracks(conn) if t.get("persistent_id")}
    covers = _write_covers(root, placed, by_pid, manifest, report)

    # 6. Playlists as .m3u8 files.
    tell("Writing playlists…", can_stop=False)
    lists_files = _write_playlists(conn, root, the_plan.playlists, placed, manifest)
    report.playlists = len(lists_files)

    # 7. The database for players that want everything.
    tell("Writing the library database…", can_stop=False)
    with tempfile.TemporaryDirectory(prefix="luxtunes-android-db-") as folder:
        local = os.path.join(folder, DATABASE)
        build_database(local, conn, placed, covers, the_plan.playlists, lists_files)
        _copy(local, root, DATABASE)
    _save_manifest(root, manifest, conn)
    ipod_settings.mark_synced(conn, record.id)          # a rename stays in LuxTunes
    log.info("synced %s: +%d -%d, %d playlists", phone.name, report.added, report.removed,
             report.playlists)
    return report


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
                  report: SyncReport) -> dict:
    """One cover file per album folder, from the first song's artwork."""
    covers = {}
    old = set(manifest.get("covers") or [])
    by_folder: dict[str, str] = {}
    for pid, path in placed.items():
        by_folder.setdefault(str(Path(path).parent), pid)
    for folder, pid in sorted(by_folder.items()):
        track = tracks.get(pid)
        if track is None or (track.get("media_kind") or "music") not in library.AUDIO_KINDS:
            continue
        existing = next((c for c in old if str(Path(c).parent) == folder), None)
        if existing and (root / existing).exists():
            covers[folder] = existing
            continue
        art = artwork_reader.read_embedded_artwork(find_file(track.get("location")))
        if not art:
            continue
        name = "cover.png" if art[:4] == b"\x89PNG" else "cover.jpg"
        relative = f"{folder}/{name}"
        try:
            _write(root, relative, art)
            covers[folder] = relative
        except OSError as error:
            report.problems.append(f"Cover for {folder}: {error.strerror or error}")
    for gone in old - set(covers.values()):
        try:
            _delete(root, gone)
            _remove_empty_folders(root, gone)
        except OSError:
            pass
    manifest["covers"] = sorted(covers.values())
    return covers


def _write_playlists(conn, root: Path, playlists: list[dict], placed: dict,
                     manifest: dict) -> dict:
    """Returns playlist persistent id -> its file (relative to the LuxTunes folder)."""
    written = {}
    names = set()
    for entry in playlists:
        name = organize.safe_name(entry["name"] or "Playlist", organize.FOLDER_MAX, True)
        while name.casefold() in names:
            name += " 2"
        names.add(name.casefold())
        relative = f"{PLAYLISTS}/{name}.m3u8"
        tracks = library.load_tracks_by_ids(conn, entry["track_ids"])
        _write(root, relative, playlist_text(entry, tracks, placed).encode("utf-8"))
        written[entry["persistent_id"]] = relative
    for gone in set(manifest.get("playlists") or []) - set(written.values()):
        try:
            _delete(root, gone)
        except OSError:
            pass
    manifest["playlists"] = sorted(written.values())
    return written
