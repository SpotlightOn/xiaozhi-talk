"""Tests for the connection log buffer and its window."""

from __future__ import annotations

import logging

from xiaozhi_talk.ui.log_viewer import LogBuffer, LogViewerDialog


def _attach(buffer: LogBuffer, name: str = "xiaozhi_talk.test") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(buffer)
    return logger


def test_buffer_keeps_only_application_records(qapp) -> None:
    buffer = LogBuffer()
    ours = _attach(buffer)
    foreign = _attach(buffer, "somewhere.else")
    try:
        ours.info("connection established")
        foreign.info("not interesting")
    finally:
        ours.removeHandler(buffer)
        foreign.removeHandler(buffer)

    lines = buffer.entries(logging.DEBUG)
    assert any("connection established" in line for line in lines)
    assert not any("not interesting" in line for line in lines)


def test_buffer_reports_new_records(qapp) -> None:
    buffer = LogBuffer()
    seen: list[tuple[int, str]] = []
    buffer.entryAdded.connect(lambda level, text: seen.append((level, text)))

    logger = _attach(buffer)
    try:
        logger.warning("socket closed")
    finally:
        logger.removeHandler(buffer)

    assert seen and seen[0][0] == logging.WARNING
    assert "socket closed" in seen[0][1]


def test_buffer_is_limited(qapp) -> None:
    buffer = LogBuffer(capacity=5)
    logger = _attach(buffer)
    try:
        for index in range(20):
            logger.info("line %s", index)
    finally:
        logger.removeHandler(buffer)

    lines = buffer.entries(logging.DEBUG)
    assert len(lines) == 5
    assert "line 19" in lines[-1]


def test_dialog_shows_old_and_new_records(qapp) -> None:
    buffer = LogBuffer()
    logger = _attach(buffer)
    try:
        logger.info("first line")
        dialog = LogViewerDialog(buffer)
        dialog.show()

        assert "first line" in dialog._view.toPlainText()

        logger.info("second line")
        qapp.processEvents()
        assert "second line" in dialog._view.toPlainText()
    finally:
        logger.removeHandler(buffer)


def test_dialog_filters_by_level(qapp) -> None:
    buffer = LogBuffer()
    logger = _attach(buffer)
    try:
        logger.debug("a detail")
        logger.error("a problem")
        dialog = LogViewerDialog(buffer)
        dialog.show()

        dialog._level.setCurrentIndex(3)  # Errors only
        text = dialog._view.toPlainText()
        assert "a problem" in text
        assert "a detail" not in text
    finally:
        logger.removeHandler(buffer)


def test_dialog_can_clear_the_buffer(qapp) -> None:
    buffer = LogBuffer()
    logger = _attach(buffer)
    try:
        logger.info("something happened")
        dialog = LogViewerDialog(buffer)
        dialog._clear()

        assert buffer.entries() == []
        assert dialog._view.toPlainText() == ""
    finally:
        logger.removeHandler(buffer)
