"""Tests for the spread shuffle."""

import random
from collections import Counter

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
    body = order[:-20]               # (the last few songs are whatever is left over)
    assert same_artist_neighbours(body, lambda s: s[0]) == 0
    near = sum(1 for i in range(len(body)) for back in range(1, shuffle.ALBUM_GAP + 1)
               if i >= back and body[i][1] == body[i - back][1])
    assert near == 0


def test_a_big_library_goes_a_long_way_before_an_album_comes_back():
    # 600 albums of 10 songs (6,000 songs): no album twice within 150 songs, no
    # artist (3 albums each) twice within a sixth of the number of artists.
    from luxtunes.core import shuffle
    songs = [(f"artist{a // 3}", f"album{a}", n) for a in range(600) for n in range(10)]
    order = spread_shuffle(songs, key=lambda s: s[0], album=lambda s: s[1], rng=random.Random(5))
    artist_gap, album_gap = shuffle.gaps(songs, lambda s: s[0], lambda s: s[1])
    assert album_gap == shuffle.ALBUM_GAP_MAX and artist_gap == 200 // 6
    last_album, last_artist = {}, {}
    for i, (artist, album, _) in enumerate(order[:3000]):
        assert i - last_album.get(album, -10_000) > album_gap
        assert i - last_artist.get(artist, -10_000) > artist_gap
        last_album[album] = last_artist[artist] = i


def test_a_big_album_comes_up_as_often_as_its_size_says_but_spread_out():
    # One album is a tenth of the list: about a tenth of any stretch, never in a clump.
    songs = [("big", "Big", n) for n in range(300)] + \
            [(f"artist{a}", f"album{a}", n) for a in range(270) for n in range(10)]
    order = spread_shuffle(songs, key=lambda s: s[0], album=lambda s: s[1], rng=random.Random(6))
    places = [i for i, s in enumerate(order) if s[1] == "Big"]
    assert 45 <= sum(1 for i in places if i < 600) <= 75
    assert min(b - a for a, b in zip(places, places[1:]) if b < 2900) >= 5


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
    from luxtunes.core.shuffle import freshness
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    tracks = [
        {"id": 1, "artist": "A", "album": "One", "play_date": "2026-10-29T08:00:00Z"},   # just played
        {"id": 2, "artist": "A", "album": "One"},                 # half its album heard lately
        {"id": 3, "artist": "B", "album": "Two", "skip_date": "2026-10-20T08:00:00Z"},  # skipped lately
        {"id": 4, "artist": "B", "album": "Two"},
        {"id": 5, "artist": "C", "album": "Three", "play_date": "2026-09-01T08:00:00Z"},  # long ago
        {"id": 6, "artist": "D", "album": "Four"},
        {"id": 7, "artist": "D", "album": "Four"},                # heard this session
        {"id": 8, "artist": "E", "album": ""},                    # no album: only itself counts
    ]
    weights = freshness(tracks, heard_ids={7}, now=now)
    assert [None if w is None else round(w, 2) for w in weights] == \
        [None, 0.6, 0.2, 0.6, 1.0, 0.6, None, 1.0]


def test_one_song_heard_barely_holds_back_its_album():
    from luxtunes.core.shuffle import freshness
    album = [{"id": i, "artist": "A", "album": "Big"} for i in range(15)]
    album[0]["play_date"] = "2000-01-01T00:00:00Z"      # long ago: doesn't count
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    album[2]["play_date"] = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    weights = freshness(album, now=now)
    assert weights[2] == 0.2                            # the song itself was heard
    assert all(0.9 < w < 1 for i, w in enumerate(weights) if i != 2)   # the rest: hardly


def test_a_new_shuffle_doesnt_favour_a_few_albums():
    # Two weeks of shuffled listening touch one song on most albums. Songs from
    # the untouched albums used to fill the start of every shuffle (a play
    # marked its whole album); now they're only a little more likely there.
    from luxtunes.core.shuffle import freshness
    rng = random.Random(7)
    songs = [{"id": i, "artist": f"a{i // 60}", "album": f"b{i // 12}"} for i in range(2400)]
    for album in range(200):
        if album % 5:                                   # 4 in 5 albums: one song heard lately
            songs[album * 12 + rng.randrange(12)]["play_date"] = "2099-01-01T00:00:00Z"
    weights = freshness(songs)
    untouched = {s["id"] for s in songs if (s["id"] // 12) % 5 == 0}
    front = Counter()
    for _ in range(60):
        order = spread_shuffle(songs, key=lambda s: s["artist"], album=lambda s: s["album"],
                               weight=lambda s: weights[s["id"]])
        front.update(s["id"] in untouched for s in order[:100])
    share = front[True] / (front[True] + front[False])
    assert 0.15 < share < 0.3                           # a fifth of the songs: about a fifth


def test_songs_just_heard_go_last_in_a_new_shuffle():
    from luxtunes.core.shuffle import freshness
    songs = [{"id": i, "artist": f"a{i // 40}", "album": f"b{i // 10}",
              "play_date": "2099-01-01T00:00:00Z" if i % 10 == 0 and i < 600 else None}
             for i in range(1000)]                 # one song of each of the first 60 albums
    weights = dict(zip((s["id"] for s in songs), freshness(songs)))
    order = spread_shuffle(songs, key=lambda s: s["artist"], album=lambda s: s["album"],
                           weight=lambda s: weights[s["id"]])
    assert sorted(s["id"] for s in order) == list(range(1000))
    just_heard = {s["id"] for s in songs if s["play_date"]}
    assert not just_heard & {s["id"] for s in order[:900]}           # songs just heard: last


def test_one_artist_with_several_albums_shuffles():
    # A soundtrack playlist: one artist, three albums (used to divide by zero).
    songs = [("Monument Valley", f"Soundtrack {i % 3}") for i in range(96)]
    for _ in range(20):
        order = spread_shuffle(list(range(96)), key=lambda i: songs[i][0].casefold(),
                               album=lambda i: songs[i][1])
        assert sorted(order) == list(range(96))
