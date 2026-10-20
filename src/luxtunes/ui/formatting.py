"""How values look in the song list, how columns sort, and how search matches.

Kept free of Qt so it can be tested on its own.
"""

from __future__ import annotations

from datetime import datetime, timezone

_ARTICLES = ("the ", "a ", "an ")


def sort_text(text: str | None) -> str:
    """Text as the lists sort it: case-insensitive, ignoring a leading The/A/An."""
    if not text:
        return ""
    text = text.strip().casefold()
    for article in _ARTICLES:
        if text.startswith(article) and len(text) > len(article):
            return text[len(article):]
    return text


def format_duration(ms: int | None) -> str:
    """180000 -> '3:00'; 3723000 -> '1:02:03'."""
    if ms is None:
        return ""
    seconds = int(round(ms / 1000))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def format_date(iso_utc: str | None) -> str:
    """'2019-10-07T03:12:11Z' -> '10/6/2019 11:12 PM' (shown in local time)."""
    if not iso_utc:
        return ""
    try:
        moment = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return iso_utc
    local = moment.astimezone()
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{local.month}/{local.day}/{local.year} {hour}:{local.minute:02} {suffix}"


def format_bit_rate(kbps: int | None) -> str:
    return f"{kbps} kbps" if kbps else ""


def library_summary(count: int, total_ms: int, total_bytes: int, of_total: int | None = None,
                    noun: str = "song") -> str:
    """The text at the bottom of the window, e.g. '5,000 songs, 14.2 days, 35.00 GB'.

    With of_total (several songs selected): '12 of 35 songs, 54.8 minutes, 127.7 MB'.
    """
    if (of_total or count) != 1:
        noun = noun[:-1] + "ies" if noun.endswith("y") else noun + "s"   # categories
    seconds = total_ms / 1000
    if seconds >= 86400:
        length = f"{seconds / 86400:.1f} days"
    elif seconds >= 3600:
        length = f"{seconds / 3600:.1f} hours"
    else:
        length = f"{seconds / 60:.1f} minutes"
    size = total_bytes / 1_000_000_000
    size_text = f"{size:.2f} GB" if size >= 1 else f"{total_bytes / 1_000_000:.1f} MB"
    counted = f"{count:,} of {of_total:,}" if of_total is not None else f"{count:,}"
    return f"{counted} {noun}, {length}, {size_text}"


DATE_KEYS = {"date_added", "play_date", "skip_date", "date_modified", "release_date", "purchase_date"}
TEXT_KEYS = {
    "genre", "kind", "composer", "comments", "grouping", "category", "description",
    "show", "episode_id", "equalizer", "sort_name", "sort_artist", "sort_album_artist",
    "sort_album", "sort_composer", "sort_show", "album_artist",
}


def format_size(size: int | None) -> str:
    """9436350 -> '9.4 MB'."""
    if not size:
        return ""
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.2f} GB"
    return f"{size / 1_000_000:.1f} MB"


def _n_of_m(number, count) -> str:
    """Track/disc numbers as shown: '3 of 12', or just '3'."""
    if not number:
        return ""
    return f"{number} of {count}" if count else str(number)


def display_value(track: dict, key: str) -> str:
    """The text shown in a column for a track."""
    value = track.get(key)
    if key == "total_time":
        return format_duration(value)
    if key in DATE_KEYS:
        return format_date(value)
    if key == "bit_rate":
        return format_bit_rate(value)
    if key == "sample_rate":
        return f"{value / 1000:.3f} kHz" if value else ""
    if key == "size":
        return format_size(value)
    if key == "track_number":
        return _n_of_m(value, track.get("track_count"))
    if key == "disc_number":
        return _n_of_m(value, track.get("disc_count"))
    if key == "album_track":            # Album List view's "#" column
        number = track.get("track_number")
        return str(number) if number else ""
    if key in ("play_count", "skip_count", "bpm"):
        return str(value) if value else ""
    if value is None:
        return ""
    return str(value)


# ---- Sorting -------------------------------------------------------------

def _album_key(t: dict) -> tuple:
    return (
        sort_text(t.get("sort_album") or t.get("album")),
        t.get("disc_number") or 0,
        t.get("track_number") or 0,
        sort_text(t.get("sort_name") or t.get("name")),
    )


def _artist_key(t: dict) -> tuple:
    # Album artist first, so an album with guest artists stays together.
    artist = (
        t.get("sort_album_artist") or t.get("album_artist")
        or t.get("sort_artist") or t.get("artist")
    )
    return (sort_text(artist),) + _album_key(t)


def sort_key(key: str):
    """Returns a function giving the sort key for a column.

    Every column falls back to Artist > Album > Disc > Track order for ties.
    """
    if key in ("artist", "album_group"):
        return _artist_key
    if key == "album_track":
        return lambda t: (t.get("disc_number") or 0, t.get("track_number") or 0) + _artist_key(t)
    if key == "album":
        return lambda t: _album_key(t) + _artist_key(t)
    if key == "name":
        return lambda t: (sort_text(t.get("sort_name") or t.get("name")),) + _artist_key(t)
    if key in TEXT_KEYS:
        return lambda t: (sort_text(t.get(key)),) + _artist_key(t)
    if key in DATE_KEYS:
        return lambda t: (t.get(key) or "",) + _artist_key(t)
    # Numbers: rating, plays, year, bit rate, time, enabled...
    return lambda t: (t.get(key) or 0,) + _artist_key(t)


# ---- Search --------------------------------------------------------------

SEARCH_FIELDS = ("name", "artist", "album_artist", "album", "composer", "genre")


def search_text(track: dict) -> str:
    return " ".join(str(track.get(f) or "") for f in SEARCH_FIELDS).casefold()


def matches(search_blob: str, words: list[str]) -> bool:
    """True if every search word appears somewhere in the track's text."""
    return all(word in search_blob for word in words)


# ---- Get Info: start / stop times ("1:08.937") ----------------------------------

def format_time_ms(ms: int | None) -> str:
    """68937 -> '1:08.937' (how Start/Stop Time is shown)."""
    if ms is None:
        return ""
    minutes, rest = divmod(max(0, int(ms)), 60_000)
    return f"{minutes}:{rest // 1000:02}.{rest % 1000:03}"


def parse_time_ms(text: str) -> int | None:
    """'1:08.937', '68.9', '1:08' -> milliseconds; '' -> None. Raises ValueError."""
    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(text)
    seconds = float(parts[-1])
    minutes = int(parts[-2]) if len(parts) > 1 else 0
    hours = int(parts[-3]) if len(parts) > 2 else 0
    if seconds < 0 or minutes < 0 or hours < 0:
        raise ValueError(text)
    return round(((hours * 60 + minutes) * 60 + seconds) * 1000)
