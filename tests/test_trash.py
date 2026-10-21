"""Moving files to the Trash, and saying why when it can't."""

import subprocess
from types import SimpleNamespace

from luxtunes.core import trash


def _gio(monkeypatch, returncode, stderr=""):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=returncode, stderr=stderr))


def test_trash_reasons(tmp_path, monkeypatch):
    song = tmp_path / "song.avi"
    song.write_bytes(b"x")
    assert trash.move_to_trash(str(tmp_path / "missing.avi")) == (trash.MISSING, "")
    _gio(monkeypatch, 0)
    assert trash.move_to_trash(str(song)) is None
    _gio(monkeypatch, 1, f"gio: file://{song}: Unable to find or create trash directory for {song}")
    kind, message = trash.move_to_trash(str(song))
    assert kind == trash.NO_TRASH and message.startswith("Unable to find or create trash")
    _gio(monkeypatch, 1, f"gio: file://{song}: Trashing on system internal mounts is not supported")
    assert trash.move_to_trash(str(song))[0] == trash.NO_TRASH
    _gio(monkeypatch, 1, f"gio: file://{song}: Permission denied")
    assert trash.move_to_trash(str(song)) == (trash.FAILED, "Permission denied")
    assert "Permission denied" in trash.explain(str(song), trash.FAILED, "Permission denied")
    assert "couldn't be found at" in trash.explain("/nowhere/x.avi", trash.MISSING)
    assert "has no Trash LuxTunes can use" in trash.explain(str(song), trash.NO_TRASH)


def test_delete_permanently(tmp_path):
    song = tmp_path / "song.avi"
    song.write_bytes(b"x")
    assert trash.delete_permanently(str(song)) is None and not song.exists()
    assert trash.delete_permanently(str(song)) == "it couldn't be found"
