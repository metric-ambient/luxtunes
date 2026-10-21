"""Get Info: the song information windows.

InfoDialog          one song: Summary, Info, Sorting, Options, Lyrics, Artwork
                    tabs, Previous/Next (which save as they go), OK/Cancel.
MultiInfoDialog     several songs ("Multiple Item Information"): Info, Sorting
                    and Options tabs. Each field has a checkbox; only checked
                    fields are changed, and editing a field ticks its box.

The dialogs only collect changes. They hand them to an `apply` function from
the main window, which saves them in the library and writes them into the
files: apply(track_id, fields, rating, artworks) -> error text or None.
"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QIntValidator, QKeySequence, QPainter, QPalette, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from luxtunes.core import library
from luxtunes.core.paths import find_file
from luxtunes.core.tags import UNCHANGED, TagError, read_details
from luxtunes.ui import formatting

IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.bmp *.gif *.webp);;All files (*)"
MAX_ART_SIDE = 2000         # pasted/added pictures are scaled down to at most this
# Get Info → Options → Media Kind. Podcasts and audiobooks get their own
# sections in the sidebar (and on the iPod).
MEDIA_KIND_CHOICES = (("Music", "music"), ("Podcast", "podcast"), ("Audiobook", "audiobook"),
                      ("Movie", "movie"), ("TV Show", "tvshow"))


# ---- Small building blocks ----------------------------------------------------------

def image_bytes(image: QImage) -> bytes:
    """A pasted/dropped picture as file bytes: PNG if it has transparency, else JPEG."""
    if max(image.width(), image.height()) > MAX_ART_SIDE:
        image = image.scaled(MAX_ART_SIDE, MAX_ART_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if image.hasAlphaChannel():
        image.save(buffer, "PNG")
    else:
        image.save(buffer, "JPG", 92)
    buffer.close()
    return bytes(data)


def images_from_mime(mime) -> list[bytes]:
    """Pictures from the clipboard or a drag-and-drop (image data or image files)."""
    found = []
    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile():
                image = QImage(url.toLocalFile())
                if not image.isNull():
                    found.append(image_bytes(image))
    if not found and mime.hasImage():
        image = QImage(mime.imageData())
        if not image.isNull():
            found.append(image_bytes(image))
    return found


class StarRating(QWidget):
    """Five clickable stars (0-100). Clicking the current single
    star again clears the rating."""

    changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0
        self.setMinimumSize(100, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def value(self) -> int:
        return self._value

    def setValue(self, value: int):
        self._value = max(0, min(100, int(value or 0)))
        self.update()

    def _star_width(self) -> int:
        return max(1, self.fontMetrics().horizontalAdvance("★") + 4)

    def mousePressEvent(self, event):
        stars = min(5, int(event.position().x() // self._star_width()) + 1)
        if stars * 20 == self._value and stars == 1:
            stars = 0
        self.setValue(stars * 20)
        self.changed.emit(self._value)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            color = self.palette().color(QPalette.ColorRole.Text)
            stars = round(self._value / 20)
            width = self._star_width()
            for i in range(5):
                painter.setPen(color if i < stars else self.palette().color(QPalette.ColorRole.PlaceholderText))
                painter.drawText(i * width, 0, width, self.height(),
                                 Qt.AlignmentFlag.AlignCenter, "★" if i < stars else "·")
        finally:
            painter.end()


def _number_edit(width=48) -> QLineEdit:
    edit = QLineEdit()
    edit.setValidator(QIntValidator(0, 99999))
    edit.setMaximumWidth(width)
    return edit


def _text(value) -> str:
    return "" if value is None else str(value)


def _combo_text(combo: QComboBox) -> str:
    return combo.currentText().strip()


class VolumeSlider(QWidget):
    """Volume Adjustment: -100% ... None ... +100%. Dragging near the
    middle snaps to None, so it's easy to put back."""

    SNAP = 6                     # percent either side of None

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(-100, 100)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(25)
        self.slider.valueChanged.connect(lambda _: self.changed.emit())
        self.slider.sliderMoved.connect(self._snap)
        labels = QHBoxLayout()
        for text, align in (("-100%", Qt.AlignmentFlag.AlignLeft), ("None", Qt.AlignmentFlag.AlignCenter),
                            ("+100%", Qt.AlignmentFlag.AlignRight)):
            label = QLabel(text)
            label.setAlignment(align)
            labels.addWidget(label, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.slider)
        layout.addLayout(labels)

    def _snap(self, value: int):
        if value and abs(value) <= self.SNAP:
            self.slider.setSliderPosition(0)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, value):
        self.slider.setValue(int(value or 0))


class ArtworkEditor(QWidget):
    """The Artwork tab: the song's pictures, with Add…/Delete, a size slider,
    Ctrl+V paste, drag-and-drop, and a Cut/Copy/Paste/Delete right-click menu."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images: list[bytes] = []
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setIconSize(QSize(200, 200))
        self.list.setSpacing(8)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.setAcceptDrops(True)
        self.list.viewport().setAcceptDrops(True)
        self.list.viewport().installEventFilter(self)

        add = QPushButton("Add…")
        add.clicked.connect(self._add_files)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_selected)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(60, 400)
        self.size_slider.setValue(200)
        self.size_slider.setMaximumWidth(120)
        self.size_slider.valueChanged.connect(lambda v: self.list.setIconSize(QSize(v, v)))

        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(delete)
        row.addStretch(1)
        row.addWidget(self.size_slider)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)

        for keys, slot in ((QKeySequence.StandardKey.Paste, self.paste),
                           (QKeySequence.StandardKey.Copy, self.copy),
                           (QKeySequence.StandardKey.Cut, self.cut),
                           (QKeySequence.StandardKey.Delete, self.delete_selected)):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)

    def images(self) -> list[bytes]:
        return list(self._images)

    def set_images(self, images: list[bytes]):
        self._images = list(images)
        self._refresh()

    def _refresh(self):
        self.list.clear()
        for data in self._images:
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            QListWidgetItem(QIcon(pixmap), "", self.list)

    def _set(self, images):
        self._images = images
        self._refresh()
        self.changed.emit()

    def _selected(self) -> int:
        row = self.list.currentRow()
        return row if 0 <= row < len(self._images) else (0 if self._images else -1)

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add artwork", "", IMAGE_FILTER)
        added = [image_bytes(QImage(p)) for p in paths if not QImage(p).isNull()]
        if added:
            self._set(self._images + added)

    def paste(self):
        added = images_from_mime(QApplication.clipboard().mimeData())
        if added:
            self._set(self._images + added)

    def copy(self):
        row = self._selected()
        if row >= 0:
            QApplication.clipboard().setImage(QImage.fromData(self._images[row]))

    def cut(self):
        self.copy()
        self.delete_selected()

    def delete_selected(self):
        row = self._selected()
        if row >= 0:
            self._set(self._images[:row] + self._images[row + 1:])

    def _menu(self, pos):
        menu = QMenu(self)
        has = self._selected() >= 0
        menu.addAction("Cut", self.cut).setEnabled(has)
        menu.addAction("Copy", self.copy).setEnabled(has)
        menu.addAction("Paste", self.paste)
        menu.addSeparator()
        menu.addAction("Delete", self.delete_selected).setEnabled(has)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def eventFilter(self, obj, event):
        if obj is self.list.viewport():
            if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                if event.mimeData().hasImage() or event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Type.Drop:
                added = images_from_mime(event.mimeData())
                if added:
                    self._set(self._images + added)
                event.acceptProposedAction()
                return True
        return super().eventFilter(obj, event)


class ArtworkBox(QLabel):
    """Multiple Item Information's single artwork well: paste (Ctrl+V), drop
    a picture, or double-click to choose a file."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: bytes | None = None
        self.setFixedSize(120, 120)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Paste, drop, or double-click to choose a picture")
        paste = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self)
        paste.setContext(Qt.ShortcutContext.WidgetShortcut)
        paste.activated.connect(lambda: self._take(images_from_mime(QApplication.clipboard().mimeData())))
        self._show()

    def _show(self):
        if self.image:
            pixmap = QPixmap()
            pixmap.loadFromData(self.image)
            self.setPixmap(pixmap.scaled(116, 116, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        else:
            self.setPixmap(QPixmap())
            self.setText("")

    def _take(self, images):
        if images:
            self.image = images[0]
            self._show()
            self.changed.emit()

    def mouseDoubleClickEvent(self, event):
        self._choose()

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose artwork", "", IMAGE_FILTER)
        if path and not QImage(path).isNull():
            self._take([image_bytes(QImage(path))])

    def paste(self):
        self._take(images_from_mime(QApplication.clipboard().mimeData()))

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        mime = QApplication.clipboard().mimeData()
        menu.addAction("Paste", self.paste).setEnabled(
            bool(mime) and (mime.hasImage() or mime.hasUrls()))
        menu.addAction("Choose Picture…", self._choose)
        menu.exec(event.globalPos())

    def dragEnterEvent(self, event):
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self._take(images_from_mime(event.mimeData()))
        event.acceptProposedAction()


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    return label


# ---- One song -----------------------------------------------------------------------

def _equalizer_combo(presets: list[str]) -> QComboBox:
    """Equalizer Preset: None, or one of the equalizer's presets."""
    combo = QComboBox()
    combo.addItem("None", None)
    for name in presets:
        combo.addItem(name, name)
    return combo


def _show_equalizer(combo: QComboBox, name: str | None) -> None:
    """Select a song's preset (one that's no longer in the list is shown anyway)."""
    if not name:
        combo.setCurrentIndex(0)
        return
    index = next((i for i in range(combo.count())
                  if (combo.itemData(i) or "").casefold() == name.casefold()), -1)
    if index < 0:
        combo.addItem(name, name)
        index = combo.count() - 1
    combo.setCurrentIndex(index)


class InfoDialog(QDialog):
    def __init__(self, conn, tracks: list[dict], index: int, apply, parent=None,
                 presets: list[str] = ()):
        super().__init__(parent)
        self.conn = conn
        self.presets = list(presets)
        self.tracks = tracks          # the list Previous/Next walk through
        self.index = index
        self.apply = apply
        self.resize(560, 520)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._summary_tab(), "Summary")
        self.tabs.addTab(self._info_tab(), "Info")
        self.tabs.addTab(self._sorting_tab(), "Sorting")
        self.tabs.addTab(self._options_tab(), "Options")
        self.lyrics = QPlainTextEdit()
        self.tabs.addTab(self.lyrics, "Lyrics")
        self.artwork = ArtworkEditor()
        self.tabs.addTab(self.artwork, "Artwork")
        self.tabs.setCurrentIndex(1)     # Info, when opened from the list
        self.tabs.currentChanged.connect(lambda _: self._sync_sorting_names())

        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        ok = QPushButton("OK")
        ok.setDefault(True)
        cancel = QPushButton("Cancel")
        self.previous_button.clicked.connect(lambda: self._step(-1))
        self.next_button.clicked.connect(lambda: self._step(1))
        ok.clicked.connect(self._ok)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.previous_button)
        buttons.addWidget(self.next_button)
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(buttons)
        self._load()

    # ---- Tabs ------------------------------------------------------------------

    def _summary_tab(self) -> QWidget:
        page = QWidget()
        self.summary_art = QLabel()
        self.summary_art.setFixedSize(110, 110)
        self.summary_art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_title = _heading("")
        self.summary_artist = QLabel()
        self.summary_album = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.summary_art)
        names = QVBoxLayout()
        for label in (self.summary_title, self.summary_artist, self.summary_album):
            names.addWidget(label)
        names.addStretch(1)
        top.addLayout(names, 1)

        self.summary_values = {}
        grid = QGridLayout()
        left = ("Kind", "Size", "Bit Rate", "Sample Rate", "Date Modified", "Plays", "Last Played")
        right = ("Format", "Channels", "Tag", "Encoded with")
        for column, names_list in ((0, left), (2, right)):
            for row, name in enumerate(names_list):
                label = QLabel(f"{name}:")
                label.setAlignment(Qt.AlignmentFlag.AlignRight)
                label.setFont(_heading("").font())
                value = QLabel()
                value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(label, row, column)
                grid.addWidget(value, row, column + 1)
                self.summary_values[name] = value
        self.summary_where = QLabel()
        self.summary_where.setWordWrap(True)
        self.summary_where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        where = QHBoxLayout()
        where.addWidget(_heading("Where:"), 0, Qt.AlignmentFlag.AlignTop)
        where.addWidget(self.summary_where, 1)

        layout = QVBoxLayout(page)
        layout.addLayout(top)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)
        layout.addLayout(grid)
        layout.addStretch(1)
        layout.addLayout(where)
        return page

    def _info_tab(self) -> QWidget:
        page = QWidget()
        self.edits = {key: QLineEdit() for key in ("name", "artist", "album_artist", "album",
                                                   "grouping", "composer")}
        for key in ("year", "track_number", "track_count", "disc_number", "disc_count", "bpm"):
            self.edits[key] = _number_edit(56 if key == "year" else 44)
        self.comments = QPlainTextEdit()
        self.comments.setMaximumHeight(70)
        self.genre = QComboBox()
        self.genre.setEditable(True)
        self.genre.addItems([""] + library.all_genres(self.conn))
        self.compilation = QCheckBox("Part of a compilation")

        grid = QGridLayout(page)
        def place(title, widget, row, column, span=1):
            grid.addWidget(_heading(title), row, column, 1, span)
            grid.addWidget(widget, row + 1, column, 1, span)
        place("Name", self.edits["name"], 0, 0, 2)
        place("Artist", self.edits["artist"], 2, 0)
        place("Year", self.edits["year"], 2, 1)
        place("Album Artist", self.edits["album_artist"], 4, 0)
        place("Track Number", self._pair("track_number", "track_count"), 4, 1)
        place("Album", self.edits["album"], 6, 0)
        place("Disc Number", self._pair("disc_number", "disc_count"), 6, 1)
        place("Grouping", self.edits["grouping"], 8, 0)
        place("BPM", self.edits["bpm"], 8, 1)
        place("Composer", self.edits["composer"], 10, 0, 2)
        place("Comments", self.comments, 12, 0, 2)
        place("Genre", self.genre, 14, 0)
        grid.addWidget(self.compilation, 15, 1)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(16, 1)
        return page

    def _pair(self, first, second) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edits[first])
        row.addWidget(QLabel("of"))
        row.addWidget(self.edits[second])
        row.addStretch(1)
        return box

    def _sorting_tab(self) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        self.sort_names = {}
        for row, (key, title) in enumerate((("name", "Name"), ("artist", "Artist"),
                                            ("album_artist", "Album Artist"), ("album", "Album"),
                                            ("composer", "Composer"))):
            shown = QLineEdit()
            shown.setReadOnly(True)        # edited on the Info tab
            self.sort_names[key] = shown
            sort_key = f"sort_{key}"
            self.edits[sort_key] = QLineEdit()
            grid.addWidget(_heading(title), row * 2, 0)
            grid.addWidget(shown, row * 2 + 1, 0)
            grid.addWidget(_heading(f"Sort {title}"), row * 2, 1)
            grid.addWidget(self.edits[sort_key], row * 2 + 1, 1)
        grid.setRowStretch(10, 1)
        return page

    def _options_tab(self) -> QWidget:
        page = QWidget()
        self.volume = VolumeSlider()
        self.equalizer = _equalizer_combo(self.presets)
        self.media_kind = QComboBox()
        for label, kind in MEDIA_KIND_CHOICES:
            self.media_kind.addItem(label, kind)
        self.media_kind.activated.connect(self._kind_picked)
        self.rating = StarRating()
        self.start_on = QCheckBox("Start Time:")
        self.start_time = QLineEdit()
        self.stop_on = QCheckBox("Stop Time:")
        self.stop_time = QLineEdit()
        for edit in (self.start_time, self.stop_time):
            edit.setMaximumWidth(110)
        self.start_time.textEdited.connect(lambda _: self.start_on.setChecked(True))
        self.stop_time.textEdited.connect(lambda _: self.stop_on.setChecked(True))
        self.bookmarkable = QCheckBox("Remember playback position")
        self.skip_shuffle = QCheckBox("Skip when shuffling")
        self.gapless = QCheckBox("Part of a gapless album")

        grid = QGridLayout(page)
        grid.addWidget(QLabel("Volume Adjustment:"), 0, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.volume, 0, 1)
        grid.addWidget(QLabel("Equalizer Preset:"), 1, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.equalizer, 1, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(QLabel("Media Kind:"), 2, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.media_kind, 2, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(QLabel("Rating:"), 3, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.rating, 3, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self.start_on, 4, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.start_time, 4, 1, Qt.AlignmentFlag.AlignLeft)
        grid.addWidget(self.stop_on, 5, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.stop_time, 5, 1, Qt.AlignmentFlag.AlignLeft)
        for row, box in enumerate((self.bookmarkable, self.skip_shuffle, self.gapless), start=6):
            grid.addWidget(box, row, 1)
        grid.setRowStretch(9, 1)
        grid.setColumnStretch(1, 1)
        return page

    def _kind_picked(self, _index: int):
        """Podcasts and audiobooks remember their position and skip shuffle by default."""
        defaults = library.kind_defaults(self.media_kind.currentData())
        if defaults:
            self.bookmarkable.setChecked(True)
            self.skip_shuffle.setChecked(True)

    # ---- Loading one song -----------------------------------------------------------

    def _load(self):
        self.track = library.load_track(self.conn, self.tracks[self.index]["id"]) or self.tracks[self.index]
        track = self.track
        path = find_file(track.get("location"))
        try:
            self.details = read_details(path) if path else None
        except TagError:
            self.details = None
        details = self.details or {}
        self.setWindowTitle(track.get("name") or "Get Info")

        for key, edit in self.edits.items():
            edit.setText(_text(track.get(key)))
        self.comments.setPlainText(_text(track.get("comments")))
        self.genre.setCurrentText(_text(track.get("genre")))
        self.compilation.setChecked(bool(track.get("compilation")))
        self.volume.setValue(track.get("volume_adjustment") or 0)
        _show_equalizer(self.equalizer, track.get("equalizer"))
        self.media_kind.setCurrentIndex(max(0, self.media_kind.findData(track.get("media_kind") or "music")))
        self.rating.setValue(0 if track.get("rating_computed") else track.get("rating") or 0)
        self.start_on.setChecked(track.get("start_time") is not None)
        self.start_time.setText(formatting.format_time_ms(track.get("start_time") or 0))
        self.stop_on.setChecked(track.get("stop_time") is not None)
        self.stop_time.setText(formatting.format_time_ms(track.get("stop_time") or track.get("total_time") or 0))
        self.bookmarkable.setChecked(bool(track.get("bookmarkable")))
        self.skip_shuffle.setChecked(bool(track.get("skip_when_shuffling")))
        self.gapless.setChecked(bool(track.get("part_of_gapless_album")))
        self.lyrics.setPlainText(details.get("lyrics") or _text(track.get("lyrics")))
        self.artwork.set_images(details.get("artworks", []))
        self._fill_summary(track, details, path)
        self._sync_sorting_names()

        self._original = self._values()
        self._original_artworks = self.artwork.images()
        self._original_rating = self.rating.value()
        self.previous_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(self.tracks) - 1)

    def _fill_summary(self, track, details, path):
        art = details.get("artworks") or []
        if art:
            pixmap = QPixmap()
            pixmap.loadFromData(art[0])
            self.summary_art.setPixmap(pixmap.scaled(110, 110, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
        else:
            self.summary_art.setPixmap(QPixmap())
            self.summary_art.setText("No Artwork")
        self.summary_title.setText(f"{track.get('name') or ''} ({formatting.format_duration(track.get('total_time'))})")
        self.summary_artist.setText(_text(track.get("artist")))
        self.summary_album.setText(_text(track.get("album")))
        values = {
            "Kind": _text(track.get("kind")),
            "Size": formatting.format_size(track.get("size")),
            "Bit Rate": formatting.format_bit_rate(track.get("bit_rate")),
            "Sample Rate": formatting.display_value(track, "sample_rate"),
            "Date Modified": formatting.format_date(track.get("date_modified")),
            "Plays": str(track.get("play_count") or 0),
            "Last Played": formatting.format_date(track.get("play_date")),
            "Format": details.get("format", ""),
            "Channels": details.get("channels", ""),
            "Tag": details.get("tag_version", ""),
            "Encoded with": details.get("encoder", ""),
        }
        for name, value in values.items():
            self.summary_values[name].setText(value)
        self.summary_where.setText(path or f"{track.get('location') or ''}  (file not found)")

    def _sync_sorting_names(self):
        for key, shown in self.sort_names.items():
            shown.setText(self.edits[key].text())

    # ---- Saving --------------------------------------------------------------------

    def _values(self) -> dict:
        values = {key: edit.text().strip() for key, edit in self.edits.items()}
        values["comments"] = self.comments.toPlainText().strip()
        values["genre"] = _combo_text(self.genre)
        values["compilation"] = self.compilation.isChecked()
        values["volume_adjustment"] = self.volume.value()
        values["equalizer"] = self.equalizer.currentData()
        values["media_kind"] = self.media_kind.currentData()
        values["start_time"] = self.start_time.text() if self.start_on.isChecked() else None
        values["stop_time"] = self.stop_time.text() if self.stop_on.isChecked() else None
        values["bookmarkable"] = self.bookmarkable.isChecked()
        values["skip_when_shuffling"] = self.skip_shuffle.isChecked()
        values["part_of_gapless_album"] = self.gapless.isChecked()
        values["lyrics"] = self.lyrics.toPlainText()
        return values

    def _save(self) -> bool:
        """Apply this song's changes. Returns False to stay on the song (bad input)."""
        current = self._values()
        fields = {k: v for k, v in current.items() if v != self._original.get(k)}
        try:
            for key in ("start_time", "stop_time"):
                if key in fields and fields[key] is not None:
                    fields[key] = formatting.parse_time_ms(fields[key])
        except ValueError:
            QMessageBox.warning(self, "LuxTunes", "Start and Stop Time must look like 1:08.937.")
            return False
        rating = self.rating.value() if self.rating.value() != self._original_rating else None
        artworks = self.artwork.images()
        artworks = artworks if artworks != self._original_artworks else UNCHANGED
        if not fields and rating is None and artworks is UNCHANGED:
            return True
        try:
            error = self.apply(self.track["id"], fields, rating, artworks)
        except ValueError as problem:
            QMessageBox.warning(self, "LuxTunes", f"That value isn't valid: {problem}")
            return False
        if error:
            QMessageBox.warning(
                self, "LuxTunes",
                f"Your changes to “{self.track.get('name')}” were saved in the library, but "
                f"the file couldn't be updated: {error}.")
        return True

    def _step(self, delta: int):
        if self._save():
            self.index = max(0, min(len(self.tracks) - 1, self.index + delta))
            self._load()

    def _ok(self):
        if self._save():
            self.accept()


# ---- Several songs ------------------------------------------------------------------

class MultiInfoDialog(QDialog):
    """Multiple Item Information. Only fields whose checkbox is ticked change."""

    def __init__(self, conn, tracks: list[dict], apply, parent=None, presets: list[str] = ()):
        super().__init__(parent)
        self.conn = conn
        self.presets = list(presets)
        self.tracks = [library.load_track(conn, t["id"]) or t for t in tracks]
        self.apply = apply
        self.setWindowTitle("Multiple Item Information")
        self.resize(560, 470)
        self.checks: dict[str, QCheckBox] = {}
        self.getters: dict[str, object] = {}

        tabs = QTabWidget()
        tabs.addTab(self._info_tab(), "Info")
        tabs.addTab(self._sorting_tab(), "Sorting")
        tabs.addTab(self._options_tab(), "Options")
        ok = QPushButton("OK")
        ok.setDefault(True)
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self._ok)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addLayout(buttons)

    def _common(self, key):
        """The value every selected song shares, or None if they differ."""
        values = {self._norm(t.get(key)) for t in self.tracks}
        return values.pop() if len(values) == 1 else None

    @staticmethod
    def _norm(value):
        return "" if value is None else value

    def _field(self, key, widget, getter, signal) -> QWidget:
        """A checkbox + widget pair. Editing the widget ticks the box."""
        box = QCheckBox()
        self.checks[key] = box
        self.getters[key] = getter
        signal.connect(lambda *_: box.setChecked(True))
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)
        layout.addWidget(widget, 1)
        return row

    def _line(self, key, number=False) -> QWidget:
        edit = _number_edit(60) if number else QLineEdit()
        common = self._common(key)
        if common not in (None, ""):
            edit.setText(str(common))
        return self._field(key, edit, lambda: edit.text().strip(), edit.textEdited)

    def _yes_no(self, key) -> QWidget:
        combo = QComboBox()
        combo.addItems(["", "Yes", "No"])
        common = self._common(key)
        if common != "" and common is not None:
            combo.setCurrentIndex(1 if common else 2)
        return self._field(key, combo, lambda: combo.currentIndex() == 1, combo.activated)

    def _info_tab(self) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)

        def place(title, widget, row, column, span=1):
            grid.addWidget(_heading(title), row, column, 1, span)
            grid.addWidget(widget, row + 1, column, 1, span)

        place("Artist", self._line("artist"), 0, 0)
        place("Year", self._line("year", number=True), 0, 1)
        place("Album Artist", self._line("album_artist"), 2, 0)
        numbers = QWidget()
        pair = QHBoxLayout(numbers)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.addWidget(self._line("track_number", number=True))   # blank: songs differ
        pair.addWidget(QLabel("of"))
        pair.addWidget(self._line("track_count", number=True))
        place("Track Number", numbers, 2, 1)
        place("Album", self._line("album"), 4, 0)
        discs = QWidget()
        pair = QHBoxLayout(discs)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.addWidget(self._line("disc_number", number=True))
        pair.addWidget(QLabel("of"))
        pair.addWidget(self._line("disc_count", number=True))
        place("Disc Number", discs, 4, 1)
        place("Grouping", self._line("grouping"), 6, 0)
        place("BPM", self._line("bpm", number=True), 6, 1)
        place("Composer", self._line("composer"), 8, 0)

        self.art_box = ArtworkBox()
        place("Artwork", self._field("artwork", self.art_box, lambda: self.art_box.image,
                                     self.art_box.changed), 8, 1)
        comments = QPlainTextEdit()
        comments.setMaximumHeight(60)
        common = self._common("comments")
        if common:
            comments.setPlainText(common)
        place("Comments", self._field("comments", comments, lambda: comments.toPlainText().strip(),
                                      comments.textChanged), 10, 0)
        rating = StarRating()
        common = self._common("rating")
        if common:
            rating.setValue(common)
        place("Rating", self._field("rating", rating, rating.value, rating.changed), 10, 1)
        genre = QComboBox()
        genre.setEditable(True)
        genre.addItems([""] + library.all_genres(self.conn))
        genre.setCurrentText(_text(self._common("genre")))
        place("Genre", self._field("genre", genre, lambda: _combo_text(genre),
                                   genre.currentTextChanged), 12, 0)
        place("Part of a compilation", self._yes_no("compilation"), 12, 1)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(14, 1)
        return page

    def _sorting_tab(self) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        for row, (key, title) in enumerate((("sort_artist", "Sort Artist"),
                                            ("sort_album_artist", "Sort Album Artist"),
                                            ("sort_album", "Sort Album"),
                                            ("sort_composer", "Sort Composer"))):
            grid.addWidget(_heading(title), row * 2, 0)
            grid.addWidget(self._line(key), row * 2 + 1, 0)
        grid.setRowStretch(8, 1)
        return page

    def _options_tab(self) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        volume = VolumeSlider()
        common = self._common("volume_adjustment")
        volume.setValue(common or 0)
        grid.addWidget(QLabel("Volume Adjustment:"), 0, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._field("volume_adjustment", volume, volume.value, volume.changed), 0, 1)
        preset = _equalizer_combo(self.presets)
        common = self._common("equalizer")
        if common is not None:
            _show_equalizer(preset, common or None)
        else:
            preset.insertItem(0, "", "\x00mixed")         # the songs differ
            preset.setCurrentIndex(0)
        grid.addWidget(QLabel("Equalizer Preset:"), 1, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._field("equalizer", preset, preset.currentData, preset.activated), 1, 1)
        kind = QComboBox()
        for label, value in MEDIA_KIND_CHOICES:
            kind.addItem(label, value)
        kind.setCurrentIndex(max(0, kind.findData(self._common("media_kind") or "music")))
        grid.addWidget(QLabel("Media Kind:"), 2, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._field("media_kind", kind, kind.currentData, kind.activated), 2, 1)
        for row, (key, title) in enumerate((("bookmarkable", "Remember playback position:"),
                                            ("skip_when_shuffling", "Skip when shuffling:"),
                                            ("part_of_gapless_album", "Part of a gapless album:")),
                                           start=3):
            grid.addWidget(QLabel(title), row, 0, Qt.AlignmentFlag.AlignRight)
            grid.addWidget(self._yes_no(key), row, 1)
        grid.setRowStretch(6, 1)
        grid.setColumnStretch(1, 1)
        return page

    def _ok(self):
        chosen = {key: self.getters[key]() for key, box in self.checks.items() if box.isChecked()}
        if chosen.get("equalizer") == "\x00mixed":       # ticked, but left blank
            del chosen["equalizer"]
        rating = chosen.pop("rating", None)
        artwork = chosen.pop("artwork", UNCHANGED)
        artworks = UNCHANGED if artwork is UNCHANGED else ([artwork] if artwork else [])
        if not chosen and rating is None and artworks is UNCHANGED:
            self.accept()
            return
        errors = 0
        for track in self.tracks:
            try:
                if self.apply(track["id"], dict(chosen), rating, artworks):
                    errors += 1
            except ValueError as error:
                QMessageBox.warning(self, "LuxTunes", f"That value isn't valid: {error}")
                return
        if errors:
            QMessageBox.warning(
                self, "LuxTunes",
                f"The changes were saved in the library, but {errors} file(s) couldn't be "
                "updated (missing or read-only). LuxTunes will try writing the tags again "
                "the next time it starts.")
        self.accept()
