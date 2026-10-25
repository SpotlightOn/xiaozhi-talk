"""In-memory log buffer and the window that shows it.

The application logs what the client and the SDK do.  A :class:`LogBuffer`
keeps the newest records in memory, so the connection log can be opened at any
time - even after a disconnect - and keeps growing while it is visible.
"""

from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..log import CAPTURED_LOGGERS, LOG_FORMAT

#: Levels offered by the filter box, lowest first.
LEVELS = (
    ("Debug", logging.DEBUG),
    ("Info", logging.INFO),
    ("Warnings", logging.WARNING),
    ("Errors", logging.ERROR),
)

DEFAULT_CAPACITY = 3000


class _CapturedLogger(logging.Filter):
    """Only keep records of this application and of ``xiaozhi_sdk``."""

    def filter(self, record: logging.LogRecord) -> bool:
        return any(
            record.name == name or record.name.startswith(f"{name}.") for name in CAPTURED_LOGGERS
        )


class LogBuffer(QObject, logging.Handler):
    """Keeps log records in memory and announces new ones to the viewer."""

    entryAdded = Signal(int, str)

    def __init__(self, capacity: int = DEFAULT_CAPACITY, parent: QObject | None = None) -> None:
        QObject.__init__(self, parent)
        logging.Handler.__init__(self, logging.DEBUG)
        self._entries: deque[tuple[int, str]] = deque(maxlen=capacity)
        self.addFilter(_CapturedLogger())
        self.setFormatter(logging.Formatter(LOG_FORMAT, "%H:%M:%S"))

    # -- logging.Handler ----------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:  # pragma: no cover - formatting never fails in practice
            self.handleError(record)
            return
        self._entries.append((record.levelno, text))
        self.entryAdded.emit(record.levelno, text)

    # -- content ------------------------------------------------------------

    def entries(self, minimum: int = logging.DEBUG) -> list[str]:
        """Return the stored lines at or above ``minimum`` level."""
        return [text for level, text in self._entries if level >= minimum]

    def clear(self) -> None:
        self._entries.clear()


class LogViewerDialog(QDialog):
    """Non modal window with the connection log, updated while it runs."""

    def __init__(self, buffer: LogBuffer, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connection log")
        self.setModal(False)
        self.resize(900, 480)
        self._buffer = buffer

        self._view = QPlainTextEdit()
        self._view.setObjectName("LogView")
        self._view.setReadOnly(True)
        # Wrapping keeps long SDK messages readable without horizontal scrolling.
        self._view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(9)
        self._view.setFont(font)

        self._level = QComboBox()
        for label, value in LEVELS:
            self._level.addItem(label, value)
        self._level.setCurrentIndex(0)
        self._level.currentIndexChanged.connect(self._reload)

        hint = QLabel("Log messages of the client and of xiaozhi-sdk.")
        hint.setObjectName("FormHint")

        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(self._copy)
        save_button = QPushButton("Save…")
        save_button.clicked.connect(self._save)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self._clear)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)

        level_row = QHBoxLayout()
        level_row.addWidget(QLabel("Show:"))
        level_row.addWidget(self._level)
        level_row.addWidget(hint, 1)
        level_row.addWidget(copy_button)
        level_row.addWidget(save_button)
        level_row.addWidget(clear_button)
        level_row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(self._view, 1)
        layout.addLayout(level_row)

        buffer.entryAdded.connect(self._on_entry)
        self._reload()

    # -- content ------------------------------------------------------------

    def _reload(self) -> None:
        self._view.setPlainText("\n".join(self._buffer.entries(self._level.currentData())))
        self._scroll_to_end()

    def _on_entry(self, level: int, text: str) -> None:
        if level < self._level.currentData():
            return
        self._view.appendPlainText(text)
        self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        bar = self._view.verticalScrollBar()
        if bar.value() >= bar.maximum() - 20:
            self._view.moveCursor(QTextCursor.End)
            bar.setValue(bar.maximum())
        self._view.horizontalScrollBar().setValue(0)

    # -- actions ------------------------------------------------------------

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self._view.toPlainText())

    def _save(self) -> None:
        name, _filter = QFileDialog.getSaveFileName(
            self, "Save connection log", str(Path.home() / "xiaozhi-talk.log"), "Log files (*.log)"
        )
        if not name:
            return
        try:
            Path(name).write_text(self._view.toPlainText() + "\n", encoding="utf-8")
        except OSError as exc:
            self._view.appendPlainText(f"Could not save the log: {exc}")

    def _clear(self) -> None:
        self._buffer.clear()
        self._view.clear()
