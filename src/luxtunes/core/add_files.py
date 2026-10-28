"""File → Add File to Library… / Add Folder to Library…

Reads each file's tags and adds it to the library. Files from elsewhere are
always copied into the LuxTunes Media folder, filed the same way "Keep
LuxTunes Media folder organized" files them, so everything LuxTunes shows
lives there. The original is left alone, unless Preferences → Advanced →
"Move original files on import" is on: then it's moved instead (taken away
from where it was only once the library has the song).
Files already in the library are skipped (the same file, or a copy of a song
already there: same name, artist, album, file type and length), and adding
carries on with the rest. No Qt in here.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from luxtunes.core import library, organize
from luxtunes.core.db import get_setting, new_persistent_id, set_setting
from luxtunes.core.tags import TagError, read_fields

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".m4p", ".aac", ".flac", ".wav", ".aif", ".aiff",
                    ".ogg", ".oga", ".opus"}
VIDEO_EXTENSIONS = {".m4v", ".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".mpg", ".mpeg"}
VIDEO_KINDS = {".mkv": "Matroska video file", ".avi": "AVI video file", ".webm": "WebM video file",
               ".wmv": "Windows Media video file", ".mpg": "MPEG video file",
               ".mpeg": "MPEG video file", ".mov": "QuickTime movie file"}
SUPPORTED = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
KEY_MOVE = "move_when_adding"


def move_when_adding(conn: sqlite3.Connection) -> bool:
    """Preferences → Advanced → Move original files on import (off unless turned on)."""
    return get_setting(conn, KEY_MOVE) == "1"


def set_move_when_adding(conn: sqlite3.Connection, on: bool) -> None:
    set_setting(conn, KEY_MOVE, "1" if on else "0")


@dataclass
class AddResult:
    added: list[int] = field(default_factory=list)       # new track ids
    already: list[int] = field(default_factory=list)     # ids of files already in the library
    failed: list[str] = field(default_factory=list)      # "file name: reason"
    stopped: bool = False


class AddStopped(Exception):
    """Stop was pressed (the file being copied is removed)."""


def collect_files(paths: list[str], found_so_far=None, stop=None) -> list[str]:
    """The music/video files among `paths`, looking inside folders (and their
    subfolders), in name order. Hidden files and folders are skipped.
    found_so_far(count) is told the count after each folder."""
    found = []
    for path in paths:
        if os.path.isdir(path):
            for folder, folders, files in os.walk(path):
                if stop is not None and stop.is_set():
                    raise AddStopped()
                folders[:] = sorted(f for f in folders if not f.startswith("."))
                found.extend(os.path.join(folder, f) for f in sorted(files)
                             if not f.startswith(".") and Path(f).suffix.lower() in SUPPORTED)
                if found_so_far is not None:
                    found_so_far(len(found))
        elif Path(path).suffix.lower() in SUPPORTED:
            found.append(path)
    return found


CHUNK = 1024 * 1024
SLOW_SECONDS = 20


def copy_file(source: str, target: Path, stop=None) -> None:
    """Copy a file a piece at a time (so Stop works even mid-way through a big
    file on a slow drive), keeping its dates. A half-copied file is removed."""
    try:
        with open(source, "rb") as src, open(target, "xb") as dst:
            while True:
                if stop is not None and stop.is_set():
                    raise AddStopped()
                block = src.read(CHUNK)
                if not block:
                    break
                dst.write(block)
        shutil.copystat(source, target)
    except BaseException:
        try:
            os.remove(target)
        except OSError:
            pass
        raise


def _key(path: str) -> str:
    return unicodedata.normalize("NFC", os.path.realpath(path))


def _known_locations(conn) -> dict[str, int]:
    return {_key(r[1]): r[0] for r in conn.execute("SELECT id, location FROM tracks")
            if r[1] and os.path.exists(r[1])}


SAME_LENGTH_MS = 2000           # a copy of a song: lengths within this much


def song_key(fields: dict, extension: str) -> tuple:
    return ((fields.get("name") or "").strip().casefold(),
            (fields.get("album_artist") or fields.get("artist") or "").strip().casefold(),
            (fields.get("album") or "").strip().casefold(), extension.lower())


def _known_songs(conn) -> dict[tuple, list[tuple[int, int | None]]]:
    """Songs in the library by song_key: [(track id, length in ms)]."""
    songs: dict = {}
    for row in conn.execute("SELECT id, name, artist, album_artist, album, total_time, location "
                            "FROM tracks WHERE name IS NOT NULL"):
        fields = {"name": row[1], "artist": row[2], "album_artist": row[3], "album": row[4]}
        key = song_key(fields, Path(row[6] or "").suffix)
        songs.setdefault(key, []).append((row[0], row[5]))
    return songs


def _same_song(songs: dict, key: tuple, length: int | None) -> int | None:
    for track_id, known in songs.get(key, ()):
        if not length or not known or abs(known - length) <= SAME_LENGTH_MS:
            return track_id
    return None


def _utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_paths(conn: sqlite3.Connection, paths: list[str], progress=None, stop=None,
              found_so_far=None) -> AddResult:
    """Add these files (and folders' contents). progress(done, total, path) is
    told before each file and may return False to stop early; so may setting
    `stop` (a threading.Event), which also stops a copy mid-way. Each file is
    saved to the library as soon as it's added, so a stopped or interrupted
    add keeps what it did, and adding the same folder again picks up where it
    left off (files already in the library are skipped quickly)."""
    result = AddResult()
    try:
        files = collect_files(paths, found_so_far, stop)
    except AddStopped:
        result.stopped = True
        return result
    known = _known_locations(conn)
    songs = _known_songs(conn)
    move = move_when_adding(conn)
    root = organize.media_folder(conn)
    log.info("adding %d files from %s", len(files), ", ".join(paths))
    for done, path in enumerate(files):
        if (stop is not None and stop.is_set()) or \
                (progress is not None and progress(done, len(files), path) is False):
            result.stopped = True
            break
        existing = known.get(_key(path))
        if existing is not None:
            result.already.append(existing)
            continue
        started = time.monotonic()
        try:
            track_id = add_file(conn, path, True, root, known, stop, songs=songs, move=move)
        except AddStopped:
            result.stopped = True
            break
        except AlreadyAdded as already:
            result.already.append(already.track_id)
            continue
        except (TagError, OSError, ValueError) as error:
            log.warning("couldn't add %s: %s", path, error)
            result.failed.append(f"{os.path.basename(path)}: {error}")
            continue
        except Exception as error:          # one odd file mustn't stop the rest
            log.exception("couldn't add %s", path)
            result.failed.append(f"{os.path.basename(path)}: {type(error).__name__}: {error}")
            continue
        seconds = time.monotonic() - started
        if seconds > SLOW_SECONDS:
            log.info("%s took %.0f s to add", path, seconds)
        known[_key(path)] = track_id
        result.added.append(track_id)
    log.info("added %d, already there %d, failed %d%s", len(result.added), len(result.already),
             len(result.failed), " (stopped)" if result.stopped else "")
    return result


def video_fields(path: str) -> dict:
    """Details for a video file without readable tags: its name, size and,
    if ffprobe (part of FFmpeg) is installed, its length."""
    import json
    import shutil
    import subprocess
    fields = {"name": Path(path).stem, "size": os.path.getsize(path),
              "kind": VIDEO_KINDS.get(Path(path).suffix.lower(), "Video file")}
    if shutil.which("ffprobe"):
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration,bit_rate:format_tags=title",
                 "-of", "json", path], capture_output=True, text=True, timeout=30)
            info = json.loads(result.stdout or "{}").get("format", {})
            if info.get("duration"):
                fields["total_time"] = int(float(info["duration"]) * 1000)
            if info.get("bit_rate"):
                fields["bit_rate"] = int(info["bit_rate"]) // 1000
            title = (info.get("tags") or {}).get("title")
            if title:
                fields["name"] = title
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return fields


class AlreadyAdded(Exception):
    def __init__(self, track_id: int):
        super().__init__(track_id)
        self.track_id = track_id


def add_file(conn: sqlite3.Connection, path: str, copy: bool, root: Path,
             known: dict[str, int] | None = None, stop=None, details: dict | None = None,
             file_it: bool = True, persistent_id: str | None = None,
             fallback: dict | None = None, date_added: str | None = None,
             songs: dict | None = None, move: bool = False) -> int:
    """Add one file and return its new track id. Raises TagError / OSError,
    or AlreadyAdded if an earlier copy of this file is already in the library.
    details: song details that win over the file's own (e.g. a converted
    copy's media kind and show). file_it=False leaves the file where it is
    instead of filing it in the media folder now. persistent_id: use this one
    (a song added on a phone keeps the phone's). fallback: details used only
    where the file's own tags have none. date_added: when it was really added.
    songs: _known_songs(), to skip a copy of a song already in the library
    (AlreadyAdded); new songs are added to it. move: with copy, the original
    is moved rather than copied (it goes only once the song is saved)."""
    try:
        fields = read_fields(path)
    except TagError:
        if Path(path).suffix.lower() not in VIDEO_EXTENSIONS:
            raise
        fields = video_fields(path)           # e.g. .mkv / .avi: no tags mutagen can read
    for key, value in (fallback or {}).items():
        if value not in (None, "") and fields.get(key) in (None, ""):
            fields[key] = value
    if not fields.get("name"):
        fields["name"] = Path(path).stem
    if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
        fields["media_kind"] = "movie"
    else:
        from luxtunes.core.tags import media_kind_hint
        fields["media_kind"] = media_kind_hint(path) or "music"
        fields.update(library.kind_defaults(fields["media_kind"]))
    fields.update({k: v for k, v in (details or {}).items() if v is not None})
    key = song_key(fields, Path(path).suffix)
    if songs is not None and (key[1] or key[2]):       # untagged files: only by location
        same = _same_song(songs, key, fields.get("total_time"))
        if same is not None:
            raise AlreadyAdded(same)
    fields["date_added"] = date_added or library.now_utc()
    fields["date_modified"] = _utc(os.path.getmtime(path))

    location = path
    renamed = False
    if copy and not organize.is_inside(path, root):
        ideal = organize.ideal_path(root, fields, Path(path).suffix)
        if known is None:
            known = _known_locations(conn)
        if (ideal.exists() and _key(str(ideal)) in known
                and os.path.getsize(ideal) == os.path.getsize(path)):
            raise AlreadyAdded(known[_key(str(ideal))])   # copied in an earlier add
        target = organize.target_path(root, fields, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if move and os.stat(path).st_dev == os.stat(target.parent).st_dev:
            os.rename(path, target)         # same drive: instant, nothing copied
            renamed = True
        else:
            copy_file(path, target, stop)
        log.info("%s %s -> %s", "moved" if renamed else "copied", path, target)
        location = str(target)
    fields["location"] = location
    fields["persistent_id"] = persistent_id or new_persistent_id()

    columns = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    row = {k: v for k, v in fields.items() if k in columns}
    try:
        with conn:
            cursor = conn.execute(
                f"INSERT INTO tracks ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                list(row.values()))
    except BaseException:
        if renamed:
            os.rename(location, path)       # not in the library after all: put it back
        raise
    track_id = cursor.lastrowid
    if move and not renamed and location != path:
        try:
            os.remove(path)                 # the copy is in the library now
        except OSError as error:
            log.warning("couldn't move %s (it was copied; the original stays): %s", path, error)
    if songs is not None:
        songs.setdefault(key, []).append((track_id, fields.get("total_time")))
    if file_it:
        organize.organize_track(conn, track_id)     # files already in the media folder get filed
    return track_id
