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
