"""Several artists on one song: the Artists list, Featured Artists with ";",
and where they're used (tags are in test_tags.py)."""

from luxtunes.core import albums, cd, db, library, musicbrainz, names, radio


def test_lists_of_names():
    assert names.split_names(" Lümen ;Grayfield;; lümen ") == ["Lümen", "Grayfield"]
    assert names.split_names("Earth, Wind & Fire") == ["Earth, Wind & Fire"]
    assert names.clean("Lümen ;Grayfield;") == "Lümen; Grayfield"
    assert names.clean("  ") is None
    assert names.join_names(["A", "", "a", "B"]) == "A; B"
    assert names.from_old_featured("Ann, Bo") == "Ann; Bo"
    assert names.from_old_featured("Ann; Tyler, the Creator") == "Ann; Tyler, the Creator"
    assert names.song_artists({"artist": "Lümen & Grayfield", "artists": "Lümen; Grayfield"}) \
        == ["Lümen", "Grayfield"]
    assert names.song_artists({"artist": "Ember Lane"}) == ["Ember Lane"]


def test_splitting_is_only_suggested_for_artists_you_have():
    known = {"lümen", "grayfield", "ember lane", "simon & garfunkel"}
    assert names.suggest_split("Lümen & Grayfield", known) == ["Lümen", "Grayfield"]
    assert names.suggest_split("Lümen x Grayfield", known) == ["Lümen", "Grayfield"]
    assert names.suggest_split("Lümen, Grayfield and Ember Lane", known) == ["Lümen", "Grayfield", "Ember Lane"]
    assert names.suggest_split("Simon & Garfunkel", known) == []
    assert names.suggest_split("Lümen & Someone", known) == []
    assert names.suggest_split("Ember Lane", known) == []
    assert names.suggest_split("", known) == []


def test_old_featured_artists_get_semicolons(tmp_path):
    path = tmp_path / "library.db"
    conn = db.connect(path)
    conn.execute("INSERT INTO tracks (persistent_id, name, featured_artist) VALUES "
                 "('00000000000000AA', 'Up', 'Ann, Bo'), ('00000000000000AB', 'Down', 'Cy')")
    conn.execute("PRAGMA user_version = 13")
    conn.execute("ALTER TABLE tracks DROP COLUMN artists")
    conn.commit()
    conn.close()
    conn = db.connect(path)
    assert [r[0] for r in conn.execute("SELECT featured_artist FROM tracks ORDER BY id")] \
        == ["Ann; Bo", "Cy"]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT artists FROM tracks").fetchone()[0] is None


def test_editing_and_suggesting(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    for i, artist in enumerate(["Lümen", "Grayfield", "Lümen & Grayfield", "Simon & Garfunkel"]):
        conn.execute("INSERT INTO tracks (persistent_id, name, artist) VALUES (?, 'x', ?)",
                     (f"{i:016X}", artist))
    conn.commit()
    assert library.clean_field_value("artists", " Lümen ;Grayfield; ") == "Lümen; Grayfield"
    assert library.clean_field_value("featured_artist", "") is None
    assert library.suggested_artists(conn, "Lümen & Grayfield") == ["Lümen", "Grayfield"]
    assert library.suggested_artists(conn, "Simon & Garfunkel") == []
    conn.execute("UPDATE tracks SET artists = 'Lümen; Newcomer' WHERE id = 3")
    assert "Newcomer" in library.field_values(conn, "artist")
    assert "Newcomer" in library.field_values(conn, "artists")


def test_grid_files_a_song_under_each_of_its_artists():
    duet = {"id": 1, "artist": "Lümen & Grayfield", "artists": "Lümen; Grayfield", "album": "Duo"}
    on_lumens_album = {"id": 2, "artist": "Lümen & Grayfield", "artists": "Lümen; Grayfield",
                       "album_artist": "Lümen", "album": "Solo"}
    other = {"id": 3, "artist": "Grayfield", "album": "B"}
    assert albums.grid_artists(duet) == ["Lümen", "Grayfield"]
    assert albums.grid_artists(on_lumens_album) == ["Lümen", "Grayfield"]
    assert albums.grid_artists({"artist": "Ember Lane", "album_artist": "Mixed", "artists": "Ann; Bo"}) \
        == ["Mixed", "Ann", "Bo"]
    groups = {g["title"]: [t["id"] for t in g["tracks"]]
              for g in albums.grid_groups([duet, on_lumens_album, other], "artists")}
    # In album-artist order: Grayfield' own album, then Lümen's, then the duet's.
    assert groups == {"Grayfield": [3, 2, 1], "Lümen": [2, 1]}


def test_artist_radio_includes_collaborations():
    library_tracks = [{"id": 1, "artist": "Lümen"},
                      {"id": 2, "artist": "Lümen & Grayfield", "artists": "Lümen; Grayfield"},
                      {"id": 3, "artist": "Grayfield"}, {"id": 4, "artist": "Ember Lane"}]
    found = radio.seed_tracks(library_tracks, "artist", library_tracks[0])
    assert [t["id"] for t in found] == [1, 2]
    found = radio.seed_tracks(library_tracks, "artist", library_tracks[1])
    assert [t["id"] for t in found] == [1, 2, 3]


def credits(*parts):
    """credits("Lümen", " & ", "Grayfield") -> a MusicBrainz artist credit."""
    out = []
    for i in range(0, len(parts), 2):
        out.append({"name": parts[i], "joinphrase": parts[i + 1] if i + 1 < len(parts) else ""})
    return out


def test_musicbrainz_credits():
    assert cd.credit_parts(credits("Lümen", " & ", "Grayfield", " feat. ", "Ann", " & ", "Bo")) \
        == ("Lümen & Grayfield", ["Lümen", "Grayfield"], ["Ann", "Bo"])
    assert cd.credit_parts(credits("Ember Lane", " (featuring ", "Ann", ")")) == ("Ember Lane", ["Ember Lane"], ["Ann"])
    assert cd.credit_parts(credits("Earth, Wind & Fire")) == \
        ("Earth, Wind & Fire", ["Earth, Wind & Fire"], [])
    assert cd.credit_parts(None) == ("", [], [])


def test_lookup_fills_artists_and_featured():
    release = {"artist-credit": credits("Lümen"), "title": "Solo", "date": "2024"}
    track = {"title": "Song (feat. Ann)", "credit": credits("Lümen", " & ", "Grayfield", " ft. ", "Ann"),
             "number": 1, "count": 9, "disc": 1, "discs": 1, "genres": []}
    fields = musicbrainz.album_fields(release, track)
    assert fields["name"] == "Song"
    assert fields["artist"] == "Lümen & Grayfield"
    assert fields["artists"] == "Lümen; Grayfield"
    assert fields["featured_artist"] == "Ann"
    assert fields["album_artist"] == "Lümen"
    solo = musicbrainz.album_fields(release, {**track, "title": "Other", "credit": credits("Lümen")})
    assert solo["artists"] is None and solo["featured_artist"] is None
    assert "album_artist" not in solo
    # The same lists written differently aren't a change; a different order of
    # featured artists isn't either, but the order of Artists is.
    song = {"name": "Song", "artist": "Lümen & Grayfield", "artists": "lümen; Grayfield",
            "featured_artist": "Ann", "album": "Solo", "album_artist": "Lümen", "year": 2024,
            "track_number": 1, "track_count": 9, "disc_number": 1, "disc_count": 1}
    assert [c.field for c in musicbrainz.changes_for(song, fields)] == []
    assert [c.field for c in musicbrainz.changes_for({**song, "artists": "Grayfield; Lümen"},
                                                     fields)] == ["artists"]
    assert musicbrainz.changes_for({**song, "featured_artist": "Bo; Ann"},
                                   {**fields, "featured_artist": "Ann; Bo"}) == []


def test_compilation_songs_under_artists_you_have_elsewhere(monkeypatch):
    from luxtunes.ui import formatting
    single = {"id": 1, "artist": "Ann", "album": "Ann Single"}
    collab = {"id": 2, "artist": "Ann & Guest", "artists": "Ann; Guest", "album": "Hits 2026",
              "compilation": 1}
    solo = {"id": 3, "artist": "Bo", "album": "Hits 2026", "compilation": 1}
    bo_twice = {"id": 4, "artist": "Bo", "album": "Hits 2027", "compilation": 1}
    normal_collab = {"id": 5, "artist": "Ann & Cy", "artists": "Ann; Cy", "album": "Duo"}
    songs = [single, collab, solo, bo_twice, normal_collab]

    def groups():
        return {g["title"]: sorted(t["id"] for t in g["tracks"])
                for g in albums.grid_groups(songs, "artists")}

    monkeypatch.setattr(formatting, "GROUP_COMPILATIONS", True)
    # The compilations stay under Various Artists; Ann (who has songs of her own)
    # also lists her compilation song; Guest and Bo (only ever on compilations)
    # get no entry. Cy, on a normal collab, does.
    assert groups() == {"Various Artists": [2, 3, 4], "Ann": [1, 2, 5], "Cy": [5]}
    monkeypatch.setattr(formatting, "GROUP_COMPILATIONS", False)
    assert groups() == {"Ann": [1, 2, 5], "Guest": [2], "Bo": [3, 4], "Cy": [5]}
