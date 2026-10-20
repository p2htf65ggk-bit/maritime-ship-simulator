# SPDX-License-Identifier: MPL-2.0
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QToolTip


def _default_ui_font_family():
    if sys.platform == "win32":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "Arial"
    return "DejaVu Sans"


def _default_numeric_font_family():
    if sys.platform == "win32":
        return "Consolas"
    if sys.platform == "darwin":
        return "Menlo"
    return "DejaVu Sans Mono"


APP_FONT_FAMILY = _default_ui_font_family()
NUMERIC_FONT_FAMILY = _default_numeric_font_family()
APP_FONT_SIZE_PT = 11
TOOLTIP_FONT_FAMILY = APP_FONT_FAMILY
TOOLTIP_FONT_SIZE_PT = 11

TOOLTIP_STYLE = f"""
QToolTip {{
    background: #18232c;
    color: #edf6fb;
    border: 1px solid #6f8793;
    border-radius: 4px;
    padding: 6px 9px;
    font-family: "{TOOLTIP_FONT_FAMILY}";
    font-size: {TOOLTIP_FONT_SIZE_PT}pt;
    font-weight: normal;
}}
"""


def _resource_path(*parts):
    base_path = getattr(sys, "_MEIPASS", None)
    if base_path:
        return Path(base_path, *parts)
    return Path(__file__).resolve().parents[1].joinpath(*parts)


CHECKMARK_STYLE_URL = _resource_path("assets", "ui_checkbox_check.svg").as_posix()

APP_STYLE = """
QMainWindow {
    background: #101419;
}
QDialog {
    background: #101419;
}
QDialog[opaqueSurface="true"] {
    background-color: #101419;
}
QMessageBox, QInputDialog {
    background-color: #101419;
}
QMessageBox QLabel, QInputDialog QLabel {
    background: transparent;
}
QDialog[opaqueSurface="true"] QLineEdit,
QDialog[opaqueSurface="true"] QTextEdit,
QDialog[opaqueSurface="true"] QPlainTextEdit,
QDialog[opaqueSurface="true"] QComboBox,
QDialog[opaqueSurface="true"] QSpinBox,
QDialog[opaqueSurface="true"] QDoubleSpinBox {
    background-color: #151b22;
}
QWidget {
    color: #d8e0e8;
    font-family: "%s";
    font-size: %dpt;
    background: transparent;
}
QLabel {
    color: #d8e0e8;
    background: transparent;
}
QPushButton {
    background: #1d2630;
    border: 1px solid #354454;
    border-radius: 4px;
    padding: 7px 10px;
    min-height: 20px;
    color: #d8e0e8;
}
QPushButton:hover {
    background: #273342;
}
QPushButton:checked {
    background: #2f6f94;
    border-color: #4ea3cf;
}
QFrame#panel {
    background: #151b22;
    border: 1px solid #27313c;
    border-radius: 4px;
}
QFrame#statusBar {
    background: #151b22;
    border: 1px solid #27313c;
    border-radius: 4px;
}
QFrame#statusCell {
    background: transparent;
    border: none;
    border-right: 1px solid #354454;
}
QLabel#statusHeader {
    color: #9eb0bd;
    background: transparent;
    font-size: 9pt;
}
QLabel#statusValue {
    color: #d8e0e8;
    background: transparent;
    font-size: 13pt;
    font-weight: 600;
    font-family: "%s";
}
QComboBox#statusTimeScale {
    min-width: 64px;
    font-size: 11pt;
    font-weight: 600;
}
QComboBox#statusTimeScale[accelerated="true"] {
    color: #ffffff;
    background: #9f2933;
    border: 1px solid #f06a73;
}
QLabel#sectionTitle, QLabel#dialogTitle {
    color: #edf6fb;
    font-weight: bold;
}
QLabel#dialogTitle {
    font-size: 15pt;
}
QLabel#secondaryText {
    color: #9eb0bd;
}
QLabel#dataReadout {
    background: #111820;
    border: 1px solid #2d3b47;
    border-radius: 3px;
    padding: 7px;
    color: #edf6fb;
}
QLabel#dataReadout[faultActive="true"] {
    border: 1px solid #e45c5c;
    color: #ffd3d3;
}
QLabel#faultBadge {
    background: #9f2929;
    color: #ffffff;
    border: 1px solid #ff7a7a;
    padding: 4px 8px;
    font-weight: bold;
}
QLabel#rudderInputLamp {
    min-width: 14px;
    max-width: 14px;
    min-height: 14px;
    max-height: 14px;
    border-radius: 7px;
    border: 1px solid #7f242d;
    background: #c74d55;
}
QLabel#rudderInputLamp[signalState="ok"] {
    border-color: #62d7a6;
    background: #37a875;
}
QLabel#rudderInputLamp[signalState="delayed"] {
    border-color: #f0cb64;
    background: #d3a635;
}
QLabel#rudderInputLamp[signalState="empty"] {
    border-color: #8a98a4;
    background: #65727d;
}
QLabel#rudderInputLamp[signalState="alarm"] {
    border-color: #ef737b;
    background: #c74d55;
}
QPushButton#toolButton {
    padding-left: 8px;
    padding-right: 8px;
}
QPushButton[feedback="true"] {
    background: #287f65;
    border-color: #54c49b;
}
QDialog#panel {
    background: #151b22;
    border: 1px solid #27313c;
    border-radius: 4px;
}
QScrollArea {
    background: #151b22;
    border: 1px solid #27313c;
    border-radius: 4px;
}
QScrollArea > QWidget > QWidget {
    background: transparent;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: transparent;
    border: 1px solid #34404d;
    border-radius: 3px;
    color: #e8eef5;
    padding: 4px;
    min-height: 22px;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #4ea3cf;
}
QLineEdit[validationState="invalid"], QComboBox[validationState="invalid"] {
    border: 1px solid #ef737b;
    background: #2a171b;
}
QLineEdit[validationState="valid"], QComboBox[validationState="valid"] {
    border-color: #3f6f62;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: #12171d;
    border-color: #252f39;
    color: #647583;
}
QLabel:disabled, QCheckBox:disabled {
    color: #647583;
}
QCheckBox::indicator:disabled {
    background: #161d24;
    border-color: #3c4a56;
}
QComboBox QAbstractItemView {
    background: #0d1116;
    color: #e8eef5;
    selection-background-color: #2f6f94;
}
QTreeWidget, QTreeView, QListView, QListWidget, QTableView, QTableWidget, QAbstractItemView {
    background: #151b22;
    color: #e8eef5;
    alternate-background-color: #1a2129;
    gridline-color: #27313c;
    selection-background-color: #2f6f94;
}
QTreeWidget QWidget, QTreeView QWidget, QListView QWidget, QListWidget QWidget, QTableView QWidget, QTableWidget QWidget {
    background: transparent;
}
QTableWidget#routePlanningTable, QTableView#routePlanningTable,
QTableWidget#routePlanningTable QAbstractItemView, QTableView#routePlanningTable QAbstractItemView {
    background: #0d1116;
    color: #e8eef5;
    alternate-background-color: #141b23;
    gridline-color: #27313c;
    selection-background-color: #2f6f94;
}
QHeaderView::section {
    background: #202934;
    border: 1px solid #34404d;
    padding: 5px;
}
QProgressBar {
    background: #111820;
    border: 1px solid #34404d;
    border-radius: 3px;
    text-align: center;
}
QProgressBar::chunk {
    background: #37a875;
}
QProgressBar[loadLevel="medium"]::chunk {
    background: #d3a635;
}
QProgressBar[loadLevel="high"]::chunk {
    background: #c74d55;
}
QTableWidget#routePlanningTable QHeaderView::section,
QTableView#routePlanningTable QHeaderView::section {
    background: #202934;
    border: 1px solid #34404d;
    padding: 5px;
}
QTableWidget#routePlanningTable QLineEdit {
    background: #151b22;
    border: 1px solid #4ea3cf;
    border-radius: 0px;
    margin: 0px;
    padding: 0px 4px;
}
QTabWidget::pane {
    border: 1px solid #27313c;
}
QTabBar::tab {
    background: #1a222b;
    border: 1px solid #34404d;
    padding: 6px 10px;
}
QTabBar::tab:selected {
    background: #2b3644;
}
QTabWidget#controlTabs QTabBar::tab {
    padding-left: 6px;
    padding-right: 6px;
}
QCheckBox {
    spacing: 6px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 3px;
    border: 1px solid #7d94a1;
    background: #111821;
}
QCheckBox::indicator:unchecked:hover {
    border: 1px solid #a6bfcd;
    background: #15202b;
}
QCheckBox::indicator:checked {
    border: 1px solid #67b6d6;
    background: #2f6f94;
    image: url(%s);
}
QCheckBox::indicator:checked:hover {
    background: #3c7ea6;
}
QCheckBox::indicator:indeterminate {
    border: 1px solid #8ca5b4;
    background: #324452;
}
QCheckBox::indicator:disabled,
QCheckBox::indicator:checked:disabled,
QCheckBox::indicator:indeterminate:disabled {
    border-color: #3c4a56;
    background: #202932;
}
QMenu {
    background: #121922;
    color: #e8eef5;
    border: 1px solid #34404d;
}
QMenu::item:selected {
    background: #2f6f94;
}
QSlider::groove:horizontal {
    height: 5px;
    background: #2d3742;
}
QSlider {
    background: transparent;
}
QSlider::handle:horizontal {
    background: #77b7d8;
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::groove:vertical {
    width: 6px;
    background: #2d3742;
}
QSlider::handle:vertical {
    background: #77b7d8;
    height: 14px;
    margin: 0 -5px;
    border-radius: 7px;
}
QDoubleSpinBox[derived="true"] {
    color: #82919c;
    border-color: #2a343e;
}
QSlider[derived="true"]::groove:horizontal {
    background: #252d35;
}
QSlider[derived="true"]::handle:horizontal {
    background: #687681;
}
QSlider[derived="true"]::groove:vertical {
    background: #252d35;
}
QSlider[derived="true"]::handle:vertical {
    background: #687681;
}
QLabel[derived="true"] {
    color: #82919c;
}
QWidget[derived="true"] QToolButton,
QToolButton[derived="true"] {
    color: #82919c;
    border-color: #2a343e;
}
QToolButton[telegraphBracket="true"] {
    color: #ffffff;
    background: #253746;
    border: 1px solid #4ea3cf;
    font-weight: 700;
}
QLabel#compactValue {
    color: #edf6fb;
    font-size: 8pt;
    font-weight: 600;
    font-family: "%s";
}
""" % (
    APP_FONT_FAMILY,
    APP_FONT_SIZE_PT,
    NUMERIC_FONT_FAMILY,
    CHECKMARK_STYLE_URL,
    NUMERIC_FONT_FAMILY,
)


def apply_global_ui_style(app):
    if app is None:
        return
    if bool(app.property("_shipsim_global_style_applied")):
        return
    app.setProperty("_shipsim_global_style_applied", True)
    app.setStyle("Fusion")
    app.setFont(QFont(APP_FONT_FAMILY, APP_FONT_SIZE_PT))
    palette = app.palette()
    palette.setColor(QPalette.Window, QColor("#101419"))
    palette.setColor(QPalette.Base, QColor("#151b22"))
    palette.setColor(QPalette.AlternateBase, QColor("#1a2129"))
    palette.setColor(QPalette.WindowText, QColor("#d8e0e8"))
    palette.setColor(QPalette.Text, QColor("#e8eef5"))
    palette.setColor(QPalette.Button, QColor("#1d2630"))
    palette.setColor(QPalette.ButtonText, QColor("#d8e0e8"))
    app.setPalette(palette)
    QToolTip.setFont(QFont(TOOLTIP_FONT_FAMILY, TOOLTIP_FONT_SIZE_PT))
    app.setStyleSheet(APP_STYLE + TOOLTIP_STYLE)


def with_tooltip_style(style):
    return style
