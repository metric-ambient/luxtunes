"""Finding the other missing files after one is located."""

from luxtunes.core import relink


def test_folder_change_from_windows_to_linux():
    change = relink.folder_change("E:/Users/Alex/Videos/Movies/Toy Story.m4v",
                                  "/home/alex/Videos/Movies/Toy Story.m4v")
    assert relink.apply_change("E:/Users/Alex/Videos/Movies/Up.m4v", change) == \
        "/home/alex/Videos/Movies/Up.m4v"
    assert relink.apply_change("D:/Other/Up.m4v", change) is None


def test_finds_others_by_path_name_and_tags(tmp_path):
    new = tmp_path / "Music" / "Album"
    new.mkdir(parents=True)
    for name in ("01 One.mp3", "02 Two.mp3", "renamed.mp3"):
        (new / name).write_bytes(b"x")
    (tmp_path / "Music" / "Other").mkdir()
    (tmp_path / "Music" / "Other" / "05 Far.mp3").write_bytes(b"x")
    missing = [
        {"id": 2, "name": "Two", "location": "E:/Old/Music/Album/02 Two.mp3"},      # same change
        {"id": 3, "name": "Far", "location": "E:/Old/Music/Other/05 Far.mp3"},      # another folder
        {"id": 4, "name": "x", "location": "/elsewhere/Renamed.MP3"},              # same file name
        {"id": 5, "name": "Gone", "location": "E:/Old/Music/Album/09 Gone.mp3"},    # not there
        {"id": 6, "name": "One", "location": "E:/Old/Music/Album/01 One.mp3"},      # the located one's file: taken
    ]
    found = relink.find_others("E:/Old/Music/Album/01 One.mp3", str(new / "01 One.mp3"), missing,
                               use_tags=False)
    assert found == {2: str(new / "02 Two.mp3"), 3: str(tmp_path / "Music/Other/05 Far.mp3"),
                     4: str(new / "renamed.mp3")}


def test_tags_match_when_names_differ(tmp_path):
    import pytest
    pytest.importorskip("mutagen")
    from luxtunes.core import tags
    folder = tmp_path / "Album"
    folder.mkdir()
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    for file, title in (("a.mp3", "Located"), ("track7.mp3", "Robbers")):
        (folder / file).write_bytes(frame * 20)
        tags.write_tags(str(folder / file), {"name": title, "artist": "The 1975"})
    missing = [{"id": 9, "name": "Robbers", "artist": "The 1975", "location": "/old/x/07 Robbers.mp3"}]
    assert relink.find_others("/old/y/Located.mp3", str(folder / "a.mp3"), missing) == \
        {9: str(folder / "track7.mp3")}
