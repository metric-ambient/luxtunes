"""The review window for Get Track Info Online…: every change MusicBrainz
suggests, grouped by album, each with a checkbox. Filling in something
that's blank starts ticked; replacing something you already have starts
unticked, so nothing of yours changes unless you tick it. Missing album
covers can be added too. Nothing is saved until you click Apply Changes.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from luxtunes.core.musicbrainz import FIELD_LABELS, LookupResult

CHANGE_ROLE = Qt.ItemDataRole.UserRole


def _show(value) -> str:
    if value in (None, ""):
        return "(empty)"
    return str(value)


class TrackInfoDialog(QDialog):
    def __init__(self, result: LookupResult, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Get Track Info Online")
        self.resize(760, 520)
        self.result = result

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Change", "Now", "MusicBrainz"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 200)
        self.tree.setIconSize(QSize(48, 48))
        changes = 0
        for album in result.albums:
            top = QTreeWidgetItem(self.tree, [album.title])
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            if album.artwork:
                art = QTreeWidgetItem(top, ["Add album artwork", "(none)", ""])
                pixmap = QPixmap()
                if pixmap.loadFromData(album.artwork):
                    art.setIcon(2, QIcon(pixmap))
                art.setText(2, f"{len(album.artwork_for)} song(s)" if pixmap.isNull() else "")
                art.setData(0, CHANGE_ROLE, ("artwork", album))
                art.setFlags(art.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                art.setCheckState(0, Qt.CheckState.Checked)
                changes += 1
            for proposal in album.songs:
                song_item = QTreeWidgetItem(top, [proposal.track.get("name") or ""])
                for change in proposal.changes:
                    item = QTreeWidgetItem(song_item, [FIELD_LABELS[change.field], _show(change.old),
                                                       _show(change.new)])
                    item.setData(0, CHANGE_ROLE, ("field", proposal.track["id"], change))
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(0, Qt.CheckState.Checked if change.fills_blank
                                       else Qt.CheckState.Unchecked)
                    changes += 1
        self.tree.expandAll()

        notes = []
        if not result.albums:
            notes.append("MusicBrainz had nothing new for these songs.")
        else:
            notes.append(f"{changes} suggested change{'s' if changes != 1 else ''}. Blanks to fill "
                         "in are ticked; replacing what you already have is up to you.")
        if result.not_found:
            notes.append(f"{len(result.not_found)} song(s) couldn't be matched and stay as they are.")
        if result.problems:
            notes.append("Problems: " + "; ".join(result.problems[:3]))
        if result.stopped:
            notes.append("The lookup was stopped part way.")
        summary = QLabel("\n".join(notes))
        summary.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.addButton("Apply Changes", QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setEnabled(bool(result.albums))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.tree, 1)
        layout.addWidget(buttons)

    def chosen(self) -> tuple[dict[int, dict], dict[int, bytes]]:
        """The ticked changes: {song id: {field: value}}, {song id: artwork}."""
        fields: dict[int, dict] = {}
        artwork: dict[int, bytes] = {}
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack.extend(item.child(i) for i in range(item.childCount()))
            data = item.data(0, CHANGE_ROLE)
            if not data or item.checkState(0) != Qt.CheckState.Checked:
                continue
            if data[0] == "field":
                _, track_id, change = data
                fields.setdefault(track_id, {})[change.field] = change.new
            else:
                album = data[1]
                for track_id in album.artwork_for:
                    artwork[track_id] = album.artwork
        return fields, artwork
