"""Builds dist/LuxTunes-<version>-Setup.exe, the Windows installer.

Used by the Windows jobs in .github/workflows; it can also be run by hand on
Windows 10/11 with Python 3.12 and Inno Setup 6, from the project folder:
    py -3.12 -m venv build\\venv && build\\venv\\Scripts\\activate
    pip install -c packaging\\build-constraints.txt . pyinstaller
    python packaging\\windows\\build.py <FFmpeg folder>

  FFmpeg (ffmpeg.exe and ffprobe.exe, for converting songs) comes from the
  folder given, made on Linux by packaging/appimage/build_ffmpeg.sh --windows:
  the same small FFmpeg the AppImage has, built from checked sources. It's
  tried first, and the build stops if it doesn't run.

  1. PyInstaller bundles LuxTunes and Python into build\\windows\\dist\\LuxTunes.
  2. Inno Setup makes the installer from LuxTunes.iss.
Everything it makes goes into build\\windows and dist.
"""

from __future__ import annotations

import ast
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "packaging" / "windows"
WORK = ROOT / "build" / "windows"
DIST = ROOT / "dist"


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
    print("1/2  Bundling LuxTunes with PyInstaller", flush=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                    "--name", "LuxTunes", "--icon", str(icon),
                    "--distpath", str(WORK / "dist"), "--workpath", str(WORK / "pyinstaller"),
                    "--specpath", str(WORK),
                    "--collect-submodules", "luxtunes", "--collect-data", "luxtunes",
                    str(HERE / "launcher.py")], check=True)
    return WORK / "dist" / "LuxTunes"


def ffmpeg(folder: Path) -> Path:
    print("Checking FFmpeg", flush=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "LICENSE", "SOURCES.txt"):
        if not (folder / name).is_file():
            raise SystemExit(f"{name} isn't in {folder}: make it with "
                             "packaging/appimage/build_ffmpeg.sh <folder> --windows")
    encoders = subprocess.run([str(folder / "ffmpeg.exe"), "-hide_banner", "-encoders"],
                              capture_output=True, text=True).stdout
    if "libmp3lame" not in encoders:
        raise SystemExit("That ffmpeg.exe doesn't run (or can't make MP3s): not using it.")
    return folder


def inno_setup() -> str:
    for candidate in (shutil.which("iscc"),
                      os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
                      os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe")):
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("Inno Setup 6 isn't installed (https://jrsoftware.org/isinfo.php).")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python packaging\\windows\\build.py <FFmpeg folder>")
    tools = ffmpeg(Path(sys.argv[1]).resolve())
    if WORK.exists():
        shutil.rmtree(WORK)                         # only this script's own build folder
    WORK.mkdir(parents=True)
    DIST.mkdir(exist_ok=True)
    icon = WORK / "luxtunes.ico"
    make_icon(ROOT / "src" / "luxtunes" / "resources" / "luxtunes-256.png", icon)
    app = bundle(icon)
    print("2/2  Making the installer with Inno Setup", flush=True)
    subprocess.run([inno_setup(), f"/DAppVersion={version()}", f"/DAppFolder={app}",
                    f"/DFFmpegFolder={tools}", f"/DIconFile={icon}",
                    f"/DLicenseFile={ROOT / 'LICENSE'}", f"/O{DIST}",
                    str(HERE / "LuxTunes.iss")], check=True)
    print(f"Built {DIST / f'LuxTunes-{version()}-Setup.exe'}")


if __name__ == "__main__":
    main()
