"""File → Add File to Library… / Add Folder to Library…

Reads each file's tags and adds it to the library. With "Copy files to
LuxTunes Media folder when adding to library" on (the default), files
from elsewhere are copied into the media folder, filed the same way "Keep
LuxTunes Media folder organized" files them; the original is left alone.
Files already in the library are skipped. No Qt in here.
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
from luxtunes.core.db import new_persistent_id
from luxtunes.core.tags import TagError, read_fields

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".m4p", ".aac", ".flac", ".wav", ".aif", ".aiff",
                    ".ogg", ".oga", ".opus"}
VIDEO_EXTENSIONS = {".m4v", ".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".mpg", ".mpeg"}
VIDEO_KINDS = {".mkv": "Matroska video file", ".avi": "AVI video file", ".webm": "WebM video file",
               ".wmv": "Windows Media video file", ".mpg": "MPEG video file",
               ".mpeg": "MPEG video file", ".mov": "QuickTime movie file"}
SUPPORTED = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
KEY_COPY = "copy_when_adding"


def copy_when_adding(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM library_info WHERE key = ?", (KEY_COPY,)).fetchone()
    return not row or row[0] != "0"


def set_copy_when_adding(conn: sqlite3.Connection, on: bool) -> None:
    with conn:
        conn.execute("INSERT INTO library_info (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (KEY_COPY, "1" if on else "0"))


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
    copy = copy_when_adding(conn)
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
            track_id = add_file(conn, path, copy, root, known, stop)
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
             known: dict[str, int] | None = None, stop=None) -> int:
    """Add one file and return its new track id. Raises TagError / OSError,
    or AlreadyAdded if an earlier copy of this file is already in the library."""
    try:
        fields = read_fields(path)
    except TagError:
        if Path(path).suffix.lower() not in VIDEO_EXTENSIONS:
            raise
        fields = video_fields(path)           # e.g. .mkv / .avi: no tags mutagen can read
    if not fields.get("name"):
        fields["name"] = Path(path).stem
    if Path(path).suffix.lower() in VIDEO_EXTENSIONS:
        fields["media_kind"] = "movie"
    else:
        from luxtunes.core.tags import media_kind_hint
        fields["media_kind"] = media_kind_hint(path) or "music"
        fields.update(library.kind_defaults(fields["media_kind"]))
    fields["date_added"] = library.now_utc()
    fields["date_modified"] = _utc(os.path.getmtime(path))

    location = path
    if copy and not organize.is_inside(path, root):
        ideal = organize.ideal_path(root, fields, Path(path).suffix)
        if known is None:
            known = _known_locations(conn)
        if (ideal.exists() and _key(str(ideal)) in known
                and os.path.getsize(ideal) == os.path.getsize(path)):
            raise AlreadyAdded(known[_key(str(ideal))])   # copied in an earlier add
        target = organize.target_path(root, fields, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_file(path, target, stop)
        log.info("copied %s -> %s", path, target)
        location = str(target)
    fields["location"] = location
    fields["persistent_id"] = new_persistent_id()

    columns = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    row = {k: v for k, v in fields.items() if k in columns}
    with conn:
        cursor = conn.execute(
            f"INSERT INTO tracks ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            list(row.values()))
    track_id = cursor.lastrowid
    organize.organize_track(conn, track_id)     # files already in the media folder get filed
    return track_id
