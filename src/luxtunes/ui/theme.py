"""Light and dark colour themes.

Colours live in one dictionary per theme; the stylesheet is a template that
pulls from it. To tweak a colour, change it here and restart LuxTunes.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

LIGHT = {
    "window": "#d9d9d9",
    "text": "#000000",
    "text_dim": "#6f7b88",
    "text_disabled": "#a3a3a3",     # menu items and buttons that can't be used right now
    "base": "#ffffff",
    "alt_base": "#f1f5fa",
    "highlight": "#3d80df",
    "highlight_text": "#ffffff",
    "toolbar_top": "#ececec",
    "toolbar_bottom": "#c3c3c3",
    "toolbar_border": "#8f8f8f",
    "button_top": "#fdfdfd",
    "button_bottom": "#c9c9c9",
    "button_border": "#7d7d7d",
    "lcd_top": "#f6f7e6",
    "lcd_bottom": "#e1e5c3",
    "lcd_border": "#9a9c80",
    "lcd_text": "#1f1f1f",
    "sidebar": "#dfe4ea",
    "sidebar_sel_top": "#7aa1d8",
    "sidebar_sel_bottom": "#4f7fc4",
    "sidebar_sel_inactive": "#b4c0d0",
    "header_top": "#ffffff",
    "header_bottom": "#e4e4e4",
    "border": "#b3b3b3",
    "art_header": "#e6e6e6",
    "art_bg": "#ffffff",
    "overlay": "rgba(240, 240, 240, 225)",
    "grid_bg": "#3a3a3a",            # the grid is dark even in the light theme (see Grid View)
    "overlay_border": "rgba(0, 0, 0, 60)",
}

DARK = {
    "window": "#2a2b2e",
    "text": "#e4e4e4",
    "text_dim": "#8b95a1",
    "text_disabled": "#6b6b6b",
    "base": "#1e1f22",
    "alt_base": "#26272b",
    "highlight": "#2f6fd0",
    "highlight_text": "#ffffff",
    "toolbar_top": "#404146",
    "toolbar_bottom": "#2b2c30",
    "toolbar_border": "#16171a",
    "button_top": "#55575c",
    "button_bottom": "#36373b",
    "button_border": "#16171a",
    "lcd_top": "#3d4133",
    "lcd_bottom": "#2e3124",
    "lcd_border": "#14150f",
    "lcd_text": "#e9edcc",
    "sidebar": "#25272b",
    "sidebar_sel_top": "#3e6fb8",
    "sidebar_sel_bottom": "#2c5596",
    "sidebar_sel_inactive": "#3a4250",
    "header_top": "#36373c",
    "header_bottom": "#2b2c30",
    "border": "#141518",
    "art_header": "#303135",
    "art_bg": "#1a1b1e",
    "overlay": "rgba(28, 29, 32, 225)",
    "grid_bg": "#262729",
    "overlay_border": "rgba(255, 255, 255, 40)",
}

STYLESHEET = Template("""
QWidget#toolbar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $toolbar_top, stop:1 $toolbar_bottom);
    border-bottom: 1px solid $toolbar_border;
}
QToolButton#transport {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $button_top, stop:1 $button_bottom);
    border: 1px solid $button_border;
    border-radius: 15px;
    min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
}
QToolButton#transport:pressed { background: $button_bottom; }
QToolButton#playButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $button_top, stop:1 $button_bottom);
    border: 1px solid $button_border;
    border-radius: 18px;
    min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px;
}
QToolButton#playButton:pressed { background: $button_bottom; }
QToolButton#viewButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $button_top, stop:1 $button_bottom);
    border: 1px solid $button_border;
    border-left: none;
    min-width: 28px; min-height: 20px;
    padding: 0px 2px;
}
QToolButton#viewButton[segment="first"] {
    border-left: 1px solid $button_border;
    border-top-left-radius: 4px; border-bottom-left-radius: 4px;
}
QToolButton#viewButton[segment="last"] {
    border-top-right-radius: 4px; border-bottom-right-radius: 4px;
}
QToolButton#viewButton:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $button_bottom, stop:1 $button_top);
}

QWidget#gridPage, QListView#grid { background: $grid_bg; border: none; }
QWidget#gridTabs { background: $grid_bg; border-bottom: 1px solid $grid_line; }
QToolButton#gridTab {
    color: $grid_tab_text; background: transparent; border: none;
    font-weight: bold; padding: 2px 10px; border-radius: 9px;
}
QToolButton#gridTab:checked { background: $grid_tab_checked; border: 1px solid $grid_tab_border; }
QToolButton#gridTab:hover:!checked { background: $grid_tab_hover; }

QWidget#devicePage { background: $base; }
QWidget#deviceTabs {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $header_top, stop:1 $header_bottom);
    border-bottom: 1px solid $border;
}
QToolButton#deviceTab {
    color: $text; font-weight: bold; border: none; padding: 1px 10px; border-radius: 9px;
    background: transparent;
}
QToolButton#deviceTab:checked {
    color: white;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #8a8a8a, stop:1 #6a6a6a);
}
QFrame#deviceBox { background: $alt_base; border: 1px solid $border; }
QListWidget#deviceList::indicator {
    width: 13px; height: 13px; border: 1px solid $text_dim; border-radius: 3px; background: $base;
}
QListWidget#deviceList::indicator:checked {
    background: $highlight; border-color: $highlight; image: url("$check_icon");
}
QListWidget#deviceList::indicator:disabled { border-color: $border; }
QLabel#deviceHeading, QLabel#deviceCount { color: $text_dim; }
QLabel#deviceNote { color: $text_dim; }
QWidget#capacityPanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #8b94a5, stop:1 #5d6677);
    border-top: 1px solid #454c58;
}
QLabel#capacityWarning { color: #ffe08a; font-weight: bold; }
QWidget#cdBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #8b94a5, stop:1 #5d6677);
    border-top: 1px solid #454c58;
}
QLabel#cdStatus { color: white; font-weight: bold; }
QPushButton#cdButton {
    color: #111; border: 1px solid #3c424c; border-radius: 4px; padding: 3px 14px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fdfdfd, stop:1 #d4d7dc);
}
QPushButton#cdButton:pressed { background: #c4c8cf; }
QPushButton#cdButton:disabled { color: #777; }
QTableWidget#cdTracks {
    border: none;
    gridline-color: transparent;
    selection-background-color: $highlight;
    selection-color: $highlight_text;
    outline: 0;
}

QWidget#dupBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #8b94a5, stop:1 #5d6677);
    border-top: 1px solid #454c58;
}
QLabel#dupLabel { color: white; font-weight: bold; }
QToolButton#dupButton {
    color: #111; border: 1px solid #3c424c; border-radius: 8px; padding: 1px 12px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fdfdfd, stop:1 #d4d7dc);
}
QToolButton#dupButton:pressed { background: #c4c8cf; }

QWidget#drillBar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $header_top, stop:1 $header_bottom);
    border-bottom: 1px solid $border;
}
QToolButton#drillBack {
    color: $text; font-weight: bold; border: none; padding: 1px 8px;
    border-right: 1px solid $border;
}
QToolButton#drillBack:hover { background: $sidebar_sel_inactive; }
QLabel#drillTitle { color: $text; font-weight: bold; }

QWidget#coverFlow { background: black; }
QScrollBar#coverFlowScroll:horizontal {
    background: #111; border: 1px solid #555; border-radius: 7px; height: 14px;
}
QScrollBar#coverFlowScroll::handle:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5a5a5a, stop:1 #333);
    border: 1px solid #777; border-radius: 6px; min-width: 40px;
}
QScrollBar#coverFlowScroll::add-line, QScrollBar#coverFlowScroll::sub-line { width: 0px; }
QSplitter#songsSplit::handle { background: #2a2a2a; height: 5px; }

QFrame#lcd {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $lcd_top, stop:1 $lcd_bottom);
    border: 1px solid $lcd_border;
    border-radius: 4px;
}
QFrame#lcd QLabel { color: $lcd_text; background: transparent; }
QLabel#lcdIdle { font-size: 20px; font-weight: bold; }
QLabel#lcdTitle { font-weight: bold; }
QToolButton#lcdButton {
    color: $lcd_text; background: transparent; border: 1px solid $lcd_border; border-radius: 10px;
    font-weight: bold; padding: 0;
}
QToolButton#lcdButton:hover { background: rgba(127, 127, 127, 60); }
QProgressBar#lcdProgress {
    border: 1px solid $lcd_border; border-radius: 4px; background: transparent;
}
QProgressBar#lcdProgress::chunk { background: $lcd_text; border-radius: 3px; }
QSlider#lcdSlider::groove:horizontal {
    height: 6px; border: 1px solid $lcd_border; background: transparent; border-radius: 3px;
}
QSlider#lcdSlider::sub-page:horizontal { background: $lcd_text; border-radius: 3px; }
QSlider#lcdSlider::handle:horizontal {
    width: 8px; margin: -3px 0; background: $lcd_text; border-radius: 4px;
}

QLineEdit#search {
    border: 1px solid $button_border;
    border-radius: 11px;
    padding: 2px 8px;
    background: $base;
    color: $text;
    min-height: 18px;
}

QTreeWidget#sidebar {
    background: $sidebar;
    border: none;
    color: $text;
    outline: 0;
}
QTreeWidget#sidebar::item { height: 20px; }
QTreeWidget#sidebar::item:selected:active {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $sidebar_sel_top, stop:1 $sidebar_sel_bottom);
    color: $highlight_text;
}
QTreeWidget#sidebar::item:selected:!active {
    background: $sidebar_sel_inactive;
    color: $text;
}

QTableView#tracks {
    border: none;
    gridline-color: transparent;
    selection-background-color: $highlight;
    selection-color: $highlight_text;
    outline: 0;
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 $header_top, stop:1 $header_bottom);
    color: $text;
    border: none;
    border-right: 1px solid $border;
    border-bottom: 1px solid $border;
    padding: 1px 4px;
    font-weight: bold;
}

QWidget#leftPanel { background: $sidebar; border-right: 1px solid $border; }
QLabel#artHeader {
    background: $art_header;
    color: $text;
    border-top: 1px solid $border;
    font-weight: bold;
    padding: 1px;
}
QLabel#artImage { background: $art_bg; color: $text_dim; }
QWidget#sidebarButtons { background: $art_header; border-top: 1px solid $border; }
QToolButton#sidebarButton {
    border: none; color: $text;
    font-size: 17px;
    min-width: 30px; min-height: 26px;
    padding: 1px 6px;
}
QToolButton#sidebarButton:hover { background: $sidebar_sel_inactive; border-radius: 4px; }
QToolButton#sidebarButton:checked {
    background: $highlight; color: $highlight_text; border-radius: 4px;
}

QWidget#artWindow { background: $art_bg; }
QLabel#artWindowImage { background: $art_bg; color: $text_dim; }
QFrame#artControls {
    background: $overlay;
    border: 1px solid $overlay_border;
    border-radius: 9px;
}
QFrame#artControls QLabel { color: $text; background: transparent; }
QToolButton#overlayButton { border: none; padding: 4px; }
QToolButton#overlayButton:hover { background: $overlay_border; border-radius: 4px; }

QStatusBar { background: $toolbar_bottom; color: $text; border-top: 1px solid $border; }
QSplitter::handle { background: $border; }
""")


def text_color(dark: bool) -> QColor:
    """The theme's text colour, read straight from the theme (the widgets'
    palettes only catch up after Qt processes the theme change)."""
    return QColor((DARK if dark else LIGHT)["text"])


def section_color(dark: bool) -> QColor:
    return QColor((DARK if dark else LIGHT)["text_dim"])


# Preferences → General → Grid View: the album wall can be dark or light,
# whichever the rest of the app is.
GRID_DARK = {"grid_line": "#222", "grid_tab_text": "#e6e6e6", "grid_tab_checked": "#1d1d1d",
             "grid_tab_border": "#555", "grid_tab_hover": "#484848",
             "grid_title": "#f2f2f2", "grid_subtitle": "#a8a8a8", "grid_empty_art": "#4a4a4a"}
GRID_LIGHT = {"grid_bg": "#f2f2f2", "grid_line": "#c8c8c8", "grid_tab_text": "#333333",
              "grid_tab_checked": "#d6d6d6", "grid_tab_border": "#b0b0b0", "grid_tab_hover": "#e2e2e2",
              "grid_title": "#1e1e1e", "grid_subtitle": "#6b6b6b", "grid_empty_art": "#d0d0d0"}
grid_colors = dict(GRID_DARK)       # read by the Grid view when it paints


def apply_theme(app: QApplication, dark: bool, grid_dark: bool = True) -> None:
    colors = dict(DARK if dark else LIGHT)
    colors.update(GRID_DARK if grid_dark else GRID_LIGHT)
    grid_colors.clear()
    grid_colors.update(colors)
    app.setStyle("Fusion")

    palette = QPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, QColor(colors["window"]))
    palette.setColor(role.WindowText, QColor(colors["text"]))
    palette.setColor(role.Base, QColor(colors["base"]))
    palette.setColor(role.AlternateBase, QColor(colors["alt_base"]))
    palette.setColor(role.Text, QColor(colors["text"]))
    palette.setColor(role.Button, QColor(colors["button_bottom"]))
    palette.setColor(role.ButtonText, QColor(colors["text"]))
    palette.setColor(role.Highlight, QColor(colors["highlight"]))
    palette.setColor(role.HighlightedText, QColor(colors["highlight_text"]))
    palette.setColor(role.ToolTipBase, QColor(colors["base"]))
    palette.setColor(role.ToolTipText, QColor(colors["text"]))
    palette.setColor(role.PlaceholderText, QColor(colors["text_dim"]))
    # setColor(role, ...) sets every state, disabled included; give things
    # that can't be used right now their own, lighter text.
    disabled = QColor(colors["text_disabled"])
    for r in (role.WindowText, role.Text, role.ButtonText, role.HighlightedText):
        palette.setColor(QPalette.ColorGroup.Disabled, r, disabled)
    # Keep the selection blue even when the song list doesn't have focus.
    palette.setColor(QPalette.ColorGroup.Inactive, role.Highlight, QColor(colors["highlight"]))
    palette.setColor(
        QPalette.ColorGroup.Inactive, role.HighlightedText, QColor(colors["highlight_text"])
    )
    app.setPalette(palette)
    check = Path(__file__).resolve().parent.parent / "resources" / "check.svg"
    app.setStyleSheet(STYLESHEET.substitute(colors, check_icon=check.as_posix()))
