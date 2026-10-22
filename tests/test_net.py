"""net.py: only HTTPS, only the services LuxTunes uses, on every redirect too,
and never more than a set amount read. Nothing goes online here."""

import io
import urllib.error

import pytest

from luxtunes.core import net


def test_only_the_services_luxtunes_uses():
    assert net.allowed("https://musicbrainz.org/ws/2/discid/x")
    assert net.allowed("https://coverartarchive.org/release/x/front-500")
    assert net.allowed("https://ia800.us.archive.org/cover.jpg")      # where covers redirect
    assert net.allowed("https://api.github.com/repos/x/releases/latest")
    assert net.allowed("https://release-assets.githubusercontent.com/x")
    assert not net.allowed("http://musicbrainz.org/ws/2/")              # not encrypted
    assert not net.allowed("https://musicbrainz.org.example.com/")
    assert not net.allowed("https://evilgithub.com/")
    assert not net.allowed("file:///etc/passwd")
    assert not net.allowed("https://localhost/")


def test_other_addresses_are_never_opened():
    with pytest.raises(urllib.error.URLError, match="not an address"):
        net.open_url(net.request("https://example.com/"))


def test_redirects_are_checked_too():
    handler = net._CheckedRedirects()
    req = net.request("https://coverartarchive.org/release/x/front-500")
    with pytest.raises(urllib.error.URLError, match="unexpected address"):
        handler.redirect_request(req, None, 307, "", {}, "http://example.com/cover.jpg")
    assert handler.redirect_request(req, None, 307, "", {}, "https://archive.org/c.jpg") is not None


def test_answers_are_capped():
    assert net.read_limited(io.BytesIO(b"x" * 10), limit=10) == b"x" * 10
    with pytest.raises(ValueError):
        net.read_limited(io.BytesIO(b"x" * 11), limit=10)


def test_approval():
    net.require(True, "This")
    for no in (False, None, 1, "yes"):
        with pytest.raises(net.NotApproved, match="This needs your OK first"):
            net.require(no, "This")
