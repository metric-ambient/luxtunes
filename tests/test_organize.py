"""Keep LuxTunes Media folder organized: naming rules and moving files."""

import unicodedata

from luxtunes.core import db, library, organize


def song(**fields):
    base = {"id": 1, "name": "Rovers", "artist": "The 1987", "album_artist": None,
            "album": "The 1987", "track_number": 6, "disc_number": None, "disc_count": None,
            "compilation": 0, "media_kind": "music"}
    base.update(fields)
    return base


def test_names_follow_the_naming_rules():
    parts = organize.relative_parts
    assert parts(song()) == ["Music", "The 1987", "The 1987", "06 Rovers"]
    assert parts(song(album_artist="Various"))[1] == "Various"
    assert parts(song(compilation=1))[1] == "Compilations"
    assert parts(song(artist=None, album=None))[1:3] == ["Unknown Artist", "Unknown Album"]
    # Disc prefix: when there's a disc number and more than one disc (or no count).
    assert parts(song(disc_number=1, disc_count=2))[3] == "1-06 Rovers"
    assert parts(song(disc_number=1, disc_count=None))[3] == "1-06 Rovers"
    assert parts(song(disc_number=1, disc_count=1))[3] == "06 Rovers"
    assert parts(song(track_number=None))[3] == "Rovers"
    # Lengths (checked against a real imported library): files 36, folders 40, no trailing spaces.
    long = parts(song(name="Your Way with Words is Through Thunder", track_number=3))
    assert long[3] == "03 Your Way with Words is Through Th"
    assert parts(song(name="Under Glass (Paper Boat) Extra", track_number=4))[3] == "04 Under Glass (Paper Boat) Extra"
    assert parts(song(name="Colder Than My Heart, If You Can Fly", track_number=7))[3] == "07 Colder Than My Heart, If You Can"
    assert len(parts(song(album="A" * 60))[2]) == 40
    # Characters that get replaced.
    assert parts(song(album="We Won’t Fade"))[2] == "We Won_t Fade"
    assert parts(song(name="Interlude; 22", track_number=6))[3] == "06 Interlude_ 22"
    assert parts(song(album="Lanterns B/W 4: Seasons?"))[2] == "Lanterns B_W 4_ Seasons_"
    assert parts(song(album="...And The Rest"))[2] == "_..And The Rest"
    assert parts(song(artist="Nyx."))[1] == "Nyx_"
    assert parts(song(name="drift to sea.", track_number=1))[3] == "01 drift to sea."
    assert parts(song(media_kind="movie", name="Night Harbor")) == ["Movies", "Night Harbor"]
    episode = song(media_kind="tvshow", name="Pilot", show="The Office", season=1, episode_number=1)
    assert parts(episode) == ["TV Shows", "The Office", "Season 1", "01 Pilot"]


def setup(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    root = tmp_path / "Media"
    organize.set_media_folder(conn, root)
    folder = root / "Music" / "The 1987" / "The 1987"
    folder.mkdir(parents=True)
    path = folder / "06 Rovers.mp3"
    path.write_bytes(b"audio")
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, track_number, location) "
                 "VALUES ('P1', 'Rovers', 'The 1987', 'The 1987', 6, ?)", (str(path),))
    conn.commit()
    return conn, root, conn.execute("SELECT id FROM tracks").fetchone()[0]


def test_album_rename_moves_file_and_removes_empty_folder(tmp_path):
    conn, root, track_id = setup(tmp_path)
    assert organize.organize_track(conn, track_id) is None          # already in place
    library.update_track_fields(conn, track_id, {"album": "The 1980"})
    assert organize.organize_track(conn, track_id) is None
    new = root / "Music" / "The 1987" / "The 1980" / "06 Rovers.mp3"
    assert new.read_bytes() == b"audio"
    assert library.load_track(conn, track_id)["location"] == str(new)
    assert not (root / "Music" / "The 1987" / "The 1987").exists()   # empty: removed
    assert (root / "Music" / "The 1987").exists()                    # still has The 1980


def test_artist_rename_removes_both_old_folders_but_keeps_music(tmp_path):
    conn, root, track_id = setup(tmp_path)
    (root / "Music" / "The 1987" / "The 1987" / "Thumbs.db").write_bytes(b"x")   # junk doesn't count
    library.update_track_fields(conn, track_id, {"artist": "Someone Else"})
    organize.organize_track(conn, track_id)
    assert (root / "Music" / "Someone Else" / "The 1987" / "06 Rovers.mp3").exists()
    assert not (root / "Music" / "The 1987").exists()
    assert (root / "Music").is_dir()


def test_existing_folder_is_reused_ignoring_case(tmp_path):
    conn, root, track_id = setup(tmp_path)
    (root / "Music" / "The 1987" / "THE 1980").mkdir()
    library.update_track_fields(conn, track_id, {"album": "The 1980"})
    organize.organize_track(conn, track_id)
    assert (root / "Music" / "The 1987" / "THE 1980" / "06 Rovers.mp3").exists()
    assert not (root / "Music" / "The 1987" / "The 1980").exists()


def test_name_clash_gets_a_number_and_nothing_is_overwritten(tmp_path):
    conn, root, track_id = setup(tmp_path)
    other = root / "Music" / "The 1987" / "The 1987" / "06 Caramel.mp3"
    other.write_bytes(b"other song")
    library.update_track_fields(conn, track_id, {"name": "Caramel"})
    organize.organize_track(conn, track_id)
    assert other.read_bytes() == b"other song"
    moved = root / "Music" / "The 1987" / "The 1987" / "06 Caramel 1.mp3"
    assert moved.read_bytes() == b"audio"


def test_turned_off_outside_and_missing_files_are_left_alone(tmp_path):
    conn, root, track_id = setup(tmp_path)
    organize.set_enabled(conn, False)
    library.update_track_fields(conn, track_id, {"album": "The 1980"})
    organize.organize_track(conn, track_id)
    assert (root / "Music" / "The 1987" / "The 1987" / "06 Rovers.mp3").exists()
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
    assert (root / "Music" / "The 1987" / "Moved" / "06 Rovers.mp3").exists()
    assert organize.plan_library(conn) == []


def test_a_moved_media_folder_is_followed(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    old, new = tmp_path / "home" / "LuxTunes Media", tmp_path / "drive" / "LuxTunes Media"
    songs = {"Moved": "Music/Artist/Album/01 Moved.mp3",
             "Not copied yet": "Music/Artist/Album/02 Not copied yet.mp3"}
    for name, relative in songs.items():
        (old / relative).parent.mkdir(parents=True, exist_ok=True)
        (old / relative).write_bytes(b"x")
        conn.execute("INSERT INTO tracks (persistent_id, name, location) VALUES (?, ?, ?)",
                     (f"00000000000000{len(name):02d}", name, str(old / relative)))
    outside = tmp_path / "elsewhere" / "song.mp3"
    conn.execute("INSERT INTO tracks (persistent_id, name, location) VALUES "
                 "('00000000000000FF', 'Outside', ?)", (str(outside),))
    conn.commit()
    (new / songs["Moved"]).parent.mkdir(parents=True)
    (new / songs["Moved"]).write_bytes(b"x")                    # only this one was copied

    result = organize.relocate_media_folder(conn, old, new)
    assert (result.updated, result.not_found) == (1, 1)
    where = dict(conn.execute("SELECT name, location FROM tracks"))
    assert where["Moved"] == str(new / songs["Moved"])
    assert where["Not copied yet"] == str(old / songs["Not copied yet"])   # still plays
    assert where["Outside"] == str(outside)
    again = organize.relocate_media_folder(conn, old, new)                # choosing it again
    assert (again.updated, again.not_found) == (0, 1)
    assert organize.relocate_media_folder(conn, new, new).updated == 0
