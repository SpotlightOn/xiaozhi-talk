"""The button that records a voice message.

The icon is painted here instead of shipping an image file: it has to follow
the button's colours in both states, and two small drawings are cheaper than
two PNGs in the package.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget

IDLE_TEXT = "Speak"
RECORDING_TEXT = "Stop"
IDLE_TOOLTIP = "Record a voice message (Ctrl+Shift+Space)"
RECORDING_TOOLTIP = "Stop recording and send it (Ctrl+Shift+Space)"

IDLE_COLOUR = "#59606a"
RECORDING_COLOUR = "#b23c2f"


class MicButton(QPushButton):
    """Microphone button with an idle and a recording look."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MicButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(38)
        self.setMinimumWidth(96)
        self.setIconSize(QSize(15, 15))
        self._recording = False
        self.setProperty("recording", False)
        self.set_recording(False)

    @property
    def recording(self) -> bool:
        return self._recording

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self.setText(RECORDING_TEXT if recording else IDLE_TEXT)
        self.setToolTip(RECORDING_TOOLTIP if recording else IDLE_TOOLTIP)
        self.setIcon(QIcon(_icon(recording)))
        if bool(self.property("recording")) != recording:
            self.setProperty("recording", recording)
            # Re-apply the style sheet for the new property value.
            style = self.style()
            style.unpolish(self)
            style.polish(self)


def _icon(recording: bool) -> QPixmap:
    """Draw the microphone, or a red dot while recording."""
    size = 15
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    colour = QColor(RECORDING_COLOUR if recording else IDLE_COLOUR)
    painter.setPen(Qt.NoPen)
    painter.setBrush(colour)

    if recording:
        painter.drawEllipse(QRectF(3.5, 3.5, 8.0, 8.0))
        painter.end()
        return pixmap

    # capsule
    painter.drawRoundedRect(QRectF(5.4, 1.4, 4.2, 7.6), 2.1, 2.1)
    # cradle and stand
    pen = QPen(colour, 1.3)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRectF(3.0, 4.2, 9.0, 8.0), 180 * 16, 180 * 16)
    painter.drawLine(7.5, 11.9, 7.5, 14.0)
    painter.drawLine(5.4, 14.0, 9.6, 14.0)
    painter.end()
    return pixmap
