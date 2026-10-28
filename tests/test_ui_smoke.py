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
    # Nor may an iPod plugged into this computer show up (or be mounted) in a test:
    # the window's own device checks see no drives. Tests add fake iPods themselves.
    from luxtunes.core.ipod import device as ipod_device
    real_mounts = ipod_device.mounted_filesystems
    monkeypatch.setattr(ipod_device, "mounted_filesystems",
                        lambda mounts_file=None: [] if mounts_file is None else real_mounts(mounts_file))
    monkeypatch.setattr(ipod_device, "apple_usb_connected", lambda *a, **k: False)
    from luxtunes.core.android import device as android_device       # nor a phone
    monkeypatch.setattr(android_device, "mounted_names", lambda folder=None: set())
    monkeypatch.setattr(android_device, "find_phones", lambda folder=None: [])
    monkeypatch.setattr(android_device, "unmounted_phones", lambda: [])
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
    _wait_for_adding(window)
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


def test_create_ipod_version_of_a_video(window, tmp_path, monkeypatch):
    import shutil
    import subprocess
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    from PySide6.QtWidgets import QMessageBox

    from luxtunes.core import library

    conn = window.conn
    clip = tmp_path / "Big Film.mkv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=s=1280x720:r=50:d=1", "-f", "lavfi", "-i", "sine=d=1", "-shortest",
                    "-c:v", "libx264", "-c:a", "aac", str(clip)], check=True)
    track = window.model.visible_tracks()[0]
    library.set_location(conn, track["id"], str(clip))
    conn.execute("UPDATE tracks SET media_kind = 'tvshow', show = 'Show', season = 2, "
                 "total_time = 1000 WHERE id = ?", (track["id"],))
    conn.commit()
    problems = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: problems.append(a[2])))
    source = library.load_track(conn, track["id"])
    menu = window._song_menu([source], source, "x", lambda: None)
    assert "Create iPod Version" in [a.text() for a in menu.actions()]
    menu.deleteLater()
    window._create_ipod_versions([source])
    assert not problems, problems
    made = tmp_path / "Big Film (iPod).m4v"
    assert made.exists() and clip.exists()
    copy = conn.execute("SELECT * FROM tracks WHERE location = ?", (str(made),)).fetchone()
    assert copy["media_kind"] == "tvshow" and copy["show"] == "Show" and copy["season"] == 2
    assert copy["name"] == source["name"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,profile", "-of", "csv=p=0", str(made)],
                           capture_output=True, text=True).stdout
    assert probe.startswith("Constrained Baseline,640,360")


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
    # Photos: ticking the box turns the rest of the tab on.
    assert page.sync_photos.isEnabled() and not page.photos_all.isEnabled()
    page.sync_photos.setChecked(True)
    app.processEvents()
    assert page.photos_all.isEnabled() and page.photo_source.isEnabled()
    page.photos_selected.setChecked(True)
    assert page.photo_folder_list.isEnabled()
    page.revert()
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
    lcd.start_task("sync", "Syncing “Test iPod”", "Preparing to sync…", "Stop syncing")
    assert lcd.showing_task == "sync" and lcd.toggle_button.isVisibleTo(lcd)
    lcd.update_count("sync", "Copying “Robbers”", 0, 3)
    lcd.show_track({"name": "Chocolate", "total_time": 1000})     # a new song doesn't hide the sync
    assert lcd.showing_task == "sync"
    lcd.toggle()
    assert lcd.showing_task is None and lcd.title.text() == "Chocolate"
    lcd.toggle()
    assert lcd.showing_task == "sync"
    lcd.finish_task("sync", "iPod sync is complete.")
    lcd.end_task("sync")
    assert lcd.showing_task is None and not lcd.toggle_button.isVisibleTo(lcd)


def test_display_steps_through_a_sync_and_an_import(window):
    lcd = window.lcd
    lcd.show_track({"name": "Robbers", "total_time": 1000})
    lcd.start_task("sync", "Syncing “Test iPod”", "Preparing to sync…", "Stop syncing")
    lcd.start_task("import", "Importing “The City”", "Starting…", "Stop importing")
    assert lcd.showing_task == "import"
    lcd.update_count("sync", "Copying “Robbers”", 4, 10)      # kept up to date while hidden
    seen = []
    for _ in range(6):
        lcd.toggle()
        seen.append(lcd.showing_task)
    assert seen == [None, "sync", "import", None, "sync", "import"]
    lcd.toggle(); lcd.toggle()
    assert lcd.showing_task == "sync" and lcd.sync_detail.text() == "Copying “Robbers” (5 of 10)"
    stopped = []
    lcd.stop_requested.connect(stopped.append)
    lcd.stop_button.click()                                   # ✕ stops the one on show
    assert stopped == ["sync"]
    lcd.end_task("sync")                                      # the next one takes its place
    assert lcd.showing_task == "import" and lcd.sync_title.text() == "Importing “The City”"
    lcd.end_task("import")
    assert lcd.showing_task is None and lcd.title.text() == "Robbers"


def test_one_ipod_syncs_at_a_time(window, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(window, "_device_name", lambda ipod: ipod.name)
    window._sync_worker = object()                            # "Red iPod" is syncing
    window._sync_ipod = SimpleNamespace(key="red", name="Red iPod")
    window._sync_name = "Red iPod"
    try:
        window._sync_device(SimpleNamespace(key="blue", name="Blue iPod"))
        window._sync_device(SimpleNamespace(key="red", name="Red iPod"))
    finally:
        window._sync_worker = None
    assert shown == ["“Red iPod” has to finish syncing before you can sync “Blue iPod”.",
                     "“Red iPod” is already syncing."]


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
    rule_box = page.movies_auto.parentWidget()
    page.sync_movies.setChecked(True)
    QApplication.instance().processEvents()
    size = rule_box.size()
    page.movies_auto.setChecked(True)
    page.movies_rule.setCurrentText("all")
    QApplication.instance().processEvents()
    assert not page.movie_list.isVisibleTo(page) and not page.movies_heading.isVisibleTo(page)
    assert rule_box.size() == size                      # only the list hides; the box stays put
    page.movies_auto.setChecked(False)
    QApplication.instance().processEvents()
    assert page.movie_list.isVisibleTo(page) and page.movies_heading.isVisibleTo(page)
    assert rule_box.size() == size


def test_background_sync_with_libgpod(window, tmp_path, monkeypatch):
    import time

    from PySide6.QtWidgets import QApplication, QMessageBox

    # The test library's songs have no files, so the sync lists them as problems;
    # don't let that message wait for a click.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    from luxtunes.core import sounds
    played = []
    monkeypatch.setattr(sounds, "play_done_sound", lambda: played.append(True))

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
    assert window.lcd.showing_task == "sync"
    deadline = time.time() + 60
    while window._sync_worker is not None and time.time() < deadline:
        app.processEvents()                 # the window stays usable while it syncs
        time.sleep(0.02)
    assert window._sync_worker is None
    assert "complete" in window.lcd.sync_detail.text() or "Sync" in window.lcd.sync_detail.text()
    assert played == [True]                                  # the "done" sound


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
    monkeypatch.setattr(window, "_approve_internet", lambda *a, **k: approvals.append(a[0]) or True)
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
    from luxtunes.core import sounds
    played = []
    monkeypatch.setattr(sounds, "play_done_sound", lambda: played.append(True))
    window._start_import(key)
    assert window.lcd.showing_task == "import" and window.sidebar.is_busy(key)
    assert page.import_button.text() == "Stop Importing"
    worker = window._import_worker
    assert worker.wait(60_000), "the import didn't finish"  # runs in the background
    app.processEvents()
    assert not popups, popups
    assert library.count_tracks(window.conn, "music") == before + 2
    names = {r[0] for r in window.conn.execute("SELECT name FROM tracks WHERE album = 'The 1975'")}
    assert names == {"The City", "Money"}
    assert played == [True]                                  # "done" sound, once
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
    monkeypatch.setattr(window, "_approve_internet", lambda *a, **k: False)
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
    monkeypatch.setattr(window, "_approve_internet", lambda *a, **k: asked.append(a[0]) or True)
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
    # Without opening any menu: Sync, Eject and playback items are already greyed.
    assert not window.sync_action.isEnabled()                 # no iPod plugged in
    assert not window.eject_action.isEnabled()                # nothing to eject
    assert not window.next_action.isEnabled()                 # nothing playing
    window._update_file_menu()
    assert not window.edit_smart_action.isEnabled()           # not on a smart playlist
    window._update_controls_menu()
    window._file_menu_closed()
    assert not window.sync_action.isEnabled() and not window.eject_action.isEnabled()
    window._discs["cd:/dev/sr0"] = object()                   # a CD goes in
    window._update_device_actions()
    assert window.eject_action.isEnabled() and window.eject_action.text() == "Eject Disc"
    del window._discs["cd:/dev/sr0"]
    window._update_device_actions()
    assert not window.eject_action.isEnabled()


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
    monkeypatch.setattr(window, "_approve_internet", lambda *a, **k: False)
    window._check_for_updates()
    assert calls == [] and window._update_worker is None     # said no: nothing went online
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    monkeypatch.setattr(window, "_approve_internet", lambda *a, **k: True)
    window._check_for_updates()
    window._update_worker.wait(10_000)
    QApplication.instance().processEvents()
    assert calls == [True] and "latest version" in shown[0]
    # A newer version, for a copy running from its source folder: points to git pull.
    window._on_update_found(updater.Release("99.0.0", "v99.0.0", "", "p", []))
    assert "git pull" in shown[-1]


def test_rating_takes_a_second_click(window, monkeypatch):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from luxtunes.ui.track_model import RATING_COLUMN, TrackRole

    table = window.table
    table.setColumnHidden(RATING_COLUMN, False)
    table.clearSelection()
    QApplication.processEvents()
    delegate = table.itemDelegateForColumn(RATING_COLUMN)
    index = table.model().index(0, RATING_COLUMN)
    rect = table.visualRect(index)
    star = table.fontMetrics().horizontalAdvance(delegate.STAR)
    third_star = QPoint(rect.left() + delegate.PADDING + int(star * 2.5), rect.center().y())

    def rating():
        return (table.model().data(index, TrackRole) or {}).get("rating") or 0

    def click(pos):
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=pos, delay=600)

    before = rating()
    # The click that brings the window to the front changes nothing.
    monkeypatch.setattr(delegate, "_window_was_in_front", lambda widget: False)
    click(third_star)
    assert rating() == before
    monkeypatch.setattr(delegate, "_window_was_in_front", lambda widget: True)
    table.clearSelection()
    click(third_star)                                   # first click: selects the song
    assert rating() == before and table.selectionModel().isRowSelected(0, index.parent())
    click(third_star)                                   # second click: sets the rating
    assert rating() == 60
    click(QPoint(rect.left() + 1, rect.center().y()))
    assert rating() == 0


def test_sync_warning_lists_the_songs_it_removes(window):
    from luxtunes.core.ipod.sync import Removal
    from luxtunes.ui.sync_confirm_dialog import SyncConfirmDialog

    removals = [Removal("Song B", "Artist B", "Album B", "Not in your sync settings"),
                Removal("Song A", "Artist A", "Album A", "Not in your LuxTunes library")]
    from PySide6.QtWidgets import QApplication

    dialog = SyncConfirmDialog("Test iPod", 5, removals, window)
    assert dialog.table.isHidden()
    dialog.show()
    QApplication.instance().processEvents()
    text = dialog.text                                   # all of it shows before expanding
    assert text.height() >= text.heightForWidth(text.width())
    dialog.details.setChecked(True)                      # Show Details
    assert not dialog.table.isHidden() and dialog.table.rowCount() == 2
    assert dialog.table.item(0, 1).text() == "Artist A"  # sorted by artist
    assert dialog.list_text().splitlines()[1] == \
        "Song A\tArtist A\tAlbum A\tNot in your LuxTunes library"
    dialog.details.setChecked(False)
    assert dialog.table.isHidden()
    QApplication.instance().processEvents()
    assert text.height() >= text.heightForWidth(text.width())
    dialog.deleteLater()


def test_eject_waits_while_the_ipod_is_busy(window, monkeypatch):
    import time
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QMessageBox

    from luxtunes.core.ipod import device

    ipod = SimpleNamespace(key="ipod", name="Test iPod", device="/dev/sdz1", mountpoint="/nowhere")
    window._ipods["ipod"] = ipod
    answers = ["DeviceBusy: target is busy", "DeviceBusy: target is busy", None]
    monkeypatch.setattr(device, "eject", lambda i: answers.pop(0))
    monkeypatch.setattr(window, "EJECT_WAIT_MS", 10)
    monkeypatch.setattr(window, "_scan_devices", lambda: None)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    window._eject_device("ipod")
    deadline = time.time() + 5
    while answers and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not answers and not warnings                  # third try worked, no error shown
    assert "/dev/sdz1" in window._mount_ejected

    # Still busy after every try: the message names who's using it.
    monkeypatch.setattr(device, "eject", lambda i: "DeviceBusy: target is busy")
    monkeypatch.setattr(device, "users_of", lambda m: [(4242, "nemo", "/nowhere/iPod_Control")])
    window._eject_device("ipod")
    deadline = time.time() + 5
    while not warnings and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert "nemo (process 4242)" in warnings[0]
    del window._ipods["ipod"]


def test_starting_luxtunes_again_shows_the_open_window(window, tmp_path):
    import time

    from PySide6.QtWidgets import QApplication

    from luxtunes.ui import app as app_module

    name = app_module.server_name(tmp_path / "library.db")
    assert not app_module.tell_running_copy(name)              # nothing running yet
    app_module.listen_for_others(name, window)
    window.showMinimized()
    QApplication.processEvents()
    assert app_module.tell_running_copy(name)                  # the second start stops here
    deadline = time.time() + 5
    while window.isMinimized() and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert not window.isMinimized()
    window._stop_single_instance()                             # e.g. restarting after an update
    assert not app_module.tell_running_copy(name)


def test_unusable_menu_items_look_greyed_out(window):
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    palette = QApplication.instance().palette()
    for dark in (False, True):
        window._toggle_dark_mode(dark)
        palette = QApplication.instance().palette()
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                     QPalette.ColorRole.ButtonText):
            assert palette.color(QPalette.ColorGroup.Disabled, role) != \
                palette.color(QPalette.ColorGroup.Active, role)
    window._toggle_dark_mode(False)
    assert not window.eject_action.isEnabled()               # and Eject is one of them


def test_media_keys(window, monkeypatch):
    from PySide6.QtWidgets import QApplication

    calls = []
    monkeypatch.setattr(window.player, "next", lambda: calls.append("next"))
    monkeypatch.setattr(window, "_start_in_current_list", lambda: calls.append("start"))
    window._media_play_pause()                               # nothing playing: starts the list
    assert calls == ["start"]
    window._media_action("next")
    window._media_action("next")                             # the same press by a second route
    assert calls == ["start", "next"]
    window._last_media_action = ("", 0.0)

    volume = window.volume.value()
    window._toggle_mute()
    assert window.volume.value() == 0
    window._toggle_mute()
    assert window.volume.value() == volume

    # The desktop's side (MPRIS): what it reads, and its requests.
    mpris = window._mpris
    assert mpris is not None, "the MPRIS player couldn't be created"
    assert mpris.status() == "Stopped"
    song = {"id": 7, "name": "Robbers", "artist": "The 1975", "album": "The 1975",
            "total_time": 254000}
    monkeypatch.setattr(type(window.player), "current", property(lambda self: song))
    data = mpris.metadata()
    assert data["xesam:title"] == "Robbers" and data["xesam:artist"] == ["The 1975"]
    assert data["mpris:length"] == 254_000_000
    if mpris.active:                                         # a desktop session is running
        from PySide6.QtDBus import QDBusConnection, QDBusInterface
        from luxtunes.ui import mpris as mpris_module
        player = QDBusInterface(mpris.service, mpris_module.PATH,
                                mpris_module.PLAYER_INTERFACE, QDBusConnection.sessionBus())
        player.call("Next")
        QApplication.processEvents()
        assert calls[-1] == "next"
        mpris.changed()                                      # mustn't raise


def test_podcasts_and_audiobooks(window):
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import library
    from luxtunes.ui import get_info
    from luxtunes.ui.widgets import SOURCE_AUDIOBOOKS, SOURCE_MUSIC, SOURCE_PODCASTS

    tracks = window.model.visible_tracks()
    # Get Info → Options → Media Kind offers both; picking one ticks their defaults.
    dialog = get_info.InfoDialog(window.conn, tracks, 0, window._apply_info, window)
    kinds = [dialog.media_kind.itemData(i) for i in range(dialog.media_kind.count())]
    assert "podcast" in kinds and "audiobook" in kinds
    dialog.media_kind.setCurrentIndex(kinds.index("podcast"))
    dialog._kind_picked(0)
    assert dialog.bookmarkable.isChecked() and dialog.skip_shuffle.isChecked()
    dialog.reject()

    library.update_track_fields(window.conn, tracks[0]["id"], {"media_kind": "podcast"})
    library.update_track_fields(window.conn, tracks[1]["id"], {"media_kind": "audiobook"})
    window.reload_library()
    labels = [window.sidebar.topLevelItem(0).child(i).text(0)
              for i in range(window.sidebar.topLevelItem(0).childCount())]
    assert "Podcasts" in labels and "Audiobooks" in labels
    window._set_view_mode("grid")
    window.show_source(SOURCE_PODCASTS)
    QApplication.processEvents()
    assert window.model.rowCount() == 1
    assert window.grid.tab_keys() == ["podcasts", "podcast_categories", "unplayed"]
    window.show_source(SOURCE_AUDIOBOOKS)
    window._refresh_views()
    assert window.grid.tab_keys() == ["books", "authors", "book_categories"]
    window.grid.set_grouping("authors")
    window._update_summary()
    assert "author" in window.summary_label.text()
    window.show_source(SOURCE_MUSIC)
    assert window.grid.tab_keys() == ["albums", "artists", "genres", "composers"]
    window.show_source(SOURCE_AUDIOBOOKS)
    assert window.grid.grouping == "authors"                 # the tab you picked last
    window._set_view_mode("list")


def test_cd_do_nothing_and_no_lookup_question(window, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import cd

    asked = []
    monkeypatch.setattr(window, "_approve_internet", lambda *a, **k: asked.append(a[0]) or False)
    monkeypatch.setattr(window, "_ask_import", lambda key: asked.append("import?"))
    monkeypatch.setattr(cd, "drives", lambda: ["/dev/sr9"])
    monkeypatch.setattr(cd, "audio_disc_present", lambda device: True)
    monkeypatch.setattr(cd, "read_toc", lambda device: cd.Toc(1, 1, [150], 300))
    before = window._current_source
    cd.set_insert_action(window.conn, "nothing")
    window._scan_cds()
    QApplication.processEvents()
    assert "cd:/dev/sr9" in window._discs                     # in the sidebar under Devices
    assert window._current_source == before and asked == []  # but no jump and no questions
    # "Don't ask again" for the lookup: the next CD skips straight to the import question.
    del window._discs["cd:/dev/sr9"]
    cd.set_insert_action(window.conn, "ask")
    cd.set_ask_lookup(window.conn, False)
    window._scan_cds()
    QApplication.processEvents()
    assert asked == ["import?"]
    window.cd_page.lookup_button.click()                     # the button still asks
    assert asked[-1] == "Get Track Names"
    cd.set_ask_lookup(window.conn, True)
    del window._discs["cd:/dev/sr9"]


def test_checkboxes_are_easy_to_see_in_both_themes(window):
    from PySide6.QtWidgets import QApplication

    from luxtunes.ui import theme

    app = QApplication.instance()
    for dark, colors in ((True, theme.DARK), (False, theme.LIGHT)):
        window._toggle_dark_mode(dark)
        sheet = app.styleSheet()
        assert f"border: 1px solid {colors['indicator_border']}" in sheet
        assert ("check-light.svg" if dark else "check-dark.svg") in sheet
        window.grab()                                       # draws with the new style
    window._toggle_dark_mode(False)


def _fake_phone(tmp_path):
    from luxtunes.core.android.device import Phone

    storage = tmp_path / "phone" / "Internal shared storage"
    storage.mkdir(parents=True)
    return Phone(str(tmp_path / "phone"), "Google", "Pixel 7", "TESTSERIAL",
                 {"Internal shared storage": str(storage)})


def test_a_phone_shows_up_and_syncs(window, tmp_path, monkeypatch):
    import time

    from PySide6.QtWidgets import QApplication

    from luxtunes.core.android import device as android_device
    from luxtunes.ui.widgets import SOURCE_MUSIC

    from PySide6.QtWidgets import QMessageBox

    from luxtunes.core import sounds

    # The test library's songs aren't real files: the sync lists them as problems.
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    monkeypatch.setattr(sounds, "play_done_sound", lambda: False)
    phone = _fake_phone(tmp_path)
    monkeypatch.setattr(android_device, "mounted_names", lambda folder=None: {"mtp:host=x"})
    monkeypatch.setattr(android_device, "find_phones", lambda folder=None: [phone])
    window._scan_devices()
    assert phone.key in window._phones
    assert window.sidebar.select_source(("device", phone.key))
    QApplication.processEvents()
    page = window.device_page
    assert page.is_android and not page.picture.isVisible() and page.storage.isVisible()
    assert page.delete_unselected.isVisible() and page.delete_unselected.isChecked()
    assert window.sync_action.isEnabled() and "Pixel 7" in window.sync_action.text()
    assert window.eject_action.text() == "Eject Phone"

    window._sync_device(phone)
    deadline = time.time() + 30
    while window._sync_worker is not None and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    assert window._sync_worker is None
    root = tmp_path / "phone" / "Internal shared storage" / "LuxTunes"
    assert (root / "library.db").is_file()
    assert (root / "LuxTunes Media").is_dir()

    # A rename in the sidebar sticks, even after the phone is seen again.
    window._on_device_renamed(phone.key, "My Phone")
    window._phones.clear()
    window._phone_names = set()
    window._scan_devices()
    assert window._device_name(window._phones[phone.key]) == "My Phone"

    # Unplugged: it leaves the sidebar, and the music library is shown.
    monkeypatch.setattr(android_device, "mounted_names", lambda folder=None: set())
    monkeypatch.setattr(android_device, "find_phones", lambda folder=None: [])
    window._scan_devices()
    QApplication.processEvents()
    assert not window._phones and window._current_source == SOURCE_MUSIC


def test_ejecting_a_phone_doesnt_open_it_again(window, tmp_path, monkeypatch):
    from luxtunes.core.android import device as android_device

    phone = _fake_phone(tmp_path)
    window._phones[phone.key] = phone
    ejected, opened = [], []
    monkeypatch.setattr(android_device, "eject", lambda p: ejected.append(p) or None)
    monkeypatch.setattr(android_device, "unmounted_phones", lambda: [phone.uri])
    monkeypatch.setattr(android_device, "mount", lambda uri: opened.append(uri))
    monkeypatch.setattr(window, "_scan_devices", lambda: None)
    window._eject_device(phone.key)
    assert ejected == [phone]
    window._auto_mount_phones()
    assert opened == []


def _wait_for_adding(window, seconds=30):
    import time

    from PySide6.QtWidgets import QApplication

    deadline = time.time() + seconds
    while window._add_worker is not None and time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)
    assert window._add_worker is None


def test_adding_a_folder_runs_in_the_background_and_can_be_stopped(window, tmp_path, monkeypatch):
    import threading

    from luxtunes.core import add_files, library

    folder = tmp_path / "lots"
    for i in range(5):
        song = folder / f"Artist {i}" / f"{i:02d} song.mp3"
        song.parent.mkdir(parents=True)
        song.write_bytes((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 30)
    before = library.count_tracks(window.conn, "music")
    # The third file is slow (e.g. a drive that stalls): stopping still works.
    real_add_file = add_files.add_file
    reached = threading.Event()

    def slow(conn, path, *a, **k):
        if "02 song" in path:
            reached.set()
            stop = k.get("stop") or a[-1]
            stop.wait(10)
            raise add_files.AddStopped()
        return real_add_file(conn, path, *a, **k)

    monkeypatch.setattr(add_files, "add_file", slow)
    window._add_paths([str(folder)])
    assert reached.wait(10)
    assert window.lcd.showing_task == "add"              # the window stays usable meanwhile
    window._stop_task("add")
    _wait_for_adding(window)
    assert library.count_tracks(window.conn, "music") == before + 2      # the first two stay
    monkeypatch.setattr(add_files, "add_file", real_add_file)
    from PySide6.QtWidgets import QMessageBox
    told = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: told.append(a[2])))
    window._add_paths([str(folder)])                     # again: carries on from there
    _wait_for_adding(window)
    assert library.count_tracks(window.conn, "music") == before + 5
    assert told == ["Added 3 items to your library. 2 files were skipped because they are "
                    "already in your library."]


def test_playlist_deletions_from_a_phone_are_asked_about(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    answers = []

    class FakeWorker:
        def answer(self, ok):
            answers.append(ok)

    window._sync_worker = FakeWorker()
    window._sync_name = "Pixel 7"
    shown = []

    chosen = []

    def fake_exec(box):
        shown.append(box.text())
        chosen.append(next(b for b in box.buttons() if b.text() == "Keep Playlists"))
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: chosen[-1])
    window._confirm_phone_deletions(["Road Trip"])
    window._sync_worker = None
    assert answers == [False] and "Road Trip" in shown[0]


def test_playlists_a_sync_deleted_leave_the_sidebar(window):
    """A phone sync deletes playlists from its own connection: the sidebar
    drops them however the sync ends, and a leftover row is never a dead end."""
    from luxtunes.core import db, library
    from luxtunes.core.ipod.sync import SyncReport
    names = lambda: {window.sidebar.topLevelItem(i).child(j).text(0)
                     for i in range(window.sidebar.topLevelItemCount())
                     for j in range(window.sidebar.topLevelItem(i).childCount())}
    first = library.create_playlist(window.conn, "On-The-Go 1")
    second = library.create_playlist(window.conn, "On-The-Go 2")
    window.reload_library()
    window.sidebar.select_source(("playlist", first))
    db_path = window.conn.execute("PRAGMA database_list").fetchone()[2]
    other = db.connect(db_path)                         # as the sync's own connection
    library.delete_playlist(other, first)
    other.close()
    window._sync_ipod, window._sync_name = None, "Pixel 7"
    window._sync_finished(SyncReport(cancelled=True))   # e.g. "Don't Sync" after the question
    assert "On-The-Go 1" not in names() and window._current_source != ("playlist", first)
    # A row left from before still goes away when it's clicked or right-clicked.
    other = db.connect(db_path)
    library.delete_playlist(other, second)
    other.close()
    assert "On-The-Go 2" in names()
    window._show_sidebar_menu(("playlist", second), window.mapToGlobal(window.rect().center()))
    assert "On-The-Go 2" not in names()


def test_each_screen_keeps_its_sort_and_scroll_position(window):
    import time

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import library
    from luxtunes.ui.track_model import NAME_COLUMN
    from luxtunes.ui.widgets import SOURCE_MUSIC
    with window.conn:
        window.conn.executemany(
            "INSERT INTO tracks (persistent_id, name, artist, album, media_kind) "
            "VALUES (?, ?, 'Many', 'Songs', 'music')",
            [(f"FEED{i:012X}", f"Song {i:03d}") for i in range(300)])
    mix = library.create_playlist(window.conn, "Mix")
    library.add_to_playlist(window.conn, mix, [1, 2])
    window.reload_library()
    window.sidebar.select_source(SOURCE_MUSIC)
    window._set_view_mode("list")
    header = window.table.horizontalHeader()
    header.setSortIndicator(NAME_COLUMN, Qt.SortOrder.DescendingOrder)   # a click on Name
    assert window.model.sort_state() == (NAME_COLUMN, Qt.SortOrder.DescendingOrder)

    def settle():
        end = time.time() + 1.2
        while time.time() < end:
            QApplication.processEvents()

    settle()                                                   # the list is laid out
    window.table.verticalScrollBar().setValue(120)
    assert window.table.verticalScrollBar().value() == 120

    window.sidebar.select_source(("playlist", mix))
    settle()
    assert window.model.sort_state()[0] == -1                 # the playlist keeps its own order
    window.sidebar.select_source(SOURCE_MUSIC)
    settle()
    assert window.model.sort_state() == (NAME_COLUMN, Qt.SortOrder.DescendingOrder)
    assert header.sortIndicatorSection() == NAME_COLUMN
    assert window.table.verticalScrollBar().value() == 120
    # The sort is kept for next time LuxTunes opens too.
    assert window.settings.value("sort/list/library/music") == "name desc"


def test_a_phone_remembers_where_it_syncs_to(window, tmp_path):
    from PySide6.QtWidgets import QApplication

    from luxtunes.core.android import sync as android_sync
    from luxtunes.core.android.device import Phone

    for name in ("Internal shared storage", "SD card"):
        (tmp_path / "p" / name).mkdir(parents=True)
    phone = Phone(str(tmp_path / "p"), "Moto", "One", "SER2",
                  {n: str(tmp_path / "p" / n) for n in ("Internal shared storage", "SD card")})
    window._phones[phone.key] = phone
    window.reload_library()
    assert window.sidebar.select_source(("device", phone.key))
    QApplication.processEvents()
    page = window.device_page
    assert page.storage.currentData() == "Internal shared storage"     # new phone: its own
    assert not (page.sync_movies.isChecked() or page.sync_books.isChecked())   # music only
    page.storage.setCurrentIndex(page.storage.findData("SD card"))
    record = android_sync.load_record(window.conn, phone)
    assert (record.settings.android_storage, record.settings.android_storage_kind) == \
        ("SD card", "removable")                                        # saved, no Apply
    assert not page.dirty
    page.set_device(window.conn, phone, record)                         # seen again later
    assert page.storage.currentData() == "SD card"
    del window._phones[phone.key]


def test_closing_during_a_sync_asks_then_stops_safely_first(window, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication, QMessageBox

    stops, closes = [], []

    class FakeWorker:
        def stop(self):
            stops.append(True)

        def deleteLater(self):
            pass

    window._sync_worker = FakeWorker()
    window._sync_name = "Pixel 7"
    window.lcd.start_task("sync", "Syncing", "…", "Stop syncing")
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    asked = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: asked.append(a[2]) or answers.pop(0)))
    # No: nothing happens, the sync carries on.
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and not stops and "sync" in asked[0].lower()
    # Yes: the sync is told to stop; the window waits for it.
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and stops == [True] and window._closing_after_sync
    # The sync has stopped and saved: now LuxTunes closes.
    monkeypatch.setattr(window, "close", lambda: closes.append(True))
    window._sync_thread_done()
    QApplication.processEvents()
    assert closes == [True] and window._sync_worker is None
    window._closing_after_sync = False


def test_paste_artwork_into_several_songs(window, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QMessageBox

    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    QApplication.clipboard().setImage(image)
    window.show_source(("library", "music"))
    tracks = [window.model.track_at(r) for r in range(min(2, window.model.rowCount()))]
    calls = []
    monkeypatch.setattr(window, "_start_tag_writes", lambda: None)   # files: not here
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    menu = window._song_menu(tracks, tracks[0], "x", lambda: None)
    paste = next(a for a in menu.actions() if a.text() == "Paste Artwork")
    assert paste.isEnabled()
    window._paste_artwork(tracks)
    calls = window._tag_jobs                     # queued for writing in the background
    assert [c[0] for c in calls] == [t["id"] for t in tracks]
    assert len(calls[0][1]) == 1 and calls[0][1] == calls[-1][1]       # the same picture
    window._tag_jobs = []


def test_get_info_writes_files_in_the_background(window, tmp_path, monkeypatch):
    pytest.importorskip("mutagen")
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import library
    from luxtunes.core.tags import UNCHANGED

    conn = window.conn
    tracks = window.model.visible_tracks()[:2]
    for n, t in enumerate(tracks):
        song = tmp_path / f"s{n}.mp3"
        song.write_bytes((b"\xff\xfb\x90\x64" + b"\x00" * 413) * 30)
        library.set_location(conn, t["id"], str(song))
    for t in tracks:                              # what Multiple Item Information's OK does
        assert window._queue_info(t["id"], {"genre": "Ambient"}, None, UNCHANGED) is None
    assert library.load_track(conn, tracks[0]["id"])["genre"] == "Ambient"   # saved at once
    window._start_tag_writes()
    worker = window._tag_worker
    assert worker is not None
    worker.wait(20_000)
    for _ in range(50):
        QApplication.instance().processEvents()
        if window._tag_worker is None:
            break
    assert window._tag_worker is None
    assert not library.load_track(conn, tracks[0]["id"])["tags_dirty"]      # written


def test_the_ipod_page_shows_only_what_the_model_can_do(window, tmp_path):
    from PySide6.QtWidgets import QApplication

    from luxtunes.core.ipod import device, models
    from test_ipod_core import make_ipod

    mount, mounts = make_ipod(tmp_path)
    [ipod] = device.find_ipods(mounts)
    ipod.identity = models.identify("", "120a")                  # iPod nano (1st generation)
    ipod.model_name = ipod.identity.name
    window._ipods[ipod.key] = ipod
    window.reload_library()
    assert window.sidebar.select_source(("device", ipod.key))
    QApplication.processEvents()
    page = window.device_page
    assert page.hidden_tabs() == {"Movies", "TV Shows"}
    assert page.picture.family == "nano_1" and page.picture.shape["body"][2] < 60   # a slim nano
    page.picture.grab()                                                            # draws
    assert page.model_note.text().startswith("This iPod doesn't play videos")
    assert page.sync_button.isEnabled()
    ipod.identity = models.identify("", "1267")                  # nano 7th: can't be synced
    page.set_device(window.conn, ipod, page.record)
    assert not page.sync_button.isEnabled() and "can't sync" in page.model_note.text()
    del window._ipods[ipod.key]


def test_equalizer_window_and_song_presets(window, monkeypatch):
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    from luxtunes.core import equalizer, library
    from luxtunes.ui import equalizer_window, get_info

    app = QApplication.instance()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._toggle_equalizer()                            # View > Show Equalizer
    eq = window._eq_window
    assert eq.isVisible() and window.equalizer_action.shortcut().toString() == "Ctrl+T"
    app.processEvents()
    eq.grab()
    eq.on.setChecked(True)
    assert window.eq_settings.on
    eq._picked(eq.presets.findData("Rock"))
    assert eq.bands[0].value() == 5.0 and window.eq_settings.preset == "Rock"
    eq.bands[9].slider.setValue(-30)                      # -3 dB: now Manual
    assert window.eq_settings.preset == equalizer.MANUAL
    assert eq.presets.currentText() == equalizer.MANUAL
    assert window.eq_settings.current.bands[9] == -3.0

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("My Rock", True))
    eq._picked(eq.presets.findData(eq.MAKE))
    assert window.eq_settings.preset == "My Rock" and eq.presets.currentText() == "My Rock"

    # A song with its own preset; renaming and deleting the preset follow through.
    track_id = window.model.visible_tracks()[0]["id"]
    window._apply_info(track_id, {"equalizer": "My Rock"}, None, get_info.UNCHANGED)
    assert library.load_track(window.conn, track_id)["equalizer"] == "My Rock"
    edit = equalizer_window.EditPresetsDialog(eq)
    edit.list.setCurrentRow(window.eq_settings.names().index("My Rock"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Loud Rock", True))
    edit._rename()
    assert library.load_track(window.conn, track_id)["equalizer"] == "Loud Rock"
    edit._delete()
    assert library.load_track(window.conn, track_id)["equalizer"] is None
    assert "Loud Rock" not in window.eq_settings.names()
    edit.accept()

    # Get Info offers the presets, for one song and for several.
    tracks = window.model.visible_tracks()
    dialog = get_info.InfoDialog(window.conn, tracks, 0, window._apply_info, window,
                                 presets=window.eq_settings.names())
    dialog.equalizer.setCurrentIndex(dialog.equalizer.findData("Jazz"))
    dialog._ok()
    assert library.load_track(window.conn, tracks[0]["id"])["equalizer"] == "Jazz"
    multi = get_info.MultiInfoDialog(window.conn, tracks[:2], window._apply_info, window,
                                     presets=window.eq_settings.names())
    multi.checks["equalizer"].setChecked(True)
    multi.getters["equalizer"] = lambda: "Pop"
    multi.checks["volume_adjustment"].setChecked(True)
    multi.getters["volume_adjustment"] = lambda: 40
    multi._ok()
    for t in tracks[:2]:
        saved = library.load_track(window.conn, t["id"])
        assert saved["equalizer"] == "Pop" and saved["volume_adjustment"] == 40

    window._toggle_equalizer()                            # Ctrl+T again hides it
    assert not eq.isVisible()

    # Volume Adjustment snaps to None when dragged near the middle.
    slider = get_info.VolumeSlider()
    slider.slider.setValue(4)
    slider.slider.sliderMoved.emit(4)
    assert slider.value() == 0
    slider.slider.setValue(30)
    slider.slider.sliderMoved.emit(30)
    assert slider.value() == 30

    window._eq_save_timer.timeout.emit()                  # saves the settings
    assert equalizer.load(window.conn).on


def test_equalizer_reads_audio_buffers():
    from PySide6.QtCore import QByteArray
    from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat

    import numpy as np

    from luxtunes.ui import eq_output

    fmt = QAudioFormat()
    fmt.setSampleRate(44100)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    raw = np.array([0, 16384, -32768, 32767], dtype=np.int16).tobytes()
    buffer = QAudioBuffer(QByteArray(raw), fmt)
    block = eq_output.samples(buffer)
    assert block.shape == (4, 2) and block.dtype == np.float32
    assert block[1, 0] == 0.5 and block[2, 1] == -1.0


def test_now_playing_bar_and_mini_player(window):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from luxtunes.ui import theme

    app = QApplication.instance()
    model = window.model
    first = model.visible_tracks()[0]
    model.set_now_playing(first["id"])
    row = model.row_of(first["id"])
    for column in (1, 3):                                 # the whole row, not just the name
        assert model.index(row, column).data(Qt.ItemDataRole.BackgroundRole) == \
            theme.now_playing_color()
    assert model.index(row + 1, 1).data(Qt.ItemDataRole.BackgroundRole) is None
    window.grab()
    model.set_now_playing(None)

    from PySide6.QtGui import QAction
    texts = [a.text() for a in window.findChildren(QAction)]    # the menu items
    assert "Pop-Out Player" in texts and "Mini Player" in texts
    window._open_mini_player()
    mini = window._mini_player
    app.processEvents()
    assert mini.isVisible()
    mini.grab()
    window._on_volume(40)
    assert mini.volume.value() == 40
    # A long artist/album line scrolls; a short one stays still.
    mini.subtitle.setText("An Artist With A Long Name — An Album Title Far Too Long To Fit " * 3)
    app.processEvents()
    assert mini.subtitle._overflows() and mini.subtitle._timer.isActive()
    mini.subtitle._step()
    mini.subtitle._step()
    assert mini.subtitle._offset == 2
    mini.grab()
    mini.subtitle.setText("Short")
    assert not mini.subtitle._timer.isActive()
    mini.restore_button.click()                            # back to the full window
    app.processEvents()
    assert window._mini_player is None and window.isVisible()
    assert window.isMaximized()                            # the whole screen, not its old size


def test_shuffle_and_repeat_are_remembered(window):
    from luxtunes.ui.player import REPEAT_OFF, REPEAT_ONE

    window._set_shuffle(True)
    window._set_repeat(REPEAT_ONE)
    window.player.shuffle = False                     # as a fresh start would be
    window.player.repeat = REPEAT_OFF
    window._restore_settings()                        # what a restart does
    assert window.player.shuffle and window.shuffle_button.isChecked()
    assert window.shuffle_action.isChecked()
    assert window.player.repeat == REPEAT_ONE and window.repeat_actions[REPEAT_ONE].isChecked()
    window.settings.setValue("repeat", "nonsense")    # a bad stored value: back to off
    window._restore_settings()
    assert window.player.repeat == REPEAT_OFF


def test_trash_failures_say_why(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from luxtunes.core import library, trash

    tracks = window.model.visible_tracks()[:2]
    library.set_location(window.conn, tracks[0]["id"], str(tmp_path / "gone.avi"))
    tracks[0]["location"] = str(tmp_path / "gone.avi")
    calls = iter([QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: next(calls))
    shown = []

    def fake_exec(box):
        shown.append(box.text() + "\n" + box.informativeText())
        for button in box.buttons():                   # choose "Move to Trash"
            if button.text() == "Move to Trash":
                box.clickedButton = lambda b=button: b
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(trash, "move_to_trash", lambda location: (
        (trash.MISSING, "") if "gone" in location else (trash.NO_TRASH, "Trashing not supported")))
    window._delete_tracks(tracks)
    report = shown[-1]
    assert "couldn't be found at" in report and "gone.avi" in report
    assert "has no Trash LuxTunes can use" in report
    for t in tracks:
        assert library.load_track(window.conn, t["id"]) is None



def test_edit_menu_cut_copy_paste_and_undo(window, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    import os

    from luxtunes.core import library, organize, trash
    from luxtunes.ui.widgets import SOURCE_MUSIC, SOURCE_PODCASTS

    conn = window.conn
    warnings = []
    # Undoing a pasted copy trashes the file LuxTunes made; here it's just removed.
    monkeypatch.setattr(trash, "move_to_trash", lambda location: os.remove(location))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    organize.set_media_folder(conn, tmp_path / "Media")
    playlists = {p["name"]: p for p in library.load_playlists(conn)}
    empty, smart_list = playlists["Empty Playlist"], playlists["My Top Rated"]

    window.show_source(SOURCE_MUSIC)
    song = window.model.visible_tracks()[0]
    real = tmp_path / "song.mp3"
    real.write_bytes(b"\xff\xfb\x90\x64" + b"\x00" * 413)
    library.set_location(conn, song["id"], str(real))
    window.reload_library()
    window.show_source(SOURCE_MUSIC)
    row = window.model.row_of(song["id"])
    window.table.selectRow(row)

    # Copy, then Paste into Music: nothing happens (no message either).
    window._copy_songs()
    count = library.count_tracks(conn, "music")
    window._paste_songs()
    assert library.count_tracks(conn, "music") == count and not warnings

    # Paste into a playlist; Undo / Redo name it.
    window.show_source(("playlist", empty["id"]))
    window._paste_songs()
    assert library.playlist_track_ids(conn, empty["id"]) == {song["id"]}
    window._update_edit_menu()
    assert window.undo_action.text() == f"Undo Copy “{song['name']}”"
    window._undo()
    assert library.playlist_track_ids(conn, empty["id"]) == set()
    window._update_edit_menu()
    assert window.redo_action.text() == f"Redo Copy “{song['name']}”"
    window._redo()
    assert library.playlist_track_ids(conn, empty["id"]) == {song["id"]}

    # Smart playlists take nothing.
    window.show_source(("playlist", smart_list["id"]))
    before = library.playlist_track_ids(conn, smart_list["id"])
    window._paste_songs()
    assert library.playlist_track_ids(conn, smart_list["id"]) == before and not warnings

    # Copy into Podcasts: a new podcast copy of the file; the original stays in Music.
    window.show_source(SOURCE_PODCASTS)
    window._paste_songs()
    podcasts = library.load_tracks(conn, media_kind="podcast")
    assert len(podcasts) == 1 and podcasts[0]["location"] != str(real)
    assert library.load_track(conn, song["id"])["media_kind"] == "music"
    window._undo()                                           # the copy goes again
    assert library.load_tracks(conn, media_kind="podcast") == []

    # Cut (or drag) into Podcasts: the song itself becomes a podcast.
    window.show_source(SOURCE_MUSIC)
    window.table.selectRow(window.model.row_of(song["id"]))
    window._copy_songs(cut=True)
    window.show_source(SOURCE_PODCASTS)
    window._paste_songs()
    assert library.load_track(conn, song["id"])["media_kind"] == "podcast"
    window._undo()
    assert library.load_track(conn, song["id"])["media_kind"] == "music"

    # Dragged to Audiobooks and back onto Music.
    window._on_tracks_dropped(("library", "audiobook"), [song["id"]])
    assert library.load_track(conn, song["id"])["media_kind"] == "audiobook"
    window._on_tracks_dropped(SOURCE_MUSIC, [song["id"]])
    assert library.load_track(conn, song["id"])["media_kind"] == "music"
    assert library.load_tracks(conn, media_kind="audiobook") == []

    # A song can't go into TV Shows (not a video): it says why.
    window._put_songs(("library", "tvshow"), [library.load_track(conn, song["id"])], move=True)
    assert warnings and "isn't a video file" in warnings[-1]

    # Deleting from the library (keeping the file) can be undone.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    def keep_files(box):
        for button in box.buttons():
            if button.text() == "Keep Files":
                box.clickedButton = lambda b=button: b
        return 0

    monkeypatch.setattr(QMessageBox, "exec", keep_files)
    window.show_source(SOURCE_MUSIC)
    window._delete_tracks([library.load_track(conn, song["id"])])
    assert library.load_track(conn, song["id"]) is None
    window._undo()
    assert library.load_track(conn, song["id"]) is not None
    assert song["id"] in library.playlist_track_ids(conn, empty["id"])

    # Select All / Select None.
    window.table.setFocus()
    window._select_all()
    assert len(window._selected_rows()) == window.model.rowCount()
    window.table_clear_selection()
    assert window._selected_rows() == []


def test_help_guides(window):
    from PySide6.QtWidgets import QApplication

    from luxtunes.ui.help_topics import GUIDES

    window._show_help()
    help_window = window._help_window
    QApplication.instance().processEvents()
    assert help_window.isVisible() and help_window.topics.count() == len(GUIDES)
    help_window.show_topic("Equalizer")
    assert "Show Equalizer" in help_window.page.toPlainText()
    help_window.search.setText("trash")                   # narrows the list
    shown = [help_window.topics.item(i).text() for i in range(help_window.topics.count())
             if not help_window.topics.item(i).isHidden()]
    assert "Deleting songs" in shown and "Equalizer" not in shown
    help_window.search.setText("zzzz nothing")
    assert "No help topics" in help_window.page.toPlainText()
    help_window.close()


def test_sleep_timer(window, monkeypatch):
    from datetime import datetime, timedelta

    from PySide6.QtWidgets import QApplication, QDialog

    from luxtunes.ui import sleep_timer

    dialog = sleep_timer.SleepTimerDialog(60, window)
    assert dialog.minutes == 60 and "Music will stop at" in dialog.preview.text()
    dialog.group.button(240).setChecked(True)
    expected = sleep_timer.clock(datetime.now() + timedelta(minutes=240))
    assert expected in dialog.preview.text()
    dialog.deleteLater()

    monkeypatch.setattr(sleep_timer.SleepTimerDialog, "exec",
                        lambda self: QDialog.DialogCode.Accepted)
    window._sleep_timer_dialog()                         # 30 minutes (offered first)
    QApplication.instance().processEvents()
    assert window.sleep_timer.active and not window.sleep_button.isHidden()
    assert window.sleep_button.text() in ("☾ 30m", "☾ 29m")
    assert window.sleep_off_action.isVisible()
    window.sleep_button.click()                          # clicking it turns it off
    assert not window.sleep_timer.active and window.sleep_button.isHidden()
    assert sleep_timer.left_text(30) == "1m" and sleep_timer.left_text(3900) == "1:05"


def test_each_library_section_remembers_its_view(window):
    from luxtunes.ui.widgets import SOURCE_MOVIES, SOURCE_MUSIC

    window.show_source(SOURCE_MUSIC)
    assert window.view_mode == "list"                     # Music: List by default
    window.show_source(SOURCE_MOVIES)
    assert window.view_mode == "grid"                     # the others: Grid
    window._set_view_mode("album")
    window.show_source(SOURCE_MUSIC)
    assert window.view_mode == "list"
    window.show_source(SOURCE_MOVIES)
    assert window.view_mode == "album"
    window.show_source(SOURCE_MUSIC)


def test_popout_lyrics_and_playback_preferences(window, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from luxtunes.ui.preferences import PreferencesDialog, appearance

    track = window.model.visible_tracks()[0]
    window.table.selectRow(0)
    monkeypatch.setattr(window, "_lyrics_for", lambda t: "First line\nSecond line")
    window._open_art_window()
    art = window._art_window
    art.lyrics_button.click()                             # lyrics over the art
    QApplication.instance().processEvents()
    assert art.lyrics_view.isVisible() and "Second line" in art.lyrics_view.toPlainText()
    assert window.settings.value("popout_lyrics", type=bool)
    art.set_lyrics("")
    assert "No lyrics" in art.lyrics_view.toPlainText()
    art.close()
    assert track

    dialog = PreferencesDialog(window.conn, window, window.settings)
    assert not dialog.crossfade.isChecked() and not dialog.sound_check.isChecked()   # off
    dialog.crossfade.setChecked(True)
    dialog.crossfade_seconds.setValue(15)
    dialog.gapless.setChecked(True)
    dialog._save()
    look = appearance(window.settings)
    assert look["crossfade"] and look["crossfade_seconds"] == 15 and look["gapless"]
    window._apply_appearance()
    assert window.player.crossfade_seconds == 15 and window.player.gapless


def test_the_next_song_is_known_in_advance(window):
    from luxtunes.ui.player import REPEAT_ALL, REPEAT_OFF

    player = window.player
    tracks = window.model.visible_tracks()
    for t in tracks:
        t.setdefault("location", "/nowhere")
    import luxtunes.ui.player as player_module
    real = player_module.find_file
    player_module.find_file = lambda location: location
    try:
        player._context = list(tracks)
        player._order = list(range(len(tracks)))
        player._pos = len(tracks) - 1                       # the last song
        player._shuffle = False
        player._repeat = REPEAT_OFF
        assert player._peek_next() is None                  # the end
        player._repeat = REPEAT_ALL
        assert player._peek_next() == ("order", 0, tracks[0])
        player.up_next = [tracks[1]]
        assert player._peek_next() == ("queue", 0, tracks[1])
    finally:
        player_module.find_file = real
        player.up_next = []
        player._repeat = REPEAT_OFF
        player._context, player._order, player._pos = [], [], -1


def test_restore_from_backup(window, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from luxtunes.core import backup, library

    conn = window.conn
    saved = backup.weekly(conn)
    before = library.count_tracks(conn, "music")
    first = window.model.visible_tracks()[0]
    library.delete_tracks(conn, [first["id"]])
    opened_in = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda parent, title, folder, kinds:
                        (opened_in.append(folder) or str(saved), kinds))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._restore_backup()
    assert opened_in == [str(backup.folder_for(conn))]       # starts in the Backups folder
    assert library.count_tracks(conn, "music") == before     # the song is back
    kept = [p.name for p in backup.folder_for(conn).iterdir() if "before restore" in p.name]
    assert len(kept) == 1


def test_search_menu_queue_menu_and_short_column_titles(window):
    from luxtunes.core import library
    from luxtunes.ui.track_model import COLUMN_INDEX
    from luxtunes.ui.widgets import SOURCE_MUSIC
    with window.conn:
        window.conn.executemany(
            "INSERT INTO tracks (persistent_id, name, artist, album, media_kind) "
            "VALUES (?, ?, ?, ?, 'music')",
            [("BEEF000000000001", "Sic Transit Gloria", "Brand New", "Deja Entendu"),
             ("BEEF000000000002", "Misery Business", "Paramore", "Brand New Eyes")])
    window.reload_library()
    window.sidebar.select_source(SOURCE_MUSIC)
    window.show_source(SOURCE_MUSIC)
    window.search.setText("brand new")
    names = lambda: {t["name"] for t in window.model.visible_tracks()}
    assert {"Sic Transit Gloria", "Misery Business"} <= names()      # All: either tag
    window._search_scope_actions["artist"].trigger()
    assert names() == {"Sic Transit Gloria"}                         # Artist: Brand New only
    window._search_scope_actions["album"].trigger()
    assert names() == {"Misery Business"}
    assert window.settings.value("search_scope") == "album"
    window._set_search_scope("all")
    window.search.clear()

    # Add to Queue plays next; Add to End of Queue after what's queued.
    a, b, c = window.model.visible_tracks()[:3]
    window.player.add_to_up_next([a])
    window.player.add_to_up_next([b], first=True)
    window.player.add_to_up_next([c])
    assert [t["id"] for t in window.player.up_next] == [b["id"], a["id"], c["id"]]
    window.player.remove_from_up_next([0, 1, 2])

    header = lambda key: window.model.headerData(COLUMN_INDEX[key], Qt_horizontal())
    assert (header("bpm"), header("disc_number"), header("track_number"),
            header("episode_number")) == ("BPM", "Disc #", "Track #", "Episode #")
    assert library.EDITABLE_TEXT_FIELDS >= {"show", "episode_id", "description"}


def Qt_horizontal():
    from PySide6.QtCore import Qt
    return Qt.Orientation.Horizontal


def test_video_tab_columns_per_section_art_viewer_and_mini_on_top(window, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from luxtunes.core import library
    from luxtunes.ui import get_info
    from luxtunes.ui.art_viewer import ArtViewer
    from luxtunes.ui.track_model import COLUMN_INDEX
    from luxtunes.ui.widgets import SOURCE_MOVIES, SOURCE_MUSIC
    with window.conn:
        window.conn.execute(
            "INSERT INTO tracks (persistent_id, name, media_kind, show, season, location) "
            "VALUES ('F11F000000000001', 'Pilot', 'tvshow', 'Narnia', 1, ?)",
            (str(tmp_path / "pilot.m4v"),))
    episode = library.load_tracks(window.conn, media_kind="tvshow")[0]
    song = window.model.visible_tracks()[0]
    # Saved the way Get Info saves: the library now, the file in the background.
    dialog = get_info.InfoDialog(window.conn, [episode, song], 0, window._queue_info, window)
    assert dialog.tabs.isTabVisible(dialog.video_tab)
    assert dialog.edits["show"].text() == "Narnia" and dialog.edits["season"].text() == "1"
    dialog.edits["episode_number"].setText("3")
    dialog.description.setPlainText("The beginning.")
    dialog._step(1)                                       # saves, then shows the song
    assert not dialog.tabs.isTabVisible(dialog.video_tab)
    dialog.reject()
    window._tag_jobs.clear()                              # (the test episode has no file)
    saved = library.load_track(window.conn, episode["id"])
    assert saved["episode_number"] == 3 and saved["description"] == "The beginning."

    # Each Library section keeps its own columns; Movies starts with the usual ones.
    window.show_source(SOURCE_MUSIC)
    genre = COLUMN_INDEX["genre"]
    window.table.setColumnHidden(genre, True)
    window.show_source(SOURCE_MOVIES)
    assert not window.table.isColumnHidden(genre)
    window.show_source(SOURCE_MUSIC)
    assert window.table.isColumnHidden(genre)
    window.table.setColumnHidden(genre, False)

    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage
    image = QImage(40, 30, QImage.Format.Format_RGB32)          # a real picture, made here
    image.fill(QColor("darkred"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    png = bytes(data)
    viewer = ArtViewer(png, "Album — Artist")
    assert viewer.has_image() and viewer.windowTitle() == "Album — Artist"
    viewer.grab()
    viewer.close()
    window.art.set_image(png, caption="Album — Artist")
    assert window.art.data == png
    window._view_full_size_art()
    QApplication.processEvents()
    assert any(isinstance(w, ArtViewer) for w in window.findChildren(ArtViewer))

    window._open_mini_player()                            # on top of other windows by default
    assert window._mini_player.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window._mini_player.set_on_top(False)
    assert not window._mini_player.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window._mini_player.restore_button.click()
    QApplication.processEvents()


def test_dragging_several_songs_shows_a_small_icon_with_a_count(window):
    from luxtunes.ui.album_view import drag_icon
    for count in (2, 48, 12000):
        icon = drag_icon(count)
        assert not icon.isNull() and icon.width() <= 64 and icon.height() <= 64
    sharp = drag_icon(5, 2.0)
    assert sharp.width() == 112 and sharp.devicePixelRatio() == 2.0


def test_lyrics_from_lrclib_are_saved_and_the_sung_line_is_highlighted(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from luxtunes.core import library, lrc, lyrics_online
    from luxtunes.ui.artwork_window import ArtworkWindow
    timed = "[00:01.00]Hello there\n[00:05.00]Second line\n[00:09.00]Last line\n"
    song = window.model.visible_tracks()[0]
    library.update_track_fields(window.conn, song["id"], {"lyrics": ""})
    told = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: told.append(a[2])))
    result = lyrics_online.LyricsResult(
        found={song["id"]: lyrics_online.Found(plain=lrc.plain(timed), timed=timed)},
        not_found=["Another"])
    monkeypatch.setattr(window, "_start_tag_writes", lambda: None)    # (test songs have no files)
    window._save_found_lyrics(result, {song["id"]: ""})
    assert window._tag_jobs == [(song["id"], window_unchanged())]
    window._tag_jobs.clear()
    saved = library.load_track(window.conn, song["id"])
    assert saved["lyrics"] == lrc.plain(timed) and saved["synced_lyrics"] == timed
    assert "added to 1 song" in told[0] and "no lyrics for 1 of the 2" in told[0]

    art = ArtworkWindow()
    art.resize(400, 400)
    art.show()
    art.set_lyrics_shown(True)
    art.set_lyrics(saved["lyrics"], saved["synced_lyrics"])
    art.update_position(6000, 12000)
    assert art._line == 1                                   # "Second line" is lit
    assert "Second line" in art.lyrics_view.toPlainText()
    art.update_position(9500, 12000)
    assert art._line == 2
    art.set_follow(False)                                   # Preferences: highlighting off
    assert art._timed == [] and "[00:" not in art.lyrics_view.toPlainText()
    assert "Second line" in art.lyrics_view.toPlainText()
    art.set_follow(True)
    assert art._line == 2
    art.set_lyrics("Plain words only")                      # untimed: shown as before
    assert art._timed == [] and "Plain words only" in art.lyrics_view.toPlainText()
    art.close()


def window_unchanged():
    from luxtunes.core.tags import UNCHANGED
    return UNCHANGED
