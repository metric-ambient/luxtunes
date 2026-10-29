# LuxTunes

**A classic desktop music manager for Linux: your own music, your own library, on your
own computer.** Play and organize your collection, rip CDs, and sync it to an iPod or an
Android phone, all without accounts, subscriptions, or anything going online behind your
back.

> [!WARNING]
> **About this software.** LuxTunes was written entirely by an AI, Claude Opus 5.5
> ("vibe-coded"). For copyright purposes, **metric-ambient is the author** of this software.
>
> The code has not been reviewed line by line by a person. It reads and writes your music
> files and their tags, organizes folders, writes to iPods and phones, and talks to the
> internet when you allow it. Bugs could lose or damage data: **keep backups of your music
> and your devices** before using it.
>
> LuxTunes is provided **"as is", without warranty of any kind**, express or implied,
> including fitness for a particular purpose. You run it at your own risk; the author is not
> liable for any damage or data loss. See the [GPL-3.0 license](LICENSE), sections 15 and 16.

## Why LuxTunes?

Plenty of people still keep their music as files: ripped CDs, purchases, years of
carefully rated songs and hand-made playlists. On Linux there was no single program that
looks after a collection like that the way the classic desktop jukeboxes did, *and* keeps
an iPod or a phone in step with it.

LuxTunes is that program:

- **Bring your library with you.** It imports a full iTunes library, so your ratings, play
  counts, dates and playlists (smart ones too) come along, not just the files.
- **Your music stays yours.** Everything lives on your computer. No account, no streaming,
  no ads, no tracking. LuxTunes only goes online when you say so.
- **Your devices keep working.** Classic iPods sync like they always did, and Android
  phones get a tidy copy of your library that any music player can use, or that the
  [LuxTunes app for Android](https://github.com/metric-ambient/luxtunes-mobile) turns into your whole library in your pocket.
- **Familiar from the first minute.** Playback controls on top, sources on the left, your
  songs on the right, and album art in the corner.

Made for Linux Mint, and it runs on most recent Linux systems.

## What it does

### Your library

- List, Album List, Grid and Cover Flow views. Each Library section keeps its own view,
  columns and sort; search everything or just artists, albums, composers or song names.
- Music, Movies, TV Shows, Podcasts and Audiobooks, kept apart.
- Playlists and live **smart playlists**, plus an **Up Next** queue.
- **Get Info** for editing details, artwork and lyrics (and show, season and episode for
  movies and TV shows), for one song or many at once. Your edits are written into the
  files' tags.
- **Get Track Info Online…** suggests corrections and missing covers from MusicBrainz, and
  **Get Lyrics Online…** fills in lyrics from LRCLIB, both only after you say yes.
- **Keeps your files tidy:** everything you add is copied (or, if you like, moved) into one
  LuxTunes Media folder, named and filed as Artist / Album / *Track Name*, and follows
  your edits.
- **Cut, copy, paste and Undo**, including moving songs between Library sections.
- **Import CDs** as MP3, AAC, Apple Lossless, AIFF or WAV, with track names looked up
  online if you agree.
- Make MP3 and AAC versions of songs, and iPod-ready copies of videos.
- **Find duplicates and missing files**, and point LuxTunes at moved files in one go.
- **A weekly backup** of your library, with the last four weeks kept.

### Listening

- A **10-band equalizer** with presets, and a preset of its own for any song.
- **Crossfade**, **gapless playback** and **Sound Check** (every song at about the same
  volume).
- A **shuffle** that starts with albums you haven't been listening to lately and keeps an
  artist's or album's songs apart, plus **Up Next** with Add to Queue (play next) and Add
  to End of Queue.
- A **Mini Player**, a **Pop-Out Player** with big artwork and lyrics (the line being sung
  highlighted, with lyrics from LRCLIB), and a **sleep timer**.
- Media keys and the desktop's sound menu work as you'd expect.

### Devices

- **iPods:** classic, mini, shuffle, nano 1st–5th generation and the original models. Sync
  music, podcasts, audiobooks, and, on models that show them, videos and photos. Play
  counts and ratings come back from the iPod, and missing album artwork is put back at the
  next sync. (The iPod nano 6th/7th generation and the iPod touch can't be supported.)
- **Android phones,** over a USB cable: songs, videos, podcasts, audiobooks, artwork,
  lyrics, playlists and your equalizer go into one `LuxTunes` folder on the phone, which
  any music player can use. With the companion
  [LuxTunes app for Android](https://github.com/metric-ambient/luxtunes-mobile), plays, ratings, playlist changes, equalizer presets,
  songs added on the phone and details edited there come back at the next sync
  ([how the folder is laid out](docs/android-library-format.md)).
- Choose to sync everything, everything with only some playlists, or just chosen
  playlists, artists, albums and genres.

### Privacy

LuxTunes asks before it ever goes online, which only happens for CD names, Get Track Info
Online…, Get Lyrics Online…, and checking for updates. There's no account and nothing is collected.

## Get LuxTunes

For **Linux Mint 22 / Ubuntu 24.04 or newer** (64-bit PC). No terminal needed:

1. Download **[LuxTunes-x86_64.AppImage](https://github.com/metric-ambient/luxtunes/releases/latest/download/LuxTunes-x86_64.AppImage)**.
2. In your Downloads folder, right-click it → **Properties** → **Permissions** → tick
   **Allow executing file as program**.
3. Double-click it. The first time, LuxTunes offers to **Add to Menu**: it then lives in the
   Applications folder in your home folder and shows up in the menu under Sound & Video.
   You can delete the downloaded file afterwards.

Then choose **File → Library → Import iTunes Library…** to bring in an existing library, or
**File → Add Folder to Library…** to add your music (it's copied into your LuxTunes Media
folder; your originals stay put unless you choose to move them). **Help → LuxTunes Help** (F1) has
short guides to everything.

**Optional extras** (install from Software Manager):

| Install | For |
|---|---|
| `ffmpeg` | movies and TV shows, MP3, AAC and iPod versions, Sound Check |
| `cdparanoia` | importing CDs |
| `libgpod-common` | iPod sync |

**Updating:** Help → Check for Updates…. LuxTunes asks before it goes online, downloads
the new version, and uses it the next time you open LuxTunes.

**Removing it:** Help → Remove LuxTunes from This Computer…. Your library, settings and
music are kept.

**On your phone:** get the [LuxTunes app for Android](https://github.com/metric-ambient/luxtunes-mobile) to keep plays, ratings,
playlists and edits in step both ways.

**Other Linux systems:** the AppImage should run on most systems at least as new as
Ubuntu 24.04, such as Fedora 40+, Debian 13 and Arch. Phone sync needs gvfs, the
desktop's support for phones plugged in by USB. Most desktops have it; on KDE, install your distribution's `gvfs-mtp` or
`gvfs-backends` package.

## Where your things are kept

| What | Where |
|---|---|
| Your library (songs' details, ratings, plays, playlists) | `~/.local/share/luxtunes/library.db` |
| Weekly backups of it | `~/.local/share/luxtunes/Backups/` |
| Window and preference settings | `~/.config/LuxTunes/LuxTunes.conf` |
| Your music files (by default) | `~/Music/LuxTunes/LuxTunes Media` |

To go back to a backup, choose **File → Library → Restore from Backup…**. Your library as
it was is kept in the Backups folder first, so you can change your mind. The music
folder can be moved anywhere in Preferences → Advanced; LuxTunes then looks for your songs
there.

## For developers

Run it from the source code:

```bash
git clone https://github.com/metric-ambient/luxtunes.git
cd luxtunes
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest
luxtunes
```

Other commands: `luxtunes import-itunes "iTunes Music Library.xml" --media-folder
"~/Music/LuxTunes/LuxTunes Media"` imports from the terminal, `luxtunes stats` shows
library totals, and `--db` picks another library file.

To install from the source folder as a normal app (its own Python environment, a
`luxtunes` command and a menu entry, all inside your home folder, no `sudo`), run
`bash packaging/install.sh`. That copy updates itself from Help → Check for Updates…
once a new version is released; to get changes from your source folder before that, run
the script again. `bash packaging/uninstall.sh` removes it and keeps your library and
music.

| Part | Built with |
|---|---|
| Language | Python 3.12+ |
| Interface and playback | Qt 6 via [PySide6](https://doc.qt.io/qtforpython-6/) (Qt Multimedia, FFmpeg) |
| Tags | [mutagen](https://mutagen.readthedocs.io/) |
| Equalizer | [NumPy](https://numpy.org/) |
| iPod | [libgpod](https://sourceforge.net/projects/gtkpod/) |
| CDs | cdparanoia and FFmpeg; names from [MusicBrainz](https://musicbrainz.org/) |
| Lyrics | [LRCLIB](https://lrclib.net/), only when you ask |
| Android app | [luxtunes-mobile](https://github.com/metric-ambient/luxtunes-mobile), a separate project |

```
src/luxtunes/
    core/          library database, tags, organizing, smart playlists, CDs, sound, updates
    core/ipod/     iPod sync (libgpod); models.py says what each model can do
    core/android/  Android sync (over USB file transfer)
    importer/      iTunes library import
    ui/            the Qt interface
    resources/     icon and licence
packaging/         install.sh / uninstall.sh, appimage/ (the ready-to-run app)
docs/              the LuxTunes folder format on Android devices (read by the Android app)
tests/
    fixtures/      small FAKE test data only, never real music or real library files
```

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
