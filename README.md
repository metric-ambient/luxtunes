# LuxTunes

A native Linux music manager for your library, your CDs and your iPod Classic, built for Linux Mint.

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
  Lossless, AIFF, WAV), movies and TV shows.
- Syncs iPod Classic (5G–7G) on stock firmware, reading play counts back from the iPod.
- Nothing goes online without asking you first (CD names, track info, updates).

## Install

```bash
cd ~/LuxTunes            # this source folder
bash packaging/install.sh
```

No `sudo`, nothing outside your home folder: LuxTunes gets its own Python environment in
`~/.local/share/luxtunes/app`, a `luxtunes` command in `~/.local/bin`, and an entry with
its icon in the applications menu (Sound & Video). Your library
(`~/.local/share/luxtunes/library.db`), settings and music are never inside the app's
folder. `bash packaging/uninstall.sh` removes the app again and keeps all of those.

Optional programs, for videos, CD import, MP3 versions and iPod sync:

```bash
sudo apt install ffmpeg cdparanoia libgpod-common
```

## Updates

Help → Check for Updates… asks GitHub for the newest release (only after you say yes),
and if there is one, downloads and installs it into the app's folder; it starts the next
time LuxTunes opens. While this repository is private, add a GitHub access token in
Preferences → Advanced (a fine-grained token with read-only access to this repository's
contents), or log in with the GitHub command-line tool (`gh auth login`).

A copy running from the source folder (below) is updated with `git pull` instead.

## Making a release

1. Change `__version__` in `src/luxtunes/__init__.py` (e.g. to `0.3.0`) and commit.
2. Tag and push the tag:
   ```bash
   git tag v0.3.0
   git push origin main v0.3.0
   ```
GitHub then runs the tests, builds the package and publishes the release
(`.github/workflows/release.yml`); installed copies find it with Check for Updates….

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
    importer/    iTunes library XML import
    ui/          Qt interface
    resources/   icon and licence
packaging/       install.sh / uninstall.sh
tests/
    fixtures/    small FAKE test data only. Never real music or real library files
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
