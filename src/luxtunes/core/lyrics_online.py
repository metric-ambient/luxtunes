"""Right-click → Get Lyrics Online…: lyrics from LRCLIB (lrclib.net), a free
community lyrics library that needs no account.

Each song is looked up by its name, artist, album and length; if that exact
match isn't there, a search by name and artist takes the first result of
about the same length. LRCLIB gives plain lyrics and often timed ones (LRC,
see lrc.py) that the Pop-Out Player uses to highlight the line being sung.

Only with approved=True (the window asks you first). Lyrics are only added to
songs that have none; songs with lyrics only get the timed version when it
says much the same. No Qt in here.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass, field

from luxtunes.core import lrc, net

API = "https://lrclib.net/api/"
SAME_LENGTH_S = 3              # a search result this close in length is the same recording
REQUEST_GAP = 0.2              # seconds between requests: LRCLIB is free, so go gently


class LyricsError(Exception):
    """LRCLIB answered strangely (the message says how)."""


class Offline(LyricsError):
    """LRCLIB couldn't be reached at all (no point asking it again now)."""


@dataclass
class Found:
    plain: str = ""
    timed: str = ""            # LRC; "" when LRCLIB has no timing for this song
    instrumental: bool = False


@dataclass
class LyricsResult:
    found: dict = field(default_factory=dict)       # track id -> Found
    not_found: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    stopped: bool = False


def _artist(track: dict) -> str:
    return (track.get("artist") or track.get("album_artist") or "").strip()


def _seconds(track: dict) -> int | None:
    ms = track.get("total_time")
    return round(ms / 1000) if ms else None


def _found(entry) -> Found | None:
    if not isinstance(entry, dict):
        return None
    timed = entry.get("syncedLyrics") or ""
    words = entry.get("plainLyrics") or lrc.plain(timed)
    if entry.get("instrumental"):
        return Found(instrumental=True)
    if not words and not timed:
        return None
    return Found(plain=str(words).strip(), timed=str(timed).strip())


def _ask(get, path: str, params: dict):
    try:
        return json.loads(get(API + path + "?" + urllib.parse.urlencode(params)))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise LyricsError(f"LRCLIB answered {error.code}.") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise Offline(f"Couldn't reach LRCLIB ({getattr(error, 'reason', error)}).") from error


def find(track: dict, *, approved: bool, get=net.get) -> Found | None:
    """LRCLIB's lyrics for one song, or None if it doesn't have them."""
    net.require(approved, "Looking up lyrics online")
    name, artist = (track.get("name") or "").strip(), _artist(track)
    if not name or not artist:
        return None
    seconds = _seconds(track)
    exact = {"track_name": name, "artist_name": artist}
    if track.get("album"):
        exact["album_name"] = track["album"]
    if seconds:
        exact["duration"] = seconds
    found = _found(_ask(get, "get", exact))
    if found is not None:
        return found
    results = _ask(get, "search", {"track_name": name, "artist_name": artist}) or []
    for entry in results if isinstance(results, list) else []:
        length = entry.get("duration") if isinstance(entry, dict) else None
        if seconds and isinstance(length, (int, float)) and abs(length - seconds) > SAME_LENGTH_S:
            continue
        found = _found(entry)
        if found is not None:
            return found
    return None


def find_many(tracks: list[dict], *, approved: bool, progress=None,
              stop: threading.Event | None = None, get=net.get) -> LyricsResult:
    """Look up each song. progress(done, total, name) is told as it goes; an
    unreachable LRCLIB ends the lookup (no point asking about the rest)."""
    net.require(approved, "Looking up lyrics online")
    result = LyricsResult()
    for done, track in enumerate(tracks):
        if stop is not None and stop.is_set():
            result.stopped = True
            break
        if progress is not None:
            progress(done, len(tracks), track.get("name") or "")
        if done:
            time.sleep(REQUEST_GAP)
        try:
            found = find(track, approved=True, get=get)
        except LyricsError as error:
            result.problems.append(str(error))
            if isinstance(error, Offline):
                break
            continue
        if found is None or found.instrumental:
            result.not_found.append(track.get("name") or "")
        else:
            result.found[track["id"]] = found
    if progress is not None:
        progress(len(tracks), len(tracks), "Done")
    return result


def to_save(track: dict, existing: str, found: Found) -> dict:
    """What to save for one song: {"lyrics": …} only when it has none, and
    {"synced_lyrics": …} when the timed lyrics fit what it ends up with."""
    changes = {}
    words = existing.strip()
    if not words:
        words = found.plain or lrc.plain(found.timed)
        if words:
            changes["lyrics"] = words
    if found.timed and lrc.same_words(words, found.timed) \
            and found.timed != (track.get("synced_lyrics") or ""):
        changes["synced_lyrics"] = found.timed
    return changes
