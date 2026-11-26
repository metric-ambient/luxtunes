"""Entry point for the Windows app (PyInstaller, built by packaging/windows/build.py)."""

import subprocess
import sys

# LuxTunes.exe has no console window; without this, every program it runs
# (FFmpeg converting a song, PowerShell ejecting an iPod) would flash one up.
if sys.platform == "win32":
    _Popen = subprocess.Popen

    class _QuietPopen(_Popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
            super().__init__(*args, **kwargs)

    subprocess.Popen = _QuietPopen

from luxtunes.__main__ import main  # noqa: E402

sys.exit(main())
