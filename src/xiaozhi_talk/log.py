"""Logging configuration for the desktop client."""

from __future__ import annotations

import logging
import os

LEVEL_ENV = "XIAOZHI_TALK_LOG_LEVEL"
DEFAULT_LEVEL = logging.WARNING

#: Loggers whose messages are kept in memory for the connection log.
CAPTURED_LOGGERS = ("xiaozhi_talk", "xiaozhi_sdk")

#: Libraries that log a lot at INFO/DEBUG level.
NOISY_LOGGERS = ("websockets", "aiohttp", "asyncio", "urllib3")

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
CONSOLE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_configured = False


class ConsoleFilter(logging.Filter):
    """Keep the SDK's chatter off stderr while it stays in the log viewer.

    ``xiaozhi_sdk`` warns about self-hosted servers in Chinese and is generally
    talkative; the window reports connection problems itself, and the log viewer
    keeps the details for whoever wants them.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("xiaozhi_sdk"):
            return record.levelno >= logging.ERROR
        return True


def configure_logging() -> None:
    """Configure logging once, honouring ``XIAOZHI_TALK_LOG_LEVEL``.

    The root logger runs at DEBUG so that the in-memory connection log sees
    everything; the console handler decides what is printed.
    """
    global _configured
    if _configured:
        return
    _configured = True

    level_name = os.environ.get(LEVEL_ENV, "").strip().upper()
    level = getattr(logging, level_name, DEFAULT_LEVEL) if level_name else DEFAULT_LEVEL

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    console.addFilter(ConsoleFilter())

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)

    for name in CAPTURED_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
