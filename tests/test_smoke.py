"""A first test: proves the package installs and imports."""

import luxtunes


def test_version():
    import re
    assert re.fullmatch(r"\d+\.\d+\.\d+", luxtunes.__version__)   # e.g. 0.2.0 (tags are v0.2.0)


def test_opened_by_the_desktop_for_a_device():
    from luxtunes.__main__ import _drop_opened_items
    assert _drop_opened_items(["file:///media/you/IPOD"]) == []       # just open the window
    assert _drop_opened_items(["/media/you/IPOD"]) == []
    assert _drop_opened_items(["stats"]) == ["stats"]
    assert _drop_opened_items(["--db", "x.db"]) == ["--db", "x.db"]
    assert _drop_opened_items([]) == []
