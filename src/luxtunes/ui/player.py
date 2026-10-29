"""Audio playback: a play order plus an "Up Next" queue, on top of QMediaPlayer.

How the next song is chosen:
  1. If "Up Next" has songs, the first one is played (and removed from Up Next).
  2. Otherwise playback continues in the list you started from (the library
     or a playlist), right after the last song played from that list.

The player only plays. It announces what happened through signals
(track_finished, track_skipped, ...) and the main window decides what to
record in the library.

Two QMediaPlayers ("decks") take turns, for Preferences → Playback:
  * Crossfade (1-15 s): the next song starts on the other deck that many
    seconds before this one ends; one fades out while the other fades in.
  * Gapless playback: the next song is loaded on the other deck a few seconds
    early and starts the moment this one ends.
Neither happens around videos, with Repeat One, or when the next song can't
be known in advance (a new shuffle pass). Sound Check scales each song's
volume by its loudness correction (core/loudness.py).
"""

from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from luxtunes.core import equalizer, loudness
from luxtunes.core.library import AUDIO_KINDS
from luxtunes.core.paths import find_file
from luxtunes.core.shuffle import album_key, artist_key, freshness_tiers, spread_shuffle
from luxtunes.ui import eq_output

# A skip counts when you jump to the next song within its first 20 seconds.
SKIP_WINDOW_MS = 20_000
# "Previous" restarts the current song if it has played longer than this.
RESTART_THRESHOLD_MS = 3_000

REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = "off", "all", "one"

# Qt's loop counts (QMediaPlayer.setLoops): -1 = forever, 1 = play once.
LOOP_FOREVER, LOOP_ONCE = -1, 1
# Repeat One: a jump from the last 2 s of the song back to its first 2 s
# means it looped, which counts as one play.
LOOP_DETECT_MS = 2_000
# Gapless: load the next song this long before the current one ends.
PRELOAD_MS = 5_000
FADE_STEP_MS = 50

log = logging.getLogger(__name__)


class Player(QObject):
    track_started = Signal(dict)
    track_finished = Signal(dict)      # played to the end -> counts as a play
    track_skipped = Signal(dict)       # skipped early -> counts as a skip
    playing_changed = Signal(bool)
    position_changed = Signal(int, int)  # position ms, duration ms
    stopped = Signal()
    problem = Signal(dict, str)        # track, message (e.g. a file that won't decode)
    file_missing = Signal(dict)        # you asked for this song, but its file is gone
    track_unavailable = Signal(dict)   # skipped during continuous play: file is gone
    relocated = Signal(dict, str)      # found the file under different capitalisation
    queue_changed = Signal()           # Up Next changed
    bookmark_changed = Signal(dict, int)  # "Remember playback position": where it stopped (ms)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._decks = []
        for _ in range(2):
            audio = QAudioOutput(self)
            deck = QMediaPlayer(self)
            deck.setAudioOutput(audio)
            self._decks.append((deck, audio))
        self._player, self._audio = self._decks[0]     # the deck playing the current song
        self._video_widget = None
        # Preferences → Playback
        self.crossfade_seconds = 0       # 0 = off
        self.gapless = False
        self.sound_check = False
        self._transition = None          # the next song, prepared on the other deck
        self._fading = None              # (deck, audio, volume) fading out
        self._fading_id = None           # the song on that deck
        self._fade_in = 1.0              # how far the new song has faded in
        self._fade_steps = 0
        self._fade_step = 0
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(FADE_STEP_MS)
        self._fade_timer.timeout.connect(self._fade_tick)

        self._context: list[dict] = []   # the list you started playing from
        self._order: list[int] = []      # indexes into _context, in play order
        self._pos = -1                   # where we are in _order
        self._current: dict | None = None
        self.up_next: list[dict] = []
        self._shuffle = False
        self._heard: set = set()        # songs started this session (a new shuffle puts them last)
        self._repeat = REPEAT_OFF
        self._repeat_retries = 0      # Repeat One: replays attempted after an error
        self._last_position = 0
        self._base_volume = 0.8         # the volume slider, 0..1 (before Volume Adjustment)
        self._pending_seek = None       # Start Time / bookmark to jump to once the file loads
        self._ended = False             # the current song reached its end (or Stop Time)
        self._looping_back = False      # Repeat One jumped from Stop Time back to Start Time
        self.equalizer = equalizer.Settings()
        self._eq = eq_output.EqualizerOutput(self._player, self)

        # Both decks report; only the one playing the current song counts.
        for deck, _ in self._decks:
            deck.mediaStatusChanged.connect(
                lambda status, d=deck: self._on_media_status(status) if d is self._player
                else self._on_spare_status(d, status))
            deck.playbackStateChanged.connect(
                lambda state, d=deck: d is self._player and self._on_playback_state(state))
            deck.positionChanged.connect(
                lambda position, d=deck: d is self._player and self._on_position(position))
            deck.durationChanged.connect(
                lambda duration, d=deck: d is self._player
                and self.position_changed.emit(d.position(), duration))
            deck.errorOccurred.connect(
                lambda error, message, d=deck: self._on_error(error, message)
                if d is self._player else self._spare_failed(d))

    # ---- State ------------------------------------------------------------

    @property
    def current(self) -> dict | None:
        return self._current

    @property
    def repeat(self) -> str:
        return self._repeat

    @repeat.setter
    def repeat(self, mode: str):
        """Repeat One uses Qt's own looping: the song simply plays again
        (no reloading the file, which is what broke the earlier versions)."""
        self._repeat = mode
        self._set_loops(self._player)
        if mode == REPEAT_ONE:
            self._drop_transition()          # the song repeats; nothing is next
        log.info("repeat set to %s", mode)

    def _set_loops(self, deck):
        try:
            deck.setLoops(LOOP_FOREVER if self._repeat == REPEAT_ONE else LOOP_ONCE)
        except AttributeError:   # very old Qt without setLoops: the fallback below handles it
            pass

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @shuffle.setter
    def shuffle(self, on: bool):
        """Turning shuffle on/off takes effect right away, even mid-song."""
        if on == self._shuffle:
            return
        self._shuffle = on
        self._drop_transition()                  # the next song may change
        if not self._context or self._pos < 0:
            return
        current_index = self._order[self._pos]
        if on:
            self._order = [current_index] + self._shuffled(
                [i for i in range(len(self._context)) if i != current_index])
            self._pos = 0
        else:
            # Back to list order, continuing after the song that's playing.
            self._order = list(range(len(self._context)))
            self._pos = current_index

    def _shuffled(self, indexes: list[int]) -> list[int]:
        context = self._context
        tiers = freshness_tiers(context, self._heard)    # albums you haven't been hearing first
        return spread_shuffle(indexes, key=lambda i: artist_key(context[i]),
                              album=lambda i: album_key(context[i]), tier=lambda i: tiers[i])

    @property
    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    # ---- Controls ---------------------------------------------------------

    def play_queue(self, tracks: list[dict], start: int):
        """Play `tracks`, starting with tracks[start]. Up Next is kept.

        If tracks[start]'s file is missing, nothing changes: `file_missing`
        is emitted so the window can ask you to locate it.
        """
        if not 0 <= start < len(tracks):
            return
        if self._locate(tracks[start]) is None:
            self.file_missing.emit(tracks[start])
            return
        self._context = list(tracks)
        indexes = list(range(len(tracks)))
        if self._shuffle:
            self._order = [start] + self._shuffled([i for i in indexes if i != start])
            first = 0
        else:
            self._order = indexes
            first = start
        # A song you double-click plays even if it's unchecked.
        self._start_at(first, force_first=True)

    def rescope(self, tracks: list[dict]):
        """The list being played changed (e.g. its search was changed or
        cleared): the song playing carries on, then play continues through
        `tracks`, reshuffled if shuffle is on."""
        if not 0 <= self._pos < len(self._order):
            return
        playing = self._context[self._order[self._pos]]
        self._drop_transition()                  # the next song may change
        context = list(tracks)
        here = next((i for i, t in enumerate(context) if t.get("id") == playing.get("id")), -1)
        if here < 0:                             # no longer in the list: keep it as the first
            context.insert(0, playing)
            here = 0
        self._context = context
        if self._shuffle:
            self._order = [here] + self._shuffled([i for i in range(len(context)) if i != here])
            self._pos = 0
        else:
            self._order = list(range(len(context)))
            self._pos = here

    def toggle(self):
        if self.is_playing:
            self._end_fade()                     # a song fading out stops too
            self._player.pause()
        elif self._current is not None:
            self._player.play()

    def next(self):
        track = self._current
        if track is None:
            return
        if self._player.position() < SKIP_WINDOW_MS:
            self.track_skipped.emit(track)
        self._advance("Next pressed")

    def previous(self):
        if self._current is None:
            return
        if self._player.position() > RESTART_THRESHOLD_MS or self._pos <= 0:
            self._jump(self._start_of(self._current))
        else:
            self._start_at(self._pos - 1, step=-1)

    def seek(self, ms: int):
        self._jump(ms)

    def set_video_output(self, widget) -> None:
        """Where movies and TV shows draw their picture (the video window)."""
        self._video_widget = widget
        self._player.setVideoOutput(widget)

    # ---- Equalizer ----------------------------------------------------------------

    def set_equalizer(self, settings: equalizer.Settings) -> None:
        self.equalizer = settings
        self.apply_equalizer()

    def apply_equalizer(self) -> None:
        """Use the equalizer for the current song, or not: it's on, it's
        available, and the song is audio (movies keep Qt's own output so the
        sound stays in step with the picture)."""
        track = self._current
        preset = self.equalizer.for_song(track) if track is not None else None
        if preset is not None and (not eq_output.available()
                                   or (track.get("media_kind") or "music") not in AUDIO_KINDS):
            preset = None
        if preset is None:
            if self._eq.active:
                self._eq.stop()
        else:
            self._eq.set_preset(preset)
            if not self._eq.active:
                self._eq.start()
                self._eq.pause(not self.is_playing)
        # With the equalizer, the deck's own output is silent (the filtered
        # sound plays instead); it still keeps the timing.
        self._audio.setMuted(self._eq.active)
        self.apply_volume()

    def set_volume(self, percent: int):
        self._base_volume = max(0, min(100, percent)) / 100
        self.apply_volume()

    def apply_volume(self):
        """Slider volume, adjusted by the song's Get Info "Volume Adjustment"
        (-100% = silent, +100% = up to twice as loud, capped at full volume)."""
        self._set_output_volume(self._target_volume())

    def _target_volume(self) -> float:
        return self._volume_for(self._current)

    def _volume_for(self, track: dict | None) -> float:
        """Slider × Volume Adjustment × Sound Check, capped at full volume."""
        track = track or {}
        adjust = track.get("volume_adjustment") or 0
        volume = self._base_volume * (1 + adjust / 100)
        if self.sound_check:
            volume *= loudness.factor(track.get("loudness"))
        return max(0.0, min(1.0, volume))

    def _set_output_volume(self, volume: float):
        volume *= self._fade_in                  # a crossfade still fading this song in
        self._audio.setVolume(volume)
        self._eq.set_volume(volume)

    def fade_to(self, fraction: float):
        """Part of the way down to silence (the sleep timer's fade); apply_volume() undoes it."""
        self._set_output_volume(self._target_volume() * max(0.0, min(1.0, fraction)))

    def stop(self):
        self._remember_position()
        self._drop_transition()
        self._end_fade()
        self._player.stop()
        self._player.setSource(QUrl())
        self._current = None
        self.apply_equalizer()            # nothing playing: back to Qt's own output
        self._context, self._order, self._pos = [], [], -1
        self.stopped.emit()

    def refresh_track(self, track_id: int, fields: dict):
        """A song's details changed (e.g. its file was moved by "Keep LuxTunes
        Media folder organized"): update every copy the player holds."""
        for track in (self._current, *self._context, *self.up_next):
            if track is not None and track.get("id") == track_id:
                track.update(fields)

    def shutdown(self):
        """LuxTunes is closing: stop, and end the equalizer's audio thread."""
        self.stop()
        self._eq.shutdown()

    def update_locations(self, locations: dict[int, str]):
        """Files moved (e.g. a new LuxTunes Media folder): track id -> new path."""
        for track in (self._current, *self._context, *self.up_next):
            if track is not None and track.get("id") in locations:
                track["location"] = locations[track["id"]]

    # ---- Up Next ------------------------------------------------------------

    def add_to_up_next(self, tracks: list[dict], first: bool = False):
        """Queue songs: at the end of Up Next, or (first) before what's queued, to play next."""
        self._drop_transition()                  # the next song may change
        if first:
            self.up_next[0:0] = tracks
        else:
            self.up_next.extend(tracks)
        self.queue_changed.emit()

    def remove_from_up_next(self, indexes: list[int]):
        self._drop_transition()
        for i in sorted(set(indexes), reverse=True):
            if 0 <= i < len(self.up_next):
                del self.up_next[i]
        self.queue_changed.emit()

    def play_from_up_next(self, index: int):
        """Jump to a song in Up Next (songs before it are dropped from the queue)."""
        if not 0 <= index < len(self.up_next):
            return
        track = self.up_next[index]
        if self._locate(track) is None:
            self.file_missing.emit(track)
            return
        del self.up_next[: index + 1]
        self.queue_changed.emit()
        self._play(track)

    def forget_tracks(self, track_ids: set[int]):
        """Songs were deleted from the library: drop them from Up Next and the play order."""
        before = len(self.up_next)
        self._drop_transition()
        self.up_next = [t for t in self.up_next if t["id"] not in track_ids]
        if len(self.up_next) != before:
            self.queue_changed.emit()
        for track in self._context:          # skip them if we reach them later
            if track["id"] in track_ids:
                track["_deleted"] = True
        if self._current is not None and self._current["id"] in track_ids:
            self.stop()

    # ---- Internals --------------------------------------------------------

    def _play(self, track: dict):
        # Diagnostic: which code asked for this song (most recent call first).
        callers = " <- ".join(f.name for f in reversed(traceback.extract_stack(limit=7)[:-1]))
        log.info("starting %r (repeat=%s) via %s", track.get("name"), self._repeat, callers)
        self._remember_position()
        self._drop_transition()
        self._end_fade()
        self._current = track
        self._heard.add(track.get("id"))
        self._last_position = 0
        self._ended = False
        self._looping_back = False
        start = self._start_of(track)
        if track.get("bookmarkable") and track.get("bookmark_time"):
            start = max(start, track["bookmark_time"])     # pick up where you left off
        self._pending_seek = start or None
        self.apply_equalizer()            # also sets the volume
        self._eq.flush()
        self._player.setSource(QUrl.fromLocalFile(track["location"]))
        self._player.play()
        self.track_started.emit(track)

    def _jump(self, ms: int) -> None:
        self._eq.flush()                  # don't finish playing what was before the jump
        if self._transition is not None and not self._transition.get("started"):
            self._drop_transition()       # jumped back: the next song isn't due yet
        self._player.setPosition(ms)

    def _on_playback_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._eq.pause(not playing)
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self._eq.flush()
        self.playing_changed.emit(playing)

    @staticmethod
    def _start_of(track: dict) -> int:
        return track.get("start_time") or 0

    def _remember_position(self):
        """Leaving a "Remember playback position" song: save where it was
        (or the start again, if it played to the end)."""
        track = self._current
        if track is None or not track.get("bookmarkable"):
            return
        position = 0 if self._ended else self._player.position()
        if position < 1000:
            position = 0
        if position != (track.get("bookmark_time") or 0):
            track["bookmark_time"] = position
            self.bookmark_changed.emit(track, position)

    def _advance(self, reason: str):
        """Go to the next song: Up Next first, then the list we were playing."""
        log.info("moving to the next song (%s; repeat=%s)", reason, self._repeat)
        while self.up_next:
            track = self.up_next.pop(0)
            self.queue_changed.emit()
            if self._locate(track) is not None:
                self._play(track)
                return
            self.track_unavailable.emit(track)
        self._start_at(self._pos + 1)

    def _start_at(self, pos: int, step: int = 1, force_first: bool = False):
        """Play the first playable song from `pos` onward (or backward) in the list."""
        wrapped = False
        first = True
        while True:
            if not 0 <= pos < len(self._order):
                if self.repeat == REPEAT_ALL and step > 0 and not wrapped and self._order:
                    pos, wrapped = 0, True
                    if self._shuffle:  # a fresh shuffle for each pass through the list
                        self._order = self._shuffled(list(range(len(self._context))))
                    continue
                self.stop()
                return
            track = self._context[self._order[pos]]
            if track.get("_deleted"):
                pass
            elif self._locate(track) is None:
                self.track_unavailable.emit(track)
            elif first and force_first:
                self._pos = pos
                self._play(track)
                return
            elif track.get("enabled", 1) and not (self._shuffle and track.get("skip_when_shuffling")):
                self._pos = pos
                self._play(track)
                return
            first = False
            pos += step

    def _locate(self, track: dict) -> str | None:
        """The song's file path if it exists (fixing capitalisation if needed)."""
        location = find_file(track.get("location"))
        if location is not None and location != track.get("location"):
            track["location"] = location
            self.relocated.emit(track, location)
        return location

    def _on_position(self, position: int):
        duration = self._player.duration()
        if self._pending_seek is not None:
            # Playing, but the jump to Start Time / the remembered position
            # hasn't happened yet: do it now.
            seek, self._pending_seek = self._pending_seek, None
            self._jump(seek)
            return
        track = self._current
        stop_at = track.get("stop_time") if track else None
        if self._looping_back and (not stop_at or position < stop_at):
            self._looping_back = False
        if stop_at and position >= stop_at and not self._ended and not self._looping_back:
            self._reached_end(track)        # Get Info's Stop Time: the song ends here
            return
        if (self._repeat == REPEAT_ONE and self._current is not None and duration > 0
                and self._last_position >= duration - LOOP_DETECT_MS
                and position < LOOP_DETECT_MS):
            # Qt looped the song (Repeat One): that's one full play.
            log.info("repeat one: looped %r", self._current.get("name"))
            self.track_finished.emit(self._current)
            if self._start_of(self._current):
                self._jump(self._start_of(self._current))   # loop from Start Time
        self._last_position = position
        if position > LOOP_DETECT_MS:
            self._repeat_retries = 0      # it's playing fine again
        self._maybe_prepare_next(position, duration)
        self._check_fade_start(position)
        self.position_changed.emit(position, duration)

    def _on_media_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            if self._pending_seek is not None:
                seek, self._pending_seek = self._pending_seek, None
                self._jump(seek)       # Start Time / remembered position
            return
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        track = self._current
        if track is None:
            return
        if self._repeat == REPEAT_ONE and self.is_playing:
            return  # Qt is looping the song; the loop is counted in _on_position
        if self._ended:
            return  # already handled at its Stop Time
        self._reached_end(track)

    def _reached_end(self, track: dict):
        """The song played to its end (or its Stop Time): count a play, move on."""
        log.info("end of song %r (repeat=%s)", track.get("name"), self._repeat)
        self.track_finished.emit(track)
        if self._repeat != REPEAT_ONE and self._transition is not None \
                and self._transition.get("ready"):
            self._ended = True
            self._switch(fade=False)          # gapless: the next song is ready to go
            return
        if self._repeat == REPEAT_ONE and track.get("stop_time"):
            self._last_position = 0
            self._looping_back = True
            self._jump(self._start_of(track))   # loop the Start-Stop part
            return
        self._ended = True
        if self._repeat == REPEAT_ONE:
            # Only reached if Qt's looping didn't take effect. Restart the song
            # by fully unloading and reloading it, once Qt is done with this event.
            QTimer.singleShot(0, lambda t=track: self._replay(t))
        else:
            self._advance("song ended")

    def _replay(self, track: dict):
        if self._current is track and self._locate(track) is not None:
            log.info("repeat one: reloading %r", track.get("name"))
            self._player.setSource(QUrl())   # unload first: Qt ignores re-setting the same file
            self._play(track)

    def _on_error(self, error, message):
        if error == QMediaPlayer.Error.NoError:
            return
        track = self._current
        log.warning("playback error %s on %r: %s", error, track and track.get("name"), message)
        if track is None:
            return
        if self._repeat == REPEAT_ONE and self._repeat_retries < 2:
            # Repeat One never moves on by itself: try the same song again.
            # (Only after 2 failed attempts in a row is the file treated as broken.)
            self._repeat_retries += 1
            QTimer.singleShot(0, lambda t=track: self._replay(t))
            return
        self.problem.emit(track, message or "could not be played")
        self._advance("playback error")

    # ---- Crossfade and gapless: the other deck ------------------------------------------

    def _spare(self):
        return next(d for d in self._decks if d[0] is not self._player)

    def _is_video(self, track: dict | None) -> bool:
        return (track or {}).get("media_kind") in ("movie", "tvshow")

    def _maybe_prepare_next(self, position: int, duration: int):
        """Near the end of a song, get the next one ready on the other deck
        (and, with crossfade, start it)."""
        if not (self.crossfade_seconds or self.gapless) or self._repeat == REPEAT_ONE:
            return
        track = self._current
        if track is None or self._transition is not None or self._ended or duration <= 0 \
                or self._is_video(track):
            return
        end = track.get("stop_time") or duration
        fade_ms = self.crossfade_seconds * 1000
        lead = max(fade_ms, PRELOAD_MS)
        if position < end - lead:
            return
        choice = self._peek_next()
        if choice is None or self._is_video(choice[2]):
            self._transition = {"none": True}        # play on as usual
            return
        deck, audio = self._spare()
        deck.stop()
        audio.setMuted(False)
        audio.setVolume(0.0)
        self._set_loops_once(deck)
        deck.setSource(QUrl.fromLocalFile(choice[2]["location"]))
        self._transition = {"choice": choice, "deck": deck, "audio": audio,
                            "ready": False, "fade_at": end - fade_ms if fade_ms else None}
        log.info("next song ready on the other deck: %r", choice[2].get("name"))

    @staticmethod
    def _set_loops_once(deck):
        try:
            deck.setLoops(LOOP_ONCE)
        except AttributeError:
            pass

    def _on_spare_status(self, deck, status):
        transition = self._transition
        if transition is None or transition.get("deck") is not deck:
            return
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            transition["ready"] = True
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._spare_failed(deck)

    def _spare_failed(self, deck):
        """The next song wouldn't load early: it gets its normal turn instead."""
        if self._transition is not None and self._transition.get("deck") is deck \
                and not self._transition.get("started"):
            log.info("couldn't prepare the next song early; it plays the usual way")
            self._drop_transition()
            self._transition = {"none": True}

    def busy_track_ids(self) -> set:
        """Songs whose files are open for playing now: the current one and the
        next one, if it's already being prepared on the other deck."""
        busy = {self._fading_id}                 # (the song fading out)
        if self._current is not None:
            busy.add(self._current.get("id"))
        choice = (self._transition or {}).get("choice")
        if choice:
            busy.add(choice[2].get("id"))
        busy.discard(None)
        return busy

    def _drop_transition(self):
        transition = self._transition
        self._transition = None
        if transition and transition.get("deck") is not None and not transition.get("started"):
            transition["deck"].stop()
            transition["deck"].setSource(QUrl())

    def _peek_next(self):
        """The song that would play next, without moving on: ("queue", index,
        track) or ("order", position, track). None if it can't be known now."""
        for i, track in enumerate(self.up_next):
            if find_file(track.get("location")) is not None:
                return ("queue", i, track)
        pos, wrapped = self._pos + 1, False
        while True:
            if not 0 <= pos < len(self._order):
                if self._repeat == REPEAT_ALL and self._order and not wrapped and not self._shuffle:
                    pos, wrapped = 0, True
                    continue
                return None                  # the end (or a new shuffle pass): no head start
            track = self._context[self._order[pos]]
            if not track.get("_deleted") and find_file(track.get("location")) is not None \
                    and track.get("enabled", 1) \
                    and not (self._shuffle and track.get("skip_when_shuffling")):
                return ("order", pos, track)
            pos += 1

    def _check_fade_start(self, position: int):
        transition = self._transition
        if transition and transition.get("fade_at") is not None and not transition.get("started") \
                and transition.get("ready") and position >= transition["fade_at"]:
            self.track_finished.emit(self._current)      # it played (to its fade)
            self._ended = True
            self._switch(fade=True)

    def _switch(self, fade: bool):
        """Hand over to the other deck: the next song becomes the current one."""
        transition = self._transition
        kind, index, track = transition["choice"]
        transition["started"] = True
        self._remember_position()
        if kind == "queue":
            del self.up_next[: index + 1]
            self.queue_changed.emit()
        else:
            self._pos = index
        old_deck, old_audio = self._player, self._audio
        new_deck, new_audio = transition["deck"], transition["audio"]
        old_volume = old_audio.volume()
        if self._eq.active:
            old_audio.setMuted(False)            # fades out on its own output
            old_audio.setVolume(old_volume if old_volume else self._volume_for(self._current))
            old_volume = old_audio.volume()
        self._player, self._audio = new_deck, new_audio
        self._transition = None
        if self._video_widget is not None:
            old_deck.setVideoOutput(None)
            new_deck.setVideoOutput(self._video_widget)
        self._eq.attach(new_deck)
        self._set_loops(new_deck)
        fading_id = (self._current or {}).get("id")
        self._current = track
        self._heard.add(track.get("id"))
        self._last_position = 0
        self._ended = False
        self._looping_back = False
        start = self._start_of(track)
        if track.get("bookmarkable") and track.get("bookmark_time"):
            start = max(start, track["bookmark_time"])
        if start:
            new_deck.setPosition(start)
        self._pending_seek = None
        if fade:
            self._fade_in = 0.0
            self._fading = (old_deck, old_audio, old_volume)
            self._fading_id = fading_id          # its file stays open until the fade ends
            self._fade_steps = max(1, self.crossfade_seconds * 1000 // FADE_STEP_MS)
            self._fade_step = 0
            self._fade_timer.start()
        else:
            self._fade_in = 1.0
            old_deck.stop()
            old_deck.setSource(QUrl())
        self._eq.flush()
        self.apply_equalizer()                   # this song's preset and volume
        new_deck.play()
        log.info("%s to %r", "crossfading" if fade else "gapless", track.get("name"))
        self.track_started.emit(track)

    def _fade_tick(self):
        self._fade_step += 1
        share = min(1.0, self._fade_step / self._fade_steps)
        self._fade_in = share
        self.apply_volume()
        if self._fading is not None:
            deck, audio, volume = self._fading
            audio.setVolume(volume * (1.0 - share))
        if share >= 1.0:
            self._end_fade()

    def _end_fade(self):
        """Finish a crossfade now: the old song stops, the new one is at full volume."""
        self._fade_timer.stop()
        if self._fading is not None:
            deck, audio, _ = self._fading
            self._fading = None
            self._fading_id = None
            deck.stop()
            deck.setSource(QUrl())
            audio.setMuted(False)
        if self._fade_in != 1.0:
            self._fade_in = 1.0
            self.apply_volume()
