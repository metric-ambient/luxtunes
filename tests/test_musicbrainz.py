"""Get Track Info Online…: matching library songs to MusicBrainz releases.
MusicBrainz's answers are made up here; nothing goes online."""

import json

import pytest

from luxtunes.core import cd, musicbrainz


def credit(name, mbid="a1"):
    return [{"name": name, "joinphrase": "", "artist": {"id": mbid, "name": name}}]


RELEASE = {
    "id": "rel-1", "title": "The 1975", "date": "2013-09-02", "status": "Official",
    "artist-credit": credit("The 1975"), "release-group": {"id": "rg-1", "genres": []},
    "genres": [{"name": "indie pop", "count": 4}],
    "media": [{"position": 1, "tracks": [
        {"position": 1, "title": "The 1975", "length": 80000, "recording": {}},
        {"position": 2, "title": "The City", "length": 244000, "recording": {}},
        {"position": 3, "title": "M.O.N.E.Y.", "length": 204000, "recording": {}},
    ]}],
}


def song(i, name, **extra):
    return {"id": i, "name": name, "artist": "The 1975", "album": "The 1975", "artwork_count": 1,
            **extra}


def fake_musicbrainz(monkeypatch, answers):
    calls = []

    def get(url, timeout=15):
        calls.append(url)
        for part, answer in answers.items():
            if part in url:
                return json.dumps(answer).encode()
        raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(musicbrainz, "_get", get)
    monkeypatch.setattr(musicbrainz, "REQUEST_GAP", 0)
    return calls


def test_nothing_goes_online_without_your_ok(monkeypatch):
    calls = fake_musicbrainz(monkeypatch, {})
    with pytest.raises(cd.NotApproved):
        musicbrainz.lookup_songs([song(1, "The City")], approved=False)
    with pytest.raises(cd.NotApproved):
        cd.lookup(cd.blank_disc("/dev/sr0", cd.Toc(1, 1, [150], 300)), approved=False)
    with pytest.raises(cd.NotApproved):
        cd.cover_art("rel-1", approved=False)
    assert calls == []


def test_normalize_and_match():
    assert musicbrainz.normalize("M.O.N.E.Y.") == musicbrainz.normalize("money")
    assert musicbrainz.normalize("Sex (feat. Someone)") == "sex"
    assert musicbrainz.normalize("Café & Bar") == "cafeandbar"
    tracks = musicbrainz._release_tracks(RELEASE)
    assert musicbrainz.match_track({"name": "the city"}, tracks, set())["number"] == 2
    # No name match: same track number with a similar length.
    by_number = {"name": "Track 03", "track_number": 3, "total_time": 203000}
    assert musicbrainz.match_track(by_number, tracks, set())["title"] == "M.O.N.E.Y."
    far_off = {"name": "Track 03", "track_number": 3, "total_time": 400000}
    assert musicbrainz.match_track(far_off, tracks, set()) is None


def test_choose_release_prefers_the_right_track_count():
    songs = [song(1, "The City", track_count=16)]
    results = [{"id": "deluxe", "score": 100, "track-count": 22, "date": "2013"},
               {"id": "standard", "score": 95, "track-count": 16, "date": "2013", "status": "Official"},
               {"id": "unsure", "score": 40, "track-count": 16}]
    assert musicbrainz.choose_release(results, songs)["id"] == "standard"
    assert musicbrainz.choose_release([results[2]], songs) is None


def test_changes_never_clear_a_value():
    old = {"name": "The City", "year": None, "genre": "Rock", "compilation": 0}
    changes = {c.field: c for c in musicbrainz.changes_for(
        old, {"name": "The City", "year": 2013, "genre": "", "compilation": 0})}
    assert set(changes) == {"year"} and changes["year"].fills_blank


def test_lookup_songs(monkeypatch):
    calls = fake_musicbrainz(monkeypatch, {
        "/release?": {"releases": [{"id": "rel-1", "score": 100, "track-count": 3}]},
        "/release/rel-1": RELEASE,
    })
    monkeypatch.setattr(musicbrainz, "cover_art", lambda *a, approved: b"\xff\xd8\xffcover")
    songs = [song(1, "The City", track_number=2, year=2013, genre="Indie Pop", track_count=3,
                  disc_number=1, disc_count=1),
             song(2, "money", track_number=None, artwork_count=0),
             song(3, "Not On This Album")]
    seen = []
    result = musicbrainz.lookup_songs(songs, approved=True, progress=lambda *a: seen.append(a))
    assert len(result.albums) == 1 and not result.problems
    album = result.albums[0]
    assert album.title == "The 1975 — The 1975 (2013)"
    by_id = {p.track["id"]: {c.field: c.new for c in p.changes} for p in album.songs}
    assert 1 not in by_id                                   # already right: nothing to change
    assert by_id[2]["name"] == "M.O.N.E.Y." and by_id[2]["track_number"] == 3
    assert by_id[2]["year"] == 2013 and by_id[2]["genre"] == "Indie Pop"
    assert album.artwork == b"\xff\xd8\xffcover" and album.artwork_for == [2]
    assert [s["id"] for s in result.not_found] == [3]
    assert 'release%3A%22The+1975%22+AND+artist%3A%22The+1975%22' in calls[0]
    assert seen and seen[-1][2] == "Done"


def test_offline_stops_early(monkeypatch):
    def offline(url, timeout=15):
        raise OSError("Network is unreachable")
    monkeypatch.setattr(musicbrainz, "_get", offline)
    monkeypatch.setattr(musicbrainz, "REQUEST_GAP", 0)
    tracks = [song(1, "A", album="One"), song(2, "B", album="Two")]
    result = musicbrainz.lookup_songs(tracks, approved=True)
    assert len(result.problems) == 1 and "Couldn't reach MusicBrainz" in result.problems[0]
