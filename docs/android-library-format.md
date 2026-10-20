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

Treat it as read-only: LuxTunes replaces it at every sync.

| Table | Columns |
|---|---|
| `info` | `key`, `value`: `format_version` (now `1`), `library_id`, `library_name`, `luxtunes_version`, `synced_at` |
| `tracks` | `id`, `path`, `artwork`, then the song details below |
| `playlists` | `id`, `name`, `is_smart`, `position` (sidebar order), `file` (its .m3u8, relative to `LuxTunes`) |
| `playlist_items` | `playlist_id`, `position`, `track_id` |

- `id` is the song's persistent ID: 16 hexadecimal digits that never change for that
  song. Use it to match songs between syncs and in `changes.json`.
- `path` and `artwork` are relative to the `LuxTunes` folder. `artwork` may be empty.
- `library_id` identifies the computer's library. If it changes, the folder was synced
  from a different library and stored plays should not be carried over.

Song details, where the library has them: `media_kind` (`music`, `movie`, `tvshow`,
`podcast`, `audiobook`), `name`, `artist`, `album_artist`, `album`, `genre`, `composer`,
`grouping`, `year`, `track_number`, `track_count`, `disc_number`, `disc_count`,
`total_time` (milliseconds), `bit_rate`, `sample_rate`, `size`, `rating` and
`album_rating` (0–100, 20 per star), `rating_computed` (1 if the rating is the album's,
not set for this song), `play_count`, `play_date`, `skip_count`, `skip_date`,
`date_added`, `compilation`, `comments`, `lyrics`, `bookmarkable`, `bookmark_time`
(milliseconds), `skip_when_shuffling`, `start_time`, `stop_time`, `volume_adjustment`,
`show`, `season`, `episode_number`, the `sort_*` names and `enabled`. Dates are UTC,
written as `YYYY-MM-DDTHH:MM:SSZ`.

## changes.json (from the device back to the computer)

A player on the device writes what happened since the last sync:

```json
{
  "version": 1,
  "tracks": [
    {"id": "1A2B3C4D5E6F7081", "plays": 3, "last_played": "2026-10-18T21:04:00Z",
     "skips": 1, "last_skipped": "2026-10-17T08:30:00Z", "rating": 80}
  ]
}
```

- `plays` and `skips` are counts **since the last sync**, not totals. They're added to
  the library's counts.
- `rating` (0–100) is included only for songs rated on the device. It replaces the
  library's rating.
- Every field except `id` may be left out.

At the next sync LuxTunes adds these to the library and then empties the file
(`"tracks": []`), so nothing counts twice. A player should keep adding to the file
between syncs, and start from the new `library.db` afterwards.
