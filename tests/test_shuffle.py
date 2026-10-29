"""Tests for the spread shuffle."""

import random

from luxtunes.core.shuffle import spread_shuffle


def same_artist_neighbours(order, key):
    return sum(1 for a, b in zip(order, order[1:]) if key(a) == key(b))


def test_keeps_every_item_exactly_once():
    items = list(range(200))
    result = spread_shuffle(items, key=lambda i: i % 7, rng=random.Random(1))
    assert sorted(result) == items
    assert result != items


def test_spreads_artists_apart():
    # 5 artists x 6 songs: a plain shuffle puts the same artist side by side
    # several times on average; the spread shuffle should rarely do it.
    songs = [(artist, n) for artist in "ABCDE" for n in range(6)]
    key = lambda s: s[0]
    plain_total = spread_total = 0
    for seed in range(200):
        rng = random.Random(seed)
        plain = songs[:]
        rng.shuffle(plain)
        plain_total += same_artist_neighbours(plain, key)
        spread_total += same_artist_neighbours(spread_shuffle(songs, key, random.Random(seed)), key)
    assert spread_total < plain_total / 3


def test_different_each_time():
    items = list(range(50))
    assert spread_shuffle(items, rng=random.Random(1)) != spread_shuffle(items, rng=random.Random(2))


def _playlist():
    """2,000 songs: a few big artists, many small ones, 12-song albums."""
    songs, artist = [], 0
    while len(songs) < 2000:
        artist += 1
        for n in range(max(1, 300 // artist)):
            songs.append((f"a{artist}", f"a{artist}-album{n // 12}", len(songs)))
    return songs[:2000]


def test_albums_and_artists_are_kept_apart():
    from luxtunes.core import shuffle
    songs = _playlist()
    order = spread_shuffle(songs, key=lambda s: s[0], album=lambda s: s[1],
                           rng=random.Random(3))
    assert sorted(order) == sorted(songs)
    assert same_artist_neighbours(order, lambda s: s[0]) == 0
    near = sum(1 for i in range(len(order)) for back in range(1, shuffle.ALBUM_GAP + 1)
               if i >= back and order[i][1] == order[i - back][1])
    assert near == 0


def test_songs_heard_recently_go_last():
    songs = _playlist()
    heard = set(songs[::3])
    order = spread_shuffle(songs, key=lambda s: s[0], album=lambda s: s[1],
                           recent=lambda s: s in heard, rng=random.Random(4))
    assert not heard & set(order[:1000])          # a new shuffle starts with music not just heard


def test_where_a_shuffle_starts_isnt_fixed():
    # Every song can open a shuffle: over many shuffles the first songs are
    # spread over the whole list, not the same few artists each time.
    songs = _playlist()
    firsts = {spread_shuffle(songs, key=lambda s: s[0])[0] for _ in range(300)}
    assert len(firsts) > 250


def test_albums_heard_lately_wait_their_turn():
    from datetime import datetime, timezone
    from luxtunes.core.shuffle import freshness_tiers
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    tracks = [
        {"id": 1, "artist": "A", "album": "One", "play_date": "2026-10-29T08:00:00Z"},   # just played
        {"id": 2, "artist": "A", "album": "One"},                 # its album was just heard
        {"id": 3, "artist": "B", "album": "Two", "skip_date": "2026-10-20T08:00:00Z"},  # skipped lately
        {"id": 4, "artist": "B", "album": "Two"},
        {"id": 5, "artist": "C", "album": "Three", "play_date": "2026-09-01T08:00:00Z"},  # long ago
        {"id": 6, "artist": "D", "album": "Four"},
        {"id": 7, "artist": "D", "album": "Four"},                # heard this session
        {"id": 8, "artist": "E", "album": ""},                    # no album: only itself counts
    ]
    assert freshness_tiers(tracks, heard_ids={7}, now=now) == [2, 1, 1, 1, 0, 1, 2, 0]


def test_a_new_shuffle_starts_with_albums_not_heard_lately():
    from luxtunes.core.shuffle import freshness_tiers
    songs = [{"id": i, "artist": f"a{i // 40}", "album": f"b{i // 10}",
              "play_date": "2099-01-01T00:00:00Z" if i % 10 == 0 and i < 600 else None}
             for i in range(1000)]                 # one song of each of the first 60 albums
    tiers = dict(zip((s["id"] for s in songs), freshness_tiers(songs)))
    order = spread_shuffle(songs, key=lambda s: s["artist"], album=lambda s: s["album"],
                           tier=lambda s: tiers[s["id"]])
    assert sorted(s["id"] for s in order) == list(range(1000))
    assert all(int(s["album"][1:]) >= 60 for s in order[:300])     # untouched albums first
