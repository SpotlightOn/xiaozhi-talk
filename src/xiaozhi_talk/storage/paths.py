"""Filesystem locations and safe file writing."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR_NAME = "xiaozhi-talk"

#: Environment variable that overrides the configuration directory.
CONFIG_DIR_ENV = "XIAOZHI_TALK_CONFIG_DIR"


def config_dir() -> Path:
    """Return the directory that holds configuration and credentials."""
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()

    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(Path.home(), ".config")
    return Path(base) / APP_DIR_NAME


def write_private_file(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path`` with owner-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")

    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)

    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - exotic filesystems
        logger.debug("Could not restrict permissions of %s", path)
