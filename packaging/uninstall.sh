#!/usr/bin/env bash
# Remove what packaging/install.sh added: the app's own folder, the luxtunes
# command, the menu entry and the icons. Your library
# (~/.local/share/luxtunes/library.db), settings (~/.config/LuxTunes) and
# music are left exactly where they are.
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$DATA/luxtunes/app"

if [ -L "$HOME/.local/bin/luxtunes" ] && [ "$(readlink "$HOME/.local/bin/luxtunes")" = "$APP/bin/luxtunes" ]; then
    rm "$HOME/.local/bin/luxtunes"
fi
rm -f "$DATA/applications/luxtunes.desktop"
for icon in "$DATA"/icons/hicolor/*/apps/luxtunes.png "$DATA/icons/hicolor/scalable/apps/luxtunes.svg"; do
    [ -e "$icon" ] && rm "$icon"
done
if [ -x "$APP/bin/python" ]; then          # only ever the folder install.sh made
    rm -rf "$APP"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
echo "LuxTunes is uninstalled. Your library and settings were kept."
