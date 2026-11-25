"""Podcast subscriptions, without going online: reading feeds, remembering
episodes, and adding a downloaded one to the library."""

import shutil
import subprocess
from pathlib import Path

import pytest

from luxtunes.core import db, net, network_prefs, organize, podcasts

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Slow Orbit Radio</title>
  <itunes:author>Paper Coast</itunes:author>
  <description>&lt;p&gt;Weekly chats about &amp;amp; around music.&lt;/p&gt;</description>
  <itunes:image href="https://example.org/cover.jpg"/>
  <item>
    <title>Episode 1: Harbor Lights</title>
    <guid>ep-1</guid>
    <pubDate>Mon, 05 Jan 2026 10:00:00 GMT</pubDate>
    <itunes:duration>1:02:03</itunes:duration>
    <enclosure url="https://cdn.example.org/ep1.mp3?x=1" length="12345" type="audio/mpeg"/>
  </item>
  <item>
    <title>Episode 2: Lantern Valley</title>
    <guid>ep-2</guid>
    <pubDate>Mon, 12 Jan 2026 10:00:00 +0100</pubDate>
    <itunes:duration>3600</itunes:duration>
    <enclosure url="/media/ep2.m4a" length="99" type="audio/x-m4a"/>
  </item>
  <item><title>A post without audio</title></item>
</channel>
</rss>"""


def test_reading_a_feed():
    feed = podcasts.parse_feed(RSS, "https://example.org/feed.xml")
    assert (feed.title, feed.author, feed.image_url) == ("Slow Orbit Radio", "Paper Coast",
                                                         "https://example.org/cover.jpg")
    assert feed.description == "Weekly chats about & around music."
    newest, oldest = feed.episodes                      # the one without audio is left out
    assert newest.title == "Episode 2: Lantern Valley" and newest.published == "2026-01-12T09:00:00Z"
    assert newest.url == "https://example.org/media/ep2.m4a" and newest.duration == 3_600_000
    assert oldest.duration == 3_723_000 and oldest.size == 12345 and oldest.guid == "ep-1"
    assert podcasts.extension_for(oldest.url, oldest.kind) == ".mp3"
    assert podcasts.extension_for("https://x.org/play?id=3", "audio/x-m4a") == ".m4a"


def test_atom_feed():
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Quay Talks</title>
      <entry><id>a1</id><title>First</title><published>2026-02-01T08:00:00Z</published>
      <link rel="enclosure" href="https://example.org/a1.mp3" type="audio/mpeg" length="5"/></entry>
    </feed>"""
    feed = podcasts.parse_feed(atom, "https://example.org/atom")
    assert feed.title == "Quay Talks" and feed.episodes[0].url == "https://example.org/a1.mp3"


def test_unsafe_or_wrong_feeds_are_refused():
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss><channel/></rss>'
    for data in (bomb, b"<html><body>Hi</body></html>", b"not xml", b"<rss></rss>"):
        with pytest.raises(podcasts.FeedError):
            podcasts.parse_feed(data, "https://example.org/x")


def test_feed_addresses():
    assert podcasts.normalize_url("feed://example.org/rss") == "https://example.org/rss"
    assert podcasts.normalize_url("example.org/rss") == "https://example.org/rss"
    assert podcasts.normalize_url(" https://example.org/rss ") == "https://example.org/rss"
    for bad in ("http://example.org/rss", "ftp://example.org", "hello"):
        with pytest.raises(podcasts.FeedError):
            podcasts.normalize_url(bad)


def test_nothing_goes_online_without_approval(tmp_path):
    for call in (lambda: podcasts.fetch_feed("https://example.org/rss"),
                 lambda: podcasts.download_episode("https://example.org/a.mp3", str(tmp_path / "a")),
                 lambda: podcasts.fetch_image("https://example.org/a.jpg")):
        with pytest.raises(net.NotApproved):
            call()
    with pytest.raises(OSError):                         # http:// is refused before connecting
        net.open_podcast_url("http://example.org/rss")


def test_subscribing_and_refreshing(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    organize.set_media_folder(conn, tmp_path / "Media")
    feed = podcasts.parse_feed(RSS, "https://example.org/feed.xml")
    feed_id, new = podcasts.save_feed(conn, feed)
    assert len(new) == 2
    [newest] = podcasts.newest(conn, new)
    assert podcasts.episodes(conn, feed_id)[0]["id"] == newest
    # A refresh with one more episode: only that one is new.
    feed.episodes.insert(0, podcasts.Episode(guid="ep-3", title="Episode 3", url="https://x.org/3.mp3",
                                             published="2026-01-19T09:00:00Z"))
    feed_id_again, new = podcasts.save_feed(conn, feed)
    assert feed_id_again == feed_id and len(new) == 1
    [info] = podcasts.feeds(conn)
    assert info["episodes"] == 3 and info["downloaded"] == 0

    download = podcasts.plan_download(conn, newest)
    target = Path(download.target)
    assert target.parent == tmp_path / "Media" / "Podcasts" / "Slow Orbit Radio"
    assert target.suffix == ".m4a" and "Lantern Valley" in target.name


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg isn't installed")
def test_a_downloaded_episode_joins_the_library(tmp_path, monkeypatch):
    pytest.importorskip("mutagen")
    conn = db.connect(tmp_path / "library.db")
    organize.set_media_folder(conn, tmp_path / "Media")
    feed_id, new = podcasts.save_feed(conn, podcasts.parse_feed(RSS, "https://example.org/feed.xml"))
    episode = podcasts.newest(conn, new)[0]
    sample = tmp_path / "sample.m4a"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=1",
                    "-c:a", "aac", str(sample)], check=True)

    def fake_download(url, target, limit, stop=None, progress=None, accept="*/*"):
        shutil.copy(sample, target)
        return "audio/mp4"
    monkeypatch.setattr(net, "download", fake_download)
    plan = podcasts.plan_download(conn, episode)
    Path(plan.target).parent.mkdir(parents=True)
    path = podcasts.download_episode(plan.url, plan.target, approved=True)
    assert Path(path).exists() and not Path(path + ".part").exists()
    track_id = podcasts.add_downloaded(conn, episode, path)
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    assert (row["media_kind"], row["album"], row["name"], row["artist"]) == (
        "podcast", "Slow Orbit Radio", "Episode 2: Lantern Valley", "Paper Coast")
    assert row["release_date"] == "2026-01-12T09:00:00Z" and row["bookmarkable"] == 1
    assert podcasts.plan_download(conn, episode) is None             # it's in the library now
    # Deleted from the library: listed again as not downloaded, never downloaded by itself.
    conn.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    conn.commit()
    assert podcasts.episodes(conn, feed_id)[0]["track_id"] is None
    # Unsubscribing forgets the feed; a downloaded episode would stay.
    podcasts.unsubscribe(conn, feed_id)
    assert podcasts.feeds(conn) == [] and podcasts.episodes(conn, feed_id) == []


def test_network_preferences(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    assert not any(network_prefs.all_allowed(conn).values())          # all off at first
    network_prefs.set_allowed(conn, "podcasts_startup", True)
    assert not network_prefs.allowed(conn, "podcasts_startup")       # needs "podcasts" too
    network_prefs.set_allowed(conn, "podcasts", True)
    assert network_prefs.allowed(conn, "podcasts_startup")
    network_prefs.set_allowed(conn, "podcasts", False)
    network_prefs.set_allowed(conn, "podcasts", True)
    assert not network_prefs.allowed(conn, "podcasts_startup")       # turned off with it
    with pytest.raises(ValueError):
        network_prefs.set_allowed(conn, "everything", True)


def test_every_connection_is_reported(monkeypatch):
    seen = []
    net.listeners.append(seen.append)
    try:
        net._tell("https://Feeds.Example.org/rss")
    finally:
        net.listeners.remove(seen.append)
    assert seen == ["feeds.example.org"]
