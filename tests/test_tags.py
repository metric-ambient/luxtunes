"""Tests for reading/writing file tags, on small MP3 and FLAC files built here."""

import struct

import pytest

mutagen = pytest.importorskip("mutagen")

from luxtunes.core import tags  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
       b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"


def make_mp3(path):
    """30 silent MPEG-1 Layer III frames (128 kbps, 44.1 kHz, joint stereo)."""
    frame = b"\xff\xfb\x90\x64" + b"\x00" * 413
    path.write_bytes(frame * 30)
    return str(path)


def make_flac(path):
    """A FLAC file with just its STREAMINFO block (enough for tags)."""
    info = struct.pack(">HH", 4096, 4096) + b"\x00\x00\x00" + b"\x00\x00\x00"
    # 44100 Hz, 2 channels, 16 bits, 0 samples (20 + 3 + 5 + 36 bits), then MD5.
    bits = (44100 << 44) | (1 << 41) | (15 << 36) | 0
    info += bits.to_bytes(8, "big") + b"\x00" * 16
    path.write_bytes(b"fLaC" + bytes([0x80]) + len(info).to_bytes(3, "big") + info)
    return str(path)


FIELDS = {
    "name": "Lights off the Lost Coast", "artist": "Silent Planet", "album_artist": "Silent Planet",
    "album": "SUPERBLOOM", "composer": "G. Weber", "genre": "Metalcore", "grouping": "Side A",
    "comments": "First track", "year": 2023, "track_number": 1, "track_count": 12,
    "disc_number": 1, "disc_count": 1, "bpm": 140, "compilation": 1,
    "sort_name": "Lights", "sort_artist": "Planet, Silent", "sort_album_artist": "Planet",
    "sort_album": "Superbloom", "sort_composer": "Weber",
}


def test_mp3_round_trip_as_id3v23(tmp_path):
    path = make_mp3(tmp_path / "song.mp3")
    tags.write_tags(path, FIELDS, lyrics="Line one\nLine two", artworks=[JPEG, PNG])
    from mutagen.id3 import ID3
    id3 = ID3(path, translate=False)    # read the file as stored, without upgrading to v2.4
    assert id3.version[:2] == (2, 3)                       # what iPods use
    assert id3["TIT2"].text == ["Lights off the Lost Coast"]
    assert id3["TRCK"].text == ["1/12"] and id3["TPOS"].text == ["1/1"]
    assert str(id3["TYER"]) == "2023"                       # v2.3 stores the year as TYER
    assert id3["TCMP"].text == ["1"] and id3["TSO2"].text == ["Planet"]
    # The sort frames survive the v2.3 conversion.
    assert id3["TSOT"].text == ["Lights"] and id3["TSOP"].text == ["Planet, Silent"]
    assert id3["TSOA"].text == ["Superbloom"]
    details = tags.read_details(path)
    assert details["lyrics"] == "Line one\nLine two"
    assert details["artworks"] == [JPEG, PNG]
    assert details["tag_version"] == "v2.3"
    assert details["format"] == "MPEG-1, Layer 3" and details["channels"] == "Joint Stereo"


def test_only_changed_fields_are_touched(tmp_path):
    path = make_mp3(tmp_path / "song.mp3")
    tags.write_tags(path, FIELDS, lyrics="Keep me", artworks=[JPEG])
    # Change just the name: everything else, including lyrics and art, survives.
    tags.write_tags(path, {"name": "New Name"})
    from mutagen.id3 import ID3
    id3 = ID3(path)
    assert id3["TIT2"].text == ["New Name"] and id3["TALB"].text == ["SUPERBLOOM"]
    details = tags.read_details(path)
    assert details["lyrics"] == "Keep me" and details["artworks"] == [JPEG]


def test_clearing_and_player_comments_survive(tmp_path):
    path = make_mp3(tmp_path / "song.mp3")
    from mutagen.id3 import COMM, ID3
    tags.write_tags(path, FIELDS)
    id3 = ID3(path)
    id3.add(COMM(encoding=3, lang="eng", desc="iTunNORM", text=["0000"]))   # another player's own data
    id3.save(path, v2_version=3)
    tags.write_tags(path, {"grouping": None, "comments": "", "compilation": 0},
                    lyrics="", artworks=[])
    id3 = ID3(path)
    assert "TIT1" not in id3 and "TCMP" not in id3
    assert [c.desc for c in id3.getall("COMM")] == ["iTunNORM"]
    assert tags.read_details(path)["artworks"] == []


def test_flac_round_trip(tmp_path):
    path = make_flac(tmp_path / "song.flac")
    tags.write_tags(path, FIELDS, lyrics="La la", artworks=[PNG])
    from mutagen.flac import FLAC
    flac = FLAC(path)
    assert flac["TITLE"] == ["Lights off the Lost Coast"]
    assert flac["TRACKNUMBER"] == ["1"] and flac["TRACKTOTAL"] == ["12"]
    assert flac["COMPILATION"] == ["1"]
    details = tags.read_details(path)
    assert details["lyrics"] == "La la" and details["artworks"] == [PNG]


def test_errors(tmp_path):
    with pytest.raises(tags.TagError):
        tags.write_tags(str(tmp_path / "missing.mp3"), {"name": "x"})
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"not audio at all")
    with pytest.raises(tags.TagError):
        tags.write_tags(str(junk), {"name": "x"})
    with pytest.raises(ValueError):
        tags.write_tags(make_mp3(tmp_path / "s.mp3"), {"play_count": 3})
