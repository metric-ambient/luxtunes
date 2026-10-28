"""Every request LuxTunes makes to the internet goes through here.

Callers check approved=True first (require()), so nothing goes online without
your OK. On top of that, this module only talks HTTPS, only to the services
LuxTunes uses (MusicBrainz, the Cover Art Archive and the archive.org servers
it sends covers from, LRCLIB for lyrics, and GitHub for updates), checks that again on every
redirect, and caps how much it reads, so a bad or hijacked answer can't send
LuxTunes somewhere else or fill up memory. No Qt in here.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from luxtunes import __version__

USER_AGENT = f"LuxTunes/{__version__} (https://github.com/metric-ambient/luxtunes)"

# A host is allowed if it is one of these or a subdomain of one.
ALLOWED_HOSTS = ("musicbrainz.org", "coverartarchive.org", "archive.org", "lrclib.net",
                 "github.com", "githubusercontent.com")

MAX_ANSWER = 32 * 1024 * 1024           # JSON answers and cover images


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
    return _opener.open(req, timeout=timeout)


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
