"""Smart playlists: the rule format and which songs match.

fixtures/itunes_smart_playlists.json holds two stock smart playlists from an
imported iTunes library ("90's Music" and "Top 25 Most Played") exactly as
written, so reading and writing can be checked byte for byte."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from luxtunes.core import db, library, smart
from luxtunes.core.smart import Group, Rule

BLOBS = json.loads((Path(__file__).parent / "fixtures" / "itunes_smart_playlists.json").read_text())
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()


def blob(name):
    return bytes.fromhex(BLOBS[name]["info"]), bytes.fromhex(BLOBS[name]["criteria"])


def iso(days_ago):
    return datetime.fromtimestamp(NOW - days_ago * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_itunes_playlists_read_and_write_back_exactly():
    info, criteria = blob("nineties")
    playlist = smart.decode(info, criteria)
    assert smart.describe(playlist) == ("Year is in the range 1990 to 1999 and "
                                        "(Media Kind is Music or Media Kind is Music Video)")
    assert playlist.info.live and not playlist.info.limit and playlist.understood
    assert smart.encode(playlist) == (info, criteria)

    info, criteria = blob("top25")
    playlist = smart.decode(info, criteria)
    i = playlist.info
    assert (i.limit, i.limit_value, i.limit_type, i.limit_sort, i.reverse) == \
        (True, 25, smart.LIMIT_ITEMS, smart.SORT_PLAYS, False)          # "most often played"
    assert smart.describe(playlist) == "Podcast is false and Plays is greater than 0"
    assert smart.encode(playlist) == (info, criteria)


def test_new_rules_survive_a_round_trip():
    playlist = smart.SmartPlaylist(
        smart.Info(live=False, limit=True, limit_type=smart.LIMIT_MINUTES, limit_value=90,
                   limit_sort=smart.SORT_ADDED, reverse=True, checked_only=True),
        Group(True, [Rule(0x04, smart.S_CONTAINS, "Beyoncé ★"),
                     Rule(0x10, smart.RANGE, 1_600_000_000, 1_700_000_000),
                     Rule(0x17, smart.NOT_IN_LAST, amount=3, units=smart.MONTH),
                     Rule(0x28, smart.IS, "00AB00CD00EF0012"),
                     Group(False, [Rule(0x19, smart.GREATER, 60), Rule(0x1f, smart.IS_NOT, 1)])]))
    again = smart.decode(*smart.encode(playlist))
    assert again == playlist


def library_with(tmp_path, songs):
    conn = db.connect(tmp_path / "library.db")
    for i, song in enumerate(songs):
        row = {"persistent_id": f"{i:016X}", "name": f"Song {i}", **song}
        conn.execute(f"INSERT INTO tracks ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                     list(row.values()))
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY id")]
    return conn, ids


def matching(conn, *rules, any_=False, **info):
    playlist = smart.SmartPlaylist(smart.Info(**info), Group(any_, list(rules)))
    return smart.evaluate(conn, playlist, now=NOW)


def test_rules_pick_the_right_songs(tmp_path):
    conn, ids = library_with(tmp_path, [
        {"artist": "The 1975", "year": 2013, "play_count": 30, "rating": 100, "media_kind": "music",
         "date_added": iso(3), "play_date": iso(1), "total_time": 200_000, "compilation": 0},
        {"artist": "Beyoncé", "year": 1995, "play_count": 0, "rating": 60, "media_kind": "music",
         "date_added": iso(400), "total_time": 250_000, "compilation": 1, "enabled": 0},
        {"artist": "Film", "year": 1998, "play_count": 30, "media_kind": "movie",
         "date_added": iso(10), "play_date": iso(5), "total_time": 6_000_000},
        {"artist": "the 1975", "year": 2016, "play_count": 12, "rating": 80, "media_kind": "music",
         "date_added": iso(30), "play_date": iso(40), "total_time": 300_000},
    ])
    a, b, c, d = ids
    assert matching(conn, Rule(0x04, smart.S_CONTAINS, "1975")) == [a, d]        # any case
    assert matching(conn, Rule(0x04, smart.S_IS_NOT, "the 1975")) == [b, c]
    assert matching(conn, Rule(0x04, smart.S_STARTS, "bey")) == [b]
    assert matching(conn, Rule(0x07, smart.RANGE, 1990, 1999)) == [b, c]
    assert matching(conn, Rule(0x19, smart.GREATER, 60)) == [a, d]                # > 3 stars
    assert matching(conn, Rule(0x10, smart.IN_LAST, amount=2, units=smart.WEEK)) == [a, c]
    assert matching(conn, Rule(0x17, smart.NOT_IN_LAST, amount=1, units=smart.WEEK)) == [b, d]
    assert matching(conn, Rule(0x3c, smart.BIT_IS, 0x01 | 0x20)) == [a, b, d]      # music or video
    assert matching(conn, Rule(0x3c, smart.BIT_IS_NOT, 0x01)) == [c]
    assert matching(conn, Rule(0x1f, smart.IS, 1)) == [b]                        # compilation
    assert matching(conn, Rule(0x0d, smart.LESS, 260_000)) == [a, b]             # under 4:20
    assert matching(conn, Rule(0x07, smart.LESS, 2000), Rule(0x07, smart.GREATER, 2015),
                    any_=True) == [b, c, d]
    nested = Group(True, [Rule(0x07, smart.IS, 2013), Rule(0x07, smart.IS, 2016)])
    assert matching(conn, Rule(0x04, smart.S_CONTAINS, "1975"), nested) == [a, d]
    assert matching(conn, Rule(0x04, smart.S_CONTAINS, ""), checked_only=True) == [a, c, d]
    # Limits: 2 most often played (a tie at 30: the more recently played wins), then by time.
    assert matching(conn, limit=True, limit_value=2, limit_sort=smart.SORT_PLAYS) == [a, c]
    assert matching(conn, limit=True, limit_value=1, limit_sort=smart.SORT_PLAYS, reverse=True) == [b]
    assert matching(conn, limit=True, limit_type=smart.LIMIT_MINUTES, limit_value=9,
                    limit_sort=smart.SORT_ADDED) == [a, d]                       # 3:20 + 5:00
    assert matching(conn, Rule(0x04, smart.S_IS, "nobody"), use_rules=False) == [a, b, c, d]


def test_playlist_rules_and_loops(tmp_path):
    conn, ids = library_with(tmp_path, [{"genre": "Rock"}, {"genre": "Pop"}, {"genre": "Rock"}])
    mix = library.create_playlist(conn, "Mix")
    library.add_to_playlist(conn, mix, [ids[1]])
    mix_pid = conn.execute("SELECT persistent_id FROM playlists WHERE id = ?", (mix,)).fetchone()[0]
    rock = smart.create(conn, "Rock", smart.SmartPlaylist(rules=Group(False, [Rule(0x08, smart.S_IS, "rock")])))
    rock_pid = conn.execute("SELECT persistent_id FROM playlists WHERE id = ?", (rock,)).fetchone()[0]
    assert library.playlist_track_ids(conn, rock) == {ids[0], ids[2]}
    either = smart.SmartPlaylist(rules=Group(True, [Rule(0x28, smart.IS, mix_pid),
                                                    Rule(0x28, smart.IS, rock_pid)]))
    assert smart.evaluate(conn, either) == ids
    # A playlist that names itself doesn't loop forever.
    loop = smart.create(conn, "Loop", smart.default_playlist())
    loop_pid = conn.execute("SELECT persistent_id FROM playlists WHERE id = ?", (loop,)).fetchone()[0]
    smart.save(conn, loop, smart.SmartPlaylist(rules=Group(False, [Rule(0x28, smart.IS, loop_pid)])))
    assert library.playlist_track_ids(conn, loop) == set()


def test_refresh_keeps_order_and_follows_the_library(tmp_path):
    conn, ids = library_with(tmp_path, [{"play_count": 0}, {"play_count": 0}, {"play_count": 3}])
    unplayed = smart.create(conn, "Unplayed", smart.SmartPlaylist(
        rules=Group(False, [Rule(0x16, smart.IS, 0)])))
    order = lambda: [r[0] for r in conn.execute(
        "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position", (unplayed,))]
    assert order() == ids[:2]
    library.set_playlist_order(conn, unplayed, [ids[1], ids[0]])      # Copy To Play Order
    library.record_play(conn, ids[0])
    conn.execute("UPDATE tracks SET play_count = 0 WHERE id = ?", (ids[2],))
    conn.commit()
    assert smart.refresh(conn, unplayed) is True
    assert order() == [ids[1], ids[2]]                       # kept its place; the new one at the end
    assert smart.refresh(conn, unplayed) is False             # nothing new
    # Not live: only changes when edited.
    playlist = smart.load(conn, unplayed)
    playlist.info.live = False
    smart.save(conn, unplayed, playlist)
    library.record_play(conn, ids[1])
    assert smart.refresh(conn, unplayed) is False and ids[1] in order()
    assert smart.refresh_all(conn) == 0
    # Duplicating keeps the rules.
    copy_id = library.duplicate_playlist(conn, unplayed)
    assert smart.load(conn, copy_id) == smart.load(conn, unplayed)


def test_unknown_rules_are_kept_and_left_alone(tmp_path):
    conn, ids = library_with(tmp_path, [{}])
    strange = Rule(0x99, 0x01, raw=b"\x00\x00\x00\x99" + b"\x00\x00\x00\x01" + bytes(44)
                   + b"\x00\x00\x00\x02" + b"\x00\x41")
    playlist = smart.SmartPlaylist(rules=Group(False, [strange]))
    info, criteria = smart.encode(playlist)
    back = smart.decode(info, criteria)
    assert not back.understood and back.rules.rules[0].raw == strange.raw
    playlist_id = library.create_playlist(conn, "Odd")
    library.add_to_playlist(conn, playlist_id, ids)
    conn.execute("UPDATE playlists SET is_smart = 1, smart_info = ?, smart_criteria = ? WHERE id = ?",
                 (info, criteria, playlist_id))
    conn.commit()
    assert smart.refresh(conn, playlist_id) is False            # keeps the songs it was imported with


def test_export(tmp_path):
    tracks = [{"name": "Robbers", "artist": "The 1975", "album": "The 1975", "total_time": 254_000,
               "location": "/music/06 Robbers.mp3"}]
    library.export_playlist(tracks, str(tmp_path / "a.m3u8"), "m3u8")
    assert (tmp_path / "a.m3u8").read_text().splitlines() == [
        "#EXTM3U", "#EXTINF:254,The 1975 - Robbers", "/music/06 Robbers.mp3"]
    library.export_playlist(tracks, str(tmp_path / "a.txt"), "txt")
    assert "Robbers\tThe 1975\tThe 1975\t\t4:14\t/music/06 Robbers.mp3" in (tmp_path / "a.txt").read_text()
