"""Radio: mixes of related songs, likes and dislikes."""

import json
import random

from luxtunes.core import db, library, radio

GENRES = ["Metalcore", "Post-hardcore", "Deathcore", "Emo", "Screamo", "Country", "Jazz"]


def small_library(tmp_path, per_genre=30):
    conn = db.connect(tmp_path / "library.db")
    n = 0
    for genre in GENRES:
        for i in range(per_genre):
            n += 1
            conn.execute(
                "INSERT INTO tracks (persistent_id, name, artist, album, genre, year, play_count, "
                "location, media_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'music')",
                (f"{n:016X}", f"{genre} song {i}", f"{genre} artist {i % 8}", f"{genre} album {i % 4}",
                 genre, 2005 + i % 10, i % 5, f"/music/{n}.mp3"))
    conn.commit()
    return conn


def song(conn, name):
    row = conn.execute("SELECT id FROM tracks WHERE name = ?", (name,)).fetchone()
    return library.load_track(conn, row[0])


def genres_of(conn):
    return [t["genre"] for t in radio.tracks(conn)]


def test_a_song_radio(tmp_path):
    conn = small_library(tmp_path)
    seed = song(conn, "Metalcore song 0")
    r = radio.start(conn, "song", seed, rng=random.Random(1))
    assert r.title == "Metalcore song 0 Radio" and r.kind == "song"
    songs = radio.tracks(conn)
    assert len(songs) == radio.RADIO_SIZE and songs[0]["id"] == seed["id"]
    assert len({t["id"] for t in songs}) == len(songs)
    picked = genres_of(conn)
    assert "Country" not in picked and "Jazz" not in picked       # unrelated genres stay out
    assert picked.count("Metalcore") > picked.count("Emo")
    artists = [t["artist"] for t in songs]
    assert max(artists.count(a) for a in set(artists)) <= round(radio.RADIO_SIZE * 0.2)
    saved = json.loads(conn.execute("SELECT value FROM library_info WHERE key = 'radio'")
                       .fetchone()[0])
    assert all(g.startswith(("mb:", "wd:")) for g in saved["genres"])   # lasting ids


def test_album_and_artist_titles():
    track = {"name": "Walls", "artist": "Emery", "album": "The Question"}
    assert radio.title_for("album", track) == "The Question Radio"
    assert radio.title_for("artist", track) == "Emery Radio"
    assert radio.title_for("song", track) == "Walls Radio"


def test_growing_refreshing_removing_and_saving(tmp_path):
    conn = small_library(tmp_path)
    radio.start(conn, "artist", song(conn, "Post-hardcore song 3"), rng=random.Random(2))
    before = [t["id"] for t in radio.tracks(conn)]
    added = radio.extend(conn, rng=random.Random(3))
    after = [t["id"] for t in radio.tracks(conn)]
    assert added and after[:len(before)] == before and not set(added) & set(before)
    radio.remove(conn, [after[1]])
    assert after[1] not in [t["id"] for t in radio.tracks(conn)]
    playlist_id = radio.save_as_playlist(conn)
    assert conn.execute("SELECT name FROM playlists WHERE id = ?", (playlist_id,)).fetchone()[0] \
        == "Post-hardcore artist 3 Radio"
    assert len(library.playlist_track_ids(conn, playlist_id)) == len(after) - 1
    r = radio.refresh(conn, discover=True, rng=random.Random(4))
    assert r.discover and r.title == "Post-hardcore artist 3 Radio" and radio.tracks(conn)


def test_left_out(tmp_path):
    conn = small_library(tmp_path)
    copy = song(conn, "Metalcore song 1")
    conn.execute("INSERT INTO tracks (persistent_id, name, artist, genre, location, media_kind) "
                 "VALUES ('FFFFFFFFFFFFFFFF', ?, ?, 'Metalcore', '/music/copy.m4a', 'music')",
                 (copy["name"], copy["artist"]))
    conn.execute("UPDATE tracks SET enabled = 0 WHERE name = 'Metalcore song 2'")
    conn.execute("UPDATE tracks SET skip_when_shuffling = 1 WHERE name = 'Metalcore song 3'")
    conn.execute("UPDATE tracks SET media_kind = 'podcast' WHERE name = 'Metalcore song 4'")
    conn.commit()
    for seed in range(5):
        radio.start(conn, "song", song(conn, "Metalcore song 0"), rng=random.Random(seed))
        names = [t["name"] for t in radio.tracks(conn)]
        assert names.count("Metalcore song 1") <= 1                  # one copy of a song
        assert not {"Metalcore song 2", "Metalcore song 3", "Metalcore song 4"} & set(names)


def test_likes_and_dislikes(tmp_path):
    conn = small_library(tmp_path)
    seed = song(conn, "Metalcore song 0")
    radio.start(conn, "song", seed, rng=random.Random(5))
    disliked = song(conn, "Deathcore song 5")
    radio.set_opinion(conn, disliked["id"], -1)
    liked = song(conn, "Emo song 7")
    radio.set_opinion(conn, liked["id"], 1)
    feedback = {t["name"]: (t["radio_opinion"], t["radio_from"]) for t in radio.feedback_tracks(conn)}
    assert feedback == {"Deathcore song 5": (-1, "Metalcore song 0 Radio"),
                        "Emo song 7": (1, "Metalcore song 0 Radio")}
    liked_seen = 0
    for n in range(10):
        radio.start(conn, "song", seed, rng=random.Random(10 + n))
        ids = [t["id"] for t in radio.tracks(conn)]
        assert disliked["id"] not in ids                             # never on radios like this
        liked_seen += liked["id"] in ids
    radio.set_opinion(conn, liked["id"], 0)
    without = 0
    for n in range(10):
        radio.start(conn, "song", seed, rng=random.Random(10 + n))
        without += liked["id"] in [t["id"] for t in radio.tracks(conn)]
    assert liked_seen >= 5 and liked_seen > without                  # an Emo song, favoured
    radio.set_opinion(conn, liked["id"], 1)
    opinions = {t["id"]: t["radio_opinion"] for t in radio.tracks(conn)}
    assert opinions.get(liked["id"], 1) == 1
    radio.set_opinion(conn, disliked["id"], 0)                       # back to neutral
    assert [t["name"] for t in radio.feedback_tracks(conn)] == ["Emo song 7"]
    library.delete_tracks(conn, [liked["id"]])
    assert radio.feedback_tracks(conn) == []                         # gone with the song


def test_discover_prefers_songs_you_rarely_play(tmp_path):
    conn = small_library(tmp_path)
    conn.execute("UPDATE tracks SET play_count = 500 WHERE name LIKE 'Metalcore song 1%'")
    conn.commit()
    seed = song(conn, "Metalcore song 0")
    plays = {}
    for discover in (False, True):
        total = 0
        for n in range(5):
            radio.start(conn, "song", seed, discover=discover, rng=random.Random(20 + n))
            total += sum(t.get("play_count") or 0 for t in radio.tracks(conn)[1:])
        plays[discover] = total
    assert plays[True] < plays[False]
