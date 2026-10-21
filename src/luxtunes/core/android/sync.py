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
    "bpm", "kind", "date_modified", "purchased")

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
"""


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
         progress=None, confirm=None, confirm_playlists=None) -> SyncReport:
    """Sync this phone. progress(text, done, total) may return False to stop
    copying; confirm(adding, removing, removals) is asked before anything on the
    phone changes; confirm_playlists(names) before deleting playlists the phone
    deleted (no: they're kept, and go back to the phone). Raises OSError if the
    phone can't be written to."""
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

    # 1. What a player on the phone sends back: plays, skips and ratings, then
    # its playlist changes; then changes.json is emptied straight away.
    merged = phone_changes.read(root)
    report.problems += merged.problems
    _log_phone_changes(conn, root, merged)
    report.stats = stats.merge(conn, merged.tracks)
    log.info("from the phone: %d plays, %d skips, %d ratings added to the library",
             report.stats.plays, report.stats.skips, report.stats.ratings)
    _apply_phone_playlists(conn, root, merged, manifest, record, report, confirm_playlists)
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
        _finish(conn, root, phone, record, report, manifest, the_plan, tell, stopped=True)
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

    # 5–7. Covers, playlists and the database, also after Stop: they describe
    # exactly what's on the phone now, so a player can use the songs copied so far.
    _finish(conn, root, phone, record, report, manifest, the_plan, tell,
            stopped=report.cancelled)
    return report


def _finish(conn, root: Path, phone: Phone, record, report: SyncReport, manifest: dict,
            the_plan, tell, stopped: bool) -> None:
    placed = {pid: f["path"] for pid, f in manifest["files"].items()}

    # 5. Album covers (for players that look for cover.jpg next to the songs).
    # After Stop, only the covers already there (no artwork is read, to stop quickly).
    tell("Adding album covers…", can_stop=False)
    by_pid = {t["persistent_id"]: t for t in library.load_tracks(conn) if t.get("persistent_id")}
    covers = _write_covers(root, placed, by_pid, manifest, report, read_new=not stopped)

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
                  report: SyncReport, read_new: bool = True) -> dict:
    """One cover file per album folder, from the first song's artwork
    (read_new False: only keep the covers already there)."""
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
        if not read_new:
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
