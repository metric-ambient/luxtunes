#!/usr/bin/env bash
# Install LuxTunes for you (no sudo, nothing outside your home folder).
#
#   packaging/install.sh                  install from this source folder
#   packaging/install.sh luxtunes-*.whl   install a downloaded release
#
# What it does, step by step:
#   1. Makes LuxTunes its own Python environment in ~/.local/share/luxtunes/app
#      and installs LuxTunes (and PySide6 + mutagen) into it with pip.
#   2. Links the `luxtunes` command into ~/.local/bin.
#   3. Adds the menu entry (~/.local/share/applications/luxtunes.desktop) and
#      the penguin icon (~/.local/share/icons/hicolor/...).
# Running it again updates the installed copy. Your library
# (~/.local/share/luxtunes/library.db), settings and music are never touched.
# packaging/uninstall.sh removes everything this adds.
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$DATA/luxtunes/app"
BIN="$HOME/.local/bin"
SOURCE="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PYTHON="${PYTHON:-python3}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }

if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    echo "LuxTunes needs Python 3.12 or newer ($PYTHON is $("$PYTHON" -V 2>&1))." >&2
    exit 1
fi
if ! "$PYTHON" -c 'import ensurepip, venv' 2>/dev/null; then
    echo "Python's venv module is missing. Install it with:  sudo apt install python3-venv" >&2
    exit 1
fi
if [ ! -e "$SOURCE" ]; then
    echo "Nothing to install at: $SOURCE" >&2
    exit 1
fi

say "1/3  Installing LuxTunes into $APP"
if [ ! -x "$APP/bin/python" ]; then
    "$PYTHON" -m venv "$APP"
fi
# shellcheck disable=SC2086  # LUXTUNES_PIP_OPTIONS may hold several options
"$APP/bin/python" -m pip install --upgrade ${LUXTUNES_PIP_OPTIONS:-} "$SOURCE"

say "2/3  Adding the luxtunes command to $BIN"
mkdir -p "$BIN"
ln -sf "$APP/bin/luxtunes" "$BIN/luxtunes"

say "3/3  Adding LuxTunes to the applications menu"
RES="$("$APP/bin/python" -c 'import luxtunes, pathlib; print(pathlib.Path(luxtunes.__file__).parent / "resources")')"
ICONS="$DATA/icons/hicolor"
for size in 16 24 32 48 64 128 256 512; do
    if [ -f "$RES/luxtunes-$size.png" ]; then
        mkdir -p "$ICONS/${size}x${size}/apps"
        cp "$RES/luxtunes-$size.png" "$ICONS/${size}x${size}/apps/luxtunes.png"
    fi
done
mkdir -p "$ICONS/scalable/apps"
cp "$RES/luxtunes.svg" "$ICONS/scalable/apps/luxtunes.svg"

mkdir -p "$DATA/applications"
cat > "$DATA/applications/luxtunes.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=LuxTunes
GenericName=Music Player
Comment=Play and organize your music, import CDs and sync your iPod
Exec=$APP/bin/luxtunes
Icon=luxtunes
Terminal=false
Categories=AudioVideo;Audio;Player;
Keywords=music;player;library;ipod;cd;
StartupWMClass=luxtunes
DESKTOP
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$ICONS" || true

VERSION="$("$APP/bin/python" -c 'import luxtunes; print(luxtunes.__version__)')"
say "LuxTunes $VERSION is installed. Open it from the applications menu (Sound & Video)."

missing=()
command -v ffmpeg >/dev/null || missing+=(ffmpeg)
command -v cdparanoia >/dev/null || missing+=(cdparanoia)
"$APP/bin/python" -c 'import ctypes.util, sys; sys.exit(not ctypes.util.find_library("gpod"))' \
    || missing+=(libgpod-common)
if [ ${#missing[@]} -gt 0 ]; then
    echo
    echo "Optional programs not found (videos/CD import/MP3 versions, CDs, iPod sync):"
    echo "    sudo apt install ${missing[*]}"
fi
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo; echo "Note: $BIN isn't on your PATH yet; it will be after you log out and back in." ;;
esac
