"""Every request LuxTunes makes to the internet goes through here.

Callers check approved=True first (require()), so nothing goes online without
your OK. On top of that, this module only talks HTTPS, only to the services
LuxTunes uses (MusicBrainz, the Cover Art Archive and the archive.org servers
it sends covers from, LRCLIB for lyrics, and GitHub for updates), checks that again on every
redirect, and caps how much it reads, so a bad or hijacked answer can't send
LuxTunes somewhere else or fill up memory. No Qt in here.

Podcasts are the one exception to the list of services: each lives on its own
website, so open_podcast_url() goes to any address, but still only over HTTPS
(every redirect too), with the same caps.

Every connection is logged and told to the functions in `listeners` (the
window shows "Connecting to …" in its status bar), so nothing goes online
unseen. Each request is one connection that's closed when it's done; LuxTunes
never listens for connections itself.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from luxtunes import __version__

USER_AGENT = f"LuxTunes/{__version__} (https://github.com/metric-ambient/luxtunes)"

# A host is allowed if it is one of these or a subdomain of one.
ALLOWED_HOSTS = ("musicbrainz.org", "coverartarchive.org", "archive.org", "lrclib.net",
                 "github.com", "githubusercontent.com")

MAX_ANSWER = 32 * 1024 * 1024           # JSON answers and cover images

log = logging.getLogger(__name__)

# Called with the host name each time LuxTunes connects (from any thread).
listeners: list = []


def _tell(url: str) -> None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    log.info("connecting to %s", host)
    for listener in list(listeners):
        try:
            listener(host)
        except Exception:               # a listener's problem mustn't stop the request
            log.exception("network listener failed")


class NotApproved(Exception):
    """Something needed the internet before you said it could."""


def require(approved: bool, what: str) -> None:
    """Raise NotApproved unless approved is exactly True."""
    if approved is not True:
        raise NotApproved(f"{what} needs your OK first.")


def allowed(url: str) -> bool:
    """An https:// address on one of ALLOWED_HOSTS."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return parts.scheme == "https" and any(host == h or host.endswith("." + h)
                                           for h in ALLOWED_HOSTS)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another allowed address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise urllib.error.URLError(f"redirected to an unexpected address ({newurl})")
        _tell(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirects)


def request(url: str, accept: str = "application/json",
            headers: dict | None = None) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                                **(headers or {})})


def open_url(req: urllib.request.Request, timeout: float = 15):
    """Open an allowed address. Raises urllib.error.URLError for any other."""
    if not allowed(req.full_url):
        raise urllib.error.URLError(f"not an address LuxTunes uses ({req.full_url})")
    _tell(req.full_url)
    return _opener.open(req, timeout=timeout)


# ---- Podcasts: any website, HTTPS only ----------------------------------------------

def secure(url: str) -> bool:
    """An https:// address with a host name."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname)


class _SecureRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to another https:// address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not secure(newurl):
            raise urllib.error.URLError(f"redirected to an address that isn't secure ({newurl})")
        _tell(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_podcast_opener = urllib.request.build_opener(_SecureRedirects)


def open_podcast_url(url: str, accept: str = "*/*", timeout: float = 30):
    """Open a podcast's feed or episode address (any https:// site).
    Raises urllib.error.URLError for an address that isn't https://."""
    if not secure(url):
        raise urllib.error.URLError("LuxTunes only uses secure (https://) addresses for "
                                    "podcasts")
    _tell(url)
    return _podcast_opener.open(request(url, accept), timeout=timeout)


def download(url: str, target: str, limit: int, stop=None, progress=None,
             accept: str = "*/*") -> str:
    """Save a podcast episode to `target`, a bit at a time. stop: a
    threading.Event; progress(bytes so far, expected total or 0). Returns the
    answer's content type. Raises ValueError past `limit` bytes (the partial
    file is removed), InterruptedError when stopped."""
    with open_podcast_url(url, accept, timeout=60) as response:
        kind = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        try:
            total = int(response.headers.get("Content-Length") or 0)
        except ValueError:
            total = 0
        if total > limit:
            raise ValueError("the episode is unexpectedly large")
        done = 0
        try:
            with open(target, "wb") as handle:
                while True:
                    if stop is not None and stop.is_set():
                        raise InterruptedError()
                    block = response.read(256 * 1024)
                    if not block:
                        break
                    done += len(block)
                    if done > limit:
                        raise ValueError("the episode is unexpectedly large")
                    handle.write(block)
                    if progress is not None:
                        progress(done, total)
        except BaseException:
            try:
                os.remove(target)
            except OSError:
                pass
            raise
    return kind


def read_limited(response, limit: int = MAX_ANSWER) -> bytes:
    """The whole answer, or ValueError if it's bigger than `limit`."""
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("the answer was unexpectedly large")
    return data


def get(url: str, timeout: float = 15, accept: str = "application/json",
        limit: int = MAX_ANSWER) -> bytes:
    with open_url(request(url, accept), timeout=timeout) as response:
        return read_limited(response, limit)
