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
    # The compilation's songs (by Alpha and Zed) are under Various Artists.
    assert [g["title"] for g in artists] == ["Bright Harbor", "Slow Orbit", "Various Artists"]
    assert artists[0]["subtitle"] == "1 album"
    genres = albums.grid_groups(LIBRARY, "genres")
    assert [g["title"] for g in genres] == ["Metal", "Rock", "Unknown Genre"]
    composers = albums.grid_groups(LIBRARY, "composers")
    assert [g["title"] for g in composers] == ["R. Holt"]   # no-composer songs left out


def test_grid_tabs_for_audiobooks_and_podcasts():
    books = [song("Part 1", "A. Writer", "Big Book", 1, genre="History"),
             song("Part 2", "A. Writer", "Big Book", 2, genre="History"),
             song("Short Story", "B. Author", None, 1, genre="")]
    titles = [(g["title"], g["subtitle"]) for g in albums.grid_groups(books, "books")]
    assert titles == [("Big Book", "A. Writer"), ("Short Story", "B. Author")]   # one-file book: its name
    assert [g["title"] for g in albums.grid_groups(books, "authors")] == ["A. Writer", "B. Author"]
    categories = albums.grid_groups(books, "book_categories")
    assert [(g["title"], g["subtitle"]) for g in categories] == [("History", "1 book"),
                                                                ("Unknown Category", "1 book")]
    episodes = [song("Ep 1", "Host", "The Show", date_added="2026-01-01", play_count=3),
                song("Ep 2", "Host", "The Show", date_added="2026-02-01"),
                song("Pilot", "Other", "Another Show", date_added="2025-01-01", play_count=1)]
    [another, show] = albums.grid_groups(episodes, "podcasts")
    assert show["subtitle"] == "2 episodes" and show["tracks"][0]["name"] == "Ep 2"   # newest first
    [unplayed] = albums.grid_groups(episodes, "unplayed")
    assert unplayed["title"] == "The Show" and unplayed["subtitle"] == "1 unplayed episode"
    assert albums.grid_groups(episodes, "podcast_categories")[0]["title"] == "Unknown Category"


def test_a_compilation_shows_its_album_artist_if_it_has_one():
    plain = {"album": "Now 1", "artist": "Alpha", "compilation": 1}
    credited = {"album": "Lantern Valley I", "artist": "Jo Marsh",
                "album_artist": "Lantern Valley", "compilation": 1}
    assert albums.album_subtitle(plain) == "Various Artists"
    assert albums.album_subtitle(credited) == "Lantern Valley"
    assert albums.album_subtitle({"artist": "Alpha"}) == "Alpha"
    assert albums.album_subtitle({}, "Unknown Author") == "Unknown Author"


def test_group_compilations_when_browsing():
    from luxtunes.ui import formatting
    song = {"album": "Now 1", "artist": "Zed", "compilation": 1, "track_number": 1}
    credited = dict(song, album_artist="Lantern Valley")
    plain = {"album": "Solo", "artist": "Zed", "album_artist": "Zed"}
    try:
        assert formatting.browse_artist(song) == "Various Artists"
        assert formatting.browse_artist(credited) == "Lantern Valley"
        assert albums.grid_artist(song) == "Various Artists"
        formatting.set_group_compilations(False)
        assert formatting.browse_artist(credited) == "Zed"       # under its own artist
        assert albums.grid_artist(credited) == "Zed"
        assert formatting.browse_artist(plain) == "Zed"
        rows = sorted([credited, {"artist": "Alpha", "album": "A"}],
                      key=formatting.sort_key("artist"))
        assert rows[0]["artist"] == "Alpha"
    finally:
        formatting.set_group_compilations(True)


def test_featured_on_and_the_name_shown():
    from luxtunes.ui import formatting
    songs = [{"id": 1, "name": "Up", "artist": "Mia", "album": "A", "featured_artist": "Ann; Bo"},
             {"id": 2, "name": "Down", "artist": "Zed", "album": "B", "featured_artist": "ann"},
             {"id": 3, "name": "Own", "artist": "Ann", "album": "C", "featured_artist": "Ann"},
             {"id": 4, "name": "Duo", "artist": "Kim", "album": "D",
              "featured_artist": "Simon & Garfunkel"}]
    assert [t["id"] for t in albums.featured_on(songs, "Ann", exclude={3})] == [1, 2]
    assert albums.featured_on(songs, "Simon & Garfunkel")[0]["id"] == 4
    assert albums.featured_on(songs, "Nobody") == []
    assert formatting.display_value(songs[0], "name") == "Up (ft. Ann, Bo)"
    assert formatting.display_value({"name": "Solo"}, "name") == "Solo"
    assert formatting.featured_names(" a ;b;; c ;A") == ["a", "b", "c"]
    assert formatting.featured_names("Tyler, the Creator; Bo") == ["Tyler, the Creator", "Bo"]
