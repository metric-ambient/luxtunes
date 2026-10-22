"""Entry point for the packaged app (PyInstaller)."""

import os
import sys

# PyInstaller puts the bundle's libraries first on the library path, and the
# programs LuxTunes runs (ffmpeg, gio, the sound player, ...) would inherit
# that and could load the wrong libraries. LuxTunes itself has already
# started with them, so the system's own path goes back for those programs.
if "LD_LIBRARY_PATH_ORIG" in os.environ:
    os.environ["LD_LIBRARY_PATH"] = os.environ.pop("LD_LIBRARY_PATH_ORIG")
else:
    os.environ.pop("LD_LIBRARY_PATH", None)

from luxtunes.__main__ import main  # noqa: E402

sys.exit(main())
