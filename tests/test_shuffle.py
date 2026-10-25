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


def test_played_recently():
    from datetime import datetime, timezone
    from luxtunes.core.shuffle import played_recently
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    assert played_recently({"play_date": "2026-10-24T20:00:00Z"}, now)
    assert not played_recently({"play_date": "2026-10-20T20:00:00Z"}, now)
    assert not played_recently({"play_date": None}, now) and not played_recently({}, now)
