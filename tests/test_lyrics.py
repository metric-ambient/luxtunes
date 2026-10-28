"""Get Lyrics Online… (LRCLIB) and timed lyrics. Nothing goes online here:
the answers are made up."""

import io
import json
import urllib.error

import pytest

from luxtunes.core import db, library, lrc, lyrics_online, net

TIMED = "[ar:Someone]\n[00:01.50]First line\n[00:04.00][00:20.00]Chorus line\n[00:10.25]Third, line!\n"


def test_reading_timed_lyrics():
    lines = lrc.parse(TIMED)
    assert lines == [(1500, "First line"), (4000, "Chorus line"), (10250, "Third, line!"),
                     (20000, "Chorus line")]
    assert lrc.plain(TIMED) == "First line\nChorus line\nThird, line!\nChorus line"
    assert lrc.plain("Just words") == "Just words" and not lrc.is_timed("Just words")
    assert [lrc.line_at(lines, ms) for ms in (0, 1500, 9000, 25000)] == [-1, 0, 1, 3]
    assert lrc.same_words("first line chorus line third line chorus line", TIMED)
    assert not lrc.same_words("an entirely different song about something else", TIMED)


def fake_get(answers: dict):
    """answers: an address part -> what LRCLIB says (a dict / list, or an HTTP error code)."""
    asked = []

    def get(url, **_):
        asked.append(url)
        for part, answer in answers.items():
            if part in url:
                if isinstance(answer, int):
                    raise urllib.error.HTTPError(url, answer, "", {}, io.BytesIO())
                return json.dumps(answer).encode()
        raise urllib.error.HTTPError(url, 404, "", {}, io.BytesIO())
    get.asked = asked
    return get


SONG = {"id": 1, "name": "Robbers", "artist": "The 1975", "album": "The 1975",
        "total_time": 254_000}


def test_nothing_goes_online_without_your_ok():
    with pytest.raises(net.NotApproved):
        lyrics_online.find(SONG, approved=False, get=fake_get({}))
    with pytest.raises(net.NotApproved):
        lyrics_online.find_many([SONG], approved=None, get=fake_get({}))
    assert net.allowed("https://lrclib.net/api/get?track_name=x")


def test_exact_match_first_then_a_search_of_the_same_length():
    get = fake_get({"/get?": {"plainLyrics": "Words", "syncedLyrics": TIMED}})
    found = lyrics_online.find(SONG, approved=True, get=get)
    assert (found.plain, found.timed) == ("Words", TIMED.strip())
    assert "duration=254" in get.asked[0] and "album_name=The+1975" in get.asked[0]

    get = fake_get({"/get?": 404, "/search?": [
        {"duration": 180, "plainLyrics": "A shorter version"},          # not this recording
        {"duration": 255.4, "plainLyrics": "", "syncedLyrics": TIMED}]})
    found = lyrics_online.find(SONG, approved=True, get=get)
    assert found.plain == lrc.plain(TIMED) and found.timed == TIMED.strip()

    assert lyrics_online.find(SONG, approved=True, get=fake_get({"/get?": {"instrumental": True}})
                              ).instrumental
    assert lyrics_online.find({"id": 2, "name": "No artist"}, approved=True,
                              get=fake_get({})) is None


def test_many_songs_and_an_unreachable_lrclib():
    songs = [SONG, {**SONG, "id": 2, "name": "Unknown"}]
    get = fake_get({"track_name=Robbers": {"plainLyrics": "Words"}})
    result = lyrics_online.find_many(songs, approved=True, get=get)
    assert list(result.found) == [1] and result.not_found == ["Unknown"]

    def offline(url, **_):
        raise urllib.error.URLError("no network")
    result = lyrics_online.find_many(songs, approved=True, get=offline)
    assert result.found == {} and len(result.problems) == 1      # stops after the first


def test_what_gets_saved():
    found = lyrics_online.Found(plain=lrc.plain(TIMED), timed=TIMED)
    assert lyrics_online.to_save({}, "", found) == {"lyrics": lrc.plain(TIMED),
                                                    "synced_lyrics": TIMED}
    # A song with its own lyrics keeps them; timed ones only if they say the same.
    mine = "First line, chorus line. Third line; chorus line"
    assert lyrics_online.to_save({}, mine, found) == {"synced_lyrics": TIMED}
    assert lyrics_online.to_save({}, "Something else entirely, my own song", found) == {}
    assert lyrics_online.to_save({"synced_lyrics": TIMED}, mine, found) == {}


def test_timed_lyrics_are_kept_in_the_library_and_follow_edits(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    conn.execute("INSERT INTO tracks (persistent_id, name, lyrics) VALUES ('P1', 'Song', ?)",
                 (lrc.plain(TIMED),))
    conn.commit()
    library.set_synced_lyrics(conn, 1, TIMED)
    assert library.load_track(conn, 1)["synced_lyrics"] == TIMED
    library.update_track_fields(conn, 1, {"lyrics": lrc.plain(TIMED).replace("Third", "3rd")})
    assert library.load_track(conn, 1)["synced_lyrics"] == TIMED        # a small fix: kept
    library.update_track_fields(conn, 1, {"lyrics": "Completely new words for this one"})
    assert library.load_track(conn, 1)["synced_lyrics"] is None         # no longer fit


def test_upgrade_to_schema_11_adds_timed_lyrics(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    conn.execute("ALTER TABLE tracks DROP COLUMN synced_lyrics")
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()
    conn = db.connect(tmp_path / "library.db")
    assert "synced_lyrics" in {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
