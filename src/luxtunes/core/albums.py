"""Grouping songs into albums (and artists, genres, composers) for the
Album List, Grid and Cover Flow views. No Qt here, so it's easy to test.
"""

from __future__ import annotations

from luxtunes.ui.formatting import sort_text


def album_artist_of(track: dict) -> str:
    return track.get("album_artist") or track.get("artist") or ""


def album_key(track: dict) -> tuple:
    """Two songs belong to the same album if album artist + album match
    (ignoring case). Compilations are grouped by album name alone."""
    album = (track.get("album") or "").casefold()
    if track.get("compilation"):
        return ("\x00compilation", album)
    return (album_artist_of(track).casefold(), album)


def consecutive_groups(tracks: list[dict]) -> list[tuple[int, int]]:
    """Runs of neighbouring songs from the same album, as (first_row, count).
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
                "subtitle": "Various Artists" if track.get("compilation")
                            else (album_artist_of(track) or "Unknown Artist"),
                "tracks": [],
            }
        album["tracks"].append(track)
    result = list(albums.values())
    for album in result:
        album["first_track"] = album["tracks"][0]
        album["art_track"] = album["tracks"][0]
    return result


GRID_GROUPINGS = ("albums", "artists", "genres", "composers")


def grid_groups(tracks: list[dict], by: str) -> list[dict]:
    """Groups for Grid view, sorted A-Z.

    by = "albums": one tile per album (subtitle: artist)
         "artists" / "genres" / "composers": one tile per name
         (subtitle: number of albums). Songs without a composer are left out
         of Composers.
    Each group's tracks are in album order (disc, track), ready to play.
    """
    if by not in GRID_GROUPINGS:
        raise ValueError(by)
    groups: dict = {}
    for track in tracks:
        if by == "albums":
            key, title = album_key(track), track.get("album") or "Unknown Album"
        elif by == "artists":
            name = album_artist_of(track)
            key, title = name.casefold(), name or "Unknown Artist"
        elif by == "genres":
            name = track.get("genre") or ""
            key, title = name.casefold(), name or "Unknown Genre"
        else:
            name = track.get("composer") or ""
            if not name:
                continue
            key, title = name.casefold(), name
        group = groups.setdefault(key, {"key": key, "title": title, "tracks": []})
        group["tracks"].append(track)

    result = list(groups.values())
    for group in result:
        group["tracks"].sort(key=lambda t: (sort_text(album_artist_of(t)),
                                            sort_text(t.get("album")), *_track_order(t)))
        group["art_track"] = group["tracks"][0]
        if by == "albums":
            first = group["tracks"][0]
            group["subtitle"] = ("Various Artists" if first.get("compilation")
                                 else album_artist_of(first) or "Unknown Artist")
            group["sort"] = (sort_text(group["subtitle"]), sort_text(group["title"]))
        else:
            count = len({album_key(t) for t in group["tracks"]})
            group["subtitle"] = f"{count} album{'s' if count != 1 else ''}"
            group["sort"] = (sort_text(group["title"]),)
    result.sort(key=lambda g: g["sort"])
    return result
