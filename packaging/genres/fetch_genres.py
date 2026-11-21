"""Downloads the raw data for LuxTunes' genre catalogue (for Genius): every
MusicBrainz genre with its relationships, and Wikidata's music genres with
their English names, other names and links. Nothing else is sent or read.

    python3 packaging/genres/fetch_genres.py [folder]

Writes into the folder (default: genre-data, which git ignores):
    musicbrainz.json   [{"id", "name", "disambiguation", "rels": {"subgenre of": [ids], ...}}]
    wikidata.json      {"items": [[qid, name, mb_id, enwiki_title, sitelinks]],
                        "alts": [[qid, other name]], "edges": [[qid, property, qid]]}

MusicBrainz asks for at most one request a second, so its part takes about
45 minutes. It can be stopped (Ctrl+C) and run again: it carries on where it
stopped. Uses only Python's standard library. Then build the catalogue with
build_genre_catalogue.py.
"""
import html.parser
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

AGENT = "LuxTunes-genre-catalogue/0.1 ( https://github.com/metric-ambient/luxtunes )"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "genre-data")
PAUSE = 1.2                     # seconds between MusicBrainz requests


def get(url: str, accept: str = "application/json", tries: int = 5) -> bytes:
    for attempt in range(tries):
        request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except Exception as error:          # 503 when busy, timeouts...
            wait = 5 * (attempt + 1)
            print(f"  (retrying in {wait} s: {error})")
            time.sleep(wait)
    raise SystemExit(f"Couldn't fetch {url}")


class Relationships(html.parser.HTMLParser):
    """A MusicBrainz genre page's relationship rows: "subgenre of" -> [genre ids],
    "Wikidata" -> [Q-ids]."""

    def __init__(self):
        super().__init__()
        self.rels: dict[str, list[str]] = {}
        self._in_details = 0
        self._label = None
        self._in_th = False
        self._th_text = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and "details" in (attrs.get("class") or ""):
            self._in_details += 1
        elif self._in_details and tag == "th":
            self._in_th, self._th_text = True, ""
        elif self._in_details and tag == "a" and self._label:
            href = attrs.get("href") or ""
            if href.startswith("/genre/"):
                self.rels.setdefault(self._label, []).append(href[7:])
            elif "wikidata.org/wiki/" in href:
                self.rels.setdefault(self._label, []).append(href.rsplit("/", 1)[-1])

    def handle_endtag(self, tag):
        if tag == "table" and self._in_details:
            self._in_details -= 1
            self._label = None
        elif tag == "th" and self._in_th:
            self._in_th = False
            label = self._th_text.strip().rstrip(":")
            self._label = None if label == "Primary tag" else label

    def handle_data(self, data):
        if self._in_th:
            self._th_text += data


def musicbrainz():
    path = OUT / "musicbrainz.json"
    done = {g["id"]: g for g in json.loads(path.read_text())} if path.exists() else {}
    genres, offset = [], 0
    print("MusicBrainz: the genre list…")
    while True:
        page = json.loads(get(f"https://musicbrainz.org/ws/2/genre/all?fmt=json&limit=100&offset={offset}"))
        genres += page["genres"]
        offset += 100
        time.sleep(PAUSE)
        if offset >= page["genre-count"]:
            break
    print(f"  {len(genres)} genres; {len(done)} already fetched")
    for n, genre in enumerate(genres, 1):
        if genre["id"] in done:
            continue
        parser = Relationships()
        parser.feed(get(f"https://musicbrainz.org/genre/{genre['id']}", "text/html").decode("utf-8", "replace"))
        done[genre["id"]] = {**genre, "rels": parser.rels}
        if n % 25 == 0 or n == len(genres):
            path.write_text(json.dumps(list(done.values())))
            print(f"  {n}/{len(genres)}  {genre['name']}")
        time.sleep(PAUSE)
    path.write_text(json.dumps(list(done.values())))


def wikidata():
    path = OUT / "wikidata.json"
    if path.exists():
        print("Wikidata: already fetched")
        return

    def run(query):
        url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
        return json.loads(get(url, "application/sparql-results+json"))["results"]["bindings"]

    last = lambda value: value.rsplit("/", 1)[-1]
    print("Wikidata: genres…")
    items = run("""SELECT ?a ?l ?mb ?wt ?sl WHERE {
      ?a wdt:P31 wd:Q188451 ; wikibase:sitelinks ?sl .
      OPTIONAL { ?a rdfs:label ?l . FILTER(LANG(?l)="en") }
      OPTIONAL { ?a wdt:P8052 ?mb }
      OPTIONAL { ?art schema:about ?a ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?wt }
    }""")
    print("Wikidata: other names…")
    alts = run("""SELECT ?a ?alt WHERE {
      ?a wdt:P31 wd:Q188451 ; skos:altLabel ?alt . FILTER(LANG(?alt)="en") }""")
    print("Wikidata: links between genres…")
    edges = run("""SELECT ?a ?p ?b WHERE {
      VALUES ?p { wdt:P279 wdt:P737 wdt:P144 wdt:P1382 wdt:P460 wdt:P361 wdt:P527
                  wdt:P941 wdt:P4969 wdt:P155 wdt:P156 }
      ?a wdt:P31 wd:Q188451 . ?a ?p ?b . ?b wdt:P31 wd:Q188451 . }""")
    data = {
        "items": [[last(b["a"]["value"]), b.get("l", {}).get("value", ""),
                   b.get("mb", {}).get("value", ""), b.get("wt", {}).get("value", ""),
                   int(b["sl"]["value"])] for b in items],
        "alts": [[last(b["a"]["value"]), b["alt"]["value"]] for b in alts],
        "edges": [[last(b["a"]["value"]), last(b["p"]["value"]), last(b["b"]["value"])]
                  for b in edges],
    }
    path.write_text(json.dumps(data))
    print(f"  {len(data['items'])} genres, {len(data['alts'])} other names, {len(data['edges'])} links")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    wikidata()
    try:
        musicbrainz()
    except KeyboardInterrupt:
        sys.exit("\nStopped. Run it again to carry on.")
    print(f"Done: {OUT}")
