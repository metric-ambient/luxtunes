"""Grouping songs into albums (and artists, genres, composers) for the
Album List, Grid and Cover Flow views. No Qt here, so it's easy to test.
"""

from __future__ import annotations

from luxtunes.core import names
from luxtunes.core.genres import split_field
from luxtunes.ui.formatting import sort_text


def album_artist_of(track: dict) -> str:
    return track.get("album_artist") or track.get("artist") or ""


def album_subtitle(track: dict, fallback: str = "Unknown Artist") -> str:
    """The artist shown under an album: a compilation shows its Album Artist,
    or "Various Artists" without one (as iTunes does)."""
    if track.get("compilation"):
        return track.get("album_artist") or "Various Artists"
    return album_artist_of(track) or fallback


def grid_artist(track: dict) -> str:
    """Grid → Artists: who a song is filed under. A compilation's songs go
    under its Album Artist or "Various Artists" (or, with "Group compilations
    when browsing" off, their own artist)."""
    from luxtunes.ui import formatting
    if track.get("compilation"):
        if formatting.GROUP_COMPILATIONS:
            return track.get("album_artist") or "Various Artists"
        return track.get("artist") or ""
    return album_artist_of(track)


def grid_artists(track: dict) -> list[str]:
    """Grid → Artists: every artist a song is filed under. A song with an
    Artists list ("Lümen; Grayfield") is under each of them rather than under
    "Lümen & Grayfield"; its album's own artist keeps it too ("Lümen", for a
    Lümen album with one song together with Grayfield)."""
    from luxtunes.ui import formatting
    base = grid_artist(track)
    listed = names.split_names(track.get("artists"))
    if not listed or track.get("compilation") and formatting.GROUP_COMPILATIONS:
        return [base]
    known = {n.casefold() for n in listed}
    together = base.casefold() == (track.get("artist") or "").casefold() \
        or names.suggest_split(base, known)
    return names.split_names("; ".join(([] if together or not base else [base]) + listed))


def _grouped_compilation(track: dict) -> bool:
    """A compilation's song kept together under Various Artists / its Album Artist."""
    from luxtunes.ui import formatting
    return bool(track.get("compilation")) and formatting.GROUP_COMPILATIONS


def album_key(track: dict) -> tuple:
    """Two songs belong to the same album if album artist + album match
    (ignoring case). Compilations are grouped by album name alone."""
    album = (track.get("album") or "").casefold()
    if track.get("compilation"):
        return ("\x00compilation", album)
    return (album_artist_of(track).casefold(), album)


def consecutive_groups(tracks: list[dict]) -> list[tuple[int, int]]:
    """Runs of neighboring songs from the same album, as (first_row, count).
    Used by Album List view: each run gets one album cell beside it."""
    groups = []
    start = 0
    for i in range(1, len(tracks) + 1):
        if i == len(tracks) or album_key(tracks[i]) != album_key(tracks[start]):
            if tracks:
                groups.append((start, i - start))
            start = i
    return groups


def _track_order(track: dict) -> tuple:
    return (track.get("disc_number") or 0, track.get("track_number") or 0,
            sort_text(track.get("name")))


def albums_in_order(tracks: list[dict]) -> list[dict]:
    """Albums in the order they first appear in `tracks` (Cover Flow follows
    the song list's order). Each album: title, subtitle, tracks (in the order
    they appear in the list), first_track."""
    albums: dict[tuple, dict] = {}
    for track in tracks:
        key = album_key(track)
        album = albums.get(key)
        if album is None:
            album = albums[key] = {
                "key": key,
                "title": track.get("album") or "Unknown Album",
                "subtitle": album_subtitle(track),
                "tracks": [],
            }
        album["tracks"].append(track)
    result = list(albums.values())
    for album in result:
        album["first_track"] = album["tracks"][0]
        album["art_track"] = album["tracks"][0]
    return result


def featured_on(tracks: list[dict], artist: str, exclude=frozenset()) -> list[dict]:
    """The songs among `tracks` that list `artist` as a Featured Artist
    (ignoring case), in Artist > Album > track order, leaving out the songs
    whose ids are in `exclude` (the artist's own)."""
    from luxtunes.ui import formatting
    wanted = artist.casefold()
    found = [t for t in tracks if t.get("id") not in exclude and wanted in
             (n.casefold() for n in formatting.featured_names(t.get("featured_artist")))]
    return sorted(found, key=formatting.sort_key("artist"))


GRID_GROUPINGS = ("albums", "artists", "genres", "composers")
PODCAST_GROUPINGS = ("podcasts", "podcast_categories", "unplayed")
AUDIOBOOK_GROUPINGS = ("books", "authors", "book_categories")
# Grid's tabs for each library section.
GRID_TABS = {"podcast": PODCAST_GROUPINGS, "audiobook": AUDIOBOOK_GROUPINGS}


def show_of(track: dict) -> str:
    """A podcast episode's show: its album (that's where podcasts keep it)."""
    return track.get("album") or track.get("artist") or ""


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'s' if count != 1 else ''}"


def grid_groups(tracks: list[dict], by: str) -> list[dict]:
    """Groups for Grid view, sorted A-Z.

    Music:      "albums" (subtitle: artist), "artists" / "genres" / "composers"
                (subtitle: number of albums; songs without a composer are left
                out of Composers).
    Audiobooks: "books" (subtitle: author = artist), "authors",
                "book_categories" (by genre; subtitle: number of books).
    Podcasts:   "podcasts" (by show), "podcast_categories" (by genre; subtitle:
                number of episodes), "unplayed" (shows with unplayed episodes,
                and only those episodes).
    Music and books are in album order (disc, track); podcast episodes newest first.
    """
    if by not in GRID_GROUPINGS + PODCAST_GROUPINGS + AUDIOBOOK_GROUPINGS:
        raise ValueError(by)
    if by == "unplayed":
        tracks = [t for t in tracks if not t.get("play_count")]
    groups: dict = {}
    on_compilations = []         # Artists: compilation songs, for their artists' own entries
    for track in tracks:
        if by in ("albums", "books"):
            fallback = "Unknown Album" if by == "albums" else "Unknown Book"
            key = album_key(track)
            title = track.get("album") or (track.get("name") if by == "books" else "") or fallback
        elif by in ("artists", "authors"):
            unknown = "Unknown Artist" if by == "artists" else "Unknown Author"
            for name in grid_artists(track) if by == "artists" else [grid_artist(track)]:
                key = name.casefold()
                group = groups.setdefault(key, {"key": key, "title": name or unknown, "tracks": []})
                group["tracks"].append(track)
            if by == "artists" and _grouped_compilation(track):
                on_compilations.append(track)
            continue
        elif by in ("genres", "book_categories", "podcast_categories"):
            # A song with several genres ("Metalcore; Post-hardcore") is in each.
            unknown = "Unknown Genre" if by == "genres" else "Unknown Category"
            for name in split_field(track.get("genre")) or [""]:
                key = name.casefold()
                group = groups.setdefault(key, {"key": key, "title": name or unknown, "tracks": []})
                group["tracks"].append(track)
            continue
        elif by in ("podcasts", "unplayed"):
            name = show_of(track)
            key, title = name.casefold(), name or "Unknown Podcast"
        else:
            name = track.get("composer") or ""
            if not name:
                continue
            key, title = name.casefold(), name
        group = groups.setdefault(key, {"key": key, "title": title, "tracks": []})
        group["tracks"].append(track)

    # A grouped compilation's song stays under Various Artists (or the Album Artist),
    # and is also listed under each of its artists who have songs outside
    # compilations. An artist only ever on compilations gets no entry of their own,
    # so a guest on one compilation track doesn't clutter the list.
    own = {key for key, group in groups.items()
           if any(not _grouped_compilation(t) for t in group["tracks"])}
    for track in on_compilations:
        for name in names.song_artists(track):
            key = name.casefold()
            if key in own and all(t is not track for t in groups[key]["tracks"]):
                groups[key]["tracks"].append(track)

    podcasts = by in PODCAST_GROUPINGS
    result = list(groups.values())
    for group in result:
        if podcasts:
            group["tracks"].sort(key=lambda t: t.get("release_date") or t.get("date_added") or "",
                                 reverse=True)
        else:
            group["tracks"].sort(key=lambda t: (sort_text(album_artist_of(t)),
                                                sort_text(t.get("album")), *_track_order(t)))
        group["art_track"] = group["tracks"][0]
        count = len(group["tracks"])
        if by in ("albums", "books"):
            first = group["tracks"][0]
            fallback = "Unknown Artist" if by == "albums" else "Unknown Author"
            group["subtitle"] = album_subtitle(first, fallback)
            group["sort"] = (sort_text(group["subtitle"]), sort_text(group["title"]))
        else:
            if podcasts:
                word = "unplayed episode" if by == "unplayed" else "episode"
                group["subtitle"] = _plural(count, word)
            else:
                books = len({album_key(t) for t in group["tracks"]})
                group["subtitle"] = _plural(books, "album" if by in GRID_GROUPINGS else "book")
            group["sort"] = (sort_text(group["title"]),)
    result.sort(key=lambda g: g["sort"])
    return result
