"""LuxTunes' genre catalogue: about 3,300 music genres and how they connect,
built from MusicBrainz and Wikidata (both public domain) by
packaging/genres/build_genre_catalogue.py, and shipped in resources/genres.json. Nothing
here goes online.

    genre_ids("Metalcore; Post-Hardcore")   -> the catalogue genres a Genre field means
    suggest("post h")                       -> catalogue names for the Genre box
    related(ids)                            -> {genre: closeness 0..1} for Genius

How close two genres are comes from the links: subgenre/parent, overlapping,
fusion, part of, influence (strongest first). From a starting genre, a random
walk that keeps returning to it spreads over its neighbours; a genre linked to
everything (Rock, Pop, Electronic) is discounted so it doesn't make every pair
of genres look related. No Qt in here.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

CATALOGUE = Path(__file__).resolve().parent.parent / "resources" / "genres.json"
WEIGHTS = {"o": 0.9, "p": 0.8, "f": 0.5, "t": 0.5, "i": 0.4}
SEPARATORS = re.compile(r"\s*[;/,]\s*")       # "Metalcore; Post-Hardcore", "Rock/Pop"
RETURN = 0.3            # how often the walk goes back to where it started
STEPS = 30
HUB_DISCOUNT = 0.5      # closeness / (number of links) ** this


def split_field(field: str | None) -> list[str]:
    """A song's genres: "Metalcore, Post-hardcore" -> ["Metalcore", "Post-hardcore"].
    Several genres are kept in the one Genre field, separated by commas or
    semicolons ("R&B/Soul" stays one: a slash only
    separates when matching names against the catalogue)."""
    return [part.strip() for part in re.split(r"[;,]", field or "") if part.strip()]


def key(text: str) -> str:
    """The form genre names are matched in (as the catalogue builder makes it):
    case, accents, spaces and punctuation don't count, "&" is "and"."""
    text = unicodedata.normalize("NFKD", (text or "").casefold())
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("&", " and ")
    return re.sub(r"[^0-9a-z]+", "", text)


class Catalogue:
    def __init__(self, data: dict):
        self.genres: list[dict] = data["genres"]
        self.names = [g["name"] for g in self.genres]
        self._index_of_id = {g["id"]: i for i, g in enumerate(self.genres)}
        self._by_key: dict[str, list[int]] = {}
        for i, g in enumerate(self.genres):
            for name in (g["name"], *g["aliases"]):
                self._by_key.setdefault(key(name), [i])
                self._by_key.setdefault(key(re.sub(r" music$", "", name, flags=re.I)), [i])
        self._store = set(data.get("store_names", {}))
        for name, ids in data.get("store_names", {}).items():
            self._by_key[key(name)] = list(ids)          # store names win: "Alternative"
        self.links: dict[int, dict[int, float]] = defaultdict(dict)
        for a, b, kinds in data["links"]:
            weight = max(WEIGHTS[k] for k in kinds) + 0.05 * (len(kinds) - 1)
            self.links[a][b] = self.links[b][a] = weight

    # ---- Names ------------------------------------------------------------------------

    def ids(self, indexes) -> list[str]:
        """Lasting ids ("mb:…", "wd:…") for genre indexes: indexes change when the
        catalogue is rebuilt, ids don't (save these)."""
        return [self.genres[i]["id"] for i in indexes if 0 <= i < len(self.genres)]

    def indexes(self, ids) -> list[int]:
        """Genre indexes for saved ids (ids no longer in the catalogue are left out)."""
        return [self._index_of_id[i] for i in ids if i in self._index_of_id]

    def genre_ids(self, field: str | None) -> list[int]:
        """The catalogue genres a Genre field names, in order, without repeats.
        The whole field is tried first ("Drum & Bass", "R&B/Soul"), then each
        part between ; / and , ("Pop, Rock"). Unknown names are skipped."""
        if not field or not field.strip():
            return []
        whole = self._whole(field)
        if whole:
            return list(whole)
        found: list[int] = []
        for part in SEPARATORS.split(field.strip()):
            for i in self._lookup(part):
                if i not in found:
                    found.append(i)
        return found

    def _whole(self, field: str) -> list[int] | None:
        """The whole field as one name: always for a store name ("R&B/Soul"),
        otherwise only when it has no separators ("Pop, Rock" is two genres,
        even though "Pop rock" is one)."""
        if field.strip() in self._store or not SEPARATORS.search(field.strip()):
            return self._by_key.get(key(field))
        return None

    def _lookup(self, part: str) -> list[int]:
        if not part:
            return []
        hit = self._by_key.get(key(part))
        if hit:
            return hit
        if " & " in part or " and " in part.lower():      # "Alternative & Punk"
            pieces = re.split(r"\s+(?:&|and)\s+", part, flags=re.I)
            if all(key(p) in self._by_key for p in pieces):
                return [i for p in pieces for i in self._by_key[key(p)]]
        return []

    def unknown_parts(self, field: str | None) -> list[str]:
        """The parts of a Genre field the catalogue doesn't know (for a "genres
        not in the catalogue" list)."""
        if not field or not field.strip() or self._whole(field):
            return []
        return [p for p in SEPARATORS.split(field.strip()) if p and not self._lookup(p)]

    def suggest(self, typed: str, limit: int = 12) -> list[str]:
        """Genre names for what's been typed so far: names that start with it
        first, then names with a word starting with it; better-known genres
        first. An alias suggests its genre's own name."""
        wanted = key(typed)
        if not wanted:
            return []
        starts, words = {}, {}
        for i, g in enumerate(self.genres):
            for name in (g["name"], *g["aliases"]):
                if key(name).startswith(wanted):
                    starts[i] = True
                elif any(key(w).startswith(wanted) for w in re.split(r"[\s\-/]+", name)[1:]):
                    words[i] = True
        rank = lambda i: (-self.genres[i].get("known", 0), self.names[i].casefold())
        ordered = sorted(starts, key=rank) + sorted((i for i in words if i not in starts), key=rank)
        return [self.names[i] for i in ordered[:limit]]

    # ---- Closeness --------------------------------------------------------------------

    def related(self, ids, limit: int | None = None) -> dict[int, float]:
        """How close every reachable genre is to these (0..1; the starting
        genres themselves are 1)."""
        ids = [i for i in dict.fromkeys(ids) if 0 <= i < len(self.genres)]
        if not ids:
            return {}
        total: dict[int, float] = defaultdict(float)
        for start in ids:
            for genre, score in self._walk(start).items():
                total[genre] = max(total[genre], score)
        for start in ids:
            total[start] = 1.0
        ordered = sorted(total.items(), key=lambda item: -item[1])
        return dict(ordered[:limit] if limit else ordered)

    @lru_cache(maxsize=512)
    def _walk_cached(self, start: int) -> tuple:
        here = {start: 1.0}
        visits: dict[int, float] = defaultdict(float)
        for _ in range(STEPS):
            nxt: dict[int, float] = defaultdict(float)
            for genre, mass in here.items():
                visits[genre] += RETURN * mass
                neighbours = self.links.get(genre)
                if not neighbours:
                    continue
                total = sum(neighbours.values())
                for other, weight in neighbours.items():
                    nxt[other] += (1 - RETURN) * mass * weight / total
            here = nxt
        scores = {g: v / len(self.links[g]) ** HUB_DISCOUNT
                  for g, v in visits.items() if g != start and self.links.get(g)}
        top = max(scores.values(), default=0) or 1
        return tuple((g, s / top) for g, s in scores.items())

    def _walk(self, start: int) -> dict[int, float]:
        return dict(self._walk_cached(start))

    def closeness(self, field_a: str | None, field_b: str | None) -> float:
        """How close two Genre fields are (0..1): the closest pair of their genres."""
        a, b = self.genre_ids(field_a), self.genre_ids(field_b)
        if not a or not b:
            return 0.0
        near = self.related(a)
        return max(near.get(i, 0.0) for i in b)


@lru_cache(maxsize=1)
def catalogue(path: Path = CATALOGUE) -> Catalogue:
    return Catalogue(json.loads(path.read_text(encoding="utf-8")))
