# LuxTunes

> [!WARNING]
> **About this software.** LuxTunes was written entirely by an AI, Claude Opus 5.5
> ("vibe-coded"). For copyright purposes, **metric-ambient is the author** of this software.
>
> The code has not been reviewed line by line by a person. It reads and writes your music
> files and their tags, organizes folders, writes to iPods and phones and talks to the internet when you
> allow it. Bugs could lose or damage data: **keep backups of your music and your devices**
> before using it.
>
> LuxTunes is provided **"as is", without warranty of any kind**, express or implied,
> including fitness for a particular purpose. You run it at your own risk; the author is not
> liable for any damage or data loss. See the [GPL-3.0 license](LICENSE), sections 15 and 16.

A native Linux music manager for your library, your CDs, your iPod Classic and your Android phone, built for Linux Mint.

## What it does

- A classic desktop music player: playback controls and display on top, Library/Playlists
  sidebar on the left, album art in the bottom-left corner, song list on the right, with
  List, Album List, Grid and Cover Flow views.
- Imports a full iTunes library from `iTunes Music Library.xml`: every track, all
  metadata (ratings, play counts, skips, dates, comments, …) and all playlists, smart
  playlists included.
- "Keep LuxTunes Media folder organized": files are named and filed by
  Artist / Album / `Disc-Track Title.mp3`, and edits are written back into the tags.
- Live smart playlists, Get Info, Display Duplicates/Missing, CD import (MP3, AAC, Apple
  Lossless, AIFF, WAV), movies, TV shows, podcasts and audiobooks.
- Syncs iPod Classic (5G–7G) on stock firmware: music, videos, podcasts, audiobooks and
  photos (one way, from a folder), reading play counts back from the iPod.
- Syncs Android phones over USB: songs, videos, podcasts, audiobooks, artwork and playlists
  (smart playlists with their rules) go into one `LuxTunes` folder that any music player
  can use. A player that supports it sends plays, skips, ratings and playlist changes back
  at the next sync ([format](docs/android-library-format.md)).
- Nothing goes online without asking you first (CD names, track info, updates).

## Download (no terminal needed)

For Linux Mint 22 / Ubuntu 24.04 or newer (64-bit PC):

1. Download **[LuxTunes-x86_64.AppImage](https://github.com/metric-ambient/luxtunes/releases/latest/download/LuxTunes-x86_64.AppImage)**.
2. In your Downloads folder, right-click it → **Properties** → **Permissions** → tick
   **Allow executing file as program**.
3. Double-click it. The first time, LuxTunes offers to **Add to Menu**: it then lives in the
   Applications folder in your home folder and shows up in the menu under Sound & Video.
   You can delete the downloaded file afterwards.

**Updates:** Help → Check for Updates… (LuxTunes asks before it goes online). It downloads
the new version and replaces itself; the new version starts next time you open it.

**Removing it:** Help → Remove LuxTunes from This Computer…. Your library, settings and music
are kept.

**Optional extras** (install from Software Manager): `ffmpeg` for videos and MP3 versions,
`cdparanoia` for importing CDs, `libgpod-common` for iPod sync.

## Install from source (for developers)

```bash
cd luxtunes              # this source folder
bash packaging/install.sh
```

No `sudo`, nothing outside your home folder: LuxTunes gets its own Python environment in
`~/.local/share/luxtunes/app`, a `luxtunes` command in `~/.local/bin`, and an entry with
its icon in the applications menu (Sound & Video). Your library
(`~/.local/share/luxtunes/library.db`), settings and music are never inside the app's
folder. `bash packaging/uninstall.sh` removes the app again and keeps all of those.

This copy also updates itself from Help → Check for Updates…, using the release's `.whl`
package. A copy running straight from the source folder (below) is updated with `git pull`.

## Making a release

1. Change `__version__` in `src/luxtunes/__init__.py` (e.g. to `0.3.0`) and commit.
2. Tag and push the tag:
   ```bash
   git tag v0.3.0
   git push origin main v0.3.0
   ```
GitHub then runs the tests, publishes the release with the `.whl` package, and a few
minutes later adds `LuxTunes-x86_64.AppImage` (`.github/workflows/release.yml`). Every
installed copy finds it with Check for Updates….

To build the AppImage yourself, see the top of `packaging/appimage/build.sh`.

## Running from source

```bash
git clone https://github.com/metric-ambient/luxtunes.git
cd luxtunes
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest
luxtunes
```

```bash
luxtunes                       # open the window
luxtunes import-itunes "iTunes Music Library.xml" --media-folder "~/Music/LuxTunes/LuxTunes Media"
luxtunes stats
```

The library is stored in `~/.local/share/luxtunes/library.db` (use `--db` to pick another
file). `--media-folder` is the folder that holds your copied `Music/` folder.

## Tech stack

| Part | Library |
|---|---|
| Language | Python 3.12+ |
| Interface and playback | Qt 6 via [PySide6](https://doc.qt.io/qtforpython-6/) (Qt Multimedia, FFmpeg) |
| Tags | [mutagen](https://mutagen.readthedocs.io/) |
| iPod | [libgpod](https://sourceforge.net/projects/gtkpod/) |
| CDs | cdparanoia + FFmpeg; names from [MusicBrainz](https://musicbrainz.org/) |

## Project layout

```
src/luxtunes/
    core/        library database, tags, organizing, smart playlists, CDs, updates
    core/ipod/   iPod Classic sync (libgpod)
    core/android/  Android sync (over USB file transfer)
    importer/    iTunes library XML import
    ui/          Qt interface
    resources/   icon and licence
packaging/       install.sh / uninstall.sh, appimage/ (the ready-to-run app)
docs/            the LuxTunes folder format on Android devices
tests/
    fixtures/    small FAKE test data only. Never real music or real library files
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
