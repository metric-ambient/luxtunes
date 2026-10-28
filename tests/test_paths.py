"""Tests for finding music files on disk."""

from luxtunes.core.paths import find_file, nearest_existing_folder


def test_find_file_exact_and_case_insensitive(tmp_path):
    song = tmp_path / "Music" / "Brand New" / "Deja Entendu" / "01 Tautou.mp3"
    song.parent.mkdir(parents=True)
    song.write_bytes(b"")
    assert find_file(str(song)) == str(song)
    wrong_case = tmp_path / "Music" / "brand new" / "DEJA ENTENDU" / "01 Tautou.mp3"
    assert find_file(str(wrong_case)) == str(song)


def test_find_file_missing(tmp_path):
    assert find_file(str(tmp_path / "Music" / "Nope" / "song.mp3")) is None
    assert find_file(None) is None


def test_nearest_existing_folder(tmp_path):
    (tmp_path / "Music").mkdir()
    expected = tmp_path / "Music" / "Missing Artist" / "Missing Album" / "song.mp3"
    assert nearest_existing_folder(str(expected)) == str(tmp_path / "Music")


def test_file_addresses_keep_commas_inside_one_path():
    from luxtunes.core.paths import file_uri
    uri = file_uri("/mnt/Drive 8TB/Music/01 - The Lion, the Witch and the Wardrobe.m4b")
    assert uri.startswith("file:///mnt/Drive%208TB/") and "," not in uri and "%2C" in uri
