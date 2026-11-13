"""Shuffle that feels random.

Pure chance repeats itself more than people expect: in a library of 7,600
songs on 1,200 albums, 38 randomly picked songs share an album about 2.5
times on average (the "birthday problem", made worse by a few very big
albums), and an artist about 6 times. So:

  1. A fair shuffle of the whole list (Fisher–Yates), seeded afresh each
     time, so no song has a set place and no two shuffles start alike.
  2. Freshness, as odds rather than a rule: a song is less likely to come up
     early the more of its album you played or skipped in ALBUM_DAYS (down to
     MIN_WEIGHT for an album heard right through, or a song heard itself),
     without being left out. Counting a share, not "any song", matters: when
     one play marked a whole album, most albums were marked after a few weeks
     and every shuffle opened with the same few untouched albums and singles.
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
ALBUM_DAYS = 14         # songs played or skipped this recently make their album less likely early:
MIN_WEIGHT = 0.2        # an album heard right through has 1 in 5 the odds of one not heard


def fresh_rng() -> random.Random:
    return random.Random(int.from_bytes(os.urandom(16), "big") ^ time.time_ns())


def spread_shuffle(items: list, key=lambda item: item, rng: random.Random | None = None,
                   album=None, recent=None, weight=None) -> list:
    """Return a new list with `items` shuffled. key(item): the artist;
    album(item): the album (optional); recent(item): True for songs heard
    recently, which go to the back (optional); weight(item): freshness()'s
    value (optional): how likely early (0–1], or None to go last."""
    rng = rng or fresh_rng()
    order = list(items)
    rng.shuffle(order)
    if weight is None and recent is not None:
        weight = lambda item: None if recent(item) else 1.0
    held = 0
    if weight is not None:
        # A weighted shuffle: an item of weight w comes before one of weight 1
        # with odds w : 1. Songs just heard (None) go after all the others.
        ranked = []
        for item in order:
            w = weight(item)
            ranked.append(((w is None, -rng.random() ** (1 / (w or 1.0))), item))
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


def freshness(tracks: list[dict], heard_ids=frozenset(),
              now: datetime | None = None) -> list[float | None]:
    """For each track, how likely it is to come up early in a new shuffle:
    None if it was played or skipped within RECENT_DAYS (or this session): it
    goes last; MIN_WEIGHT if it was itself within ALBUM_DAYS; otherwise 1,
    lowered by the share of its album (in `tracks`) heard within ALBUM_DAYS."""
    now = now or datetime.now(timezone.utc)
    song_since, album_since = now - timedelta(days=RECENT_DAYS), now - timedelta(days=ALBUM_DAYS)
    heard_at = []
    for track in tracks:
        times = [t for t in (_when(track.get("play_date")), _when(track.get("skip_date"))) if t]
        if track.get("id") in heard_ids:
            times.append(now)
        heard_at.append(max(times) if times else None)
    size: Counter = Counter()
    heard: Counter = Counter()
    for track, at in zip(tracks, heard_at):
        name = album_key(track)
        if name:
            size[name] += 1
            if at is not None and at >= album_since:
                heard[name] += 1
    weights = []
    for track, at in zip(tracks, heard_at):
        name = album_key(track)
        if at is not None and at >= song_since:
            weights.append(None)
        elif at is not None and at >= album_since:
            weights.append(MIN_WEIGHT)
        else:
            share = heard[name] / size[name] if name else 0.0
            weights.append(1.0 - (1.0 - MIN_WEIGHT) * share)
    return weights


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
    # (An artist or album with most of the list's songs waits 0: it can repeat.)
    artist_wait = {who: max(0, min(artist_gap, int(total * EVEN / n) - 1))
                   for who, n in artist_count.items()}
    album_wait = {what: max(0, min(album_gap, int(total * EVEN / n) - 1))
                  for what, n in album_count.items()}
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
