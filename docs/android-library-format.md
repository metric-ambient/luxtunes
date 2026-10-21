# The LuxTunes folder on Android devices

LuxTunes syncs to an Android device over USB ("File transfer" mode). Everything goes in
one folder, `LuxTunes`, at the top of the storage chosen on the device's page (the
device's own storage or an SD card). Any music player can scan it; a player that wants
ratings, play counts and playlists exactly as LuxTunes has them reads `library.db`.

```
LuxTunes/
    LuxTunes Media/
        Music/<Artist>/<Album>/<disc-track Name>.<ext>
        Movies/  TV Shows/  Podcasts/  Audiobooks/
        .../cover.jpg            one per album folder (or cover.png)
    Playlists/<name>.m3u8        one per synced playlist
    library.db                   SQLite, rewritten at every sync
    changes.json                 written by a player on the device (optional)
    .luxtunes-sync.json          LuxTunes' own list of what it put here
```

Songs keep their own embedded artwork (also converted ones: "Convert higher bit rate
songs" writes the details, lyrics and artwork back into the converted copy). Each album
folder also gets a `cover.jpg` (or `.png`) from the first of its songs that has artwork,
written as soon as that song is copied; `tracks.artwork` in `library.db` points to it.

LuxTunes never writes or deletes anything outside the `LuxTunes` folder (it follows
links before deciding, so a link can't lead it elsewhere). With **Delete media not in this
selection** on (the default), each sync removes media that's no longer chosen, and any
other file in `LuxTunes Media` that LuxTunes didn't put there (hidden files excepted).
With it off, those stay until it's turned on. Files are never edited in place on the
device: a changed song is removed and copied again.

## Playlists

UTF-8 `.m3u8` files with `#EXTM3U`, `#PLAYLIST:<name>` and `#EXTINF:<seconds>,<artist> -
<name>` lines. Paths are relative to the `Playlists` folder, for example
`../LuxTunes Media/Music/Artist/Album/01 Song.mp3`.

## library.db

Treat it as read-only: LuxTunes replaces it at every sync, also one that's stopped
part-way or cancelled at the sync question (it then lists exactly the songs on the
phone). This page describes format version **2** (LuxTunes 0.6.0 and newer); version 1
lacked the smart playlist rules, the extra song details and the reference tables.

| Table | Columns |
|---|---|
| `info` | `key`, `value`: `format_version` (now `2`), `library_id`, `library_name`, `luxtunes_version`, `synced_at` |
| `tracks` | `id`, `path`, `artwork`, then the song details below |
| `playlists` | `id`, `name`, `is_smart`, `position` (sidebar order), `file` (its .m3u8, relative to `LuxTunes`), `smart_info`, `smart_criteria` |
| `playlist_items` | `playlist_id`, `position`, `track_id` |
| `reference_playlists` | `id`, `name`, `is_smart`, `is_folder` |
| `reference_items` | `playlist_id`, `track_id` |

- `id` is the song's persistent ID: 16 hexadecimal digits that never change for that
  song. Use it to match songs between syncs and in `changes.json`.
- `path` and `artwork` are relative to the `LuxTunes` folder. `artwork` may be empty.
- `library_id` identifies the computer's library. If it changes, the folder was synced
  from a different library and stored plays should not be carried over.
- `playlist_items` lists only songs that are on the phone, also for smart playlists
  (their current songs, so players that don't understand rules still work).

### Smart playlists

`smart_info` and `smart_criteria` are the smart playlist's two binary blocks, byte for
byte as LuxTunes stores them (empty for plain playlists). The format is described at the
top of `src/luxtunes/core/smart.py`: Smart Info (112 bytes: live updating, limits,
"match only checked items") and Smart Criteria ("SLst" rules, nested groups). A player
that evaluates them sees only the songs on the phone, so a live smart playlist there can
hold fewer songs than on the computer.

A "Playlist is …" rule points to another playlist by persistent ID. If that playlist
is synced, it's in `playlists`. If not, it's in `reference_playlists` with its songs
(those on the phone) in `reference_items`: use them to evaluate the rule, but don't show
them as playlists. A folder's songs are those of all its playlists.

### Song details

Where the library has them: `media_kind` (`music`, `movie`, `tvshow`, `podcast`,
`audiobook`), `name`, `artist`, `album_artist`, `album`, `genre`, `composer`, `grouping`,
`year`, `track_number`, `track_count`, `disc_number`, `disc_count`, `total_time`
(milliseconds), `bit_rate`, `sample_rate`, `size`, `rating` and `album_rating` (0–100, 20
per star), `rating_computed` (1 if the rating is the album's, not set for this song),
`play_count`, `play_date`, `skip_count`, `skip_date`, `date_added`, `date_modified`,
`compilation`, `comments`, `lyrics`, `bookmarkable`, `bookmark_time` (milliseconds),
`skip_when_shuffling`, `start_time`, `stop_time`, `volume_adjustment`, `show`, `season`,
`episode_number`, the `sort_*` names, `enabled`, `bpm`, `kind` (e.g. "MPEG audio file")
and `purchased`. That's every detail a smart playlist rule can test. Dates are UTC,
written as `YYYY-MM-DDTHH:MM:SSZ`.

## changes.json (from the device back to the computer)

A player on the device writes what happened since the last sync. Version 2:

```json
{
  "version": 2,
  "library_id": "0123456789ABCDEF",
  "tracks": [
    {"id": "1A2B3C4D5E6F7081", "plays": 3, "last_played": "2026-10-18T21:04:00Z",
     "skips": 1, "last_skipped": "2026-10-17T08:30:00Z", "rating": 80}
  ],
  "playlists": [
    {"id": "9F8E7D6C5B4A3921", "action": "create", "name": "Road Trip",
     "is_smart": 0, "items": ["1A2B3C4D5E6F7081", "00FF00FF00FF00FF"]},
    {"id": "77AA77AA77AA77AA", "action": "update", "name": "Top Rated",
     "is_smart": 1, "smart_info": "<base64>", "smart_criteria": "<base64>"},
    {"id": "1234123412341234", "action": "delete"}
  ]
}
```

Version 1 files (just `version` and `tracks`) are still read the same way.

### tracks

- `plays` and `skips` are counts **since the last sync**, not totals. They're added to
  the library's counts.
- `rating` (0–100) is included only for songs rated on the device. It replaces the
  library's rating.
- Every field except `id` may be left out. Tracks merge whatever `library_id` says
  (they're matched by ID).

### playlists

- `library_id` must match `info.library_id` of this computer's library, or the whole
  `playlists` section is skipped (and the sync says so).
- At most one entry per playlist, with its final state since the last sync. `id` is the
  playlist's persistent ID; for `create` the player makes a new random one (16 hex
  digits) and LuxTunes keeps it, so the playlist comes back under the same ID.
- `create`: a new playlist at the top level (not in a folder). If the ID is taken by
  something LuxTunes won't change, it gets a new ID. When this phone syncs selected
  playlists, the new playlist is added to its selection, so it stays on the phone. If
  Sync Music is off for this phone, the sync says the playlist won't go back.
- `update`: name and contents of a playlist **LuxTunes sent to this phone**. If it no
  longer exists, it's created. Plain playlists: `items` is the full song order (track
  IDs; unknown IDs are ignored); songs that weren't on the phone stay, in their old
  order, after the phone's items. Smart playlists: `smart_info` / `smart_criteria`
  (base64) replace the rules and the computer re-evaluates them against the whole
  library. A playlist never switches between plain and smart.
- `delete`: deletes a playlist LuxTunes sent to this phone (its songs stay). LuxTunes
  always asks first; if the answer is no, the playlist is kept and goes back to the phone.
- Only playlists LuxTunes sent are changed: never folders, the hidden Library list or
  the library's sections. Entries that can't be read (bad IDs, unreadable rules) are
  skipped and listed in the sync's problems, never stored.
- When both sides changed a playlist between syncs, the phone's version wins.

### Emptying the file

Right after merging (at the start of the sync), LuxTunes writes
`{"version": 2, "tracks": [], "playlists": []}`. If the player wrote to the file in
between, what it added is kept: plays and skips above what was merged, other ratings and
other playlist entries. An empty file means everything was delivered. A player should
keep adding to the file between syncs, avoid writing while the phone is connected for
file transfer, and start from the new `library.db` afterwards.
