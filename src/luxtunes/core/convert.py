"""Right-click → Create MP3 Version (for FLAC files).

FFmpeg (the `ffmpeg` command, `sudo apt install ffmpeg`) encodes the audio
as a 320 kbps MP3 next to the original. LuxTunes then writes the tags itself
(from the library, plus the FLAC's lyrics and artwork) as ID3 v2.3, exactly
as Get Info would, and adds the new file to the library. The FLAC is never
changed. No Qt in here; the window runs the command so it can show progress.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from luxtunes.core import add_files, library, organize
from luxtunes.core.tags import TAG_FIELDS, TagError, read_details, write_tags

BIT_RATE = "320k"
MP3_MAX_SAMPLE_RATE = 48000        # MP3 can't go higher; 88.2/96 kHz FLACs become 44.1 kHz


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def can_convert(track: dict) -> bool:
    return (track.get("location") or "").lower().endswith(".flac")


def mp3_target(source: str) -> Path:
    """Same folder and name as the FLAC, ending .mp3 (" 1", " 2", ... if taken)."""
    source = Path(source)
    target = source.with_suffix(".mp3")
    copy = 1
    while target.exists():
        target = source.with_name(f"{source.stem} {copy}.mp3")
        copy += 1
    return target


def command(ffmpeg: str, source: str, target: str, sample_rate: int | None) -> list[str]:
    """The FFmpeg command: audio only, no tags copied (LuxTunes writes them), 320 kbps CBR."""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-i", source, "-map", "0:a:0", "-map_metadata", "-1", "-vn",
           "-codec:a", "libmp3lame", "-b:a", BIT_RATE]
    if sample_rate and sample_rate > MP3_MAX_SAMPLE_RATE:
        cmd += ["-ar", "44100"]
    return cmd + [target]


def finish(conn: sqlite3.Connection, source_track: dict, source: str, target: str) -> int:
    """After FFmpeg made `target`: tag it like the FLAC and add it to the
    library. Returns the new song's id. Raises TagError / OSError."""
    try:
        details = read_details(source)
    except TagError:
        details = {}
    fields = {key: source_track.get(key) for key in TAG_FIELDS}
    lyrics = details.get("lyrics") or source_track.get("lyrics") or ""
    write_tags(target, fields, lyrics=lyrics, artworks=details.get("artworks", []))
    track_id = add_files.add_file(conn, target, copy=False, root=organize.media_folder(conn))
    # Carry your rating over to the new version.
    if source_track.get("rating") and not source_track.get("rating_computed"):
        library.set_rating(conn, track_id, source_track["rating"])
    return track_id


def remove_partial(target: str) -> None:
    try:
        os.remove(target)
    except OSError:
        pass
