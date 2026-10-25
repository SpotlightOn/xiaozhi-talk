"""Scrollable chat log with message bubbles."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from ..text import join_chunks


class NoticeLabel(QLabel):
    """A status line in the chat log.

    Wrapping is driven by an explicit width: a label that only had a maximum
    width was laid out with the height of a single line, so longer messages
    (connection errors, for example) were cut off at the bottom.
    """

    MAX_WIDTH = 720

    def __init__(
        self,
        text: str,
        *,
        error: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("ChatError" if error else "ChatNotice")
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter if error else Qt.AlignHCenter)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self._width = 0

    def fit_width(self, width: int) -> None:
        """Wrap the text to ``width`` pixels, staying readable when very wide."""
        width = max(240, min(width, self.MAX_WIDTH))
        if width != self._width:
            self._width = width
            self.setFixedWidth(width)


class MessageBubble(QFrame):
    """One chat bubble, either from the user or from Xiaozhi."""

    HORIZONTAL_MARGIN = 26

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.role = role
        self.setObjectName("UserBubble" if role == "user" else "AssistantBubble")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 8, 13, 10)
        layout.setSpacing(2)

        self._role_label = QLabel("Xiaozhi")
        self._role_label.setObjectName("BubbleRole")
        self._role_label.setVisible(role == "assistant")
        layout.addWidget(self._role_label)

        self._text_label = QLabel("")
        self._text_label.setWordWrap(True)
        self._text_label.setTextFormat(Qt.PlainText)
        self._text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._text_label)

        self._text = ""
        self._emotion = ""
        self._max_width = 480

    # -- content ------------------------------------------------------------

    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        self._text = text
        self._text_label.setText(text)
        self._fit_label_to_text()

    def append_text(self, chunk: str) -> None:
        self.set_text(join_chunks(self._text, chunk))

    def set_emotion(self, emotion: str) -> None:
        self._emotion = emotion.strip()
        self._role_label.setText(f"Xiaozhi {self._emotion}".strip())

    # -- sizing -------------------------------------------------------------

    def set_max_width(self, width: int) -> None:
        """Limit the bubble width; the text wraps inside that limit."""
        self._max_width = max(220, width)
        self.setMaximumWidth(self._max_width)
        self._fit_label_to_text()

    def _fit_label_to_text(self) -> None:
        """Shrink the bubble to the text instead of wrapping it too early."""
        available = max(80, self._max_width - self.HORIZONTAL_MARGIN)
        metrics = self._text_label.fontMetrics()
        bounds = metrics.boundingRect(
            QRect(0, 0, available, 100_000), Qt.TextWordWrap, self._text or " "
        )
        self._text_label.setFixedWidth(min(available, max(48, bounds.width() + 4)))


class ChatView(QScrollArea):
    """Holds the conversation and keeps the newest message in view."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatScroll")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._canvas = QWidget()
        self._canvas.setObjectName("ChatCanvas")
        self._layout = QVBoxLayout(self._canvas)
        self._layout.setContentsMargins(20, 18, 20, 18)
        self._layout.setSpacing(10)
        self._layout.addStretch(1)
        self.setWidget(self._canvas)

        self._items: list[QWidget] = []
        self._bubbles: list[MessageBubble] = []
        self._notices: list[NoticeLabel] = []
        self._reply: MessageBubble | None = None
        self._emotion = ""
        self._stick_to_bottom = True

        self._empty_hint = QLabel(
            "No messages yet.\nType below and press Enter to talk to Xiaozhi."
        )
        self._empty_hint.setObjectName("EmptyHint")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        # A spacer above the hint centres it vertically, but only while the
        # conversation is empty.
        self._top_spacer = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Minimum)
        self._layout.insertSpacerItem(0, self._top_spacer)
        self._layout.insertWidget(1, self._empty_hint, 0, Qt.AlignHCenter)

        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(0)
        self._scroll_timer.timeout.connect(self._scroll_to_bottom)
        self._update_empty_hint()

    # -- content ------------------------------------------------------------

    def messages(self) -> list[tuple[str, str]]:
        """Return ``(role, text)`` for every bubble, in display order."""
        return [(bubble.role, bubble.text) for bubble in self._bubbles]

    def add_user_message(self, text: str) -> MessageBubble:
        bubble = MessageBubble("user")
        bubble.set_text(text)
        self._add_bubble(bubble, Qt.AlignRight)
        return bubble

    def add_notice(self, text: str, *, error: bool = False) -> NoticeLabel:
        """Add a status line, highlighted and left aligned when it is an error."""
        label = NoticeLabel(text, error=error)
        label.fit_width(self._notice_width())
        self._notices.append(label)
        self._add_widget(label, Qt.AlignHCenter)
        return label

    def start_reply(self) -> MessageBubble:
        """Open a new bubble for the reply that is streaming in."""
        if self._reply is not None and not self._reply.text:
            return self._reply
        bubble = MessageBubble("assistant")
        if self._emotion:
            bubble.set_emotion(self._emotion)
        self._add_bubble(bubble, Qt.AlignLeft)
        self._reply = bubble
        return bubble

    def append_reply(self, chunk: str) -> None:
        if self._reply is None:
            self.start_reply()
        assert self._reply is not None
        self._reply.append_text(chunk)
        self._auto_scroll()

    def finish_reply(self) -> None:
        """Close the current reply, dropping it if nothing was streamed."""
        bubble, self._reply = self._reply, None
        if bubble is None:
            return
        if not bubble.text.strip():
            self._remove_widget(bubble)
            return
        self._auto_scroll()

    def set_emotion(self, emotion: str) -> None:
        self._emotion = emotion.strip()
        if self._reply is not None:
            self._reply.set_emotion(self._emotion)

    def clear_messages(self) -> None:
        self._reply = None
        for widget in self._items:
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._items.clear()
        self._bubbles.clear()
        self._notices.clear()
        self._update_empty_hint()

    # -- internals ----------------------------------------------------------

    def _add_bubble(self, bubble: MessageBubble, alignment: Qt.AlignmentFlag) -> None:
        self._bubbles.append(bubble)
        bubble.set_max_width(self._bubble_width())
        self._add_widget(bubble, alignment)

    def _add_widget(self, widget: QWidget, alignment: Qt.AlignmentFlag) -> None:
        self._items.append(widget)
        self._layout.insertWidget(self._layout.count() - 1, widget, 0, alignment)
        self._update_empty_hint()
        self._auto_scroll()

    def _remove_widget(self, widget: QWidget) -> None:
        if widget in self._bubbles:
            self._bubbles.remove(widget)
        if widget in self._notices:
            self._notices.remove(widget)
        if widget in self._items:
            self._items.remove(widget)
        self._layout.removeWidget(widget)
        widget.deleteLater()
        self._update_empty_hint()

    def _update_empty_hint(self) -> None:
        is_empty = not self._items
        self._empty_hint.setVisible(is_empty)
        # Give the top spacer half of the free height so the hint sits in the
        # middle; without content it would otherwise stick to the top.
        self._layout.setStretch(0, 1 if is_empty else 0)
        self._layout.invalidate()

    def _bubble_width(self) -> int:
        return max(300, int(self.viewport().width() * 0.72))

    def _notice_width(self) -> int:
        return max(240, self.viewport().width() - 40)

    def _on_scrolled(self, value: int) -> None:
        bar = self.verticalScrollBar()
        self._stick_to_bottom = value >= bar.maximum() - 12

    def _auto_scroll(self) -> None:
        if self._stick_to_bottom:
            self._scroll_timer.start()

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        bubble_width = self._bubble_width()
        for bubble in self._bubbles:
            bubble.set_max_width(bubble_width)
        notice_width = self._notice_width()
        for notice in self._notices:
            notice.fit_width(notice_width)
