#!/usr/bin/env bash
# Builds dist/LuxTunes-x86_64.AppImage, the ready-to-run app.
#
# Used by .github/workflows/release.yml; it can also be run by hand on
# Ubuntu 24.04 / Linux Mint 22 or newer, from the project folder:
#     python3 -m venv build/venv && source build/venv/bin/activate
#     pip install . pyinstaller
#     bash packaging/appimage/build.sh
# Everything it makes goes into build/ and dist/ in the project folder.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/packaging/appimage"
WORK="$ROOT/build/appimage"
APPDIR="$WORK/LuxTunes.AppDir"
OUT="$ROOT/dist/LuxTunes-x86_64.AppImage"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

rm -rf "$WORK"                      # only this script's own build folder
mkdir -p "$WORK" "$ROOT/dist"

echo "1/3  Bundling LuxTunes with PyInstaller"
pyinstaller --noconfirm --clean --name luxtunes \
    --distpath "$WORK/dist" --workpath "$WORK/pyinstaller" --specpath "$WORK" \
    --collect-submodules luxtunes --collect-data luxtunes \
    "$HERE/launcher.py"

# Qt 6 needs libxcb-cursor on X11 and many systems don't have it, so bring it
# along: next to Qt's own libraries, where Qt's xcb plugin looks first. The
# build stops if it can't be found, so a release never ships without it.
BUNDLE="$WORK/dist/luxtunes"
CURSOR="$(ldconfig -p | awk '/libxcb-cursor\.so\.0 / && /x86-64/ {print $NF; exit}')"
if [ -z "$CURSOR" ]; then
    echo "libxcb-cursor.so.0 not found: install libxcb-cursor0 and build again." >&2
    exit 1
fi
QT_CORE="$(find "$BUNDLE/_internal" -name 'libQt6Core.so.6' -print -quit)"
if [ -z "$QT_CORE" ]; then
    echo "Qt's libraries weren't found in the bundle." >&2
    exit 1
fi
QT_LIB="$(dirname "$QT_CORE")"
for dir in "$BUNDLE/_internal" "$QT_LIB"; do
    cp -L "$CURSOR" "$dir/libxcb-cursor.so.0"
done
echo "Bundled $CURSOR into: _internal and ${QT_LIB#"$BUNDLE/"}"

echo "2/3  Making the AppDir"
mkdir -p "$APPDIR/usr/lib"
cp -a "$BUNDLE" "$APPDIR/usr/lib/luxtunes"
install -m 755 "$HERE/AppRun" "$APPDIR/AppRun"
cp "$HERE/luxtunes.desktop" "$APPDIR/luxtunes.desktop"
cp "$ROOT/src/luxtunes/resources/luxtunes-256.png" "$APPDIR/luxtunes.png"
ln -s luxtunes.png "$APPDIR/.DirIcon"

echo "3/3  Packing the AppImage"
TOOL="$WORK/appimagetool"
curl -fsSL -o "$TOOL" "$TOOL_URL"
chmod +x "$TOOL"
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT"
echo "Built $OUT"
