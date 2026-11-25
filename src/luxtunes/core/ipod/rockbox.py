"""Making a synced iPod work in Rockbox too, on an iPod that also runs it.

Rockbox (and RepriseOS) can be installed beside Apple's firmware on the same
iPod. They all use the one disk, so a single normal sync serves all of them:
Apple's firmware and RepriseOS read Apple's song list (iTunesDB); Rockbox
doesn't, it reads the tags in the song files themselves (its Database menu).
So when the iPod has Rockbox, LuxTunes also:

  * writes the synced playlists as .m3u8 files in /Playlists, pointing at
    the song files in iPod_Control/Music (Rockbox's playlist folder). Only
    files LuxTunes wrote (marked by a "#LuxTunes" line) are ever replaced or
    removed; your own playlists there are left alone.
  * makes sure each song file on the iPod has its tags and a cover Rockbox
    can show. Rockbox only reads embedded art that's a plain ("baseline")
    JPEG, not a PNG or a progressive JPEG, and big pictures are slow on it;
    so the iPod's copy (never your library's file) gets a JPEG of at most
    ART_SIDE pixels instead. Songs converted on their way to the iPod are
    given their tags and art too (the stock firmware doesn't need them).
    Each song is checked once (again after its details are edited, so Rockbox
    shows the new ones); STATE remembers which.
  * reads plays from Rockbox's scrobbler log (.scrobbler.log, written when
    Rockbox's "Last.fm scrobbler" plugin is on). The log isn't changed (other
    tools may want it): STATE remembers how much of it was read already.

Nothing in Rockbox's own folder (/.rockbox) or RepriseOS's files is changed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from luxtunes.core.ipod import stats

log = logging.getLogger(__name__)

ROCKBOX_FOLDER = ".rockbox"
REPRISE_MARKERS = ("osos-cfw.bin", "Reprise")
PLAYLISTS = "Playlists"
MARK = "#LuxTunes"
STATE = ("iPod_Control", "iTunes", "LuxTunes Rockbox.json")
SCROBBLER_LOGS = (".scrobbler.log", ".rockbox/.scrobbler.log")
ART_SIDE = 500           # pixels: the most a cover written for Rockbox is
ART_MAX_SIDE = 1000      # a baseline JPEG bigger than this is made smaller too


# ---- Which firmware the iPod has ----------------------------------------------------------

def other_firmware(mountpoint: str) -> list[str]:
    """Firmware installed beside Apple's: "Rockbox", "RepriseOS"."""
    root = Path(mountpoint)
    found = []
    if (root / ROCKBOX_FOLDER).is_dir():
        found.append("Rockbox")
    if any((root / marker).exists() for marker in REPRISE_MARKERS):
        found.append("RepriseOS")
    return found


def has_rockbox(mountpoint: str) -> bool:
    return (Path(mountpoint) / ROCKBOX_FOLDER).is_dir()


# ---- What LuxTunes remembers on the iPod ---------------------------------------------------

def load_state(mountpoint: str) -> dict:
    try:
        state = json.loads(Path(mountpoint, *STATE).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(mountpoint: str, state: dict) -> None:
    path = Path(mountpoint, *STATE)
    try:
        temporary = path.with_name(path.name + ".new")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        log.warning("couldn't save %s: %s", path, error)


# ---- Playlists ---------------------------------------------------------------------------

_NOT_IN_NAMES = set('<>:"/\\|?*')


def safe_name(name: str) -> str:
    """A playlist name as a file name the iPod's (FAT) disk takes."""
    clean = "".join("_" if c in _NOT_IN_NAMES or ord(c) < 32 else c for c in name or "")
    clean = clean.strip().rstrip(". ")[:100].rstrip(". ")
    return clean or "Playlist"


def _ours(path: Path) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return any(handle.readline().startswith(MARK) for _ in range(2))
    except OSError:
        return False


def _free_name(base: str, written: set, existing: dict) -> str:
    """A file name not used by another playlist written now, nor by one of
    your own (two playlists of one name both go over: "Mix", "Mix (2)")."""
    def taken(name):
        lower = name.lower()
        return lower in written or (lower in existing and not _ours(existing[lower]))
    if not taken(f"{base}.m3u8"):
        return f"{base}.m3u8"
    number = 2
    while taken(f"{base} ({number}).m3u8"):
        number += 1
    return f"{base} ({number}).m3u8"


def write_playlists(mountpoint: str, playlists: list[tuple[str, list[dict]]]) -> int:
    """playlists: (name, songs), each song {"path": file on the iPod, "seconds",
    "artist", "name"}. Writes /Playlists/<name>.m3u8 for each and removes the
    ones LuxTunes wrote before that aren't wanted now. Returns how many were written."""
    root = Path(mountpoint)
    folder = root / PLAYLISTS
    try:
        folder.mkdir(exist_ok=True)
    except OSError as error:
        log.warning("couldn't make %s: %s", folder, error)
        return 0
    existing = {}
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.name.lower().endswith(".m3u8"):
                existing[entry.name.lower()] = Path(entry.path)
    except OSError:
        pass
    written: set[str] = set()
    count = 0
    for name, songs in playlists:
        file_name = _free_name(safe_name(name), written, existing)
        lines = ["#EXTM3U", f"{MARK}: written by LuxTunes at each sync (changes here are replaced)"]
        for song in songs:
            try:
                relative = Path(song["path"]).relative_to(root)
            except ValueError:
                continue
            title = " - ".join(t for t in (song.get("artist"), song.get("name")) if t)
            lines.append(f"#EXTINF:{int(song.get('seconds') or 0)},{title}")
            lines.append("/" + relative.as_posix())
        path = folder / file_name
        try:
            temporary = folder / f".{file_name}.new"
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            log.warning("couldn't write the Rockbox playlist %s: %s", path, error)
            continue
        written.add(file_name.lower())
        count += 1
    for lower, path in existing.items():
        if lower not in written and _ours(path):
            try:
                path.unlink()
            except OSError as error:
                log.warning("couldn't remove the old Rockbox playlist %s: %s", path, error)
    return count


# ---- Album art and tags -----------------------------------------------------------------------

def jpeg_shape(data: bytes) -> tuple[bool, int, int] | None:
    """(progressive, width, height) of a JPEG, or None if it isn't one."""
    if not data or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 1 if marker == 0xFF else 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD,
                      0xCE, 0xCF):
            if i + 9 > len(data):
                return None
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return marker != 0xC0 and marker != 0xC1, width, height
        if marker == 0xDA:          # image data before any frame header: not usable
            return None
        i += 2 + length
    return None


def rockbox_can_show(data: bytes | None) -> bool:
    """A picture Rockbox shows (well): a baseline JPEG of at most ART_MAX_SIDE."""
    shape = jpeg_shape(data or b"")
    return shape is not None and not shape[0] and max(shape[1], shape[2]) <= ART_MAX_SIDE


def baseline_jpeg(data: bytes, ffmpeg: str | None) -> bytes | None:
    """The picture as a baseline JPEG of at most ART_SIDE pixels (FFmpeg)."""
    if not ffmpeg or not data:
        return None
    with tempfile.TemporaryDirectory(prefix="luxtunes-art-") as folder:
        source, target = os.path.join(folder, "in"), os.path.join(folder, "out.jpg")
        with open(source, "wb") as handle:
            handle.write(data)
        side = ART_SIDE
        command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", source,
                   "-vf", f"scale=w='min({side},iw)':h='min({side},ih)'"
                          ":force_original_aspect_ratio=decrease",
                   "-pix_fmt", "yuvj420p", "-q:v", "3", "-frames:v", "1", target]
        try:
            subprocess.run(command, check=True, timeout=60, capture_output=True)
            with open(target, "rb") as handle:
                made = handle.read()
        except (OSError, subprocess.SubprocessError) as error:
            log.warning("couldn't make a cover for Rockbox: %s", error)
            return None
    return made if rockbox_can_show(made) else None


def _has_title(path: str) -> bool:
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        return bool(audio is not None and audio.tags and audio.tags.get("title"))
    except Exception:
        return False


def tag_fields(song: dict) -> dict:
    from luxtunes.core.tags import TAG_FIELDS
    fields = {}
    for key in TAG_FIELDS:
        value = song.get(key)
        if value not in (None, ""):
            fields[key] = value
    return fields


def fix_file(path: str, song: dict, library_file: str | None, ffmpeg: str | None,
             needs_tags: bool | None = None) -> bool:
    """Give the iPod's copy of a song (`path`) what Rockbox needs: its tags
    (when it has none, as converted songs don't) and a cover it can show (made
    from the copy's own picture, or the library file's when the copy has
    none). Returns True if the file was changed. Never raises."""
    from luxtunes.core import artwork as artwork_reader
    from luxtunes.core import tags

    try:
        fields = tag_fields(song) if (needs_tags if needs_tags is not None
                                      else not _has_title(path)) else {}
        art = artwork_reader.read_embedded_artwork(path)
        borrowed = False
        if art is None and library_file and library_file != path:
            art = artwork_reader.read_embedded_artwork(library_file)
            borrowed = art is not None
        new_art = tags.UNCHANGED
        if art is not None and (borrowed or not rockbox_can_show(art)):
            made = art if rockbox_can_show(art) else baseline_jpeg(art, ffmpeg)
            if made is not None:
                new_art = [made]
        if not fields and new_art is tags.UNCHANGED:
            return False
        tags.write_tags(path, fields, artworks=new_art)
        return True
    except Exception as error:          # an odd file: the stock firmware doesn't mind
        log.warning("couldn't prepare %s for Rockbox: %s", path, error)
        return False


def tags_digest(song: dict) -> str:
    """A short fingerprint of a song's details, to notice when they're edited."""
    text = json.dumps(tag_fields(song), sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def checked_key(path: str, song: dict) -> str:
    """What a checked song looks like (its file on the iPod and its details),
    so it's only checked again once either changed."""
    try:
        status = os.stat(path)
    except OSError:
        return ""
    return f"{status.st_size}:{int(status.st_mtime)}|{tags_digest(song)}"


def details_changed(stored: str, song: dict) -> bool:
    """The song's details were edited since its file was checked."""
    return bool(stored) and stored.rsplit("|", 1)[-1] != tags_digest(song)


# ---- Plays from Rockbox --------------------------------------------------------------------

def _digest(lines: list[str]) -> str:
    return hashlib.sha1("\n".join(lines).encode("utf-8", "replace")).hexdigest()


def read_scrobbler_log(mountpoint: str, state: dict) -> tuple[list[dict], dict]:
    """The plays and skips Rockbox logged since the last sync, and what to
    remember about the log afterwards. Each: {"artist", "album", "title",
    "skipped", "when" (Unix time)}."""
    root = Path(mountpoint)
    for name in SCROBBLER_LOGS:
        path = root / name
        if path.is_file():
            break
    else:
        return [], {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        log.warning("couldn't read %s: %s", path, error)
        return [], {}
    local_time = "#TZ/UTC" not in text
    entries = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    seen = state.get("scrobbler") or {}
    start = 0
    count = seen.get("entries", 0)
    if seen.get("file") == name and 0 < count <= len(entries) \
            and _digest(entries[:count]) == seen.get("digest"):
        start = count
    plays = []
    for line in entries[start:]:
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        try:
            when = int(parts[6])
        except ValueError:
            continue
        if local_time:          # "#TZ/UNKNOWN": the iPod's clock, i.e. this computer's time
            when = int(datetime.fromtimestamp(when, timezone.utc).replace(tzinfo=None)
                       .astimezone(timezone.utc).timestamp())
        plays.append({"artist": parts[0], "album": parts[1], "title": parts[2],
                      "skipped": parts[5].strip().upper() == "S", "when": when})
    return plays, {"file": name, "entries": len(entries), "digest": _digest(entries)}


def _fold(text) -> str:
    return " ".join(str(text or "").casefold().split())


def plays_to_stats(conn, plays: list[dict]) -> list[stats.IPodTrackStats]:
    """Match logged plays to library songs (by artist, album and title, or
    artist and title when that's unique) for stats.merge."""
    if not plays:
        return []
    exact: dict = {}
    loose: dict = {}
    for row in conn.execute("SELECT persistent_id, name, artist, album_artist, album "
                            "FROM tracks WHERE persistent_id IS NOT NULL"):
        title = _fold(row["name"])
        for artist in {_fold(row["artist"]), _fold(row["album_artist"])} - {""}:
            exact.setdefault((artist, _fold(row["album"]), title), set()).add(row["persistent_id"])
            loose.setdefault((artist, title), set()).add(row["persistent_id"])
    found: dict[str, stats.IPodTrackStats] = {}
    unmatched = 0
    for play in plays:
        artist, title = _fold(play["artist"]), _fold(play["title"])
        ids = exact.get((artist, _fold(play["album"]), title)) or loose.get((artist, title))
        if not ids or len(ids) > 1:
            unmatched += 1
            continue
        pid = next(iter(ids))
        try:
            dbid = int(pid, 16)
        except ValueError:
            continue
        item = found.setdefault(pid, stats.IPodTrackStats(dbid=dbid))
        if play["skipped"]:
            item.recent_skips += 1
            item.last_skipped = max(item.last_skipped, play["when"])
        else:
            item.recent_plays += 1
            item.last_played = max(item.last_played, play["when"])
    if unmatched:
        log.info("%d Rockbox plays didn't match one library song", unmatched)
    return list(found.values())
