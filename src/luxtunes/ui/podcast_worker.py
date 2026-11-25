"""Podcasts, in the background so the window stays usable: reading feeds, then
downloading episodes. Only started after you said yes to going online (or
turned on Preferences → Network → Refresh podcasts without asking). The
library itself is only changed on the main thread (MainWindow), from what
these report."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QThread, Signal

from luxtunes.core import podcasts


class FeedWorker(QThread):
    """Reads each feed: feeds is [(feed id or None for a new subscription, address)]."""
    read = Signal(object, str, object, str)     # feed id or None, address, podcasts.Feed or None, problem
    progress = Signal(int, int, str)            # feeds done, in all, which

    def __init__(self, feeds: list[tuple], approved: bool, parent=None):
        super().__init__(parent)
        self.feeds = list(feeds)
        self.approved = approved
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        for done, (feed_id, url) in enumerate(self.feeds):
            if self._stop.is_set():
                break
            self.progress.emit(done, len(self.feeds), url)
            try:
                self.read.emit(feed_id, url, podcasts.fetch_feed(url, approved=self.approved), "")
            except (podcasts.FeedError, OSError, ValueError) as error:
                self.read.emit(feed_id, url, None, _say(error))
            except Exception as error:      # never let a crash in the thread go unreported
                self.read.emit(feed_id, url, None, f"{type(error).__name__}: {error}")


class DownloadWorker(QThread):
    """Downloads episodes (podcasts.Download), each podcast's cover once."""
    downloaded = Signal(int, str, object, str)  # episode id, file (or ""), cover bytes or None, problem
    progress = Signal(int, int, str, int, int)  # episodes done, in all, title, bytes so far, expected

    def __init__(self, downloads: list, covers: dict, approved: bool, parent=None):
        super().__init__(parent)
        self.downloads = list(downloads)
        self.covers = dict(covers)               # podcast name -> its cover's address
        self.approved = approved
        self._stop = threading.Event()
        self._images: dict = {}

    def stop(self):
        self._stop.set()

    def _cover(self, podcast: str):
        if podcast not in self._images:
            self._images[podcast] = podcasts.fetch_image(self.covers.get(podcast, ""),
                                                         approved=self.approved)
        return self._images[podcast]

    def run(self):
        total = len(self.downloads)
        for done, item in enumerate(self.downloads):
            if self._stop.is_set():
                break
            self.progress.emit(done, total, item.title, 0, 0)
            last = [0]

            def tell(got, expected, d=done, t=item.title):
                if got - last[0] >= 1_000_000:       # about once a megabyte
                    last[0] = got
                    self.progress.emit(d, total, t, got, expected)
            try:
                os.makedirs(os.path.dirname(item.target), exist_ok=True)
                path = podcasts.download_episode(item.url, item.target, approved=self.approved,
                                                 stop=self._stop, progress=tell)
                self.downloaded.emit(item.episode_id, path, self._cover(item.podcast), "")
            except InterruptedError:
                break
            except (podcasts.FeedError, OSError, ValueError) as error:
                self.downloaded.emit(item.episode_id, "", None, _say(error))
            except Exception as error:
                self.downloaded.emit(item.episode_id, "", None, f"{type(error).__name__}: {error}")


def _say(error) -> str:
    """A problem in words (urllib's errors wrap the reason)."""
    reason = getattr(error, "reason", None)
    if reason is not None and not isinstance(reason, int):
        return str(reason)
    code = getattr(error, "code", None)
    if code:
        return f"the site answered {code} {getattr(error, 'msg', '') or ''}".strip()
    return str(error) or type(error).__name__
