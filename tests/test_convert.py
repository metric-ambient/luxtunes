"""Create MP3 Version: FLAC -> 320 kbps MP3 beside the original."""

import shutil
import subprocess

import pytest

pytest.importorskip("mutagen")

from luxtunes.core import convert, db, library, organize, tags  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
       b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")


def test_target_and_command(tmp_path):
    flac = tmp_path / "06 Robbers.flac"
    flac.write_bytes(b"x")
    assert convert.mp3_target(str(flac)) == tmp_path / "06 Robbers.mp3"
    (tmp_path / "06 Robbers.mp3").write_bytes(b"x")
    assert convert.mp3_target(str(flac)) == tmp_path / "06 Robbers 1.mp3"
    cmd = convert.command("ffmpeg", "a.flac", "a.mp3", 96000)
    assert cmd[-1] == "a.mp3" and "320k" in cmd and "44100" in cmd
    assert "44100" not in convert.command("ffmpeg", "a.flac", "a.mp3", 44100)
    assert convert.can_convert({"location": "/x/Song.FLAC"})
    assert not convert.can_convert({"location": "/x/Song.mp3"})


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_real_conversion(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    root = tmp_path / "Media"
    organize.set_media_folder(conn, root)
    folder = root / "Music" / "The 1975" / "The 1975"
    folder.mkdir(parents=True)
    flac = folder / "06 Robbers.flac"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(flac)], check=True)
    tags.write_tags(str(flac), {"name": "Robbers", "artist": "The 1975", "album": "The 1975",
                                "track_number": 6}, lyrics="La la", artworks=[PNG])
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, album, track_number, rating, "
                 "location) VALUES ('P1', 'Robbers', 'The 1975', 'The 1975', 6, 80, ?)", (str(flac),))
    conn.commit()
    source = library.load_track(conn, 1)

    target = convert.mp3_target(str(flac))
    subprocess.run(convert.command("ffmpeg", str(flac), str(target), 44100), check=True)
    new_id = convert.finish(conn, source, str(flac), str(target))

    new = library.load_track(conn, new_id)
    assert new["location"] == str(folder / "06 Robbers.mp3")
    assert new["name"] == "Robbers" and new["kind"] == "MPEG audio file" and new["rating"] == 80
    assert new["bit_rate"] == 320
    details = tags.read_details(new["location"])
    assert details["lyrics"] == "La la" and details["artworks"] == [PNG]
    assert details["tag_version"] == "v2.3"
    assert flac.exists()
