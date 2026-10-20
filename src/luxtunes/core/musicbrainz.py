"""Right-click → Get Track Info Online…: look up songs already in the library
on MusicBrainz (the free, open music database) and propose corrections:
names, artists, album, year, track and disc numbers, genre, and missing
album covers (from the Cover Art Archive).

Nothing here talks to the internet unless the caller passes approved=True,
which the window only does after you've said yes. Nothing is changed either:
this returns proposals, which you review (each change can be unticked)
before anything is saved.

Songs are looked up an album at a time: the album is found by its name and
artist, then each song is matched to a track on it by its name (or, failing
that, its track number and length). Songs without an album are looked up by
name and artist. MusicBrainz asks for no more than one request a second.
No Qt in here.
"""

from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
from dataclasses import dataclass, field

from luxtunes.core.cd import (CdError, NotApproved, VARIOUS_ARTISTS, _credit, _get,
                              _top_genre, cover_art)

API = "https://musicbrainz.org/ws/2/"
REQUEST_GAP = 1.1                      # seconds between requests (MusicBrainz's limit)
MIN_SCORE = 80                         # how sure a search hit must be (0-100)

FIELD_LABELS = {"name": "Name", "artist": "Artist", "album_artist": "Album Artist",
                "album": "Album", "year": "Year", "track_number": "Track Number",
                "track_count": "Track Count", "disc_number": "Disc Number",
                "disc_count": "Disc Count", "genre": "Genre", "compilation": "Compilation"}


@dataclass
class Change:
    field: str
    old: object
    new: object

    @property
    def fills_blank(self) -> bool:
        return self.old in (None, "", 0)


@dataclass
class SongProposal:
    track: dict
    changes: list[Change]


@dataclass
class AlbumProposal:
    title: str                          # what was found ("The 1975 — The 1975 (2013)")
    songs: list[SongProposal] = field(default_factory=list)
    unmatched: list[dict] = field(default_factory=list)
    artwork: bytes | None = None        # offered when some of its songs have no artwork
    artwork_for: list[int] = field(default_factory=list)


@dataclass
class LookupResult:
    albums: list[AlbumProposal] = field(default_factory=list)
    not_found: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    stopped: bool = False


class _Client:
    """Spaces requests a second apart."""

    def __init__(self):
        self._last = 0.0

    def get(self, path: str, params: dict) -> dict:
        wait = self._last + REQUEST_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        url = API + path + "?" + urllib.parse.urlencode({**params, "fmt": "json"})
        try:
            return json.loads(_get(url))
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return {}
            raise CdError(f"MusicBrainz answered {error.code}.") from error
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise CdError(f"Couldn't reach MusicBrainz ({getattr(error, 'reason', error)}).") from error


# ---- Matching -----------------------------------------------------------------------

def _quote(text: str) -> str:
    """A phrase for MusicBrainz's search (Lucene) syntax."""
    return '"' + re.sub(r'(["\\\\])', r"\\\1", text) + '"'


def normalize(title: str | None) -> str:
    """Titles compared loosely: no case, accents, punctuation, or "(feat. …)"."""
    text = unicodedata.normalize("NFKD", title or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"[\(\[](feat|ft|with)\.?\s[^\)\]]*[\)\]]", "", text)
    text = text.replace("&", "and")
    return re.sub(r"[^0-9a-z]+", "", text)


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _album_artist(track: dict) -> str:
    return track.get("album_artist") or track.get("artist") or ""


def group_by_album(tracks: list[dict]) -> tuple[dict[tuple[str, str], list[dict]], list[dict]]:
    """(album artist, album) -> songs, plus the songs that have no album."""
    albums: dict[tuple[str, str], list[dict]] = {}
    loose = []
    for t in tracks:
        if t.get("album"):
            key = (_album_artist(t).casefold(), t["album"].casefold())
            albums.setdefault(key, []).append(t)
        else:
            loose.append(t)
    return albums, loose


def choose_release(results: list[dict], songs: list[dict]) -> dict | None:
    """The search hit that fits these songs best: a confident match whose
    track count fits (the songs' Track Count, or at least their highest track
    number), official releases and earlier ones first."""
    wanted_count = next((_int(s.get("track_count")) for s in songs if _int(s.get("track_count"))), None)
    highest = max((_int(s.get("track_number")) or 0 for s in songs), default=0)
    best, best_key = None, None
    for release in results:
        score = _int(release.get("score")) or 0
        if score < MIN_SCORE:
            continue
        count = _int(release.get("track-count")) or 0
        fits = (count == wanted_count) if wanted_count else count >= max(highest, len(songs))
        year = (release.get("date") or "")[:4]
        key = (fits, score, release.get("status") == "Official",
               -(int(year) if year.isdigit() else 9999))
        if best_key is None or key > best_key:
            best, best_key = release, key
    return best


def _release_tracks(release: dict) -> list[dict]:
    """Every track on the release, with its disc's position and size."""
    out = []
    media = release.get("media") or []
    for medium in media:
        tracks = medium.get("tracks") or []
        for info in tracks:
            recording = info.get("recording") or {}
            out.append({"title": info.get("title") or recording.get("title") or "",
                        "artist": _credit(info.get("artist-credit"))
                        or _credit(recording.get("artist-credit")),
                        "number": _int(info.get("position")),
                        "count": len(tracks),
                        "disc": _int(medium.get("position")),
                        "discs": len(media),
                        "length": _int(info.get("length")) or _int(recording.get("length")),
                        "genres": recording.get("genres") or []})
    return out


def match_track(song: dict, tracks: list[dict], used: set[int]) -> dict | None:
    """The release track this song is: same name (closest length if several),
    else the same disc and track number with a similar length."""
    name = normalize(song.get("name"))
    length = _int(song.get("total_time"))

    def length_gap(t):
        return abs((t["length"] or 0) - length) if length and t["length"] else 0

    same_name = [t for i, t in enumerate(tracks) if i not in used and normalize(t["title"]) == name]
    if same_name:
        return min(same_name, key=length_gap)
    number, disc = _int(song.get("track_number")), _int(song.get("disc_number")) or 1
    for i, t in enumerate(tracks):
        if i in used or t["number"] != number or (t["disc"] or 1) != disc:
            continue
        if not length or not t["length"] or length_gap(t) <= 5000:
            return t
    return None


def changes_for(song: dict, new: dict) -> list[Change]:
    """What would change: only real differences, and never clearing a value."""
    out = []
    for key in FIELD_LABELS:
        value = new.get(key)
        if value in (None, ""):
            continue
        old = song.get(key)
        if key == "compilation":
            old, value = 1 if old else 0, 1 if value else 0
            if not value:
                continue
        same = (str(old).strip() == str(value).strip()) if old not in (None, "") else False
        if not same:
            out.append(Change(key, old, value))
    return out


def album_fields(release: dict, track: dict) -> dict:
    credits = release.get("artist-credit") or []
    album_artist = _credit(credits)
    compilation = any((c.get("artist") or {}).get("id") == VARIOUS_ARTISTS for c in credits)
    group = release.get("release-group") or {}
    date = release.get("date") or group.get("first-release-date") or ""
    fields = {"name": track["title"], "artist": track["artist"] or album_artist,
              "album": release.get("title"), "year": int(date[:4]) if date[:4].isdigit() else None,
              "track_number": track["number"], "track_count": track["count"],
              "disc_number": track["disc"], "disc_count": track["discs"],
              "genre": _top_genre(release.get("genres"), group.get("genres"), track["genres"]),
              "compilation": 1 if compilation else 0}
    if compilation or (track["artist"] and track["artist"] != album_artist):
        fields["album_artist"] = album_artist
    return fields


def propose_album(release: dict, songs: list[dict]) -> AlbumProposal:
    tracks = _release_tracks(release)
    artist = _credit(release.get("artist-credit"))
    date = (release.get("date") or "")[:4]
    title = f"{artist} — {release.get('title', '')}" + (f" ({date})" if date else "")
    proposal = AlbumProposal(title)
    used: set[int] = set()
    for song in sorted(songs, key=lambda s: (_int(s.get("disc_number")) or 1,
                                             _int(s.get("track_number")) or 999)):
        track = match_track(song, tracks, used)
        if track is None:
            proposal.unmatched.append(song)
            continue
        used.add(tracks.index(track))
        changes = changes_for(song, album_fields(release, track))
        if changes:
            proposal.songs.append(SongProposal(song, changes))
    return proposal


# ---- Looking up -------------------------------------------------------------------

def lookup_songs(tracks: list[dict], *, approved: bool, artwork: bool = True, progress=None,
                 stop: threading.Event | None = None) -> LookupResult:
    """Proposals for these songs. progress(done, total, text) is called as it goes."""
    if approved is not True:
        raise NotApproved("Looking up track info online needs your OK first.")
    client = _Client()
    albums, loose = group_by_album(tracks)
    result = LookupResult()
    total = len(albums) + len(loose)
    done = 0

    def report(text):
        if progress is not None:
            progress(done, total, text)

    for songs in albums.values():
        if stop is not None and stop.is_set():
            result.stopped = True
            return result
        album, artist = songs[0]["album"], _album_artist(songs[0])
        report(f"Looking up “{album}”")
        try:
            query = f"release:{_quote(album)}" + (f" AND artist:{_quote(artist)}" if artist else "")
            found = client.get("release", {"query": query, "limit": 15}).get("releases") or []
            choice = choose_release(found, songs)
            if choice is None:
                result.not_found.extend(songs)
            else:
                release = client.get(f"release/{choice['id']}",
                                     {"inc": "recordings artist-credits genres release-groups"})
                proposal = propose_album(release, songs)
                needs_art = [s["id"] for s in songs if not s.get("artwork_count")]
                if artwork and needs_art:
                    group_id = (release.get("release-group") or {}).get("id", "")
                    proposal.artwork = cover_art(release.get("id", ""), group_id, approved=True)
                    if proposal.artwork:
                        proposal.artwork_for = needs_art
                if proposal.songs or proposal.artwork:
                    result.albums.append(proposal)
                result.not_found.extend(proposal.unmatched)
        except CdError as error:
            result.problems.append(f"{album}: {error}")
            if "reach" in str(error):
                return result               # offline: no point trying the rest
        done += 1

    for song in loose:
        if stop is not None and stop.is_set():
            result.stopped = True
            return result
        report(f"Looking up “{song.get('name')}”")
        try:
            proposal = _lookup_loose_song(client, song)
            if proposal is None:
                result.not_found.append(song)
            else:
                result.albums.append(proposal)
        except CdError as error:
            result.problems.append(f"{song.get('name')}: {error}")
            if "reach" in str(error):
                return result
        done += 1
    report("Done")
    return result


def _lookup_loose_song(client: _Client, song: dict) -> AlbumProposal | None:
    """A song with no album: find the recording by name and artist, then the
    earliest official release it's on."""
    query = f"recording:{_quote(song.get('name') or '')}"
    if song.get("artist"):
        query += f" AND artist:{_quote(song['artist'])}"
    recordings = client.get("recording", {"query": query, "limit": 5}).get("recordings") or []
    best = next((r for r in recordings if (_int(r.get("score")) or 0) >= 90
                 and normalize(r.get("title")) == normalize(song.get("name"))), None)
    if best is None or not best.get("releases"):
        return None
    releases = sorted(best["releases"], key=lambda r: (r.get("status") != "Official",
                                                        (r.get("date") or "9999")))
    release = releases[0]
    medium = (release.get("media") or [{}])[0]
    track = (medium.get("track") or [{}])[0]
    offset = _int(medium.get("track-offset"))
    info = {"title": best.get("title") or song.get("name"),
            "artist": _credit(best.get("artist-credit")),
            "number": (offset + 1) if offset is not None else _int(track.get("number")),
            "count": _int(medium.get("track-count")),
            "disc": _int(medium.get("position")), "discs": None, "genres": []}
    fields = album_fields(release, info)
    fields.pop("compilation", None)
    changes = changes_for(song, fields)
    if not changes:
        return None
    date = (release.get("date") or "")[:4]
    proposal = AlbumProposal(f"{info['artist']} — {release.get('title', '')}"
                             + (f" ({date})" if date else ""))
    proposal.songs.append(SongProposal(song, changes))
    return proposal
