"""Timed lyrics in the LRC format: "[01:23.45]A line of the song".

Used to highlight the line being sung in the Pop-Out Player. A line may carry
several times ("[00:12.00][01:40.00]Chorus"), tags like "[ar:Artist]" are
skipped, and lines without a time are ignored. No Qt in here.
"""

from __future__ import annotations

import bisect
import difflib
import re

TIME = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse(text: str | None) -> list[tuple[int, str]]:
    """[(milliseconds, line)] in time order; [] if the text has no timed lines."""
    lines = []
    for raw in (text or "").splitlines():
        times = []
        rest = raw.strip()
        while True:
            match = TIME.match(rest)
            if not match:
                break
            minutes, seconds, fraction = match.groups()
            ms = int((fraction or "0").ljust(3, "0")[:3])
            times.append((int(minutes) * 60 + int(seconds)) * 1000 + ms)
            rest = rest[match.end():].lstrip()
        for at in times:
            lines.append((at, rest))
    lines.sort(key=lambda line: line[0])
    return lines


def is_timed(text: str | None) -> bool:
    return bool(parse(text))


def plain(text: str | None) -> str:
    """The words without the times."""
    timed = parse(text)
    if not timed:
        return (text or "").strip()
    return "\n".join(line for _, line in timed).strip()


def line_at(lines: list[tuple[int, str]], position_ms: int) -> int:
    """The index of the line being sung at this moment (-1 before the first)."""
    return bisect.bisect_right([at for at, _ in lines], position_ms) - 1


def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", (text or "").casefold()).split()


def same_words(lyrics: str | None, timed: str | None, at_least: float = 0.8) -> bool:
    """Do these lyrics and the timed ones say much the same (so the timed ones
    can highlight them)?"""
    first, second = _words(lyrics or ""), _words(plain(timed))
    if not first or not second:
        return False
    return difflib.SequenceMatcher(None, first, second, autojunk=False).ratio() >= at_least
