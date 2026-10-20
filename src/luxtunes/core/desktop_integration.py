"""Adding the LuxTunes AppImage to the applications menu.

The first time the AppImage opens, LuxTunes offers to keep it in
~/Applications and add a menu entry with its icon. Everything stays in the
user's home folder and nothing goes online. No Qt in here.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

RESOURCES = Path(__file__).resolve().parent.parent / "resources"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
MARKER = "X-LuxTunes-AppImage=true"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def home_appimage() -> Path:
    """Where the menu's copy of the AppImage lives."""
    return Path.home() / "Applications" / "LuxTunes.AppImage"


def desktop_file() -> Path:
    return _data_home() / "applications" / "luxtunes.desktop"


def menu_entry_is_ours() -> bool | None:
    """True: the menu entry runs an AppImage. False: another copy (install.sh)
    owns it. None: there's no entry."""
    try:
        text = desktop_file().read_text()
    except OSError:
        return None
    return MARKER in text


def should_offer(appimage: Path | None) -> bool:
    """Offer to add to the menu when running an AppImage that isn't already
    the menu's copy, and no install.sh copy owns the menu entry."""
    if appimage is None:
        return False
    owner = menu_entry_is_ours()
    if owner is False:
        return False
    try:
        in_place = appimage.resolve() == home_appimage().resolve()
    except OSError:
        in_place = False
    return not (in_place and owner)


def _copy_atomically(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".LuxTunes-", suffix=".AppImage", dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temp)
        os.chmod(temp, 0o755)
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)


def _quote(path: Path) -> str:
    text = str(path)
    for c in ('\\', '"', '`', '$'):
        text = text.replace(c, '\\' + c)
    return f'"{text}"'


def integrate(appimage: Path) -> Path:
    """Copy the AppImage to ~/Applications (if it isn't there already), add
    the menu entry and icons. Returns the AppImage the menu now runs."""
    target = home_appimage()
    try:
        same = appimage.resolve() == target.resolve()
    except OSError:
        same = False
    if not same:
        _copy_atomically(appimage, target)

    icons = _data_home() / "icons" / "hicolor"
    for size in ICON_SIZES:
        png = RESOURCES / f"luxtunes-{size}.png"
        if png.is_file():
            folder = icons / f"{size}x{size}" / "apps"
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(png, folder / "luxtunes.png")
    svg = RESOURCES / "luxtunes.svg"
    if svg.is_file():
        (icons / "scalable" / "apps").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(svg, icons / "scalable" / "apps" / "luxtunes.svg")

    entry = desktop_file()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=LuxTunes\n"
        "GenericName=Music Player\n"
        "Comment=Play and organize your music, import CDs and sync your iPod\n"
        f"Exec={_quote(target)} %U\n"
        "Icon=luxtunes\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;Player;\n"
        "Keywords=music;player;library;ipod;cd;\n"
        "StartupWMClass=luxtunes\n"
        f"{MARKER}\n")
    return target


def remove() -> None:
    """Take LuxTunes out of the menu and delete the ~/Applications copy.
    The library, settings and music are not touched."""
    if menu_entry_is_ours():
        desktop_file().unlink(missing_ok=True)
    home_appimage().unlink(missing_ok=True)
