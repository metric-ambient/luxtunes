"""Help → Check for Updates…: versions, GitHub's answers (made up here, nothing
goes online), the downloaded package, and the access token file."""

import io
import json
import os
import stat
import subprocess
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

from luxtunes.core import updater

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def test_versions():
    assert updater.parse_version("v0.2.10") == (0, 2, 10)
    assert updater.parse_version("1.0") == (1, 0)
    assert updater.parse_version("0.3.0rc1") == (0, 3, 0)
    assert updater.is_newer("0.10.0", than="0.9.9")
    assert not updater.is_newer("v0.2.0", than="0.2.0")
    assert not updater.is_newer("0.1.9", than="0.2.0")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_github(monkeypatch, answers, seen=None):
    def fake_open(request, timeout=30):
        if seen is not None:
            seen.append((request.full_url, request.get_header("Accept"),
                         request.unredirected_hdrs.get("Authorization")))
        answer = answers[request.full_url]
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "error", {}, None)
        return FakeResponse(answer if isinstance(answer, bytes) else json.dumps(answer).encode())
    monkeypatch.setattr(updater, "_open", fake_open)


def wheel_bytes(name="luxtunes"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(f"{name}/__init__.py", "__version__ = '9.9.9'\n")
    return buffer.getvalue()


RELEASE = {"tag_name": "v9.9.9", "body": "Smart playlists.", "html_url": "https://github.com/x",
           "assets": [{"name": "luxtunes-9.9.9.tar.gz", "url": "https://api/tar", "size": 5},
                      {"name": "luxtunes-9.9.9-py3-none-any.whl", "url": "https://api/whl", "size": 99}]}


def no_token(monkeypatch, tmp_path):
    monkeypatch.delenv("LUXTUNES_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(updater, "gh_token", lambda: "")


def test_nothing_goes_online_without_your_ok(monkeypatch, tmp_path):
    fake_github(monkeypatch, {})
    with pytest.raises(updater.NotApproved):
        updater.latest_release(approved=False)
    release = updater.Release("9.9.9", "v9.9.9", "", "luxtunes-9.9.9-py3-none-any.whl", "u", 1, "p")
    with pytest.raises(updater.NotApproved):
        updater.download(release, tmp_path, approved=False)


def test_latest_release_and_download(monkeypatch, tmp_path):
    no_token(monkeypatch, tmp_path)
    monkeypatch.setenv("LUXTUNES_GITHUB_TOKEN", "secret")
    seen = []
    fake_github(monkeypatch, {updater.LATEST: RELEASE, "https://api/whl": wheel_bytes()}, seen)
    release = updater.latest_release(approved=True)
    assert (release.version, release.wheel_name, release.notes) == \
        ("9.9.9", "luxtunes-9.9.9-py3-none-any.whl", "Smart playlists.")
    assert updater.is_newer(release.version)
    done = []
    path = updater.download(release, tmp_path, approved=True, progress=lambda *a: done.append(a))
    assert path.exists() and done[-1] == (len(wheel_bytes()), 99)
    # The token goes to GitHub only (not to wherever the download is redirected).
    assert seen[0] == (updater.LATEST, "application/vnd.github+json", "Bearer secret")
    assert seen[1][1] == "application/octet-stream"


def test_problems_say_what_to_do(monkeypatch, tmp_path):
    no_token(monkeypatch, tmp_path)
    fake_github(monkeypatch, {updater.LATEST: 404})
    with pytest.raises(updater.UpdateError, match="access token"):
        updater.latest_release(approved=True)
    monkeypatch.setenv("LUXTUNES_GITHUB_TOKEN", "wrong")
    fake_github(monkeypatch, {updater.LATEST: 401})
    with pytest.raises(updater.UpdateError, match="didn't accept"):
        updater.latest_release(approved=True)
    fake_github(monkeypatch, {updater.LATEST: {**RELEASE, "assets": []}})
    with pytest.raises(updater.UpdateError, match="no installable"):
        updater.latest_release(approved=True)
    release = updater.Release("9.9.9", "v9.9.9", "", "luxtunes-9.9.9-py3-none-any.whl", "https://api/bad", 1, "p")
    fake_github(monkeypatch, {"https://api/bad": wheel_bytes("something_else")})
    with pytest.raises(updater.UpdateError, match="isn't a LuxTunes package"):
        updater.download(release, tmp_path, approved=True)


def test_token_file_is_private(monkeypatch, tmp_path):
    no_token(monkeypatch, tmp_path)
    assert updater.token() == ""
    updater.save_token("  github_pat_abc  ")
    path = updater.token_file()
    assert path.read_text() == "github_pat_abc\n"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert updater.token() == "github_pat_abc"
    updater.save_token("")
    assert not path.exists() and updater.token() == ""


def test_source_copy_never_installs(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert not updater.installed_copy()                     # the tests run from the source folder
    with pytest.raises(updater.UpdateError, match="git pull"):
        updater.install(tmp_path / "x.whl")


def test_install_scripts(tmp_path):
    """install.sh/uninstall.sh: valid, and uninstall leaves the library alone."""
    for script in ("install.sh", "uninstall.sh"):
        subprocess.run(["bash", "-n", str(PACKAGING / script)], check=True)
    home = tmp_path / "home"
    data = home / ".local" / "share"
    library = data / "luxtunes" / "library.db"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"my library")
    (data / "applications").mkdir(parents=True)
    (data / "applications" / "luxtunes.desktop").write_text("[Desktop Entry]\n")
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(data)}
    subprocess.run(["bash", str(PACKAGING / "uninstall.sh")], check=True, env=env,
                   capture_output=True)
    assert library.read_bytes() == b"my library"
    assert not (data / "applications" / "luxtunes.desktop").exists()
