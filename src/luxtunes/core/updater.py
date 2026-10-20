"""Help → Check for Updates…: new versions of LuxTunes from GitHub releases.

Each release (built by .github/workflows/release.yml when a version tag is
pushed) carries two downloads:

  * LuxTunes-x86_64.AppImage, the ready-to-run app. A running AppImage
    updates by downloading the new one next to itself and swapping it in.
  * a .whl package, for copies installed with packaging/install.sh, which
    update by pip-installing it into their own environment.

A copy running from a source checkout is updated with `git pull` instead.

Nothing here goes online unless the caller passes approved=True, which the
window only does after the user says yes. No Qt in here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from luxtunes import __version__

REPO = "metric-ambient/luxtunes"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
APPIMAGE_NAME = "LuxTunes-x86_64.AppImage"
DOWNLOAD_PAGE = f"{RELEASES_PAGE}/latest/download/{APPIMAGE_NAME}"


class UpdateError(Exception):
    """Checking or installing didn't work; the message says why, in plain words."""


class NotApproved(Exception):
    """Something needed the internet before the user said it could."""


# ---- Which copy is running ---------------------------------------------------------

def app_dir() -> Path:
    """Where install.sh puts its copy (its own Python environment)."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "luxtunes" / "app"


def running_appimage() -> Path | None:
    """The AppImage file this copy runs from, or None."""
    path = os.environ.get("APPIMAGE")
    if path and getattr(sys, "frozen", False) and Path(path).is_file():
        return Path(path)
    return None


def installed_copy() -> bool:
    """True for the copy install.sh made."""
    try:
        return Path(sys.prefix).resolve() == app_dir().resolve()
    except OSError:
        return False


def kind() -> str:
    """"appimage", "installed" or "source": how this copy updates."""
    if running_appimage():
        return "appimage"
    if installed_copy():
        return "installed"
    return "source"


def launcher() -> list[str]:
    """The command that starts this copy again (to restart after an update)."""
    appimage = running_appimage()
    if appimage:
        return [str(appimage)]
    script = Path(sys.prefix) / "bin" / "luxtunes"
    if script.exists():
        return [str(script)]
    return [sys.executable, "-m", "luxtunes"]


# ---- Versions ----------------------------------------------------------------------

def parse_version(text: str) -> tuple[int, ...]:
    """"v0.2.10" -> (0, 2, 10). Anything after a non-digit part is ignored."""
    parts = []
    for piece in text.strip().lstrip("vV").split("."):
        digits = ""
        for c in piece:                 # leading digits only: "0rc1" -> 0
            if not c.isdigit():
                break
            digits += c
        if not digits:
            break
        parts.append(int(digits))
        if len(digits) < len(piece):    # "3.0rc1": stop after the 0
            break
    return tuple(parts)


def is_newer(version: str, than: str = __version__) -> bool:
    return parse_version(version) > parse_version(than)


# ---- Talking to GitHub -------------------------------------------------------------

def _request(url: str, accept: str) -> urllib.request.Request:
    request = urllib.request.Request(url, headers={"Accept": accept,
                                                   "User-Agent": f"LuxTunes/{__version__}",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
    # Optional, only to raise GitHub's limit on how often it can be asked.
    key = os.environ.get("LUXTUNES_GITHUB_TOKEN", "").strip()
    if key:
        # Not passed on when GitHub redirects a download to its file storage.
        request.add_unredirected_header("Authorization", f"Bearer {key}")
    return request


def _open(request: urllib.request.Request, timeout: float = 30):
    return urllib.request.urlopen(request, timeout=timeout)


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass
class Release:
    version: str               # "0.3.0"
    tag: str                   # "v0.3.0"
    notes: str
    page: str                  # the release's page on github.com
    assets: list[Asset] = field(default_factory=list)

    def asset(self, kind_: str | None = None) -> Asset | None:
        """The download this copy needs: the AppImage or the .whl package."""
        suffix = ".AppImage" if (kind_ or kind()) == "appimage" else ".whl"
        return next((a for a in self.assets if a.name.endswith(suffix)), None)


def latest_release(*, approved: bool) -> Release:
    """The newest published release. Raises UpdateError (with what to do)."""
    if approved is not True:
        raise NotApproved("Checking for updates needs your OK first.")
    try:
        with _open(_request(LATEST, "application/vnd.github+json")) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError("No LuxTunes release has been published yet.") from error
        if error.code in (403, 429):
            raise UpdateError("GitHub is busy or limiting how often it can be asked. "
                              "Try again in an hour.") from error
        raise UpdateError(f"GitHub answered {error.code}.") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise UpdateError(f"Couldn't reach GitHub ({getattr(error, 'reason', error)}).") from error
    assets = [Asset(a["name"], a.get("browser_download_url") or a["url"], int(a.get("size") or 0))
              for a in data.get("assets") or [] if a.get("name")]
    tag = data.get("tag_name") or ""
    release = Release(version=tag.lstrip("vV"), tag=tag, notes=(data.get("body") or "").strip(),
                      page=data.get("html_url") or RELEASES_PAGE, assets=assets)
    if not release.asset("appimage") and not release.asset("installed"):
        raise UpdateError(f"The latest release ({tag or '?'}) has nothing to install yet. If it "
                          "was just published, GitHub may still be building it: try again in "
                          "a few minutes.")
    return release


def _fetch(asset: Asset, target: Path, progress=None) -> None:
    try:
        with _open(_request(asset.url, "application/octet-stream"), timeout=120) as response, \
                open(target, "wb") as out:
            done = 0
            while chunk := response.read(256 * 1024):
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, asset.size)
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError(f"The download didn't finish ({getattr(error, 'reason', error)}).") from error


def _checked_name(asset: Asset | None, prefix: str) -> Asset:
    if asset is None:
        raise UpdateError("This release doesn't have the download this copy needs yet. "
                          "Try again in a few minutes.")
    if "/" in asset.name or not asset.name.startswith(prefix):
        raise UpdateError(f"Unexpected file in the release: {asset.name}")
    return asset


# ---- Package (.whl) updates, for install.sh copies ----------------------------------

def download(release: Release, folder: Path, *, approved: bool, progress=None) -> Path:
    """Download the release's .whl package into `folder`."""
    if approved is not True:
        raise NotApproved("Downloading an update needs your OK first.")
    asset = _checked_name(release.asset("installed"), "luxtunes-")
    target = Path(folder) / asset.name
    _fetch(asset, target, progress)
    if not zipfile.is_zipfile(target):
        raise UpdateError("The downloaded file isn't a LuxTunes package.")
    with zipfile.ZipFile(target) as wheel:
        if not any(n.startswith("luxtunes/") for n in wheel.namelist()):
            raise UpdateError("The downloaded file isn't a LuxTunes package.")
    return target


def install(wheel: Path) -> None:
    """pip-install the package into this copy's own environment."""
    if not installed_copy():
        raise UpdateError("This copy of LuxTunes runs from its source folder: update it with "
                          "git pull instead.")
    try:
        result = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--no-input",
                                 "--disable-pip-version-check", str(wheel)],
                                capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as error:
        raise UpdateError(f"Installing didn't work: {error}") from error
    if result.returncode != 0:
        lines = [l for l in (result.stderr or result.stdout).splitlines() if l.strip()]
        raise UpdateError("Installing didn't work:\n" + "\n".join(lines[-6:]))


# ---- AppImage updates --------------------------------------------------------------

def looks_like_appimage(path: Path) -> bool:
    """An x86-64 ELF program with the AppImage type 2 mark."""
    try:
        with open(path, "rb") as f:
            head = f.read(20)
    except OSError:
        return False
    return (len(head) == 20 and head[:4] == b"\x7fELF" and head[8:11] == b"AI\x02"
            and head[18:20] == b"\x3e\x00")


def replace_appimage(release: Release, current: Path, *, approved: bool, progress=None) -> Path:
    """Download the new AppImage next to `current` and swap it in.

    The running app keeps working (it still holds the old file open); the new
    version starts on the next launch. If anything goes wrong the old file is
    left as it was.
    """
    if approved is not True:
        raise NotApproved("Downloading an update needs your OK first.")
    asset = _checked_name(release.asset("appimage"), "LuxTunes")
    folder = current.parent
    if not os.access(folder, os.W_OK):
        raise UpdateError(f"LuxTunes can't write to {folder}, so it can't update itself there. "
                          f"Download the new version from {RELEASES_PAGE} instead.")
    fd, temp = tempfile.mkstemp(prefix=".LuxTunes-update-", suffix=".AppImage", dir=folder)
    os.close(fd)
    temp = Path(temp)
    try:
        _fetch(asset, temp, progress)
        if not looks_like_appimage(temp):
            raise UpdateError("The downloaded file isn't a LuxTunes AppImage.")
        temp.chmod(0o755)
        os.replace(temp, current)
    finally:
        temp.unlink(missing_ok=True)
    return current


# ---- Either kind ---------------------------------------------------------------------

def download_and_install(release: Release, *, approved: bool, progress=None,
                         installing=None) -> None:
    """Update this copy, whichever kind it is. installing() is called once the
    download is done and the install step starts."""
    appimage = running_appimage()
    if appimage:
        replace_appimage(release, appimage, approved=approved, progress=progress)
        return
    with tempfile.TemporaryDirectory(prefix="luxtunes-update-") as folder:
        wheel = download(release, Path(folder), approved=approved, progress=progress)
        if installing is not None:
            installing()
        install(wheel)
