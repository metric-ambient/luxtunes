"""Help → Check for Updates… and the AppImage menu entry: versions, GitHub's
answers (made up here, nothing goes online), downloads and swapping in a new
AppImage, all inside a temporary folder."""

import io
import json
import os
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
                      {"name": "luxtunes-9.9.9-py3-none-any.whl", "url": "https://api/whl",
                       "browser_download_url": "https://dl/whl", "size": 99},
                      {"name": "LuxTunes-x86_64.AppImage", "url": "https://api/app",
                       "browser_download_url": "https://dl/app", "size": 64}]}


def appimage_bytes(size=64):
    head = b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 5 + b"\x02\x00\x3e\x00"
    return head + b"\x00" * (size - len(head))


def release(**changes):
    data = {**RELEASE, **changes}
    return updater.Release(data["tag_name"].lstrip("v"), data["tag_name"], data["body"],
                           data["html_url"], [updater.Asset(a["name"], a.get("browser_download_url")
                                                            or a["url"], a["size"])
                                              for a in data["assets"]])


def test_nothing_goes_online_without_your_ok(monkeypatch, tmp_path):
    fake_github(monkeypatch, {})
    with pytest.raises(updater.NotApproved):
        updater.latest_release(approved=False)
    with pytest.raises(updater.NotApproved):
        updater.download(release(), tmp_path, approved=False)
    with pytest.raises(updater.NotApproved):
        updater.replace_appimage(release(), tmp_path / "LuxTunes.AppImage", approved=False)


def test_latest_release_and_download(monkeypatch, tmp_path):
    monkeypatch.delenv("LUXTUNES_GITHUB_TOKEN", raising=False)
    seen = []
    fake_github(monkeypatch, {updater.LATEST: RELEASE, "https://dl/whl": wheel_bytes()}, seen)
    found = updater.latest_release(approved=True)
    assert (found.version, found.notes) == ("9.9.9", "Smart playlists.")
    assert found.asset("installed").name == "luxtunes-9.9.9-py3-none-any.whl"
    assert found.asset("appimage").name == "LuxTunes-x86_64.AppImage"
    assert updater.is_newer(found.version)
    done = []
    path = updater.download(found, tmp_path, approved=True, progress=lambda *a: done.append(a))
    assert path.exists() and done[-1] == (len(wheel_bytes()), 99)
    # The repository is public: no key is sent.
    assert seen[0] == (updater.LATEST, "application/vnd.github+json", None)
    assert seen[1][1] == "application/octet-stream"


def test_problems_say_what_to_do(monkeypatch, tmp_path):
    fake_github(monkeypatch, {updater.LATEST: 404})
    with pytest.raises(updater.UpdateError, match="No LuxTunes release"):
        updater.latest_release(approved=True)
    fake_github(monkeypatch, {updater.LATEST: 403})
    with pytest.raises(updater.UpdateError, match="Try again"):
        updater.latest_release(approved=True)
    fake_github(monkeypatch, {updater.LATEST: {**RELEASE, "assets": RELEASE["assets"][:1]}})
    with pytest.raises(updater.UpdateError, match="nothing to install"):
        updater.latest_release(approved=True)
    fake_github(monkeypatch, {"https://dl/whl": wheel_bytes("something_else")})
    with pytest.raises(updater.UpdateError, match="isn't a LuxTunes package"):
        updater.download(release(), tmp_path, approved=True)


def running_as_appimage(monkeypatch, path):
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPIMAGE", str(path))


def test_appimage_replaces_itself(monkeypatch, tmp_path):
    current = tmp_path / "Applications" / "LuxTunes.AppImage"
    current.parent.mkdir()
    current.write_bytes(b"old version")
    running_as_appimage(monkeypatch, current)
    assert updater.kind() == "appimage" and updater.launcher() == [str(current)]
    fake_github(monkeypatch, {"https://dl/app": appimage_bytes()})
    updater.download_and_install(release(), approved=True)
    assert current.read_bytes() == appimage_bytes()
    assert os.access(current, os.X_OK)
    assert sorted(p.name for p in current.parent.iterdir()) == ["LuxTunes.AppImage"]


def test_a_bad_appimage_download_changes_nothing(monkeypatch, tmp_path):
    current = tmp_path / "LuxTunes.AppImage"
    current.write_bytes(b"old version")
    running_as_appimage(monkeypatch, current)
    fake_github(monkeypatch, {"https://dl/app": b"<html>not a program</html>"})
    with pytest.raises(updater.UpdateError, match="isn't a LuxTunes AppImage"):
        updater.download_and_install(release(), approved=True)
    assert current.read_bytes() == b"old version"
    assert [p.name for p in tmp_path.iterdir()] == ["LuxTunes.AppImage"]


def test_not_an_appimage_unless_packaged(monkeypatch, tmp_path):
    current = tmp_path / "LuxTunes.AppImage"
    current.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(current))
    monkeypatch.delattr(updater.sys, "frozen", raising=False)
    assert updater.running_appimage() is None


def test_menu_entry_for_the_appimage(monkeypatch, tmp_path):
    from luxtunes.core import desktop_integration as di
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    downloaded = home / "Downloads" / "LuxTunes-x86_64.AppImage"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(appimage_bytes())
    assert di.should_offer(downloaded) and not di.should_offer(None)

    target = di.integrate(downloaded)
    assert target == home / "Applications" / "LuxTunes.AppImage"
    assert target.read_bytes() == appimage_bytes() and os.access(target, os.X_OK)
    entry = di.desktop_file().read_text()
    assert f'Exec="{target}" %U' in entry and di.MARKER in entry
    assert "MimeType=x-content/audio-player;" in entry          # offered for "Music player"
    assert (home / ".local/share/icons/hicolor/128x128/apps/luxtunes.png").exists()
    assert not di.should_offer(target)          # already in the menu
    assert downloaded.exists()                  # the download is left for the user

    di.remove()
    assert not target.exists() and not di.desktop_file().exists()


def test_menu_entry_left_alone_for_install_sh_copies(monkeypatch, tmp_path):
    from luxtunes.core import desktop_integration as di
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    di.desktop_file().parent.mkdir(parents=True)
    di.desktop_file().write_text("[Desktop Entry]\nExec=/somewhere/bin/luxtunes\n")
    appimage = tmp_path / "LuxTunes.AppImage"
    appimage.write_bytes(appimage_bytes())
    assert not di.should_offer(appimage)
    di.remove()
    assert di.desktop_file().exists()


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
