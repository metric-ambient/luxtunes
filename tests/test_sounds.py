"""The "done" sound: which file and player are used (nothing is played here)."""

from luxtunes.core import sounds


def test_uses_the_desktop_sound_and_an_installed_player(monkeypatch, tmp_path):
    chosen = tmp_path / "ding.oga"
    chosen.write_bytes(b"OggS")
    settings = {"notification-enabled": "true", "notification-file": str(chosen)}
    monkeypatch.setattr(sounds, "_gsetting", lambda schema, key: settings.get(key))
    assert sounds.desktop_sound() == str(chosen)

    started = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            started.append(command)

        def wait(self):
            return 0

    monkeypatch.setattr(sounds.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(sounds.shutil, "which",
                        lambda name: "/usr/bin/paplay" if name == "paplay" else None)
    assert sounds.play_done_sound()
    assert started == [["/usr/bin/paplay", str(chosen)]]


def test_quiet_when_sounds_are_off_or_missing(monkeypatch):
    monkeypatch.setattr(sounds, "_gsetting", lambda schema, key: "false")
    assert sounds.desktop_sound() is None and not sounds.play_done_sound()
    monkeypatch.setattr(sounds, "_gsetting", lambda schema, key: None)
    monkeypatch.setattr(sounds, "FALLBACK_SOUNDS", ("/nowhere/a.oga",))
    assert sounds.desktop_sound() is None
