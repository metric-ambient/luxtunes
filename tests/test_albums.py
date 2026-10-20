"""Tests for grouping songs into albums / artists / genres / composers."""

from luxtunes.core import albums


def song(name, artist, album, track=1, disc=1, **extra):
    return {"name": name, "artist": artist, "album": album,
            "track_number": track, "disc_number": disc, **extra}


LIBRARY = [
    song("Slow Seasons", "Bright Harbor", "The Dawn and Dusk", 1, genre="Rock"),
    song("Milestone", "Bright Harbor", "The Dawn and Dusk", 2, genre="Rock"),
    song("Comet", "Slow Orbit", "STARFALL", 3, genre="Metal", composer="R. Holt"),
    song("Outrider", "Slow Orbit", "STARFALL", 2, genre="Metal"),
    song("Intro", "Alpha", "Mix Tape", 1, compilation=1),
    song("Outro", "Zed", "Mix Tape", 2, compilation=1),
]


def test_consecutive_groups():
    assert albums.consecutive_groups(LIBRARY) == [(0, 2), (2, 2), (4, 2)]
    assert albums.consecutive_groups([]) == []
    # The same album split by another song makes two runs.
    split = [LIBRARY[0], LIBRARY[2], LIBRARY[1]]
    assert albums.consecutive_groups(split) == [(0, 1), (1, 1), (2, 1)]


def test_album_key_ignores_case_and_groups_compilations():
    a = song("x", "Bright Harbor", "the dawn and dusk")
    assert albums.album_key(a) == albums.album_key(LIBRARY[0])
    assert albums.album_key(LIBRARY[4]) == albums.album_key(LIBRARY[5])


def test_albums_in_order_follows_the_list():
    result = albums.albums_in_order([LIBRARY[2], LIBRARY[0], LIBRARY[3]])
    assert [a["title"] for a in result] == ["STARFALL", "The Dawn and Dusk"]
    assert result[0]["first_track"]["name"] == "Comet"
    assert [a["subtitle"] for a in albums.albums_in_order(LIBRARY)][-1] == "Various Artists"


def test_grid_albums_sorted_and_tracks_in_album_order():
    groups = albums.grid_groups(LIBRARY, "albums")
    assert [g["title"] for g in groups] == ["The Dawn and Dusk", "STARFALL", "Mix Tape"]
    starfall = groups[1]
    assert [t["name"] for t in starfall["tracks"]] == ["Outrider", "Comet"]
    assert starfall["subtitle"] == "Slow Orbit"


def test_grid_artists_genres_composers():
    artists = albums.grid_groups(LIBRARY, "artists")
    assert [g["title"] for g in artists] == ["Alpha", "Bright Harbor", "Slow Orbit", "Zed"]
    assert artists[1]["subtitle"] == "1 album"
    genres = albums.grid_groups(LIBRARY, "genres")
    assert [g["title"] for g in genres] == ["Metal", "Rock", "Unknown Genre"]
    composers = albums.grid_groups(LIBRARY, "composers")
    assert [g["title"] for g in composers] == ["R. Holt"]   # no-composer songs left out
