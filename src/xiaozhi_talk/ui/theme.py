"""Application wide look and feel."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

STYLESHEET = """
QMainWindow, QDialog {
    background: #f2f4f7;
}
QWidget {
    color: #1b1f24;
}
/* Without this the colour above would also hide that a widget is disabled. */
QWidget:disabled {
    color: #a9afb8;
}
QMenuBar {
    background: #ffffff;
    border-bottom: 1px solid #e2e5ea;
    padding: 2px 4px;
}
QMenuBar::item {
    padding: 5px 10px;
    background: transparent;
    border-radius: 5px;
}
QMenuBar::item:selected {
    background: #e9eef6;
}
QMenu {
    background: #ffffff;
    border: 1px solid #dde1e7;
    padding: 5px;
}
QMenu::item {
    padding: 6px 26px 6px 18px;
    border-radius: 5px;
}
QMenu::item:selected {
    background: #eaf1fd;
}
QMenu::item:disabled {
    color: #a9afb8;
}
QMenu::separator {
    height: 1px;
    background: #e8eaee;
    margin: 5px 8px;
}
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #e2e5ea;
    color: #59606a;
    padding: 2px 8px;
}
QStatusBar::item {
    border: none;
}
QScrollArea#ChatScroll {
    border: none;
    background: #ffffff;
}
QWidget#ChatCanvas {
    background: #ffffff;
}
QFrame#UserBubble {
    background: #2f6fed;
    border-radius: 12px;
}
QFrame#UserBubble QLabel {
    color: #ffffff;
}
QFrame#AssistantBubble {
    background: #f1f3f7;
    border-radius: 12px;
}
QFrame#AssistantBubble QLabel {
    color: #15181d;
}
QLabel#BubbleRole {
    color: #8b929c;
    font-size: 11px;
}
QLabel#ChatNotice {
    color: #5c636d;
    font-size: 12px;
    padding: 2px 6px;
}
QLabel#ChatError {
    color: #8c2f22;
    background: #fdf1ef;
    border: 1px solid #f0c8c0;
    border-radius: 8px;
    font-size: 12px;
    padding: 8px 12px;
}
QLabel#EmptyHint {
    color: #98a0ab;
    font-size: 13px;
}
QPlainTextEdit#ComposerEdit {
    background: #ffffff;
    border: 1px solid #d7dbe1;
    border-radius: 10px;
    padding: 8px 10px;
    selection-background-color: #b9d4ff;
}
QPlainTextEdit#ComposerEdit:focus {
    border: 1px solid #2f6fed;
}
QPushButton#SendButton {
    background: #2f6fed;
    color: #ffffff;
    border: none;
    border-radius: 9px;
    padding: 9px 22px;
    font-weight: 600;
}
QPushButton#SendButton:hover {
    background: #2560d8;
}
QPushButton#SendButton:disabled {
    background: #c3cbd8;
    color: #f4f6f9;
}
QPushButton#MicButton {
    background: #ffffff;
    border: 1px solid #d2d7df;
    border-radius: 9px;
    padding: 8px 16px;
    font-weight: 600;
    color: #3a414b;
}
QPushButton#MicButton:hover {
    background: #f3f6fb;
}
QPushButton#MicButton[recording="true"] {
    background: #fdeeec;
    border: 1px solid #e0a79f;
    color: #a4342a;
}
QPushButton#MicButton[recording="true"]:hover {
    background: #fbe3df;
}
QPushButton#MicButton:disabled {
    background: #f4f5f8;
    border: 1px solid #e3e6eb;
    color: #a4abb5;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #d2d7df;
    border-radius: 7px;
    padding: 6px 14px;
}
QPushButton:hover {
    background: #f3f6fb;
}
QPushButton:disabled {
    color: #a4abb5;
}
QListWidget {
    background: #ffffff;
    border: 1px solid #dde1e7;
    border-radius: 8px;
    padding: 4px;
}
QListWidget::item {
    padding: 7px 9px;
    border-radius: 6px;
}
QListWidget::item:selected {
    background: #eaf1fd;
    color: #1b1f24;
}
QCheckBox {
    color: #3a414b;
    spacing: 7px;
}
QCheckBox:disabled {
    color: #a9afb8;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
}
QComboBox {
    background: #ffffff;
    border: 1px solid #d7dbe1;
    border-radius: 7px;
    padding: 5px 9px;
    min-height: 18px;
}
QComboBox:focus {
    border: 1px solid #2f6fed;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #dde1e7;
    outline: none;
    selection-background-color: #eaf1fd;
    selection-color: #1b1f24;
}
QLineEdit, QSpinBox {
    background: #ffffff;
    border: 1px solid #d7dbe1;
    border-radius: 7px;
    padding: 6px 9px;
}
QLineEdit:focus {
    border: 1px solid #2f6fed;
}
QGroupBox {
    border: 1px solid #dde1e7;
    border-radius: 9px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    background: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #59606a;
}
QTabWidget::pane {
    border: 1px solid #dde1e7;
    border-radius: 9px;
    background: #ffffff;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    padding: 7px 16px;
    border: 1px solid transparent;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    color: #59606a;
}
QTabBar::tab:selected {
    background: #ffffff;
    border-color: #dde1e7;
    border-bottom-color: #ffffff;
    color: #1b1f24;
}
QSplitter::handle {
    background: #eceff3;
    height: 4px;
}
QSplitter::handle:hover {
    background: #d8e0ec;
}
QLabel#FormHint {
    color: #7b828d;
    font-size: 11px;
}
QLabel#FormError {
    color: #c0392b;
    font-size: 11px;
}
QLabel#SectionTitle {
    font-size: 15px;
    font-weight: 600;
}
"""


def apply_theme(app: QApplication) -> None:
    """Apply the application style sheet and a readable base font."""
    font = app.font()
    if font.pointSizeF() < 10:
        font.setPointSizeF(10.5)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)
