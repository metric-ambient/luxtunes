"""Builds LuxTunes' genre catalogue (resources/genres.json) from the raw data
that fetch_genres.py downloads: MusicBrainz's genres and their relationships,
merged with Wikidata's music genres (both public domain, CC0).

    python3 packaging/genres/build_genre_catalogue.py genre-data

(genre-data: the folder fetch_genres.py filled.)

The catalogue:
    {"version": 1, "sources": "...",
     "genres": [{"id", "name", "aliases": [...], "known"}],   id: "mb:<MBID>" or "wd:<Q-id>";
                                                     known: Wikipedias with an article on it
     "store_names": {"Hip-Hop/Rap": [genre indexes], ...},
     "links":  [[a, b, kinds]]}                          indexes into genres; kinds: see KINDS

A MusicBrainz genre and the Wikidata item it points to are one genre.
Wikidata genres MusicBrainz doesn't have are kept when they have an English
Wikipedia article and at least one link (overview articles like "Music of
Peru" are left out). ALIASES adds the genre names music stores and CD
databases use ("Alternative", "Hip-Hop/Rap", "Soundtrack"...).
"""
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

KINDS = {          # one letter each in the file
    "p": "parent and subgenre",
    "o": "overlapping / said to be the same",
    "f": "fusion",
    "t": "part of",
    "i": "influence (either way)",
}
MB_KINDS = {"subgenre of": "p", "subgenres": "p", "influenced by": "i", "influenced genres": "i",
            "influenced by and influenced genres": "i", "fusion of": "f", "has fusion genres": "f"}
WD_KINDS = {"P279": "p", "P361": "t", "P527": "t", "P737": "i", "P941": "i", "P144": "i",
            "P4969": "i", "P155": "i", "P156": "i", "P1382": "o", "P460": "o"}

# Store and CD-database genre names -> the catalogue genre(s) they mean, by a
# name the catalogue knows. Applied after everything else.
ALIASES = {
    "Alternative": ["alternative rock"], "AlternRock": ["alternative rock"],
    "Alt. Rock": ["alternative rock"], "Alt Rock": ["alternative rock"],
    "Alternative & Punk": ["alternative rock", "punk"],
    "Alternative & Indie": ["alternative rock", "indie music"],
    "Alt. Metal": ["alternative metal"],
    "Hip-Hop/Rap": ["hip-hop"], "Hip Hop/Rap": ["hip-hop"], "Rap": ["hip-hop"],
    "R&B/Soul": ["rhythm and blues", "soul"], "R&B": ["rhythm and blues"],
    "Soundtrack": ["film score"], "Soundtracks": ["film score"], "Film": ["film score"],
    "Original Score": ["film score"], "Score": ["film score"],
    "Films/Games": ["film score", "video game music"],
    "Game": ["video game music"], "Games": ["video game music"], "Video Game": ["video game music"],
    "Game Soundtrack": ["video game music"], "Video Game Soundtrack": ["video game music"],
    "Gospel & Religious": ["gospel", "christian music"],
    "Christian & Gospel": ["christian music", "gospel"], "Christian": ["christian music"],
    "Inspirational": ["contemporary christian music"], "Religious": ["christian music"],
    "Heavy Christian": ["christian metal"],
    "Singer & Songwriter": ["singer-songwriter"], "Singer/Songwriter": ["singer-songwriter"],
    "Dance": ["dance music"], "Electronic": ["electronic"], "General Electronic": ["electronic"],
    "Electronica/Dance": ["electronic", "dance music"],
    "Classical": ["classical music"], "Metal": ["metal"], "Hardcore": ["hardcore punk"],
    "Folk": ["folk music"], "Indie": ["indie music"], "Holiday": ["christmas music"],
    "Christmas": ["christmas music"], "World": ["world music"], "Latin": ["latin music"],
    "Latino": ["latin music"], "Children's Music": ["children's music"],
    "New Metal": ["nu metal"], "Anime": ["anime music"], "Vocal": ["traditional pop"],
}
# A few links the sources lack, between genres many libraries are full of
# (soundtracks and game music had almost none). By names the catalogue knows.
EXTRA_LINKS = [
    ("video game music", "chiptune", "p"),
    ("video game music", "film score", "t"),
    ("video game music", "electronic", "i"),
    ("film score", "orchestral", "i"),
    ("film score", "classical music", "i"),
]
# MusicBrainz writes names in lower case; these are acronyms.
CAPITALS = {"aor": "AOR", "asmr": "ASMR", "rkt": "RKT", "tbm": "TBM", "uk82": "UK82"}
DROP_TITLE = re.compile(r"^(classical )?music of |\bmusic in\b| in [A-Z]|\(radio format\)|^history of|representation",
                        re.IGNORECASE)


def key(text: str) -> str:
    """The form names are matched in: case, accents, spaces and punctuation
    don't count, and "&" is "and" ("Drum & Bass" = "drum and bass")."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("&", " and ")
    return re.sub(r"[^0-9a-z]+", "", text)


def clean_title(title: str) -> str:
    return re.sub(r"\s*\((music|genre|music genre|musical genre|style)\)$", "", title, flags=re.I)


def build(raw: Path) -> dict:
    mb = json.loads((raw / "musicbrainz.json").read_text())
    wd = json.loads((raw / "wikidata.json").read_text())
    wd_items = {q: (label, mbid, title, links) for q, label, mbid, title, links in wd["items"]}
    wd_alts = collections.defaultdict(list)
    for q, alt in wd["alts"]:
        wd_alts[q].append(alt)

    genres, by_q, by_mb = {}, {}, {}
    for g in mb:
        gid = "mb:" + g["id"]
        genres[gid] = {"mb_name": g["name"], "names": [], "title": "", "sitelinks": 0}
        by_mb[g["id"]] = gid
        for q in g["rels"].get("Wikidata", []):
            by_q.setdefault(q, gid)
    for q, (_, mbid, _, _) in wd_items.items():
        if mbid in by_mb:
            by_q.setdefault(q, by_mb[mbid])
    for q, (label, mbid, title, links) in wd_items.items():
        gid = by_q.get(q)
        if gid is None:
            if not title or DROP_TITLE.search(title):
                continue
            gid = by_q[q] = "wd:" + q
            genres[gid] = {"mb_name": "", "names": [], "title": "", "sitelinks": 0}
        g = genres[gid]
        if title and not g["title"]:
            g["title"] = clean_title(title)
        if label:
            g["names"].append(label)
        g["names"] += wd_alts.get(q, [])
        g["sitelinks"] = max(g["sitelinks"], links)

    links = collections.defaultdict(set)

    def link(a, b, kind):
        if a and b and a != b and a in genres and b in genres:
            links[tuple(sorted((a, b)))].add(kind)
    for g in mb:
        for rel, targets in g["rels"].items():
            if rel in MB_KINDS:
                for t in targets:
                    link(by_mb[g["id"]], by_mb.get(t), MB_KINDS[rel])
    for a, prop, b in wd["edges"]:
        link(by_q.get(a), by_q.get(b), WD_KINDS[prop])
    linked = {g for pair in links for g in pair}
    genres = {gid: g for gid, g in genres.items() if gid.startswith("mb:") or gid in linked}
    links = {pair: kinds for pair, kinds in links.items() if pair[0] in genres and pair[1] in genres}

    # Names: the Wikipedia title reads best ("Drum and bass"); otherwise
    # MusicBrainz's name with a capital. A name two genres share goes to the
    # one with the strongest claim: its own title/MusicBrainz name first,
    # then the better-known genre.
    claims = {}
    for gid, g in genres.items():
        g["name"] = g["title"] or CAPITALS.get(g["mb_name"]) or next(
            # Wikidata's spelling when it only adds capitals ("FM synthesis")
            (n for n in g["names"] if n.casefold() == g["mb_name"] and n != n.casefold()),
            g["mb_name"][:1].upper() + g["mb_name"][1:])
        # "Rock music" -> "Rock" when the genre is also known as just "rock".
        short = re.sub(r" music$", "", g["name"], flags=re.I)
        if short != g["name"] and key(short) in {key(n) for n in [g["mb_name"], *g["names"]] if n}:
            g["name"] = short
        tier_names = [(0, g["name"]), (0, g["mb_name"]), (1, g["title"])] + [(2, n) for n in g["names"]]
        g["keys"] = set()
        for tier, name in tier_names:
            if not name:
                continue
            for variant in {name, re.sub(r" music$", "", name, flags=re.I)}:
                k = key(variant)
                if not k:
                    continue
                g["keys"].add(k)
                claim = (tier, -g["sitelinks"], gid)
                if k not in claims or claim < claims[k]:
                    claims[k] = claim
    owner = {k: c[2] for k, c in claims.items()}

    order = sorted(genres, key=lambda gid: (key(genres[gid]["name"]), gid))
    index = {gid: i for i, gid in enumerate(order)}
    out_genres = []
    for gid in order:
        g = genres[gid]
        seen, aliases = set(), []
        for n in sorted((n for n in [g["mb_name"], g["title"], *g["names"]] if n), key=str.casefold):
            if owner.get(key(n)) == gid and n.casefold() != g["name"].casefold() \
                    and n.casefold() not in seen:
                seen.add(n.casefold())
                aliases.append(n)
        # "known": how many Wikipedias have an article on it, to put the
        # well-known genres first in suggestions.
        out_genres.append({"id": gid, "name": g["name"], "aliases": aliases, "known": g["sitelinks"]})

    # Store/CD-database names: resolved through the names the catalogue now owns.
    by_key = {k: index[gid] for k, gid in owner.items()}
    extra = {}
    for alias, targets in ALIASES.items():
        found = [by_key[key(t)] for t in targets if key(t) in by_key]
        missing = [t for t in targets if key(t) not in by_key]
        if missing:
            print(f"  alias {alias!r}: no genre called {missing}", file=sys.stderr)
        if found:
            extra[alias] = found
    for a, b, kind in EXTRA_LINKS:
        if key(a) in by_key and key(b) in by_key:
            pair = tuple(sorted((order[by_key[key(a)]], order[by_key[key(b)]])))
            links.setdefault(pair, set()).add(kind)
        else:
            print(f"  extra link {a!r} - {b!r}: not found", file=sys.stderr)
    out_links = sorted([index[a], index[b], "".join(sorted(k))] for (a, b), k in links.items())
    return {
        "version": 1,
        "sources": "MusicBrainz genres (musicbrainz.org) and Wikidata (wikidata.org), both CC0",
        "genres": out_genres,
        "store_names": extra,
        "links": out_links,
    }


if __name__ == "__main__":
    raw = Path(sys.argv[1])
    catalogue = build(raw)
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else \
        Path(__file__).resolve().parents[2] / "src" / "luxtunes" / "resources" / "genres.json"
    target.write_text(json.dumps(catalogue, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(catalogue['genres'])} genres, {len(catalogue['links'])} links, "
          f"{len(catalogue['store_names'])} store names -> {target} ({target.stat().st_size // 1024} KB)")
