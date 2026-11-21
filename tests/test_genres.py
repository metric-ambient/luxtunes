"""The genre catalogue (resources/genres.json) and what Genius will build on."""

import json

from luxtunes.core import genres


def names(cat, field):
    return [cat.names[i] for i in cat.genre_ids(field)]


def test_the_catalogue_is_sound():
    data = json.loads(genres.CATALOGUE.read_text(encoding="utf-8"))
    assert len(data["genres"]) > 3000 and len(data["links"]) > 5000
    assert "CC0" in data["sources"]
    ids = [g["id"] for g in data["genres"]]
    assert len(ids) == len(set(ids))
    assert all(i.startswith(("mb:", "wd:")) for i in ids)
    count = len(data["genres"])
    assert all(0 <= a < count and 0 <= b < count and a != b and kinds
               for a, b, kinds in data["links"])
    assert all(all(k in genres.WEIGHTS for k in kinds) for _, _, kinds in data["links"])


def test_genre_fields_are_understood():
    cat = genres.catalogue()
    assert names(cat, "Metalcore") == ["Metalcore"]
    assert names(cat, "post hardcore") == names(cat, "Post-Hardcore") == ["Post-hardcore"]
    assert names(cat, "Drum & Bass") == ["Drum and bass"]
    assert names(cat, "Pop, Rock") == ["Pop", "Rock"]          # two genres, not "Pop rock"
    assert names(cat, "Metalcore; Post-Hardcore") == ["Metalcore", "Post-hardcore"]
    assert names(cat, "R&B/Soul") == ["Rhythm and blues", "Soul"]   # a store's name
    assert names(cat, "Alternative") == ["Alternative rock"]
    assert names(cat, "Alternative & Punk") == ["Alternative rock", "Punk"]
    assert names(cat, "Game Soundtrack") == ["Video game music"]
    assert names(cat, "") == names(cat, None) == names(cat, "Other") == []
    assert cat.unknown_parts("Pop Rock / Female Vocal") == ["Female Vocal"]
    assert cat.unknown_parts("Rock") == []


def test_suggestions_for_the_genre_box():
    cat = genres.catalogue()
    assert cat.suggest("post h")[0] == "Post-hardcore"
    assert cat.suggest("hip")[0] == "Hip-hop"
    assert "Drum and bass" in cat.suggest("drum &")
    assert cat.suggest("") == []


def test_close_genres_come_first():
    cat = genres.catalogue()
    near = cat.related(cat.genre_ids("Metalcore"))
    assert near[cat.genre_ids("Metalcore")[0]] == 1.0
    assert cat.closeness("Metalcore", "Deathcore") > cat.closeness("Metalcore", "Post-Hardcore") \
        > cat.closeness("Metalcore", "Country") >= 0
    assert cat.closeness("Video game music", "Chiptune") > cat.closeness("Video game music", "Jazz")
    assert cat.closeness("Rock", "") == 0.0


def test_a_song_with_several_genres():
    from luxtunes.core import albums
    assert genres.split_field(" Metalcore ;Post-hardcore, Emo, ") == ["Metalcore", "Post-hardcore", "Emo"]
    assert genres.split_field("R&B/Soul") == ["R&B/Soul"]
    assert genres.split_field(None) == []
    songs = [{"id": 1, "genre": "Metalcore, Post-hardcore", "album": "A"},
             {"id": 2, "genre": "metalcore", "album": "B"}, {"id": 3, "album": "C"}]
    grid = {g["title"]: [t["id"] for t in g["tracks"]] for g in albums.grid_groups(songs, "genres")}
    assert grid == {"Metalcore": [1, 2], "Post-hardcore": [1], "Unknown Genre": [3]}


def test_get_info_suggests_what_the_library_has(tmp_path):
    from luxtunes.core import db, library
    conn = db.connect(tmp_path / "library.db")
    rows = [("Harbor Lights", "Ember Lane", "Ember Lane", None, "Metalcore, Post-hardcore"),
            ("Duet", "Mira Vale", None, "Ember Lane, Ann", "metalcore"),
            ("Song", "ember lane", None, None, "")]
    for i, (name, artist, album_artist, featured, genre) in enumerate(rows):
        conn.execute("INSERT INTO tracks (persistent_id, name, artist, album_artist, featured_artist, "
                     "genre) VALUES (?, ?, ?, ?, ?, ?)", (f"{i:016X}", name, artist, album_artist,
                                                         featured, genre))
    conn.commit()
    assert library.field_values(conn, "artist") == ["Ann", "Ember Lane", "Mira Vale"]
    assert library.field_values(conn, "featured_artist") == ["Ann", "Ember Lane", "Mira Vale"]
    assert library.field_values(conn, "genre") == ["Metalcore", "Post-hardcore"]
