"""Keep LuxTunes Media folder organized: naming rules and moving files."""

import unicodedata

from luxtunes.core import db, library, organize


def song(**fields):
    base = {"id": 1, "name": "Robbers", "artist": "The 1975", "album_artist": None,
            "album": "The 1975", "track_number": 6, "disc_number": None, "disc_count": None,
            "compilation": 0, "media_kind": "music"}
    base.update(fields)
    return base


def test_names_follow_the_naming_rules():
    parts = organize.relative_parts
    assert parts(song()) == ["Music", "The 1975", "The 1975", "06 Robbers"]
    assert parts(song(album_artist="Various"))[1] == "Various"
    assert parts(song(compilation=1))[1] == "Compilations"
    assert parts(song(artist=None, album=None))[1:3] == ["Unknown Artist", "Unknown Album"]
    # Disc prefix: when there's a disc number and more than one disc (or no count).
    assert parts(song(disc_number=1, disc_count=2))[3] == "1-06 Robbers"
    assert parts(song(disc_number=1, disc_count=None))[3] == "1-06 Robbers"
    assert parts(song(disc_number=1, disc_count=1))[3] == "06 Robbers"
    assert parts(song(track_number=None))[3] == "Robbers"
    # Lengths (checked against a real imported library): files 36, folders 40, no trailing spaces.
    long = parts(song(name="Your Way with Words is Through Silence", track_number=3))
    assert long[3] == "03 Your Way with Words is Through Si"
    assert parts(song(name="Over My Head (Cable Car) Extra", track_number=4))[3] == "04 Over My Head (Cable Car) Extra"
    assert parts(song(name="Colder Than My Heart, If You Can Fly", track_number=7))[3] == "07 Colder Than My Heart, If You Can"
    assert len(parts(song(album="A" * 60))[2]) == 40
    # Characters that get replaced.
    assert parts(song(album="We Don’t Stop"))[2] == "We Don_t Stop"
    assert parts(song(name="Interlude; 22", track_number=6))[3] == "06 Interlude_ 22"
    assert parts(song(album="Hypocrites B/W 3: Months?"))[2] == "Hypocrites B_W 3_ Months_"
    assert parts(song(album="...And The Rest"))[2] == "_..And The Rest"
    assert parts(song(artist="Luhx."))[1] == "Luhx_"
    assert parts(song(name="go to hell.", track_number=1))[3] == "01 go to hell."
    assert parts(song(media_kind="movie", name="Toy Story")) == ["Movies", "Toy Story"]
    episode = song(media_kind="tvshow", name="Pilot", show="The Office", season=1, episode_number=1)
    assert parts(episode) == ["TV Shows", "The Office", "Season 1", "01 Pilot"]


def setup(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    root = tmp_path / "Media"
    organize.set_media_folder(conn, root)
    folder = root / "Music" / "The 1975" / "The 1975"
    folder.mkdir(parents=True)
    path = folder / "06 Robbers.mp3"
    path.write_bytes(b"audio")
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, track_number, location) "
                 "VALUES ('P1', 'Robbers', 'The 1975', 'The 1975', 6, ?)", (str(path),))
    conn.commit()
    return conn, root, conn.execute("SELECT id FROM tracks").fetchone()[0]


def test_album_rename_moves_file_and_removes_empty_folder(tmp_path):
    conn, root, track_id = setup(tmp_path)
    assert organize.organize_track(conn, track_id) is None          # already in place
    library.update_track_fields(conn, track_id, {"album": "The 1980"})
    assert organize.organize_track(conn, track_id) is None
    new = root / "Music" / "The 1975" / "The 1980" / "06 Robbers.mp3"
    assert new.read_bytes() == b"audio"
    assert library.load_track(conn, track_id)["location"] == str(new)
    assert not (root / "Music" / "The 1975" / "The 1975").exists()   # empty: removed
    assert (root / "Music" / "The 1975").exists()                    # still has The 1980


def test_artist_rename_removes_both_old_folders_but_keeps_music(tmp_path):
    conn, root, track_id = setup(tmp_path)
    (root / "Music" / "The 1975" / "The 1975" / "Thumbs.db").write_bytes(b"x")   # junk doesn't count
    library.update_track_fields(conn, track_id, {"artist": "Someone Else"})
    organize.organize_track(conn, track_id)
    assert (root / "Music" / "Someone Else" / "The 1975" / "06 Robbers.mp3").exists()
    assert not (root / "Music" / "The 1975").exists()
    assert (root / "Music").is_dir()


def test_existing_folder_is_reused_ignoring_case(tmp_path):
    conn, root, track_id = setup(tmp_path)
    (root / "Music" / "The 1975" / "THE 1980").mkdir()
    library.update_track_fields(conn, track_id, {"album": "The 1980"})
    organize.organize_track(conn, track_id)
    assert (root / "Music" / "The 1975" / "THE 1980" / "06 Robbers.mp3").exists()
    assert not (root / "Music" / "The 1975" / "The 1980").exists()


def test_name_clash_gets_a_number_and_nothing_is_overwritten(tmp_path):
    conn, root, track_id = setup(tmp_path)
    other = root / "Music" / "The 1975" / "The 1975" / "06 Chocolate.mp3"
    other.write_bytes(b"other song")
    library.update_track_fields(conn, track_id, {"name": "Chocolate"})
    organize.organize_track(conn, track_id)
    assert other.read_bytes() == b"other song"
    moved = root / "Music" / "The 1975" / "The 1975" / "06 Chocolate 1.mp3"
    assert moved.read_bytes() == b"audio"


def test_turned_off_outside_and_missing_files_are_left_alone(tmp_path):
    conn, root, track_id = setup(tmp_path)
    organize.set_enabled(conn, False)
    library.update_track_fields(conn, track_id, {"album": "The 1980"})
    organize.organize_track(conn, track_id)
    assert (root / "Music" / "The 1975" / "The 1975" / "06 Robbers.mp3").exists()
    organize.set_enabled(conn, True)
    # A file outside the media folder never moves.
    outside = tmp_path / "Elsewhere" / "song.mp3"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    library.set_location(conn, track_id, str(outside))
    assert organize.organize_track(conn, track_id) is None and outside.exists()
    # A missing file: nothing to do, no error.
    library.set_location(conn, track_id, str(tmp_path / "gone.mp3"))
    assert organize.organize_track(conn, track_id) is None


def test_accents_spelled_differently_are_not_moved(tmp_path):
    conn, root, track_id = setup(tmp_path)
    decomposed = unicodedata.normalize("NFD", "Noé")
    folder = root / "Music" / decomposed / "Album"
    folder.mkdir(parents=True)
    path = folder / "01 Song.mp3"
    path.write_bytes(b"a")
    library.set_location(conn, track_id, str(path))
    library.update_track_fields(conn, track_id, {"artist": "Noé", "album": "Album", "name": "Song",
                                                 "track_number": "1"})
    assert organize.planned_move(conn, library.load_track(conn, track_id)) is None


def test_organize_library(tmp_path):
    conn, root, track_id = setup(tmp_path)
    conn.execute("UPDATE tracks SET album = 'Moved' WHERE id = ?", (track_id,))
    conn.commit()
    plan = organize.plan_library(conn)
    assert [m.track_id for m in plan] == [track_id]
    moved, problems = organize.organize_library(conn, [m.track_id for m in plan])
    assert (moved, problems) == ([track_id], [])
    assert (root / "Music" / "The 1975" / "Moved" / "06 Robbers.mp3").exists()
    assert organize.plan_library(conn) == []
