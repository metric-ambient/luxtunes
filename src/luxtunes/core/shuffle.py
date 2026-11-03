"""Shuffle that feels random.

Pure chance repeats itself more than people expect: in a library of 7,600
songs on 1,200 albums, 38 randomly picked songs share an album about 2.5
times on average (the "birthday problem", made worse by a few very big
albums), and an artist about 6 times. So:

  1. A fair shuffle of the whole list (Fisher–Yates), seeded afresh each
     time, so no song has a set place and no two shuffles start alike.
  2. Freshness, by album, as odds rather than a rule: songs from albums you
     played or skipped anything from in ALBUM_DAYS are less likely to come up
     early (WEIGHTS), without being left out. (Leaving them out entirely made
     a heavy listener's shuffles draw on a small corner of the library.)
     Songs you played or skipped yourself in RECENT_DAYS, or this session,
     come after everything else.
  3. Albums and artists are spaced out (_spread): an album doesn't come back
     for a quarter as many songs as the list has albums (up to ALBUM_GAP_MAX),
     an artist for a sixth as many as it has artists (up to ARTIST_GAP_MAX).
     An album or artist with lots of songs waits less, so its songs are spread
     evenly rather than piled up at the end: it still comes up as often as its
     size says, just never in a cluster.

The random generator is seeded from the system's own random source mixed
with the time in nanoseconds, so every shuffle is fresh.
"""

from __future__ import annotations

import os
import random
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

ARTIST_GAP = 2          # at least this many songs between two by the same artist…
ARTIST_GAP_MAX = 40     # …up to this many in a big library (see gaps())
ALBUM_GAP = 8           # and between two from the same album…
ALBUM_GAP_MAX = 150     # …up to this many
LOOK_AHEAD = 400        # how far ahead to look for a song that fits
EVEN = 0.8              # a big artist/album waits at most this share of its even spacing
RECENT_DAYS = 2         # a song played or skipped this recently waits until last
ALBUM_DAYS = 14         # an album with any song played or skipped this recently…
WEIGHTS = {1: 0.2}      # …is less likely to come up early (1 in 5 the odds of the rest)
HEARD = 2               # freshness_tiers' value for a song heard itself: it waits until last


def fresh_rng() -> random.Random:
    return random.Random(int.from_bytes(os.urandom(16), "big") ^ time.time_ns())


def spread_shuffle(items: list, key=lambda item: item, rng: random.Random | None = None,
                   album=None, recent=None, tier=None) -> list:
    """Return a new list with `items` shuffled. key(item): the artist;
    album(item): the album (optional); recent(item): True for songs heard
    recently, which go to the back (optional); tier(item): freshness_tiers'
    value (optional): 1 comes up early less often (WEIGHTS), HEARD goes last."""
    rng = rng or fresh_rng()
    order = list(items)
    rng.shuffle(order)
    if tier is None and recent is not None:
        tier = lambda item: HEARD if recent(item) else 0
    held = 0
    if tier is not None:
        # A weighted shuffle: an item of weight w comes before one of weight 1
        # with odds w : 1. Songs just heard (HEARD) go after all the others.
        ranked = []
        for item in order:
            level = tier(item)
            ranked.append(((level >= HEARD, -rng.random() ** (1 / WEIGHTS.get(level, 1.0))), item))
        ranked.sort(key=lambda pair: pair[0])
        order = [item for _, item in ranked]
        held = sum(1 for (heard, _), _ in ranked if heard)
    _spread(order, key, album, held)
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
    shuffle leans towards albums you haven't been hearing, not just songs."""
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


def gaps(items: list, artist, album) -> tuple[int, int]:
    """How far apart songs by one artist, and from one album, are kept: a share
    of how many artists and albums the list has, so a big library goes hours
    before coming back to an album, and a small playlist still mixes."""
    artists = len({artist(item) for item in items} - {""})
    albums = len({album(item) for item in items} - {""}) if album else 0
    artist_gap = max(ARTIST_GAP, min(ARTIST_GAP_MAX, artists // 6))
    album_gap = max(ALBUM_GAP, min(ALBUM_GAP_MAX, albums // 4)) if album else 0
    return artist_gap, album_gap


def _spread(order: list, artist, album, held: int = 0) -> None:
    """Reorder in place so each song is the first one, in shuffled order, whose
    artist and album haven't been heard within their gaps (see gaps()). If
    none of the next LOOK_AHEAD songs fits, the one whose artist and album were
    heard longest ago plays. Songs that wait aren't lost: each is taken as soon
    as it fits, so the order stays random apart from those moves. The last
    `held` songs (the ones just heard) only come once all the others have."""
    artist_gap, album_gap = gaps(order, artist, album)
    entries = [(item, artist(item), album(item) if album else "") for item in order]
    # An artist or album with many songs can't wait the full gap every time
    # (its songs would pile up at the end): it waits a share of the list that
    # spaces its songs evenly instead, so it still comes up as often as its
    # number of songs says, just never in a cluster.
    total = len(entries)
    artist_count = Counter(who for _, who, _ in entries)
    album_count = Counter(what for _, _, what in entries)
    artist_wait = {who: min(artist_gap, int(total * EVEN / n) - 1) for who, n in artist_count.items()}
    album_wait = {what: min(album_gap, int(total * EVEN / n) - 1) for what, n in album_count.items()}
    split = len(entries) - held
    queues = [entries[:split], entries[split:]]
    last_artist: dict = {}
    last_album: dict = {}
    order.clear()
    far = 1 << 30
    for position in range(len(entries)):
        queue = queues[0] if queues[0] else queues[1]
        best, best_score = 0, -1.0
        for j in range(min(len(queue), LOOK_AHEAD)):
            _, who, what = queue[j]
            since_artist = position - last_artist.get(who, -far) if who else far
            since_album = position - last_album.get(what, -far) if what else far
            wait_artist = artist_wait[who] if who else 0
            wait_album = album_wait[what] if what else 0
            if since_artist > wait_artist and since_album > wait_album:
                best = j
                break
            score = min(since_artist / (wait_artist + 1), since_album / (wait_album + 1))
            if score > best_score:
                best, best_score = j, score
        item, who, what = queue.pop(best)
        order.append(item)
        if who:
            last_artist[who] = position
        if what:
            last_album[what] = position


def artist_key(track: dict) -> str:
    return (track.get("album_artist") or track.get("artist") or "").casefold()


def album_key(track: dict) -> str:
    """Album and its artist, so two artists' "Greatest Hits" are different albums."""
    name = (track.get("album") or "").casefold()
    return f"{artist_key(track)}\x1f{name}" if name else ""
