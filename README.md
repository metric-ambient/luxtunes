# LuxTunes

**A classic desktop music manager for Linux: your own music, your own library, on your
own computer.** Play and organize your collection, rip CDs, and sync it to an iPod or an
Android phone, all without accounts, subscriptions, or anything going online behind your
back.

> **Disclaimer**
>
> LuxTunes was written entirely by Claude Opus 5.5.
> For the purpose of copyright, **metric-ambient is the author** of this software.
>
> The code has not been reviewed line by line by a person. It reads and writes your music
> files and their tags, organizes folders, writes to iPods and phones, and talks to the
> internet when you allow it. Bugs could lose or damage data: **keep backups of your music
> and your devices** before using it.
>
> LuxTunes is provided **"as is", without warranty of any kind**, express or implied,
> including fitness for a particular purpose. You run it at your own risk; the author is not
> liable for any damage or data loss. See the [GPL-3.0 license](LICENSE), sections 15 and 16.

[![List view](https://luxtunes.org/screenshots/list-view.png)](https://luxtunes.org/screenshots/list-view.png)
[![Carousel](https://luxtunes.org/screenshots/carousel.png)](https://luxtunes.org/screenshots/carousel.png)
[![iPod sync](https://luxtunes.org/screenshots/ipod.png)](https://luxtunes.org/screenshots/ipod.png)
[![Pop-Out Player with lyrics](https://luxtunes.org/screenshots/lyrics.png)](https://luxtunes.org/screenshots/lyrics.png)

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

Made for Linux Mint, and it runs on most recent Linux systems.

## What it does

### The window

- **Three looks, as on the phone:** View → Appearance… offers Dark, Material (the
  default) and Light, each with your own background, text and accent colours, or an
  accent picked from the playing song's album art.
- Music, Movies, TV Shows, Podcasts and Audiobooks are tabs along the top, with the song
  playing in the middle and the views and search on the right. Along the bottom: the album
  art, the sleep timer, the playback controls, the buttons for what's on screen (Sync,
  Display All, the Radio Mix's and podcasts' buttons) and the volume. The sidebar folds
  away with one click when the list needs the room.
- On Linux, LuxTunes draws its own title bar in the look's colours, with the menus in it
  (it can be turned off in View → Appearance…).

### Your library

- List, Album, Grid and Carousel views. Each Library section keeps its own view,
  columns and sort; search everything or just artists, albums, composers or song names.
  Accents and capitals don't matter: "beyonce" finds Beyoncé.
- Music, Movies, TV Shows, Podcasts and Audiobooks, kept apart.
- **Podcast subscriptions:** paste a podcast's feed address (no account needed), then
  **Refresh Podcasts** downloads new episodes; **File → Podcasts…** lists older ones to
  download.
- Playlists and live **smart playlists**, plus an **Up Next** queue.
- **Get Info** for editing details, artwork and lyrics (and show, season and episode for
  movies and TV shows), for one song or many at once. Your edits are written into the
  files' tags. Add **featured artists** and they show after the song's name, "Song (ft.
  Ann, Bo)", on iPods and the phone too. A song by **several artists** keeps its Artist as
  you want it shown ("Lümen & Grayfield") and lists each of them, so browsing finds it under
  both. Genres, featured artists and artists are shown as pills. While you type an artist,
  album or genre, what's already in your library is suggested, and genres come from a
  built-in list of about 3,300 (a song can have several).
- **Get Track Info Online…** suggests corrections, genres and missing covers from
  MusicBrainz (a credit like "Lümen & Grayfield feat. Ann" fills in the artists and the
  featured artist), and **Get Lyrics Online…** fills in lyrics from LRCLIB, both only for
  the songs you choose and only after you say yes (or without the question, if you allow it
  in Preferences → Network).
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
- **Radio Mix:** right-click a song, album or artist to start an endless mix of related songs
  from your own library, by genre and your listening. Like (♥) or skip for good (⊘) songs,
  turn on Discover for songs you rarely play, or save a mix as a playlist. Worked out on your
  computer; nothing goes online.
- A **shuffle** that leans towards albums you haven't been listening to lately and keeps an
  artist's or album's songs well apart, plus **Up Next** with Add to Queue (play next) and Add
  to End of Queue.
- A **Mini Player**, a **Pop-Out Player** with big artwork and lyrics (the line being sung
  highlighted, with lyrics from LRCLIB), and a **sleep timer**.
- Media keys and the desktop's sound menu work as you'd expect.

### Devices

- **iPods:** Supports all Classics, Minis, Nanos, and Shuffles. Sync
  music, podcasts, audiobooks, and, on models that show them, videos and photos. Play
  counts and ratings come back from the iPod, and missing album artwork is put back at the
  next sync. An iPod synced with another computer? LuxTunes offers to add its songs that
  aren't in your library, with their play counts and ratings. Before syncing a nano 6th or
  7th generation, LuxTunes keeps a copy of its song list in case it needs to be put back.
  Keep FLACs in your library: songs the iPod can't play are converted as they sync, to AAC
  or (your choice) Apple Lossless, and your library's files are never changed.
  (The iPod touch can't be supported.)
- **A new iPod sync engine (experimental),** in Preferences → Advanced: LuxTunes' own way of
  writing the iPod's song list, album artwork and signatures, in place of libgpod. It covers
  every iPod above except the shuffles, keeps a copy of the song list before each sync, and
  checks what it wrote by reading it back. libgpod stays the default for now.
- **iPods that also run Rockbox or RepriseOS:** one normal sync serves every firmware on the
  iPod. RepriseOS is built on Apple's own software and reads the same song list; it's
  expected to work but hasn't been tested yet. For Rockbox, LuxTunes also writes your
  playlists into the iPod's `Playlists` folder, gives the iPod's copies of your songs their
  details and a cover Rockbox can show (your library's files are never changed), and adds
  plays from Rockbox's scrobbler log to your library.
- **Android phones,** over a USB cable: songs, videos, podcasts, audiobooks, artwork,
  lyrics, playlists and your equalizer go into one `LuxTunes` folder on the phone, which
  any music player can use. With the companion
  [LuxTunes app for Android](https://github.com/metric-ambient/luxtunes-mobile), plays, ratings, playlist changes, equalizer presets,
  songs added on the phone, details edited there and a song's own equalizer preset come
  back at the next sync
  ([how the folder is laid out](docs/android-library-format.md)).
- Choose to sync everything, everything with only some playlists, or just chosen
  playlists, artists, albums and genres. A sync in progress can be stopped with **Stop
  Sync** at any point, even mid-file; what's already copied stays, and the next sync
  carries on. Each sync keeps a log (Help → Show Sync Logs).

### Privacy

LuxTunes asks before it ever goes online, which only happens for CD names, Get Track Info
Online…, Get Lyrics Online…, podcasts, and checking for updates. There's no account and
nothing is collected. **Preferences → Network** can let any of those go ahead without asking
(all off at first); updates can then be checked, and podcasts refreshed, when LuxTunes
starts. The bottom bar shows whenever LuxTunes connects, only secure (https://) connections
are made, and LuxTunes never accepts connections from outside.

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
folder and your originals stay put; or turn off "Keep LuxTunes Media folder organized" in
Preferences → Advanced to use your music where it already is). **Help → LuxTunes Help** (F1) has
short guides to everything.

**Optional extras** (install from Software Manager):

| Install | For |
|---|---|
| `ffmpeg` | movies and TV shows, MP3, AAC and iPod versions, Sound Check, covers for Rockbox |
| `cdparanoia` | importing CDs |
| `libgpod-common` | iPod sync |

**Updating:** Help → Check for Updates…. LuxTunes asks before it goes online, downloads
the new version, and uses it the next time you open LuxTunes. To have it check by itself
each time it starts, turn that on in Preferences → Network; it still asks before installing.

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

**Restoring a backup:** choose **File → Library → Restore from Backup…** and pick a week.
Before it restores, LuxTunes saves your library as it is right now into the Backups folder,
so if you change your mind you can restore that copy the same way.

**Moved your music folder?** Point LuxTunes at the new location in **Edit → Preferences… →
Advanced** and it finds your songs there.

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
| iPod | [libgpod](https://sourceforge.net/projects/gtkpod/), or LuxTunes' own engine (`core/ipod/itdb/`, ported from [ZenPod](https://github.com/ishiichan-dayo/ZenPod)) |
| iPod nano 6th/7th generation signature | a Python translation of [hashab](https://github.com/dstaley/hashab) by Dylan Staley |
| CDs | cdparanoia and FFmpeg; names from [MusicBrainz](https://musicbrainz.org/) |
| Lyrics | [LRCLIB](https://lrclib.net/), only when you ask |
| Android app | [luxtunes-mobile](https://github.com/metric-ambient/luxtunes-mobile), a separate project |

```
src/luxtunes/
    core/          library database, tags, organizing, smart playlists, CDs, sound, updates
    core/ipod/     iPod sync (libgpod); models.py says what each model can do
    core/ipod/itdb/  LuxTunes' own iPod engine (song list, artwork, signatures, nano SQLite)
    core/android/  Android sync (over USB file transfer)
    importer/      iTunes library import
    ui/            the Qt interface
    resources/     icon and licence
packaging/         install.sh / uninstall.sh, appimage/ (the ready-to-run app)
docs/              the LuxTunes folder format on Android devices (read by the Android app)
tests/
    fixtures/      small test data only
```

## Credits

Syncing the iPod nano 6th and 7th generation is possible thanks to Dylan Staley's
[hashab](https://github.com/dstaley/hashab), an open implementation of the
signature these nanos check on their song list, reverse-engineered from the nano's
firmware and released under the Unlicense. LuxTunes' `core/ipod/hashab.py` is a Python
translation of it, and its lookup tables are copied from it unchanged. hashab in turn
uses the EDON-R hash from Aleksey Kravchenko's [RHash](https://github.com/rhash/RHash).

iPod database formats ported from [ZenPod](https://github.com/ishiichan-dayo/ZenPod) by
ishiichan-dayo (GPL-3.0-or-later), which builds on
[libgpod](https://sourceforge.net/projects/gtkpod/). LuxTunes' new iPod engine
(`core/ipod/itdb/`: the song list, album artwork, the hash58 and hash72 signatures and the
newer nanos' SQLite library) is a Python port of ZenPod's code, and was checked against
libgpod's own output.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
