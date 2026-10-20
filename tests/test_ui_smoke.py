"""Smoke test for the real Qt window.

Builds the actual LuxTunes main window invisibly (Qt's "offscreen" platform)
using a fake library, then exercises the main features. It catches crashes
such as a wrong import or a misspelled Qt method before you launch the app.

Skipped automatically where PySide6 isn't installed.
"""

import importlib
import os
import pkgutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no window on screen

FIXTURE = Path(__file__).parent / "fixtures" / "small_library.xml"


def test_every_ui_module_imports():
    import luxtunes.ui
    for module in pkgutil.iter_modules(luxtunes.ui.__path__):
        importlib.import_module(f"luxtunes.ui.{module.name}")


@pytest.fixture
def window(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    # Keep the test's settings away from your real LuxTunes settings.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for settings_format in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
        QSettings.setPath(settings_format, QSettings.Scope.UserScope, str(tmp_path / "config"))

    app = QApplication.instance() or QApplication([])
    # A real CD in this computer's drive mustn't pop up "Import this CD?" mid-test.
    monkeypatch.setattr("luxtunes.core.cd.drives", lambda: [])
    from luxtunes.core import db
    from luxtunes.importer.itunes_xml import import_library
    from luxtunes.ui.main_window import MainWindow

    conn = db.connect(tmp_path / "library.db")
    import_library(conn, FIXTURE, tmp_path / "media")
    win = MainWindow(conn)
    win.show()
    app.processEvents()
    yield win
    win.close()                  # closing stops its device checks (no CD questions later)
    app.processEvents()


def test_views_and_features(window):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    for mode in ("album", "grid", "coverflow", "list"):
        window._set_view_mode(mode)
        app.processEvents()
        window.grab()                         # forces a real paint of every widget
    window._set_view_mode("grid")
    for grouping in ("artists", "genres", "composers", "albums"):
        window.grid.set_grouping(grouping)
    group = window.grid.model.groups[0]
    # Right-clicking a tile gives the same menu as the song list, for its songs.
    menu = window._song_menu(group["tracks"], group["tracks"][0], group["title"], lambda: None)
    texts = [a.text() for a in menu.actions() if a.text()]
    for expected in ("Get Info", "Get Track Info Online…", "Rating", "Add to Playlist", "Delete"):
        assert expected in texts
    menu.deleteLater()
    window._grid_group = group
    assert window._info_targets()[0] == list(group["tracks"])
    window._open_grid_group(group)
    app.processEvents()
    window.grab()
    window._close_grid_group()
    window._set_view_mode("list")

    window._on_search("opening")
    window._on_search("")
    window._toggle_dark_mode(True)
    window.grab()
    window._toggle_dark_mode(False)
    window._set_column_shown(14, True)
    window._auto_size_all()
    window._on_column_dropped_off(14)
    window._new_playlist()
    window._open_art_window()
    window.player.add_to_up_next(window.model.visible_tracks()[:1])
    window.show_source(("queue", "up_next"))
    window.show_source(("library", "music"))
    app.processEvents()
    assert window.model.rowCount() == 3


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
       b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")


def test_get_info(window, tmp_path, monkeypatch):
    pytest.importorskip("mutagen")
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import library, tags
    from luxtunes.ui import get_info

    # Warnings would wait for a click; just let them through.
    monkeypatch.setattr(get_info.QMessageBox, "warning", lambda *a, **k: None)
    app = QApplication.instance()
    conn = window.conn
    tracks = window.model.visible_tracks()
    first = tracks[0]["id"]
    song = tmp_path / "song.mp3"
    song.write_bytes((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 30)   # silent MP3
    library.set_location(conn, first, str(song))
    window._after_edit(first)

    # One song: every tab paints; Previous/Next work; OK saves and writes the file.
    window.table.selectRow(0)
    dialog = get_info.InfoDialog(conn, window.model.visible_tracks(), 0, window._apply_info, window)
    dialog.show()
    for tab in range(dialog.tabs.count()):
        dialog.tabs.setCurrentIndex(tab)
        app.processEvents()
        dialog.grab()
    dialog._step(1)
    dialog._step(-1)
    dialog.edits["name"].setText("Edited Name")
    dialog.start_on.setChecked(True)
    dialog.start_time.setText("0:01.500")
    dialog.rating.setValue(80)
    dialog.artwork.set_images([PNG])
    dialog._ok()
    saved = library.load_track(conn, first)
    assert saved["name"] == "Edited Name" and saved["start_time"] == 1500 and saved["rating"] == 80
    assert saved["artwork_count"] == 1 and not saved["tags_dirty"]
    assert tags.read_details(str(song))["artworks"] == [PNG]
    assert window.model.track_at(window.model.row_of(first))["name"] == "Edited Name"

    # Several songs: only ticked fields change.
    window.settings.setValue("confirm_multi_info", False)
    assert window._confirm_multi_info()
    multi = get_info.MultiInfoDialog(conn, tracks[:2], window._apply_info, window)
    multi.show()
    app.processEvents()
    multi.grab()
    multi.checks["genre"].setChecked(True)
    multi.getters["genre"] = lambda: "Ambient"
    multi._ok()
    for t in tracks[:2]:
        edited = library.load_track(conn, t["id"])
        assert edited["genre"] == "Ambient"
    assert library.load_track(conn, first)["name"] == "Edited Name"     # untouched


def test_keep_media_organized(window, tmp_path):
    pytest.importorskip("mutagen")
    from luxtunes.core import library, organize
    from luxtunes.ui.preferences import PreferencesDialog

    conn = window.conn
    root = organize.media_folder(conn)
    track = window.model.visible_tracks()[0]
    folder = root / "Music" / "Old Artist" / "Old Album"
    folder.mkdir(parents=True)
    song = folder / "song.mp3"
    song.write_bytes((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 30)   # silent MP3
    library.set_location(conn, track["id"], str(song))
    window._after_edit(track["id"])

    window._on_field_edited(track["id"], "album", "The 1980")       # edit in the list
    saved = library.load_track(conn, track["id"])
    assert "/The 1980/" in saved["location"] and (root / saved["location"]).exists()
    assert not folder.exists()                                       # old empty folder removed
    assert window.model.track_at(window.model.row_of(track["id"]))["location"] == saved["location"]

    prefs = PreferencesDialog(conn, window)
    prefs.keep_organized.setChecked(False)
    prefs._save()
    assert not organize.is_enabled(conn)


def test_menus_duplicates_recent_and_adding(window, tmp_path):
    pytest.importorskip("mutagen")
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import library
    from luxtunes.ui.view_options import ViewOptionsDialog

    app = QApplication.instance()
    window.show_source(("library", "music"))
    for mode, action in window.view_actions.items():          # View → as List ... as Cover Flow
        action.trigger()
        app.processEvents()
        assert window.view_mode == mode
    window.view_actions["list"].trigger()

    window._display_duplicates()
    assert window.dup_bar.isVisibleTo(window)
    window._display_all()
    assert not window.dup_bar.isVisibleTo(window) and window.model.rowCount() == 3

    window.table.selectRow(0)
    window._update_file_menu()
    window._file_menu_closed()
    first = window.model.track_at(0)
    window._remember_recent(first["id"])
    window._fill_recent_menu()
    assert window.recent_menu.actions()[0].text() == first["name"]

    dialog = ViewOptionsDialog("Music", None, {2, 3}, window)
    dialog.grab()
    assert dialog.shown_columns() == {2, 3}

    window._new_folder()
    window.show_source(("library", "music"))
    window.table.selectRow(0)
    window._new_playlist_from_selection()

    window.show_source(("library", "music"))
    folder = tmp_path / "new music"
    folder.mkdir()
    (folder / "song.mp3").write_bytes((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 30)
    before = library.count_tracks(window.conn, "music")
    window._add_paths([str(folder)])
    assert library.count_tracks(window.conn, "music") == before + 1
    assert window.model.rowCount() == before + 1


def test_edited_song_moves_to_its_sorted_place(window):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from luxtunes.ui.track_model import NAME_COLUMN

    window.show_source(("library", "music"))
    window.table.sortByColumn(NAME_COLUMN, Qt.SortOrder.AscendingOrder)
    first = window.model.track_at(0)
    window.table.selectRow(0)
    window._on_field_edited(first["id"], "name", "zzz Last")
    QApplication.instance().processEvents()
    last = window.model.rowCount() - 1
    assert window.model.track_at(last)["id"] == first["id"]
    assert window._selected_rows() == [last]                # the selection moved with it


def test_create_mp3_version(window, tmp_path):
    import shutil
    import subprocess
    pytest.importorskip("mutagen")
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    from luxtunes.core import library

    conn = window.conn
    track = window.model.visible_tracks()[0]
    flac = tmp_path / "song.flac"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(flac)], check=True)
    library.set_location(conn, track["id"], str(flac))
    before = library.count_tracks(conn, "music")
    window._create_mp3_versions([library.load_track(conn, track["id"])])
    assert library.count_tracks(conn, "music") == before + 1
    assert (tmp_path / "song.mp3").exists() and flac.exists()


def test_about_and_icon(window):
    from luxtunes.ui.about import AboutDialog, app_icon

    assert not app_icon().isNull()
    dialog = AboutDialog(window.conn, window)
    dialog.grab()
    text = dialog.info_text()
    assert "LuxTunes: " in text and "Library database: " in text


def test_ipod_device_page(window, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from luxtunes.core.ipod import device
    from test_ipod_core import make_ipod

    app = QApplication.instance()
    mount, mounts = make_ipod(tmp_path)
    [ipod] = device.find_ipods(mounts)
    window._ipods[ipod.key] = ipod
    window.reload_library()
    assert window.sidebar.select_source(("device", ipod.key))
    app.processEvents()
    page = window.device_page
    assert window.views.currentWidget() is page
    # The little eject button at the right end of the iPod's row.
    from PySide6.QtCore import QPoint
    sidebar = window.sidebar
    rect = sidebar.visualItemRect(sidebar.currentItem())
    assert sidebar._eject_hit(QPoint(sidebar.viewport().width() - 5, rect.center().y())) == ipod.key
    assert sidebar._eject_hit(QPoint(10, rect.center().y())) is None
    # While it syncs, the button spins and clicking it doesn't eject.
    ejected = []
    sidebar.eject_requested.connect(ejected.append)
    sidebar.set_syncing(ipod.key)
    sidebar._spin()
    window.grab()
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    click = QPointF(sidebar.viewport().width() - 5, rect.center().y())
    sidebar.mousePressEvent(QMouseEvent(QEvent.Type.MouseButtonPress, click, click,
                                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                        Qt.KeyboardModifier.NoModifier))
    assert ejected == []
    sidebar.set_syncing(None)
    for i in range(page.pages.count()):              # every tab paints
        page.tab_group.button(i).click()
        app.processEvents()
        window.grab()
    page.music_selected.setChecked(True)
    if page.playlist_list.count():
        page.playlist_list.item(0).setCheckState(Qt.CheckState.Checked)
    app.processEvents()
    assert page.dirty and page.apply_button.isVisibleTo(page)
    page.revert()
    assert not page.dirty and page.sync_button.isVisibleTo(page)
    window._on_device_renamed(ipod.key, "Alex's iPod")
    app.processEvents()
    assert window._device_name(ipod) == "Alex's iPod"
    window.sidebar.select_source(("library", "music"))
    app.processEvents()
    assert window.views.currentWidget() is not page


def test_sync_progress_in_the_display(window):
    lcd = window.lcd
    lcd.show_track({"name": "Robbers", "artist": "The 1975", "total_time": 1000})
    lcd.start_sync("Test iPod")
    assert lcd.showing_sync and lcd.toggle_button.isVisibleTo(lcd)
    lcd.update_sync("Copying “Robbers”", 0, 3)
    lcd.show_track({"name": "Chocolate", "total_time": 1000})     # a new song doesn't hide the sync
    assert lcd.showing_sync
    lcd.toggle()
    assert not lcd.showing_sync and lcd.title.text() == "Chocolate"
    lcd.toggle()
    assert lcd.showing_sync
    lcd.finish_sync("iPod sync is complete.")
    lcd.end_sync()
    assert not lcd.showing_sync and not lcd.toggle_button.isVisibleTo(lcd)


def test_movie_list_hides_when_syncing_all_movies(window, tmp_path):
    from PySide6.QtWidgets import QApplication

    from luxtunes.core.ipod import device
    from test_ipod_core import make_ipod

    mount, mounts = make_ipod(tmp_path)
    [ipod] = device.find_ipods(mounts)
    window._ipods[ipod.key] = ipod
    window.reload_library()
    window.sidebar.select_source(("device", ipod.key))
    QApplication.instance().processEvents()
    page = window.device_page
    page._show_tab(2)                                   # the Movies tab
    QApplication.instance().processEvents()
    page.movies_auto.setChecked(True)
    page.movies_rule.setCurrentText("all")
    assert page.movie_list.isHidden() and page.movies_heading.isHidden()
    page.movies_auto.setChecked(False)
    assert not page.movie_list.isHidden() and not page.movies_heading.isHidden()
    assert page.movie_list.isVisibleTo(page)


def test_background_sync_with_libgpod(window, tmp_path, monkeypatch):
    import time

    from PySide6.QtWidgets import QApplication, QMessageBox

    # The test library's songs have no files, so the sync lists them as problems;
    # don't let that message wait for a click.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    from luxtunes.core.ipod import device, gpod
    if not gpod.available():
        pytest.skip("libgpod isn't installed")
    pytest.importorskip("mutagen")
    from test_gpod import fake_ipod

    app = QApplication.instance()
    mount = fake_ipod(tmp_path)
    ipod = device.read_ipod(str(mount), "", "vfat")
    window._ipods[ipod.key] = ipod
    window.reload_library()
    window.sidebar.select_source(("device", ipod.key))
    app.processEvents()
    window._sync_device()
    assert window.lcd.showing_sync
    deadline = time.time() + 60
    while window._sync_worker is not None and time.time() < deadline:
        app.processEvents()                 # the window stays usable while it syncs
        time.sleep(0.02)
    assert window._sync_worker is None
    assert "complete" in window.lcd.sync_detail.text() or "Sync" in window.lcd.sync_detail.text()


def test_sync_symbol_draws_its_ring(window):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from luxtunes.ui.widgets import draw_sync_arrows

    image = QImage(30, 30, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)          # as the sidebar leaves it
    draw_sync_arrows(painter, QColor("black"), 15, 15, 0)
    painter.end()
    assert image.pixelColor(15, 9).alpha() > 0       # top of the ring (an arc, not an arrowhead)
    assert image.pixelColor(15, 21).alpha() > 0      # bottom of the ring
    assert image.pixelColor(15, 15).alpha() == 0     # the middle stays empty


def test_display_missing(window, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import library
    from luxtunes.ui.track_model import NAME_COLUMN

    conn = window.conn
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY id")]
    present = tmp_path / "here.mp3"
    present.write_bytes(b"x")
    library.set_location(conn, ids[0], str(present))            # this one's file exists
    with conn:
        conn.execute("UPDATE tracks SET tags_dirty = 1 WHERE id = ?", (ids[0],))   # but has unsaved edits
    window._broken_ids.add(ids[1])
    problems = window.library_problems()
    assert problems[ids[0]] == "Edits not saved to the file"
    assert problems[ids[1]] == "File not found"                 # a missing file wins over "didn't play"
    assert len(problems) == len(ids)

    window._display_missing()
    QApplication.instance().processEvents()
    assert window.dup_bar.isVisibleTo(window) and window.filter_label.text() == "Displaying Missing"
    assert window.model.rowCount() == len(ids)
    row = window.model.row_of(ids[0])
    tip = window.model.data(window.model.index(row, NAME_COLUMN), Qt.ItemDataRole.ToolTipRole)
    assert tip.startswith("Edits not saved")
    window._display_all()
    QApplication.instance().processEvents()
    assert not window.dup_bar.isVisibleTo(window) and window._current_source == ("library", "music")


def test_locate_one_then_find_the_rest(window, tmp_path, monkeypatch):
    import os

    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from luxtunes.core import library

    conn = window.conn
    tracks = library.load_tracks(conn)
    moved = tmp_path / "moved"
    moved.mkdir()
    for t in tracks:                                  # every file now lives in one new folder
        (moved / os.path.basename(t["location"])).write_bytes(b"x")
    first = tracks[0]
    target = str(moved / os.path.basename(first["location"]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (target, ""))
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    assert window._locate_track(first)
    assert asked and "search for the remaining files in this location" in asked[0]
    for t in library.load_tracks(conn):
        assert t["location"] == str(moved / os.path.basename(t["location"])), t["name"]
        assert os.path.exists(t["location"])


def test_video_plays_inside_the_window(window):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    movie = {"id": 999, "name": "Film", "media_kind": "movie", "total_time": 60000}
    window._show_video(movie)
    app.processEvents()
    panel = window.video_panel
    assert window.showing_video() and not window.splitter.isVisible()
    assert window.toolbar.isVisible() and window.statusBar().isVisible()   # bars stay
    assert panel.controls.isVisible() and panel.close_button.isVisible()
    window._on_position(5000, 60000)
    assert panel.elapsed.text() == "0:05" and panel.remaining.text() == "-0:55"
    # The controls float over the picture, centred near the bottom.
    geo = panel.controls.geometry()
    assert abs(geo.center().x() - panel.width() // 2) <= 1 and geo.bottom() < panel.height()

    window._toggle_video_full_screen()
    app.processEvents()
    assert not window.toolbar.isVisible() and not window.menuBar().isVisible()
    panel._escape()                          # Esc leaves full screen first...
    app.processEvents()
    assert window.toolbar.isVisible() and window.showing_video()
    seen = []
    panel.close_requested.connect(lambda: seen.append(True))
    panel._escape()                          # ...then closes the video
    assert seen
    window._hide_video()
    app.processEvents()
    assert not window.showing_video() and window.splitter.isVisible()


def test_video_surface_paints_frames(window):
    from PySide6.QtGui import QColor, QImage

    surface = window.video_panel.surface
    surface.resize(200, 100)
    image = QImage(40, 40, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    surface.image = image
    shot = surface.grab().toImage()
    assert shot.pixelColor(100, 50) == QColor("red")        # picture, letterboxed in the middle
    assert shot.pixelColor(5, 50) == QColor("black")


def test_tv_shows_in_the_sidebar(window):
    from luxtunes.ui.widgets import SOURCE_TV_SHOWS

    window.conn.execute("UPDATE tracks SET media_kind = 'tvshow', show = 'Show' WHERE id = "
                        "(SELECT MIN(id) FROM tracks)")
    window.conn.commit()
    window.reload_library()
    window.show_source(SOURCE_TV_SHOWS)
    assert window.model.rowCount() == 1


def test_audio_cd_import(window, tmp_path, monkeypatch):
    import shutil

    from PySide6.QtWidgets import QApplication, QMessageBox

    from luxtunes.core import cd, library, organize
    from test_cd import fake_cdparanoia, musicbrainz_answer

    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    app = QApplication.instance()
    # A message box would wait forever for a click: record it and fail instead.
    popups = []
    for kind in ("warning", "information", "question"):
        monkeypatch.setattr(QMessageBox, kind,
                            staticmethod(lambda *args, **kw: popups.append(args[2:3]) or 0))
    organize.set_media_folder(window.conn, tmp_path / "Media")
    cd.set_insert_action(window.conn, "show")              # no question pops up in the test
    toc = cd.Toc(1, 2, [150, 300], 375)
    fake_cdparanoia(tmp_path, monkeypatch, [150, 75])
    monkeypatch.setattr(cd, "drives", lambda: ["/dev/sr9"])
    monkeypatch.setattr(cd, "audio_disc_present", lambda device: True)
    monkeypatch.setattr(cd, "read_toc", lambda device: toc)
    looked_up = []

    def fake_lookup(disc, *, approved, artwork=True):
        assert approved is True
        looked_up.append(disc.key)
        cd.apply_release(disc, musicbrainz_answer(disc.toc.disc_id()))
        return disc

    monkeypatch.setattr(cd, "lookup", fake_lookup)
    approvals = []
    monkeypatch.setattr(window, "_approve_internet", lambda *a: approvals.append(a[0]) or True)
    before = library.count_tracks(window.conn, "music")

    window._scan_cds()                                      # the CD goes in
    key = "cd:/dev/sr9"
    assert key in window._discs and window._current_source == ("device", key)
    assert not looked_up and key not in window._lookups     # nothing online before you say so
    app.processEvents()                                     # "Look it up online?" (yes)
    assert approvals == ["Get Track Names"]
    window._lookups[key].wait(10_000)                       # the lookup runs in the background
    app.processEvents()
    assert not popups, popups
    assert window.views.currentWidget() is window.cd_page and looked_up == [key]
    assert not window.view_buttons[0].isVisible()           # no List/Grid/... on a CD's page
    page = window.cd_page
    assert page.table.rowCount() == 2 and page.table.item(0, 3).text() == "The City"
    assert window.sidebar.currentItem().text(0) == "The 1975"

    page.table.item(1, 3).setText("Money")                  # a correction before importing
    window._start_import(key)
    assert window._lcd_task == "import" and window.sidebar.is_busy(key)
    assert page.import_button.text() == "Stop Importing"
    worker = window._import_worker
    assert worker.wait(60_000), "the import didn't finish"  # runs in the background
    app.processEvents()
    assert not popups, popups
    assert library.count_tracks(window.conn, "music") == before + 2
    names = {r[0] for r in window.conn.execute("SELECT name FROM tracks WHERE album = 'The 1975'")}
    assert names == {"The City", "Money"}
    assert not window.sidebar.is_busy(key) and page.import_button.text() == "Import CD"
    assert page.table.item(0, 1).icon().isNull() is False            # the green tick

    monkeypatch.setattr(cd, "audio_disc_present", lambda device: False)
    window._scan_cds()                                      # the CD comes out
    assert key not in window._discs and window._current_source[0] != "device"


def test_closed_window_stops_looking_for_devices(window):
    assert window._device_timer.isActive()
    window._cd_pending["cd:/dev/sr9"] = "ask"
    window.close()
    assert not window._device_timer.isActive() and not window._cd_pending
    window._discs["cd:/dev/sr9"] = object()  # a disc it still knew about...
    window._ask_import("cd:/dev/sr9")        # ...but a closed window never asks (no hang here)
    del window._discs["cd:/dev/sr9"]


def test_nothing_goes_online_when_you_say_no(window, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import cd, musicbrainz

    def no_network(*args, **kwargs):
        raise AssertionError("went online without asking")

    monkeypatch.setattr(cd, "_get", no_network)
    monkeypatch.setattr(musicbrainz, "_get", no_network)
    monkeypatch.setattr(window, "_approve_internet", lambda *a: False)
    cd.set_insert_action(window.conn, "show")
    monkeypatch.setattr(cd, "drives", lambda: ["/dev/sr9"])
    monkeypatch.setattr(cd, "audio_disc_present", lambda device: True)
    monkeypatch.setattr(cd, "read_toc", lambda device: cd.Toc(1, 1, [150], 300))
    window._scan_cds()
    QApplication.instance().processEvents()
    assert not window._lookups and "cd:/dev/sr9" not in window._cd_pending
    assert window.cd_page.table.item(0, 3).text() == "Track 01"
    window.cd_page.lookup_button.click()                    # Get Track Names: asked again, no
    assert not window._lookups
    window._get_track_info_online([window.model.track_at(0)])
    assert window._song_lookup is None
    window.show_source(("library", "music"))
    QApplication.instance().processEvents()
    assert window.view_buttons[0].isVisible()               # back on a song list


def test_get_track_info_online(window, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from luxtunes.core import library, musicbrainz
    from luxtunes.ui import track_info_dialog

    track = window.model.track_at(0)
    asked = []
    monkeypatch.setattr(window, "_approve_internet", lambda *a: asked.append(a[0]) or True)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: 0))

    def fake_lookup(tracks, *, approved, progress=None, stop=None, artwork=True):
        assert approved is True and tracks[0]["id"] == track["id"]
        album = musicbrainz.AlbumProposal("Someone — Somewhere (2001)")
        album.songs.append(musicbrainz.SongProposal(tracks[0], [
            musicbrainz.Change("year", None, 2001),                    # a blank: ticked
            musicbrainz.Change("name", tracks[0]["name"], "Renamed")]))   # a replacement: not
        return musicbrainz.LookupResult(albums=[album])

    monkeypatch.setattr(musicbrainz, "lookup_songs", fake_lookup)
    shown = []

    def accept(dialog):
        shown.append(dialog.tree.topLevelItemCount())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(track_info_dialog.TrackInfoDialog, "exec", accept)
    window._get_track_info_online([track])
    assert asked == ["Get Track Info Online"]
    window._song_lookup.wait(10_000)
    QApplication.instance().processEvents()
    assert shown == [1]
    saved = library.load_track(window.conn, track["id"])
    assert saved["year"] == 2001 and saved["name"] == track["name"]    # only the ticked change


def test_import_settings_dialogs(window):
    from luxtunes.core import encoders
    from luxtunes.ui.import_settings import CustomEncoderDialog, ImportSettingsDialog

    dialog = ImportSettingsDialog(encoders.ImportSettings(), window)
    assert dialog.setting.currentText() == "Custom..." and "320 kbps" in dialog.details.text()
    dialog.encoder.setCurrentIndex(dialog.encoder.findData(encoders.AAC))
    assert dialog.setting.currentText() == "Higher Quality (256 kbps)"
    assert [dialog.setting.itemText(i) for i in range(dialog.setting.count())][-1] == "Custom..."
    dialog.encoder.setCurrentIndex(dialog.encoder.findData(encoders.ALAC))
    assert dialog.setting.count() == 1
    dialog.error_correction.setChecked(True)
    result = dialog.result_settings()
    assert result.encoder == encoders.ALAC and result.error_correction

    custom = CustomEncoderDialog(encoders.ImportSettings(), window)
    custom.vbr.setChecked(True)
    assert custom.quality.isEnabled()
    custom.quality.setCurrentIndex(custom.quality.findData("Highest"))
    s = custom.result_settings()
    assert s.mp3_vbr and s.mp3_quality == "Highest" and s.setting == "Custom..."
    custom._defaults()
    assert not custom.vbr.isChecked() and custom.bit_rate.currentData() == 320


def test_smart_playlist_editor_reads_and_writes_itunes_rules(window):
    import json
    from pathlib import Path

    from luxtunes.core import smart
    from luxtunes.ui.smart_playlist_dialog import GroupBox, RuleRow, SmartPlaylistDialog

    blobs = json.loads((Path(__file__).parent / "fixtures" / "itunes_smart_playlists.json").read_text())
    nineties = smart.decode(bytes.fromhex(blobs["nineties"]["info"]),
                            bytes.fromhex(blobs["nineties"]["criteria"]))
    dialog = SmartPlaylistDialog(nineties, "90's Music", [], window)
    top = dialog.rules
    assert isinstance(top.items[0], RuleRow) and isinstance(top.items[1], GroupBox)
    assert top.items[0].field.currentText() == "Year" and top.items[0].op.currentText() == "is in the range"
    assert top.items[1].match.currentText() == "any"
    assert dialog.playlist() == nineties                     # unchanged when nothing was edited

    row = top.items[0]
    row.field.setCurrentIndex(row.field.findText("Artist"))   # the value box follows the field
    row.op.setCurrentIndex(row.op.findText("starts with"))
    row.editor.first.setText("The")
    top.add_group_after(row)
    assert len(top.items) == 3
    edited = dialog.playlist()
    assert edited.rules.rules[0] == smart.Rule(0x04, smart.S_STARTS, "The")
    assert isinstance(edited.rules.rules[1], smart.Group)
    top.remove(top.items[1])
    top.remove(top.items[1])
    assert not top.items[0].minus.isEnabled()                # the last rule can't be removed

    top25 = smart.decode(bytes.fromhex(blobs["top25"]["info"]), bytes.fromhex(blobs["top25"]["criteria"]))
    limited = SmartPlaylistDialog(top25, "Top 25 Most Played", [], window)
    assert limited.limit.isChecked() and limited.limit_value.value() == 25
    assert limited.limit_sort.currentText() == "most often played"
    assert limited.playlist() == top25

    dates = SmartPlaylistDialog(smart.SmartPlaylist(rules=smart.Group(False, [
        smart.Rule(0x10, smart.IN_LAST, amount=2, units=smart.WEEK)])), "x", [], window)
    editor = dates.rules.items[0].editor
    assert editor.first.value() == 2 and editor.units.currentText() == "weeks"


def test_new_and_edited_smart_playlists(window, monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog

    from luxtunes.core import library, smart
    from luxtunes.ui import smart_playlist_dialog

    app = QApplication.instance()
    every_song = smart.SmartPlaylist(rules=smart.Group(False, [smart.Rule(0x02, smart.S_CONTAINS, "")]))
    monkeypatch.setattr(smart_playlist_dialog.SmartPlaylistDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(smart_playlist_dialog.SmartPlaylistDialog, "playlist", lambda self: every_song)
    window._new_smart_playlist()
    app.processEvents()
    source = window._current_source
    playlist = window._playlist_by_id(source[1])
    assert playlist["is_smart"] and window._current_smart_id() == playlist["id"]
    from PySide6.QtCore import Qt
    assert window.sidebar.currentItem().flags() & Qt.ItemFlag.ItemIsEditable      # renameable
    everything = library.load_tracks(window.conn)
    assert len(library.playlist_track_ids(window.conn, playlist["id"])) == len(everything)

    first = everything[0]
    only_one = smart.SmartPlaylist(rules=smart.Group(False, [smart.Rule(0x02, smart.S_IS, first["name"])]))
    monkeypatch.setattr(smart_playlist_dialog.SmartPlaylistDialog, "playlist", lambda self: only_one)
    window._edit_smart_playlist(playlist["id"])
    app.processEvents()
    assert [window.model.track_at(r)["name"] for r in range(window.model.rowCount())] == [first["name"]]

    # Live updating: a song renamed to match joins the list on show.
    other = everything[1]
    window._apply_info(other["id"], {"name": first["name"]}, None)
    QApplication.processEvents()
    window._refresh_shown_smart_playlist()
    assert window.model.rowCount() == 2


def test_things_that_cant_be_done_are_greyed_out(window):
    window._update_file_menu()
    assert not window.sync_action.isEnabled()                 # no iPod plugged in
    assert not window.edit_smart_action.isEnabled()           # not on a smart playlist
    window._update_controls_menu()
    assert not window.eject_action.isEnabled()                # nothing to eject
    assert not window.next_action.isEnabled()                 # nothing playing
    window._file_menu_closed()
    window._controls_menu_closed()
    assert window.sync_action.isEnabled() and window.eject_action.isEnabled()   # shortcuts still work


def test_general_preferences_apply(window, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from luxtunes.core import library
    from luxtunes.ui import preferences, theme
    from luxtunes.ui.track_model import ENABLED_COLUMN

    window.conn.execute("UPDATE tracks SET media_kind = 'movie' WHERE id = (SELECT MIN(id) FROM tracks)")
    window.conn.commit()
    dialog = preferences.PreferencesDialog(window.conn, window, window.settings)
    dialog.library_name.setText("Test Library")
    dialog.show_movies.setChecked(False)
    dialog.list_text.setCurrentIndex(1)                      # Large
    dialog.grid_view.setCurrentIndex(1)                      # Light
    dialog.list_checkboxes.setChecked(False)
    dialog.source_icons.setChecked(False)
    dialog._save()
    window._apply_appearance()
    assert library.library_name(window.conn) == "Test Library"
    assert window.windowTitle() == "LuxTunes — Test Library"
    labels = []
    stack = [window.sidebar.topLevelItem(i) for i in range(window.sidebar.topLevelItemCount())]
    while stack:
        item = stack.pop()
        labels.append(item.text(0))
        assert item.icon(0).isNull()                         # no source icons
        stack.extend(item.child(i) for i in range(item.childCount()))
    assert "Movies" not in labels
    assert "TV Shows" in labels                              # shown even with no TV shows
    assert window.table.isColumnHidden(ENABLED_COLUMN)
    assert window.table.verticalHeader().defaultSectionSize() == 23
    assert theme.grid_colors["grid_title"] == theme.GRID_LIGHT["grid_title"]


def test_check_for_updates(window, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from luxtunes.core import updater

    calls = []
    monkeypatch.setattr(updater, "latest_release", lambda *, approved: calls.append(approved) or
                        updater.Release("0.0.1", "v0.0.1", "", "p",
                                        [updater.Asset("luxtunes-0.0.1-py3-none-any.whl", "u", 1)]))
    monkeypatch.setattr(window, "_approve_internet", lambda *a: False)
    window._check_for_updates()
    assert calls == [] and window._update_worker is None     # said no: nothing went online
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(window, "_approve_internet", lambda *a: True)
    window._check_for_updates()
    window._update_worker.wait(10_000)
    QApplication.instance().processEvents()
    assert calls == [True] and "latest version" in shown[0]
    # A newer version, for a copy running from its source folder: points to git pull.
    window._on_update_found(updater.Release("99.0.0", "v99.0.0", "", "p", []))
    assert "git pull" in shown[-1]
