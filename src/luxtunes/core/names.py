"""Lists of names in one field: a song's Artists (each artist of a
collaboration, "Lümen; Grayfield") and its Featured Artists ("Ann; Bo").

Names are separated by semicolons, never commas or "&": those are part of
real names ("Earth, Wind & Fire", "Tyler, the Creator", "Simon & Garfunkel"),
while no artist name has a semicolon. Get Info shows these lists as pills,
so the semicolon only shows when editing a cell in the song list. Genres
are different: a genre list may use commas (no genre name has one).

The Artist field itself stays the text you see ("Lümen & Grayfield"); the
Artists list is what browsing goes by (Grid → Artists, Radio Mix, the phone):
each of its artists gets the song. No Qt in here.
"""

from __future__ import annotations

import re

SEPARATOR = ";"
# How collaborations are usually written in an Artist field.
COLLABORATION = re.compile(r"\s+(?:&|and|x|×|\+|with|vs\.?|/)\s+|\s*,\s+", re.IGNORECASE)


def split_names(text: str | None) -> list[str]:
    """"Lümen; Grayfield" -> ["Lümen", "Grayfield"]: each name once (ignoring case), in order."""
    seen, names = set(), []
    for part in (text or "").split(SEPARATOR):
        name = " ".join(part.split())
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


def join_names(names) -> str | None:
    """["Lümen", "Grayfield"] -> "Lümen; Grayfield" (None for no names): how they're stored."""
    text = "; ".join(split_names(SEPARATOR.join(n for n in names if n)))
    return text or None


def clean(text: str | None) -> str | None:
    """A typed or imported list, tidied: "Lümen ;Grayfield;" -> "Lümen; Grayfield"."""
    return join_names(split_names(text))


def from_old_featured(text: str | None) -> str | None:
    """Featured Artist from LuxTunes 0.9.0 and 0.9.1, which separated names with commas:
    "Ann, Bo" -> "Ann; Bo" (text that already has a semicolon is new style)."""
    if not text or SEPARATOR in text:
        return clean(text)
    return join_names(text.split(","))


def song_artists(track: dict) -> list[str]:
    """Who a song is by, for browsing: its Artists list, else its Artist."""
    names = split_names(track.get("artists"))
    if names:
        return names
    artist = (track.get("artist") or "").strip()
    return [artist] if artist else []


def suggest_split(artist: str | None, known) -> list[str]:
    """The Artists an Artist text probably means, or [] if it's one name:
    "Lümen & Grayfield" -> ["Lümen", "Grayfield"], but only when each part is
    already an artist in `known` (casefolded names). So "Simon & Garfunkel"
    stays whole: neither Simon nor Garfunkel is an artist on their own."""
    text = (artist or "").strip()
    if not text or text.casefold() in known and not COLLABORATION.search(text):
        return []
    parts = [p.strip() for p in COLLABORATION.split(text) if p and p.strip()]
    if len(parts) < 2 or not all(p.casefold() in known for p in parts):
        return []
    return split_names(SEPARATOR.join(parts))
