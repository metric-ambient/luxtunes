"""Weekly library backups: made when a week has passed, four kept."""

from datetime import date, timedelta

from luxtunes.core import backup, db


def test_weekly_backups_keep_four(tmp_path):
    conn = db.connect(tmp_path / "library.db")
    conn.execute("INSERT INTO tracks (persistent_id, name) VALUES ('00000000000000B1', 'Song')")
    conn.commit()
    folder = tmp_path / "Backups"
    start = date(2026, 10, 1)
    made = backup.weekly(conn, start)
    assert made == folder / "library 2026-10-01.db"
    assert backup.weekly(conn, start + timedelta(days=6)) is None     # not a week yet
    for week in range(1, 6):
        assert backup.weekly(conn, start + timedelta(days=7 * week)) is not None
    names = [p.name for _, p in backup.backups(folder)]
    assert names == ["library 2026-10-15.db", "library 2026-10-22.db",
                     "library 2026-10-29.db", "library 2026-11-05.db"]
    import sqlite3
    with sqlite3.connect(folder / names[-1]) as copy:
        assert copy.execute("SELECT name FROM tracks").fetchall() == [("Song",)]
    (folder / "notes.txt").write_text("mine")                          # other files stay
    backup.weekly(conn, start + timedelta(days=60))
    assert (folder / "notes.txt").exists() and len(backup.backups(folder)) == 4


def test_restoring_a_backup(tmp_path):
    import sqlite3

    import pytest

    conn = db.connect(tmp_path / "library.db")
    conn.execute("INSERT INTO tracks (persistent_id, name) VALUES ('00000000000000B1', 'Old')")
    conn.commit()
    saved = backup.weekly(conn, date(2026, 10, 1))
    conn.execute("UPDATE tracks SET name = 'New'")
    conn.execute("INSERT INTO tracks (persistent_id, name) VALUES ('00000000000000B2', 'Extra')")
    conn.commit()
    assert backup.check(saved) == 1
    kept = backup.restore(conn, saved, date(2026, 10, 9))
    assert [r[0] for r in conn.execute("SELECT name FROM tracks")] == ["Old"]
    assert kept.name == "library 2026-10-09 before restore.db"
    with sqlite3.connect(kept) as before:
        assert before.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 2
    assert len(backup.backups(tmp_path / "Backups")) == 1    # the kept copy isn't rotated away
    junk = tmp_path / "junk.db"
    junk.write_text("not a library")
    with pytest.raises(backup.RestoreError):
        backup.check(junk)
