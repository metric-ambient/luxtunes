"""Adding an iPod's songs to the library: for an iPod last synced with
another computer (or another library), whose songs aren't in this one.

Only songs that aren't in the library already are offered: not the same song
(the iPod's database id is the library song's persistent ID, which is how a
sync knows its own songs), and not a copy of one (same name, artist and album,
and about the same length, whatever the file type).

Each new song's file is copied from the iPod into the LuxTunes Media folder,
filed like any added song, and keeps what the iPod knew about it: plays,
skips, rating, when it was added and last played. Its persistent ID is the
iPod's id for it, so the next sync sees the iPod's copy as the same song
instead of removing it and copying it again. Nothing on the iPod changes, so
iPods that can only be read (formatted for Mac) work too.

Reading the iPod uses libgpod (gpod.py); the rest has no libgpod in it, so
it's unit-tested directly.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from luxtunes.core import add_files, library, organize

log = logging.getLogger(__name__)

PROTECTED = (".m4p",)            # iTunes Store copy protection: can't play on Linux


@dataclass
class IPodSong:
    path: str                        # the file on the iPod
    dbid: int                        # the iPod's id for it
    fields: dict                     # name, artist, album… as the iPod's database has them
    stats: dict = field(default_factory=dict)   # play_count, rating, date_added…


@dataclass
class ImportResult:
    added: list[int] = field(default_factory=list)
    already: int = 0                 # turned out to be in the library after all
    failed: list[str] = field(default_factory=list)
    stopped: bool = False


def _iso(unix) -> str | None:
    try:
        unix = int(unix or 0)
    except (TypeError, ValueError):
        return None
    if unix <= 0:
        return None
    return datetime.fromtimestamp(unix, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def persistent_id(dbid: int) -> str | None:
    return f"{dbid:016X}" if dbid else None


# ---- What's on the iPod (libgpod) ------------------------------------------------------

def read_songs(db) -> list[IPodSong]:
    """Every song in the iPod's database whose file is there."""
    from luxtunes.core.ipod import gpod
    kinds = {gpod.MEDIATYPE_MOVIE: "movie", gpod.MEDIATYPE_PODCAST: "podcast",
             gpod.MEDIATYPE_AUDIOBOOK: "audiobook", gpod.MEDIATYPE_TVSHOW: "tvshow",
             gpod.MEDIATYPE_MUSICVIDEO: "movie"}
    songs = []
    for pointer in db.tracks():
        path = db.file_of(pointer)
        if not path or not os.path.isfile(path):
            continue
        t = pointer.contents
        text = lambda name: (gpod.get_str(t, name) or "").strip() or None   # noqa: E731
        kind = next((k for bit, k in kinds.items() if t.mediatype & bit), "music")
        fields = {
            "name": text("title"), "artist": text("artist"), "album": text("album"),
            "album_artist": text("albumartist"), "genre": text("genre"),
            "composer": text("composer"), "comments": text("comment"),
            "grouping": text("grouping"), "year": t.year or None,
            "track_number": t.track_nr or None, "track_count": t.tracks or None,
            "disc_number": t.cd_nr or None, "disc_count": t.cds or None,
            "bpm": t.BPM or None, "total_time": t.tracklen or None,
            "compilation": 1 if t.compilation else None,
            "show": text("tvshow"), "season": t.season_nr or None,
            "episode_number": t.episode_nr or None, "episode_id": text("tvepisode"),
        }
        stats = {
            "media_kind": kind,
            "play_count": int(t.playcount or 0), "play_date": _iso(t.time_played),
            "skip_count": int(t.skipcount or 0), "skip_date": _iso(t.last_skipped),
            "rating": int(t.rating or 0), "date_added": _iso(t.time_added),
            "bookmark_time": int(t.bookmark_time or 0) or None,
        }
        songs.append(IPodSong(path, int(t.dbid or 0), fields, stats))
    return songs


def read_ipod(mountpoint: str, model_hint: str | None = None) -> list[IPodSong]:
    from luxtunes.core.ipod import gpod
    with gpod.Database(mountpoint, model_hint=model_hint) as db:
        return read_songs(db)


# ---- Which are new ------------------------------------------------------------------------

def _song_key(fields: dict) -> tuple:
    """Name, artist, album: a copy of the same song, whatever its file type."""
    return add_files.song_key(fields, "")[:3]


def new_songs(conn, songs: list[IPodSong]) -> list[IPodSong]:
    """The iPod's songs that aren't in the library (nor a copy of one), and
    that LuxTunes can play."""
    ids = set()
    for (pid,) in conn.execute("SELECT persistent_id FROM tracks WHERE persistent_id IS NOT NULL"):
        try:
            ids.add(int(pid, 16))
        except ValueError:
            pass
    known: dict = {}
    for key, entries in add_files._known_songs(conn).items():
        known.setdefault(key[:3], []).extend(entries)
    found, seen = [], set()
    for song in songs:
        extension = Path(song.path).suffix.lower()
        if song.dbid in ids or extension in PROTECTED or extension not in add_files.SUPPORTED:
            continue
        key = _song_key(song.fields)
        if key[0] and add_files._same_song(known, key, song.fields.get("total_time")) is not None:
            continue
        if key[0] and (key, song.fields.get("total_time")) in seen:
            continue                          # the iPod has it twice
        seen.add((key, song.fields.get("total_time")))
        found.append(song)
    return found


def protected_count(songs: list[IPodSong]) -> int:
    return sum(1 for s in songs if Path(s.path).suffix.lower() in PROTECTED)


# ---- Adding them ----------------------------------------------------------------------------

def import_songs(conn, songs: list[IPodSong], progress=None, stop=None) -> ImportResult:
    """Copy these songs into the library. progress(done, total, name) is told
    before each song and may return False to stop; so may `stop` (a
    threading.Event), which also stops a copy mid-way. Each song is saved as
    soon as it's added."""
    result = ImportResult()
    root = organize.media_folder(conn)
    known_songs = add_files._known_songs(conn)
    for done, song in enumerate(songs):
        name = song.fields.get("name") or Path(song.path).name
        if (stop is not None and stop.is_set()) or \
                (progress is not None and progress(done, len(songs), name) is False):
            result.stopped = True
            break
        pid = persistent_id(song.dbid)
        if pid and conn.execute("SELECT 1 FROM tracks WHERE persistent_id = ?", (pid,)).fetchone():
            pid = None                         # (taken: a fresh one)
        try:
            # The file's own tags first; the iPod's database fills in what they lack.
            track_id = add_files.add_file(
                conn, song.path, copy=True, root=root, stop=stop,
                details=_kind(song.stats.get("media_kind")),
                fallback=song.fields, persistent_id=pid,
                date_added=song.stats.get("date_added"), songs=known_songs)
        except add_files.AddStopped:
            result.stopped = True
            break
        except add_files.AlreadyAdded:
            result.already += 1
            continue
        except Exception as error:            # one odd file mustn't stop the rest
            log.warning("couldn't import %s from the iPod: %s", song.path, error)
            result.failed.append(f"{name}: {error}")
            continue
        _keep_stats(conn, track_id, song.stats)
        result.added.append(track_id)
    log.info("imported %d songs from the iPod (%d already there, %d failed)%s",
             len(result.added), result.already, len(result.failed),
             " (stopped)" if result.stopped else "")
    return result


def _kind(kind: str | None) -> dict:
    """The iPod's media kind and that kind's defaults (a podcast remembers its place…)."""
    if not kind:
        return {}
    return {"media_kind": kind, **library.kind_defaults(kind)}


def _keep_stats(conn, track_id: int, stats: dict) -> None:
    values = {key: stats.get(key) for key in ("play_count", "play_date", "skip_count", "skip_date",
                                             "rating", "bookmark_time")
              if stats.get(key) not in (None, 0, "")}
    if values:
        with conn:
            conn.execute(f"UPDATE tracks SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?",
                         [*values.values(), track_id])
