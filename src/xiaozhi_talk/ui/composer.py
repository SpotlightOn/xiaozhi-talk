"""Text input with a send button: Enter sends, Shift+Enter adds a line."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .mic_button import MicButton

PLACEHOLDER = "Message Xiaozhi…   (Enter to send, Shift+Enter for a new line)"

AUTO_SPEAK_LABEL = "Auto-speak"
AUTO_SPEAK_TOOLTIP = (
    "Hands-free: the microphone stays open, Xiaozhi answers by itself after a "
    "short pause, and the reply is spoken aloud."
)
MUTE_LABEL = "Mute"
MUTE_TOOLTIP = "Mute the microphone without leaving hands-free mode (Ctrl+M)."


class Composer(QWidget):
    """The message box below the chat log."""

    sendRequested = Signal(str)
    micRequested = Signal()
    autoSpeakToggled = Signal(bool)
    muteToggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        self.editor = QPlainTextEdit()
        self.editor.setObjectName("ComposerEdit")
        self.editor.setPlaceholderText(PLACEHOLDER)
        self.editor.setTabChangesFocus(True)
        self.editor.installEventFilter(self)
        self.editor.setMinimumHeight(int(self.editor.fontMetrics().lineSpacing() * 3) + 26)

        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("SendButton")
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.setMinimumHeight(38)
        self.send_button.setAutoDefault(False)
        self.send_button.clicked.connect(self.submit)

        self.mic_button = MicButton()
        self.mic_button.clicked.connect(self.micRequested.emit)

        # Microphone above Send, both in one column next to the text box.
        buttons = QWidget()
        column = QVBoxLayout(buttons)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        column.addStretch(1)
        column.addWidget(self.mic_button)
        column.addWidget(self.send_button)

        layout = QHBoxLayout()
        layout.setContentsMargins(20, 8, 20, 4)
        layout.setSpacing(10)
        layout.addWidget(self.editor, 1)
        layout.addWidget(buttons, 0, Qt.AlignBottom)

        # One row below the text box: hands-free listening and a microphone mute.
        self.auto_speak = QCheckBox(AUTO_SPEAK_LABEL)
        self.auto_speak.setObjectName("AutoSpeak")
        self.auto_speak.setToolTip(AUTO_SPEAK_TOOLTIP)
        self.auto_speak.setCursor(Qt.PointingHandCursor)
        self.mute = QCheckBox(MUTE_LABEL)
        self.mute.setObjectName("MuteBox")
        self.mute.setToolTip(MUTE_TOOLTIP)
        self.mute.setCursor(Qt.PointingHandCursor)

        options = QHBoxLayout()
        options.setContentsMargins(22, 0, 20, 12)
        options.setSpacing(18)
        options.addWidget(self.auto_speak)
        options.addWidget(self.mute)
        options.addStretch(1)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addLayout(layout)
        root.addLayout(options)

        self.auto_speak.toggled.connect(self.autoSpeakToggled.emit)
        self.mute.toggled.connect(self.muteToggled.emit)
        self.editor.textChanged.connect(self._update_send_enabled)
        self._update_send_enabled()

    # -- public API ---------------------------------------------------------

    def text(self) -> str:
        return self.editor.toPlainText()

    def set_recording(self, recording: bool) -> None:
        """Show the microphone button in its recording state."""
        self.mic_button.set_recording(recording)

    def set_text(self, text: str) -> None:
        self.editor.setPlainText(text)
        self.editor.moveCursor(QTextCursor.End)
        self.focus_editor()

    def focus_editor(self) -> None:
        self.editor.setFocus(Qt.OtherFocusReason)

    def submit(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            return
        self.editor.clear()
        self.sendRequested.emit(text)

    # -- internals ----------------------------------------------------------

    def _update_send_enabled(self) -> None:
        self.send_button.setEnabled(bool(self.editor.toPlainText().strip()))

    def eventFilter(self, obj, event: QEvent) -> bool:  # noqa: N802 - Qt naming
        if obj is self.editor and event.type() == QEvent.KeyPress:
            key_event = event
            assert isinstance(key_event, QKeyEvent)
            if key_event.key() in (Qt.Key_Return, Qt.Key_Enter):
                modifiers = key_event.modifiers()
                if modifiers & (Qt.ShiftModifier | Qt.ControlModifier):
                    return False  # let the editor insert a new line
                self.submit()
                return True
        return super().eventFilter(obj, event)
