"""Right-click → Create MP3 Version / Create AAC Version (for songs, podcasts
and audiobooks) and Create iPod Version (for movies and TV shows).

Every audio file can get both, except into its own format: an MP3 only gets
Create AAC Version, an AAC file only Create MP3 Version; FLAC, WAV, AIFF,
Apple Lossless, Ogg and Opus get both. FFmpeg (the `ffmpeg` command,
`sudo apt install ffmpeg`) encodes a 320 kbps MP3 or a 256 kbps AAC (.m4a)
next to the original. LuxTunes then writes the tags itself (from the library,
plus the original's lyrics and artwork), exactly as Get Info would, and adds
the new file to the library. The original is never changed.

Create iPod Version makes an .m4v the iPod can play (H.264 Baseline, at most
640×480 and 30 frames a second, plain AAC stereo sound), next to the original,
which is never changed. The copy gets the original's details (name, show,
season, episode...) and appears in the library beside it.

A converted copy always stays in the original's folder (it isn't filed by
"Keep LuxTunes Media folder organized" when it's made, which used to put a
TV episode's copy under Movies). With Preferences → Advanced → "Delete
original after conversion" on, the original's file goes to the Trash and the
copy takes its place in the library (replace_original).

No Qt in here; the window runs the commands so it can show progress.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

from luxtunes.core import add_files, library, organize
from luxtunes.core.db import get_setting, set_setting
from luxtunes.core.tags import TAG_FIELDS, TagError, read_details, write_tags

KEY_DELETE_ORIGINAL = "delete_original_after_conversion"
BIT_RATE = "320k"
AAC_BIT_RATE = "256k"
MP3_MAX_SAMPLE_RATE = 48000        # MP3 can't go higher; 88.2/96 kHz FLACs become 44.1 kHz
FORMATS = ("mp3", "aac")
EXTENSIONS = {"mp3": ".mp3", "aac": ".m4a"}
# Audio files FFmpeg can read (.m4p is copy-protected, so not those).
AUDIO_SOURCES = {".flac", ".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus", ".mp3", ".m4a",
                 ".m4b", ".aac"}


def delete_original(conn: sqlite3.Connection) -> bool:
    """Preferences → Advanced → Delete original after conversion (off unless turned on)."""
    return get_setting(conn, KEY_DELETE_ORIGINAL) == "1"


def set_delete_original(conn: sqlite3.Connection, on: bool) -> None:
    set_setting(conn, KEY_DELETE_ORIGINAL, "1" if on else "0")


# What the converted copy takes over from the original it replaces.
CARRIED_OVER = ("play_count", "play_date", "skip_count", "skip_date", "date_added",
                "enabled", "volume_adjustment", "equalizer", "start_time", "stop_time",
                "bookmarkable", "bookmark_time", "skip_when_shuffling")


def replace_original(conn: sqlite3.Connection, original_id: int, copy_id: int) -> None:
    """The original's file is gone (to the Trash): the copy takes its place,
    with its plays, settings and place in every playlist; the original's entry
    is removed."""
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (original_id,)).fetchone()
    if row is None:
        return
    keys = row.keys()
    carried = {k: row[k] for k in CARRIED_OVER if k in keys}
    with conn:
        if carried:
            conn.execute(f"UPDATE tracks SET {', '.join(f'{k} = ?' for k in carried)} "
                         "WHERE id = ?", (*carried.values(), copy_id))
        conn.execute("UPDATE playlist_items SET track_id = ? WHERE track_id = ?",
                     (copy_id, original_id))
    library.delete_tracks(conn, [original_id])


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def audio_format(track: dict) -> str | None:
    """"mp3", "aac", "other" (FLAC, WAV, Apple Lossless...), or None for what
    isn't an audio file LuxTunes can convert (videos, protected files)."""
    if track.get("media_kind") not in (None, *library.AUDIO_KINDS):
        return None
    extension = Path(track.get("location") or "").suffix.lower()
    if extension not in AUDIO_SOURCES:
        return None
    if extension == ".mp3":
        return "mp3"
    if extension == ".aac":
        return "aac"
    if extension in (".m4a", ".m4b"):          # AAC, unless it's Apple Lossless
        return "other" if "lossless" in (track.get("kind") or "").lower() else "aac"
    return "other"


def can_convert(track: dict, to: str = "mp3") -> bool:
    """Create MP3 / AAC Version is offered: an audio file not already in that format."""
    found = audio_format(track)
    return found is not None and found != to


def target_for(source: str, to: str = "mp3") -> Path:
    """Same folder and name as the original, with the new format's ending
    (" 1", " 2", ... if taken)."""
    source = Path(source)
    extension = EXTENSIONS[to]
    target = source.with_suffix(extension)
    copy = 1
    while target.exists():
        target = source.with_name(f"{source.stem} {copy}{extension}")
        copy += 1
    return target


def mp3_target(source: str) -> Path:
    return target_for(source, "mp3")


def command(ffmpeg: str, source: str, target: str, sample_rate: int | None,
            to: str = "mp3") -> list[str]:
    """The FFmpeg command: audio only, no tags copied (LuxTunes writes them);
    MP3 at 320 kbps CBR, or AAC-LC at 256 kbps that iPods play cleanly."""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-i", source, "-map", "0:a:0", "-map_metadata", "-1", "-vn"]
    if to == "aac":
        return cmd + [*IPOD_AAC, "-b:a", AAC_BIT_RATE, "-movflags", "+faststart", target]
    cmd += ["-codec:a", "libmp3lame", "-b:a", BIT_RATE]
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
    track_id = add_files.add_file(conn, target, copy=False, root=organize.media_folder(conn),
                                  details={"media_kind": source_track.get("media_kind")},
                                  file_it=False)       # stays beside the original
    # Carry your rating over to the new version.
    if source_track.get("rating") and not source_track.get("rating_computed"):
        library.set_rating(conn, track_id, source_track["rating"])
    return track_id


def remove_partial(target: str) -> None:
    try:
        os.remove(target)
    except OSError:
        pass


# ---- AAC the iPod's decoder handles cleanly ----------------------------------------------

# FFmpeg's AAC encoder turns on three optional coding tricks by default
# (intensity stereo, noise substitution, temporal noise shaping). The iPod's
# decoder mishandles them, which sounds like short bursts of static in one
# channel, so they're switched off. Plain AAC-LC, 44.1 kHz, never clipped
# when surround sound is folded into stereo.
IPOD_AAC = ["-c:a", "aac", "-profile:a", "aac_low", "-aac_is", "0", "-aac_pns", "0",
            "-aac_tns", "0", "-ac", "2", "-af", "aresample=44100:rematrix_maxval=1.0"]


def ipod_audio_command(ffmpeg: str, source: str, target: str, kbps: int) -> list[str]:
    """A song as AAC for the iPod (Sync → "Convert higher bit rate songs")."""
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", source,
            "-map", "0:a:0", "-map_metadata", "-1", "-vn", *IPOD_AAC, "-b:a", f"{kbps}k",
            "-movflags", "+faststart", "-f", "ipod", target]


# ---- Create iPod Version (movies and TV shows) ---------------------------------------

IPOD_VIDEO = {"width": 640, "height": 480, "fps": 30, "video_kbps": 1500, "audio_kbps": 160}
VIDEO_KINDS = ("movie", "tvshow")
# Copied from the original onto the iPod version.
VIDEO_DETAILS = ("name", "artist", "album_artist", "album", "genre", "year", "comments",
                 "description", "media_kind", "show", "season", "episode_number", "episode_id",
                 "sort_name", "sort_artist", "sort_album")


def can_make_ipod_video(track: dict) -> bool:
    return track.get("media_kind") in VIDEO_KINDS and bool(track.get("location"))


def ipod_video_target(source: str) -> Path:
    """"Film.mkv" -> "Film (iPod).m4v" in the same folder (" 2", " 3"... if taken)."""
    source = Path(source)
    target = source.with_name(f"{source.stem} (iPod).m4v")
    copy = 2
    while target.exists():
        target = source.with_name(f"{source.stem} (iPod) {copy}.m4v")
        copy += 1
    return target


def ipod_video_command(ffmpeg: str, source: str, target: str) -> list[str]:
    """Fits inside 640×480 keeping its shape (even sizes, as H.264 needs), no
    more than 30 fps; progress is written to stdout (-progress)."""
    v = IPOD_VIDEO
    scale = (f"scale='min({v['width']},iw)':'min({v['height']},ih)'"
             ":force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1")
    kbps = v["video_kbps"]
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-progress", "pipe:1", "-i", source,
            "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-sn", "-dn",
            "-vf", scale, "-fpsmax", str(v["fps"]),
            "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0", "-pix_fmt", "yuv420p",
            "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{kbps * 2}k",
            *IPOD_AAC, "-b:a", f"{v['audio_kbps']}k",
            "-movflags", "+faststart", "-f", "ipod", target]


def finish_video(conn: sqlite3.Connection, source_track: dict, target: str) -> int:
    """After FFmpeg made the iPod version: add it to the library with the
    original's details. Returns the new item's id."""
    # The original's details (TV show, season...) from the start, and the file
    # stays beside the original: filing it now would go by the bare .m4v
    # (a "movie") and put a TV episode's copy under Movies.
    details = {k: source_track.get(k) for k in VIDEO_DETAILS if source_track.get(k) is not None}
    track_id = add_files.add_file(conn, target, copy=False, root=organize.media_folder(conn),
                                  details=details, file_it=False)
    if source_track.get("rating") and not source_track.get("rating_computed"):
        library.set_rating(conn, track_id, source_track["rating"])
    return track_id
