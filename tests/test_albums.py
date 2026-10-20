"""Tests for grouping songs into albums / artists / genres / composers."""

from luxtunes.core import albums


def song(name, artist, album, track=1, disc=1, **extra):
    return {"name": name, "artist": artist, "album": album,
            "track_number": track, "disc_number": disc, **extra}


LIBRARY = [
    song("Sowing Season", "Brand New", "The Devil and God", 1, genre="Rock"),
    song("Millstone", "Brand New", "The Devil and God", 2, genre="Rock"),
    song("Collider", "Silent Planet", "SUPERBLOOM", 3, genre="Metal", composer="G. Weber"),
    song("Offworlder", "Silent Planet", "SUPERBLOOM", 2, genre="Metal"),
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
    a = song("x", "Brand New", "the devil and god")
    assert albums.album_key(a) == albums.album_key(LIBRARY[0])
    assert albums.album_key(LIBRARY[4]) == albums.album_key(LIBRARY[5])


def test_albums_in_order_follows_the_list():
    result = albums.albums_in_order([LIBRARY[2], LIBRARY[0], LIBRARY[3]])
    assert [a["title"] for a in result] == ["SUPERBLOOM", "The Devil and God"]
    assert result[0]["first_track"]["name"] == "Collider"
    assert [a["subtitle"] for a in albums.albums_in_order(LIBRARY)][-1] == "Various Artists"


def test_grid_albums_sorted_and_tracks_in_album_order():
    groups = albums.grid_groups(LIBRARY, "albums")
    assert [g["title"] for g in groups] == ["The Devil and God", "SUPERBLOOM", "Mix Tape"]
    superbloom = groups[1]
    assert [t["name"] for t in superbloom["tracks"]] == ["Offworlder", "Collider"]
    assert superbloom["subtitle"] == "Silent Planet"


def test_grid_artists_genres_composers():
    artists = albums.grid_groups(LIBRARY, "artists")
    assert [g["title"] for g in artists] == ["Alpha", "Brand New", "Silent Planet", "Zed"]
    assert artists[1]["subtitle"] == "1 album"
    genres = albums.grid_groups(LIBRARY, "genres")
    assert [g["title"] for g in genres] == ["Metal", "Rock", "Unknown Genre"]
    composers = albums.grid_groups(LIBRARY, "composers")
    assert [g["title"] for g in composers] == ["G. Weber"]   # no-composer songs left out
