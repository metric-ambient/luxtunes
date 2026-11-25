"""Preferences → Network: what may go online without asking each time.

Everything is off until you turn it on, and turning one on asks you to
confirm first (ui/preferences.py). With a feature off, LuxTunes asks before
each use, as it always has; with it on, it goes ahead (the status bar still
says when it connects, and the request is logged).

  updates           check GitHub for a new LuxTunes when it starts (it never
                    installs one without asking)
  track_info        Get Track Info Online… without the question
  lyrics            Get Lyrics Online… without the question
  cd_names          look up an inserted CD's names without the question
  podcasts          Refresh Podcasts without the question
  podcasts_startup  …and refresh them when LuxTunes starts (needs podcasts)

Kept in the library's own settings, so they stay with your library.
"""

from __future__ import annotations

import sqlite3

from luxtunes.core.db import get_setting, set_setting

FEATURES = {
    "updates": "Check for updates when LuxTunes starts",
    "track_info": "Get Track Info Online without asking",
    "lyrics": "Get Lyrics Online without asking",
    "cd_names": "Look up CD track names without asking",
    "podcasts": "Refresh podcasts without asking",
    "podcasts_startup": "Refresh podcasts when LuxTunes starts",
}

# What each one sends, and to whom (shown when you turn it on).
SENDS = {
    "updates": "LuxTunes asks GitHub for its latest version each time it starts. Nothing about "
               "you or your library is sent, and an update is never installed without asking.",
    "track_info": "The names, artists and albums of the songs you choose are sent to "
                  "MusicBrainz, and covers come from the Cover Art Archive.",
    "lyrics": "The names, artists, albums and lengths of the songs you choose are sent to "
              "LRCLIB.",
    "cd_names": "When you insert a CD, its layout (track lengths, not your music) is sent to "
                "MusicBrainz, and its cover comes from the Cover Art Archive.",
    "podcasts": "LuxTunes contacts the website of each podcast you subscribe to, and downloads "
                "new episodes from it. Those sites (and services they use to count listeners) "
                "see your IP address, as they would for any podcast app.",
    "podcasts_startup": "Each time LuxTunes starts, it checks your podcasts for new episodes "
                        "and downloads them.",
}


def allowed(conn: sqlite3.Connection, feature: str) -> bool:
    if feature not in FEATURES:
        return False
    if feature == "podcasts_startup" and not allowed(conn, "podcasts"):
        return False
    return get_setting(conn, f"network.{feature}") == "1"


def set_allowed(conn: sqlite3.Connection, feature: str, on: bool) -> None:
    if feature not in FEATURES:
        raise ValueError(f"not a network feature: {feature}")
    set_setting(conn, f"network.{feature}", "1" if on else None)
    if feature == "podcasts" and not on:
        set_setting(conn, "network.podcasts_startup", None)


def all_allowed(conn: sqlite3.Connection) -> dict[str, bool]:
    return {feature: allowed(conn, feature) for feature in FEATURES}
