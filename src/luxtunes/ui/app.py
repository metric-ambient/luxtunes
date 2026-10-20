"""Starts the LuxTunes window.

Only one LuxTunes runs at a time (two would both be changing the same
library). Starting it again, from the menu or because the desktop opens it
when an iPod or CD is plugged in, brings the open window to the front.
"""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication

from luxtunes.core import db


def server_name(db_path: str | None) -> str:
    """One name per user and library file."""
    from luxtunes.core import db as library_db
    import hashlib
    path = os.path.abspath(os.path.expanduser(str(db_path or library_db.default_db_path())))
    return f"luxtunes-{os.getuid()}-{hashlib.sha1(path.encode()).hexdigest()[:10]}"


def tell_running_copy(name: str) -> bool:
    """If LuxTunes is already open, ask it to come to the front. True if it was."""
    from PySide6.QtNetwork import QLocalSocket
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"show\n")
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def listen_for_others(name: str, window):
    """Bring `window` to the front whenever LuxTunes is started again."""
    from PySide6.QtNetwork import QLocalServer
    QLocalServer.removeServer(name)          # left over if LuxTunes ever crashed
    server = QLocalServer(window)
    server.listen(name)

    def on_connection():
        while server.hasPendingConnections():
            server.nextPendingConnection().deleteLater()
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    server.newConnection.connect(on_connection)
    return server


def run(db_path: str | None = None) -> int:
    # Short status lines in the terminal (e.g. "repeat set to one"), handy for
    # reporting problems. Only a few events are logged, so it stays quiet.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    app = QApplication(sys.argv)
    app.setApplicationName("LuxTunes")
    app.setOrganizationName("LuxTunes")
    app.setDesktopFileName("luxtunes")
    from luxtunes.ui.about import app_icon
    app.setWindowIcon(app_icon())           # the penguin, in the taskbar and title bar
    name = server_name(db_path)
    if tell_running_copy(name):
        logging.getLogger(__name__).info("LuxTunes is already open: showing that window")
        return 0

    # Imported here so `luxtunes import-itunes` works without loading Qt.
    from luxtunes.ui.main_window import MainWindow

    conn = db.connect(db_path)
    from luxtunes.core import starter_playlists
    starter_playlists.add_if_new(conn)          # a new library's example smart playlists
    window = MainWindow(conn)
    listen_for_others(name, window)
    window.show()
    code = app.exec()
    conn.close()
    return code
