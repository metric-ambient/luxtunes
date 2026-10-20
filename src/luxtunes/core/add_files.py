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


def collect_files(paths: list[str]) -> list[str]:
    """The music/video files among `paths`, looking inside folders (and their
    subfolders), in name order. Hidden files and folders are skipped."""
    found = []
    for path in paths:
        if os.path.isdir(path):
            for folder, folders, files in os.walk(path):
                folders[:] = sorted(f for f in folders if not f.startswith("."))
                found.extend(os.path.join(folder, f) for f in sorted(files)
                             if not f.startswith(".") and Path(f).suffix.lower() in SUPPORTED)
        elif Path(path).suffix.lower() in SUPPORTED:
            found.append(path)
    return found


def _key(path: str) -> str:
    return unicodedata.normalize("NFC", os.path.realpath(path))


def _known_locations(conn) -> dict[str, int]:
    return {_key(r[1]): r[0] for r in conn.execute("SELECT id, location FROM tracks")
            if r[1] and os.path.exists(r[1])}


def _utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_paths(conn: sqlite3.Connection, paths: list[str], progress=None) -> AddResult:
    """Add these files (and folders' contents). progress(done, total) may
    return False to stop early."""
    files = collect_files(paths)
    result = AddResult()
    known = _known_locations(conn)
    copy = copy_when_adding(conn)
    root = organize.media_folder(conn)
    for done, path in enumerate(files):
        if progress is not None and progress(done, len(files)) is False:
            break
        existing = known.get(_key(path))
        if existing is not None:
            result.already.append(existing)
            continue
        try:
            track_id = add_file(conn, path, copy, root, known)
        except AlreadyAdded as already:
            result.already.append(already.track_id)
            continue
        except (TagError, OSError) as error:
            result.failed.append(f"{os.path.basename(path)}: {error}")
            continue
        known[_key(path)] = track_id
        result.added.append(track_id)
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
             known: dict[str, int] | None = None) -> int:
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
        shutil.copy2(path, target)
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
