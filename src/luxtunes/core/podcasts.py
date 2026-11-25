"""Podcast subscriptions: reading a podcast's feed and downloading its episodes.

A podcast's feed is a public web address (RSS, sometimes Atom) listing its
episodes, each with a link to its audio (or video) file. No account or login:
subscribing means remembering the address. Nothing here goes online by
itself; every function that does takes approved=True (net.require), which the
window only passes after you said yes, or turned it on in Preferences →
Network.

  * Subscribe: the feed is read, every episode it lists is remembered, and the
    newest one is downloaded.
  * Refresh: each feed is read again; episodes that weren't there before are
    downloaded. Older episodes stay listed (Podcasts window) to download by hand.
  * An episode you delete from your library isn't downloaded again.
  * Unsubscribing keeps the episodes you downloaded.

Downloaded episodes go in the LuxTunes Media folder (Podcasts/<podcast>/) and
join the library as podcasts, with the podcast's name as their album.

Safety: only https:// addresses (redirects too); a feed is at most
MAX_FEED and an episode MAX_EPISODE; feeds that declare a DOCTYPE (which an
RSS feed never needs, and which XML attacks rely on) are refused; episode file
names are made from the title, cleaned, never taken from the website.
No Qt in here: the network parts run on a worker thread, the library parts
on the main one.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import urllib.parse
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from luxtunes.core import library, net, organize

log = logging.getLogger(__name__)

MAX_FEED = 16 * 1024 * 1024
MAX_EPISODE = 4 * 1024 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"

AUDIO_TYPES = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a",
               "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/m4b": ".m4b",
               "audio/x-m4b": ".m4b", "audio/ogg": ".ogg", "audio/opus": ".opus",
               "audio/flac": ".flac", "audio/wav": ".wav", "audio/x-wav": ".wav",
               "video/mp4": ".mp4", "video/x-m4v": ".m4v", "video/quicktime": ".mov"}
EXTENSIONS = set(AUDIO_TYPES.values())


class FeedError(Exception):
    """The address isn't a podcast feed LuxTunes can read."""


@dataclass
class Episode:
    guid: str
    title: str
    url: str
    published: str | None = None        # ISO 8601, UTC
    description: str = ""
    size: int | None = None
    duration: int | None = None         # ms
    kind: str = ""                      # the enclosure's type, e.g. audio/mpeg


@dataclass
class Feed:
    url: str
    title: str
    author: str = ""
    description: str = ""
    image_url: str = ""
    link: str = ""
    episodes: list[Episode] = field(default_factory=list)     # newest first


# ---- Reading a feed -----------------------------------------------------------------------

def _text(element, *names) -> str:
    for name in names:
        found = element.find(name) if element is not None else None
        if found is not None and (found.text or "").strip():
            return found.text.strip()
    return ""


def _when(text: str) -> str | None:
    """An RSS date ("Tue, 03 Feb 2026 10:00:00 GMT") or ISO date, as ISO UTC."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        moment = None
    if moment is None:
        from datetime import datetime
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration(text: str) -> int | None:
    """itunes:duration: "1:02:03", "62:03" or seconds, as ms."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return int(seconds * 1000) if 0 < seconds < 100 * 3600 else None


def _clean(html: str) -> str:
    """A description as plain text (feeds often hold HTML)."""
    text = re.sub(r"<\s*(br|/p|/div|/li)\b[^>]*>", "\n", html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    import html as html_module
    text = html_module.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in text.splitlines())).strip()


def _number(text) -> int | None:
    try:
        value = int(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_feed(data: bytes, url: str) -> Feed:
    """Read an RSS or Atom podcast feed. Raises FeedError."""
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data[:65536].lower():
        raise FeedError("the feed declares a DOCTYPE, which podcast feeds don't need; "
                        "LuxTunes doesn't read those, to be safe")
    if b"<html" in head:
        raise FeedError("that's a web page, not a podcast feed (look for an “RSS” "
                        "link on the podcast's site)")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise FeedError(f"the feed couldn't be read ({error})") from None
    if root.tag == f"{ATOM}feed":
        return _parse_atom(root, url)
    channel = root.find("channel")
    if channel is None:
        raise FeedError("that address isn't a podcast feed")
    image = channel.find(f"{ITUNES}image")
    feed = Feed(url=url, title=_text(channel, "title") or "Untitled Podcast",
                author=_text(channel, f"{ITUNES}author", "managingEditor"),
                description=_clean(_text(channel, f"{ITUNES}summary", "description")),
                image_url=(image.get("href", "") if image is not None else "")
                or _text(channel.find("image"), "url"),
                link=_text(channel, "link"))
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            content = item.find(f"{MEDIA}content")
            enclosure = content if content is not None else None
        if enclosure is None or not enclosure.get("url"):
            continue
        address = urllib.parse.urljoin(url, enclosure.get("url").strip())
        feed.episodes.append(Episode(
            guid=_text(item, "guid") or address,
            title=_text(item, "title", f"{ITUNES}title") or "Untitled Episode",
            url=address,
            published=_when(_text(item, "pubDate")),
            description=_clean(_text(item, f"{ITUNES}summary", "description",
                                     f"{CONTENT}encoded")),
            size=_number(enclosure.get("length") or enclosure.get("fileSize")),
            duration=_duration(_text(item, f"{ITUNES}duration")),
            kind=(enclosure.get("type") or "").lower()))
    feed.episodes.sort(key=lambda e: e.published or "", reverse=True)
    return feed


def _parse_atom(root, url: str) -> Feed:
    link = next((l.get("href", "") for l in root.findall(f"{ATOM}link")
                 if l.get("rel", "alternate") == "alternate"), "")
    feed = Feed(url=url, title=_text(root, f"{ATOM}title") or "Untitled Podcast",
                author=_text(root.find(f"{ATOM}author"), f"{ATOM}name"),
                description=_clean(_text(root, f"{ATOM}subtitle")),
                image_url=_text(root, f"{ATOM}logo", f"{ATOM}icon"), link=link)
    for entry in root.findall(f"{ATOM}entry"):
        enclosure = next((l for l in entry.findall(f"{ATOM}link")
                          if l.get("rel") == "enclosure" and l.get("href")), None)
        if enclosure is None:
            continue
        address = urllib.parse.urljoin(url, enclosure.get("href").strip())
        feed.episodes.append(Episode(
            guid=_text(entry, f"{ATOM}id") or address,
            title=_text(entry, f"{ATOM}title") or "Untitled Episode", url=address,
            published=_when(_text(entry, f"{ATOM}published", f"{ATOM}updated")),
            description=_clean(_text(entry, f"{ATOM}summary", f"{ATOM}content")),
            size=_number(enclosure.get("length")), kind=(enclosure.get("type") or "").lower()))
    feed.episodes.sort(key=lambda e: e.published or "", reverse=True)
    return feed


def normalize_url(text: str) -> str:
    """What you typed or pasted, as a feed address: podcast apps' "feed://"
    and "pcast://" links become https://. An http:// address is refused
    (LuxTunes only uses secure addresses), as is anything else."""
    text = (text or "").strip()
    for prefix in ("feed://", "pcast://", "itpc://", "podcast://"):
        if text.lower().startswith(prefix):
            text = "https://" + text[len(prefix):]
    if text.lower().startswith("feed:https://"):
        text = text[5:]
    if "://" not in text and "." in text.split("/")[0]:
        text = "https://" + text
    if text.lower().startswith("http://"):
        raise FeedError("LuxTunes only uses secure (https://) addresses. Most podcasts have "
                        "one: try the same address starting with https://.")
    if not net.secure(text):
        raise FeedError("that isn't a web address")
    return text


def fetch_feed(url: str, approved: bool = False) -> Feed:
    """Download and read a feed. Raises FeedError, net.NotApproved or OSError."""
    net.require(approved, "Checking a podcast for new episodes")
    try:
        with net.open_podcast_url(url, "application/rss+xml, application/xml, text/xml, */*") \
                as response:
            data = net.read_limited(response, MAX_FEED)
            final = response.geturl() or url
    except ValueError as error:
        raise FeedError(str(error)) from None
    feed = parse_feed(data, final)
    feed.url = url                       # remembered as you gave it
    return feed


def fetch_image(url: str, approved: bool = False) -> bytes | None:
    """The podcast's cover (JPEG or PNG), or None."""
    net.require(approved, "Downloading a podcast's cover")
    if not url or not net.secure(url):
        return None
    try:
        with net.open_podcast_url(url, "image/jpeg, image/png") as response:
            data = net.read_limited(response, MAX_IMAGE)
    except (OSError, ValueError) as error:
        log.info("couldn't download the cover %s: %s", url, error)
        return None
    return data if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n" else None


def extension_for(url: str, kind: str) -> str:
    suffix = os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()
    if suffix in EXTENSIONS:
        return suffix
    return AUDIO_TYPES.get((kind or "").lower(), ".mp3")


def download_episode(url: str, target: str, approved: bool = False, stop=None,
                     progress=None) -> str:
    """Download an episode to `target` (written as target + ".part" first).
    Raises net.NotApproved, OSError, ValueError, InterruptedError, FeedError."""
    net.require(approved, "Downloading a podcast episode")
    partial = target + ".part"
    kind = net.download(url, partial, MAX_EPISODE, stop, progress)
    if kind.startswith("text/"):
        os.remove(partial)
        raise FeedError("the podcast's site sent a web page instead of the episode")
    os.replace(partial, target)
    return target


# ---- The library side (main thread) -----------------------------------------------------------

def _now() -> str:
    return library.now_utc()


def feeds(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT f.*, (SELECT COUNT(*) FROM podcast_episodes e WHERE e.feed_id = f.id) AS episodes, "
        "(SELECT COUNT(*) FROM podcast_episodes e WHERE e.feed_id = f.id AND e.track_id IS NOT NULL)"
        " AS downloaded FROM podcast_feeds f").fetchall()
    return sorted((dict(r) for r in rows), key=lambda f: (f["title"] or "").casefold())


def feed(conn: sqlite3.Connection, feed_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM podcast_feeds WHERE id = ?", (feed_id,)).fetchone()
    return dict(row) if row else None


def episodes(conn: sqlite3.Connection, feed_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM podcast_episodes WHERE feed_id = ? "
                        "ORDER BY published DESC, id DESC", (feed_id,)).fetchall()
    return [dict(r) for r in rows]


def is_subscribed(conn: sqlite3.Connection, url: str) -> bool:
    return conn.execute("SELECT 1 FROM podcast_feeds WHERE url = ?", (url,)).fetchone() is not None


def save_feed(conn: sqlite3.Connection, read: Feed) -> tuple[int, list[int]]:
    """Remember a feed you subscribed to (or update it after a refresh).
    Returns (feed id, ids of the episodes it hadn't listed before)."""
    with conn:
        row = conn.execute("SELECT id FROM podcast_feeds WHERE url = ?", (read.url,)).fetchone()
        details = (read.title, read.author, read.description, read.image_url, read.link, _now())
        if row is None:
            feed_id = conn.execute(
                "INSERT INTO podcast_feeds (title, author, description, image_url, link, "
                "last_checked, url, added) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*details, read.url, _now())).lastrowid
        else:
            feed_id = row[0]
            conn.execute("UPDATE podcast_feeds SET title = ?, author = ?, description = ?, "
                         "image_url = ?, link = ?, last_checked = ?, last_error = NULL "
                         "WHERE id = ?", (*details, feed_id))
        known = {r[0] for r in conn.execute("SELECT guid FROM podcast_episodes WHERE feed_id = ?",
                                            (feed_id,))}
        new = []
        for e in read.episodes:
            values = (e.title, e.description, e.url, e.published, e.size, e.duration)
            if e.guid in known:
                conn.execute("UPDATE podcast_episodes SET title = ?, description = ?, url = ?, "
                             "published = ?, size = ?, duration = ? WHERE feed_id = ? AND guid = ?",
                             (*values, feed_id, e.guid))
                continue
            known.add(e.guid)
            new.append(conn.execute(
                "INSERT INTO podcast_episodes (title, description, url, published, size, duration, "
                "feed_id, guid, first_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, feed_id, e.guid, _now())).lastrowid)
    return feed_id, new


def note_problem(conn: sqlite3.Connection, feed_id: int, message: str) -> None:
    with conn:
        conn.execute("UPDATE podcast_feeds SET last_error = ?, last_checked = ? WHERE id = ?",
                     (message, _now(), feed_id))


def unsubscribe(conn: sqlite3.Connection, feed_id: int) -> None:
    """Forget the feed; the episodes you downloaded stay in your library."""
    with conn:
        conn.execute("DELETE FROM podcast_feeds WHERE id = ?", (feed_id,))


def newest(conn: sqlite3.Connection, episode_ids: list[int], count: int = 1) -> list[int]:
    """The `count` newest of these episodes."""
    if not episode_ids:
        return []
    marks = ",".join("?" for _ in episode_ids)
    rows = conn.execute(f"SELECT id FROM podcast_episodes WHERE id IN ({marks}) "
                        "ORDER BY published DESC, id DESC LIMIT ?", (*episode_ids, count))
    return [r[0] for r in rows]


@dataclass
class Download:
    """One episode to download (made on the main thread, used by the worker)."""
    episode_id: int
    url: str
    target: str
    title: str
    podcast: str


def plan_download(conn: sqlite3.Connection, episode_id: int) -> Download | None:
    """Where an episode goes (Podcasts/<podcast>/<title>.mp3 in the LuxTunes Media
    folder), or None if it's already in the library or gone."""
    row = conn.execute("SELECT e.*, f.title AS podcast FROM podcast_episodes e "
                       "JOIN podcast_feeds f ON f.id = e.feed_id WHERE e.id = ?",
                       (episode_id,)).fetchone()
    if row is None or row["track_id"] is not None or not row["url"]:
        return None
    folder = Path(organize.media_folder(conn)) / "Podcasts" / organize.safe_name(
        row["podcast"] or "Unknown Podcast", organize.FOLDER_MAX, True)
    stem = organize.safe_name(row["title"] or "Untitled Episode", organize.FILE_MAX, False)
    extension = extension_for(row["url"], "")
    target = folder / f"{stem}{extension}"
    number = 2
    while target.exists() or Path(str(target) + ".part").exists():
        target = folder / f"{stem} {number}{extension}"
        number += 1
    return Download(episode_id, row["url"], str(target), row["title"] or "", row["podcast"] or "")


def add_downloaded(conn: sqlite3.Connection, episode_id: int, path: str,
                   cover: bytes | None = None) -> int:
    """Put a downloaded episode in the library as a podcast. Its file's own
    tags are kept where the feed says nothing; the feed's names win (so all of
    a podcast's episodes share its name as their album). Returns the track id."""
    from luxtunes.core import add_files, tags
    row = conn.execute("SELECT e.*, f.title AS podcast, f.author AS author FROM podcast_episodes e "
                       "JOIN podcast_feeds f ON f.id = e.feed_id WHERE e.id = ?",
                       (episode_id,)).fetchone()
    if row is None:
        raise ValueError("that episode is no longer listed")
    details = {"media_kind": "podcast", "name": row["title"], "album": row["podcast"],
               "release_date": row["published"], "description": row["description"] or None,
               "genre": "Podcast", **library.kind_defaults("podcast")}
    fallback = {"artist": row["author"] or row["podcast"], "total_time": row["duration"]}
    if cover:
        from luxtunes.core.artwork import read_embedded_artwork
        if read_embedded_artwork(path) is None:
            try:
                tags.write_tags(path, {}, artworks=[cover])
            except Exception as error:      # the episode is still fine without it
                log.info("couldn't add the podcast's cover to %s: %s", path, error)
    track_id = add_files.add_file(conn, path, copy=False, root=organize.media_folder(conn),
                                  details=details, fallback=fallback, file_it=False)
    with conn:
        conn.execute("UPDATE podcast_episodes SET track_id = ? WHERE id = ?", (track_id, episode_id))
    return track_id
