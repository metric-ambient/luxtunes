"""The "done" sound: which file and player are used (nothing is played here)."""

from types import SimpleNamespace

from luxtunes.core import sounds


def _players(monkeypatch, installed, results):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        code = results.pop(0) if results else 0
        return SimpleNamespace(returncode=code, stderr="" if code == 0 else "Connection refused")

    monkeypatch.setattr(sounds.subprocess, "run", run)
    monkeypatch.setattr(sounds.shutil, "which",
                        lambda name: f"/usr/bin/{name}" if name in installed else None)
    return calls


def test_uses_the_desktop_sound_and_the_first_player_that_works(monkeypatch, tmp_path):
    chosen = tmp_path / "ding.oga"
    chosen.write_bytes(b"OggS")
    monkeypatch.setattr(sounds, "_gsetting", lambda schema, key: str(chosen)
                        if key == "notification-file" else "false")
    assert sounds.desktop_sound() == str(chosen)      # the desktop's switch isn't consulted
    calls = _players(monkeypatch, {"pw-play", "paplay"}, [1, 0])
    played, steps = sounds.play_now()
    assert played and calls == [["/usr/bin/pw-play", str(chosen)],
                                ["/usr/bin/paplay", str(chosen)]]
    assert "failed (Connection refused)" in steps[0] and steps[-1].startswith("Played")


def test_says_why_nothing_played(monkeypatch, tmp_path):
    monkeypatch.setattr(sounds, "_gsetting", lambda schema, key: None)
    monkeypatch.setattr(sounds, "FALLBACK_SOUNDS", ("/nowhere/a.oga",))
    played, steps = sounds.play_now()
    assert not played and "No sound file" in steps[0] and not sounds.play_done_sound()
    sound = tmp_path / "a.oga"
    sound.write_bytes(b"OggS")
    monkeypatch.setattr(sounds, "FALLBACK_SOUNDS", (str(sound),))
    _players(monkeypatch, set(), [])
    played, steps = sounds.play_now()
    assert not played and "No sound player" in steps[0]


def test_system_programs_get_the_systems_library_path(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    assert sounds.system_env()["LD_LIBRARY_PATH"] == "/usr/local/lib"
