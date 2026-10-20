"""Help → Check for Updates…: new versions of LuxTunes from GitHub.

How it fits together:

  * packaging/install.sh installs LuxTunes for you (no sudo) into its own
    Python environment, ~/.local/share/luxtunes/app, plus a menu entry and
    icon. Your library, settings and music are never inside that folder.
  * Each new version is a GitHub release (made automatically when a version
    tag such as v0.3.0 is pushed: .github/workflows/release.yml) holding a
    "wheel", the installable package.
  * The installed app asks GitHub for the latest release, and if it's newer,
    downloads that wheel and installs it into its own environment with pip.
    The new version starts the next time LuxTunes opens.

Nothing here goes online unless the caller passes approved=True, which the
window only does after you've said yes (every time; there's no automatic
check). A copy running from its source folder (a git checkout) is updated
with `git pull` instead.

The repository is private, so GitHub needs a key to show it the releases:
a token you paste into Preferences → Advanced (kept in a file only you can
read), or the GitHub command-line tool's login (`gh auth login`) if that's
installed. No Qt in here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from luxtunes import __version__

REPO = "metric-ambient/luxtunes"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"


def app_dir() -> Path:
    """Where install.sh puts the installed app (its own Python environment)."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "luxtunes" / "app"


def token_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "LuxTunes" / "github-token"


class UpdateError(Exception):
    """Checking or installing didn't work; the message says why, in plain words."""


class NotApproved(Exception):
    """Something needed the internet before you said it could."""


# ---- Which copy is running ----------------------------------------------------------

def installed_copy() -> bool:
    """True when this is the copy install.sh made (it can update itself);
    False when running from the source folder (update with git pull)."""
    try:
        return Path(sys.prefix).resolve() == app_dir().resolve()
    except OSError:
        return False


def launcher() -> list[str]:
    """The command that starts this copy of LuxTunes again (to restart after an update)."""
    script = Path(sys.prefix) / "bin" / "luxtunes"
    if script.exists():
        return [str(script)]
    return [sys.executable, "-m", "luxtunes"]


# ---- Versions --------------------------------------------------------------------------

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


# ---- The key for the private repository ----------------------------------------------------

def save_token(token: str) -> None:
    """Keep the token in a file only you can read (or remove it when empty)."""
    path = token_file()
    token = token.strip()
    if not token:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    os.chmod(path, 0o600)


def saved_token() -> str:
    try:
        return token_file().read_text().strip()
    except OSError:
        return ""


def gh_token() -> str:
    """The GitHub command-line tool's login, if it's installed and logged in."""
    gh = shutil.which("gh")
    if not gh:
        return ""
    try:
        result = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def token() -> str:
    return os.environ.get("LUXTUNES_GITHUB_TOKEN", "").strip() or saved_token() or gh_token()


# ---- Talking to GitHub --------------------------------------------------------------------

def _request(url: str, accept: str, key: str) -> urllib.request.Request:
    request = urllib.request.Request(url, headers={"Accept": accept,
                                                   "User-Agent": f"LuxTunes/{__version__}",
                                                   "X-GitHub-Api-Version": "2022-11-28"})
    if key:
        # Not passed on when GitHub redirects the download to its file storage.
        request.add_unredirected_header("Authorization", f"Bearer {key}")
    return request


def _open(request: urllib.request.Request, timeout: float = 30):
    return urllib.request.urlopen(request, timeout=timeout)


@dataclass
class Release:
    version: str               # "0.3.0"
    tag: str                   # "v0.3.0"
    notes: str
    wheel_name: str
    wheel_url: str             # the API address of the wheel (works for private repos)
    wheel_size: int
    page: str                  # the release's page on github.com


def latest_release(*, approved: bool) -> Release:
    """The newest published release. Raises UpdateError (with what to do)."""
    if approved is not True:
        raise NotApproved("Checking for updates needs your OK first.")
    key = token()
    try:
        with _open(_request(LATEST, "application/vnd.github+json", key)) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code in (401, 403) and key:
            raise UpdateError("GitHub didn't accept the access token. Check it in "
                              "Preferences → Advanced (it needs read access to the "
                              "repository's contents).") from error
        if error.code == 404:
            if not key:
                raise UpdateError(
                    "GitHub didn't show the releases. While the LuxTunes repository is private, "
                    "updates need a GitHub access token: add one in Preferences → Advanced "
                    "(or log in with the GitHub command-line tool: gh auth login).") from error
            raise UpdateError("No release found yet: publish one by pushing a version tag "
                              "(see the README).") from error
        raise UpdateError(f"GitHub answered {error.code}.") from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise UpdateError(f"Couldn't reach GitHub ({getattr(error, 'reason', error)}).") from error
    wheels = [a for a in data.get("assets") or [] if a.get("name", "").endswith(".whl")]
    if not wheels:
        raise UpdateError(f"The latest release ({data.get('tag_name', '?')}) has no installable "
                          "package attached yet. If it was just tagged, GitHub may still be "
                          "building it: try again in a few minutes.")
    wheel = wheels[0]
    tag = data.get("tag_name") or ""
    return Release(version=tag.lstrip("vV"), tag=tag, notes=(data.get("body") or "").strip(),
                   wheel_name=wheel["name"], wheel_url=wheel["url"],
                   wheel_size=int(wheel.get("size") or 0),
                   page=data.get("html_url") or RELEASES_PAGE)


def download(release: Release, folder: Path, *, approved: bool, progress=None) -> Path:
    """Download the release's wheel into `folder`. progress(done, total) as it goes."""
    if approved is not True:
        raise NotApproved("Downloading an update needs your OK first.")
    if "/" in release.wheel_name or not release.wheel_name.startswith("luxtunes-"):
        raise UpdateError(f"Unexpected file in the release: {release.wheel_name}")
    target = Path(folder) / release.wheel_name
    try:
        with _open(_request(release.wheel_url, "application/octet-stream", token()), timeout=120) \
                as response, open(target, "wb") as out:
            done = 0
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, release.wheel_size)
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError(f"The download didn't finish ({getattr(error, 'reason', error)}).") from error
    if not zipfile.is_zipfile(target):
        raise UpdateError("The downloaded file isn't a LuxTunes package.")
    with zipfile.ZipFile(target) as wheel:
        if not any(n.startswith("luxtunes/") for n in wheel.namelist()):
            raise UpdateError("The downloaded file isn't a LuxTunes package.")
    return target


def install(wheel: Path) -> None:
    """Install the downloaded version into this copy's own environment (pip).
    Only for the installed copy; the running window keeps working, and the new
    version starts next time."""
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


def download_and_install(release: Release, *, approved: bool, progress=None) -> None:
    with tempfile.TemporaryDirectory(prefix="luxtunes-update-") as folder:
        install(download(release, Path(folder), approved=approved, progress=progress))
