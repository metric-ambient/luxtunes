"""Builds dist/LuxTunes-<version>-Setup.exe, the Windows installer.

Used by the "windows" job in .github/workflows/release.yml; it can also be
run by hand on Windows 10/11 with Python 3.12 and Inno Setup 6, from the
project folder:
    py -3.12 -m venv build\\venv && build\\venv\\Scripts\\activate
    pip install -c packaging\\build-constraints.txt . pyinstaller
    python packaging\\windows\\build.py

  1. PyInstaller bundles LuxTunes and Python into build\\windows\\dist\\LuxTunes.
  2. FFmpeg (ffmpeg.exe and ffprobe.exe, for converting songs) is downloaded
     from a pinned release and checked against its SHA-256; the build stops
     if it doesn't match.
  3. Inno Setup makes the installer from LuxTunes.iss.
Everything it makes goes into build\\windows and dist.
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "packaging" / "windows"
WORK = ROOT / "build" / "windows"
DIST = ROOT / "dist"

# FFmpeg 7.1.1 "essentials" (GPL), built by Gyan Doshi, from its GitHub mirror.
FFMPEG_VERSION = "7.1.1"
FFMPEG_URL = ("https://github.com/GyanD/codexffmpeg/releases/download/"
              f"{FFMPEG_VERSION}/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip")
# Its SHA-256, checked once by hand and written here. Empty: the build prints
# what it downloaded and stops, so a person checks it before it's trusted.
FFMPEG_SHA256 = ""


def version() -> str:
    text = (ROOT / "src" / "luxtunes" / "__init__.py").read_text("utf-8")
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "__version__":
            return node.value.value
    raise SystemExit("no __version__ in src/luxtunes/__init__.py")


def make_icon(png: Path, ico: Path) -> None:
    """A .ico holding the 256-pixel PNG as it is (Windows Vista and later read that)."""
    data = png.read_bytes()
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(data), 6 + 16)
    ico.write_bytes(header + entry + data)


def bundle(icon: Path) -> Path:
    print("1/3  Bundling LuxTunes with PyInstaller", flush=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                    "--name", "LuxTunes", "--icon", str(icon),
                    "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "pyinstaller"),
                    "--specpath", str(WORK),
                    "--collect-submodules", "luxtunes", "--collect-data", "luxtunes",
                    str(HERE / "launcher.py")], check=True)
    return WORK / "dist" / "LuxTunes"


def ffmpeg() -> Path:
    print(f"2/3  FFmpeg {FFMPEG_VERSION}", flush=True)
    archive = WORK / "ffmpeg.zip"
    with urllib.request.urlopen(FFMPEG_URL, timeout=300) as response, open(archive, "wb") as out:
        shutil.copyfileobj(response, out)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if not FFMPEG_SHA256:
        raise SystemExit(f"FFmpeg downloaded with SHA-256 {digest}. Check it against the "
                         f"release page, then set FFMPEG_SHA256 in {Path(__file__).name}.")
    if digest != FFMPEG_SHA256:
        raise SystemExit(f"FFmpeg's checksum doesn't match ({digest}): not using it.")
    folder = WORK / "ffmpeg"
    folder.mkdir()
    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():
            name = member.rsplit("/", 1)[-1]
            if name in ("ffmpeg.exe", "ffprobe.exe", "LICENSE"):
                (folder / name).write_bytes(z.read(member))
    if not (folder / "ffmpeg.exe").exists():
        raise SystemExit("ffmpeg.exe wasn't in the download.")
    return folder


def inno_setup() -> str:
    for candidate in (shutil.which("iscc"),
                      os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
                      os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe")):
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("Inno Setup 6 isn't installed (https://jrsoftware.org/isinfo.php).")


def main() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)                         # only this script's own build folder
    WORK.mkdir(parents=True)
    DIST.mkdir(exist_ok=True)
    icon = WORK / "luxtunes.ico"
    make_icon(ROOT / "src" / "luxtunes" / "resources" / "luxtunes-256.png", icon)
    app = bundle(icon)
    tools = ffmpeg()
    print("3/3  Making the installer with Inno Setup", flush=True)
    subprocess.run([inno_setup(), f"/DAppVersion={version()}", f"/DAppFolder={app}",
                    f"/DFFmpegFolder={tools}", f"/DIconFile={icon}",
                    f"/DLicenseFile={ROOT / 'LICENSE'}", f"/O{DIST}",
                    str(HERE / "LuxTunes.iss")], check=True)
    print(f"Built {DIST / f'LuxTunes-{version()}-Setup.exe'}")


if __name__ == "__main__":
    main()
