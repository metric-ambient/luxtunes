"""A first test: proves the package installs and imports."""

import luxtunes


def test_version():
    import re
    assert re.fullmatch(r"\d+\.\d+\.\d+", luxtunes.__version__)   # e.g. 0.2.0 (tags are v0.2.0)
