"""Shuffle that feels random.

A plain shuffle (every order equally likely) often plays two or three songs
by the same artist in a row, which people hear as "not random". LuxTunes
uses a "spread" shuffle instead (the approach Spotify described publicly):

  1. Group the songs by artist and shuffle each group.
  2. Spread each artist's songs evenly across the whole play order, with a
     random starting offset and a little jitter, so the pattern isn't fixed.
  3. Sort everything by those positions.

The result is still unpredictable, but the same artist rarely comes up twice
in a row unless one artist makes up most of the list.
"""

from __future__ import annotations

import random
from collections import defaultdict


def spread_shuffle(items: list, key=lambda item: item, rng: random.Random | None = None) -> list:
    """Return a new list with `items` shuffled, spreading out equal `key`s."""
    rng = rng or random.Random()
    groups: dict = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)

    placed = []
    for group in groups.values():
        rng.shuffle(group)
        n = len(group)
        offset = rng.random() / n                  # where this artist's first song lands
        for i, item in enumerate(group):
            jitter = rng.uniform(-0.1, 0.1) / n    # keep the spacing from looking regular
            placed.append((offset + i / n + jitter, rng.random(), item))
    placed.sort(key=lambda p: (p[0], p[1]))
    return [item for _, _, item in placed]


def artist_key(track: dict) -> str:
    return ((track.get("album_artist") or track.get("artist") or "")).casefold()
