"""Shuffle that feels random.

  1. A fair shuffle of the whole list (Fisher–Yates): every order is equally
     likely, so no song, album or artist has a set place, and two shuffles
     of the same playlist start differently.
  2. Songs heard recently (this session, or played in the last RECENT_DAYS)
     move to the back, keeping their shuffled order, so a new shuffle starts
     with music you haven't just heard.
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
RECENT_DAYS = 2


def fresh_rng() -> random.Random:
    return random.Random(int.from_bytes(os.urandom(16), "big") ^ time.time_ns())


def spread_shuffle(items: list, key=lambda item: item, rng: random.Random | None = None,
                   album=None, recent=None) -> list:
    """Return a new list with `items` shuffled. key(item): the artist;
    album(item): the album (optional); recent(item): True for songs heard
    recently, which go to the back (optional)."""
    rng = rng or fresh_rng()
    order = list(items)
    rng.shuffle(order)
    if recent is not None:
        order = [i for i in order if not recent(i)] + [i for i in order if recent(i)]
    _spread(order, key, album)
    return order


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


def played_recently(track: dict, now: datetime | None = None) -> bool:
    """Played within RECENT_DAYS (play_date as "YYYY-MM-DDTHH:MM:SSZ")."""
    text = track.get("play_date")
    if not text:
        return False
    try:
        played = datetime.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now or datetime.now(timezone.utc)) - played < timedelta(days=RECENT_DAYS)
