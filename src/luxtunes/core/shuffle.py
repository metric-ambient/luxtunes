"""Shuffle that feels random.

  1. A fair shuffle of the whole list (Fisher–Yates): every order is equally
     likely, so no song, album or artist has a set place, and two shuffles
     of the same playlist start differently.
  2. Freshness, by album: songs from albums you haven't played or skipped
     anything from in ALBUM_DAYS come first; then songs from albums you have;
     songs you played or skipped yourself in RECENT_DAYS (or this session)
     last. Each part keeps its shuffled order. Freshness by song alone wasn't
     enough: with a week of listening most albums have had a song played, so
     the start of every shuffle kept landing on the same albums, just a
     different song from each.
  3. Neighbours are spread apart: a song isn't played right after another by
     the same artist (ARTIST_GAP) or soon after one from the same album
     (ALBUM_GAP). A clashing song swaps places with the nearest later song
     that fits, so the order stays random apart from those small moves.

The random generator is seeded from the system's own random source mixed
with the time in nanoseconds, so every shuffle is fresh.
"""

from __future__ import annotations

import os
import random
import time
from datetime import datetime, timedelta, timezone

ARTIST_GAP = 2          # at least this many songs between two by the same artist
ALBUM_GAP = 8           # and this many between two from the same album
LOOK_AHEAD = 300        # how far ahead to look for a song that fits
RECENT_DAYS = 2         # a song played or skipped this recently waits until last
ALBUM_DAYS = 14         # an album with any song played or skipped this recently waits too


def fresh_rng() -> random.Random:
    return random.Random(int.from_bytes(os.urandom(16), "big") ^ time.time_ns())


def spread_shuffle(items: list, key=lambda item: item, rng: random.Random | None = None,
                   album=None, recent=None, tier=None) -> list:
    """Return a new list with `items` shuffled. key(item): the artist;
    album(item): the album (optional); recent(item): True for songs heard
    recently, which go to the back (optional); tier(item): 0 first, then 1,
    then 2… (optional, see freshness_tiers), each tier in shuffled order."""
    rng = rng or fresh_rng()
    order = list(items)
    rng.shuffle(order)
    if tier is None and recent is not None:
        tier = lambda item: 1 if recent(item) else 0
    if tier is not None:
        order.sort(key=tier)                    # stable: each tier keeps its shuffled order
    _spread(order, key, album)
    return order


def _when(text) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def freshness_tiers(tracks: list[dict], heard_ids=frozenset(),
                    now: datetime | None = None) -> list[int]:
    """For each track: 0 if neither it nor anything from its album was played
    or skipped lately, 1 if another song from its album was (within ALBUM_DAYS),
    2 if the song itself was (within RECENT_DAYS, or this session). So a new
    shuffle starts with albums you haven't been hearing, not just songs."""
    now = now or datetime.now(timezone.utc)
    song_since, album_since = now - timedelta(days=RECENT_DAYS), now - timedelta(days=ALBUM_DAYS)
    heard_at = []
    for track in tracks:
        times = [t for t in (_when(track.get("play_date")), _when(track.get("skip_date"))) if t]
        if track.get("id") in heard_ids:
            times.append(now)
        heard_at.append(max(times) if times else None)
    album_heard: dict = {}
    for track, at in zip(tracks, heard_at):
        name = album_key(track)
        if name and at is not None and (name not in album_heard or at > album_heard[name]):
            album_heard[name] = at
    tiers = []
    for track, at in zip(tracks, heard_at):
        if at is not None and at >= song_since:
            tiers.append(2)
        elif album_heard.get(album_key(track), album_since - timedelta(days=1)) >= album_since:
            tiers.append(1)
        else:
            tiers.append(0)
    return tiers


def _spread(order: list, artist, album) -> None:
    """Move clashing songs back, in place (see the module docstring)."""
    artists = [artist(item) for item in order]
    albums = [album(item) if album else None for item in order]

    def clashes(position: int, candidate: int) -> bool:
        who, what = artists[candidate], albums[candidate]
        for back in range(1, max(ARTIST_GAP, ALBUM_GAP if album else 0) + 1):
            before = position - back
            if before < 0:
                break
            if back <= ARTIST_GAP and who and artists[before] == who:
                return True
            if album and back <= ALBUM_GAP and what and albums[before] == what:
                return True
        return False

    for i in range(1, len(order)):
        if not clashes(i, i):
            continue
        for j in range(i + 1, min(len(order), i + LOOK_AHEAD)):
            if not clashes(i, j):
                # Bring the song that fits forward; the rest shift back by one.
                for column in (order, artists, albums):
                    column.insert(i, column.pop(j))
                break


def artist_key(track: dict) -> str:
    return (track.get("album_artist") or track.get("artist") or "").casefold()


def album_key(track: dict) -> str:
    """Album and its artist, so two artists' "Greatest Hits" are different albums."""
    name = (track.get("album") or "").casefold()
    return f"{artist_key(track)}\x1f{name}" if name else ""
