"""Errors raised by the credential store."""

from __future__ import annotations


class StorageError(RuntimeError):
    """Raised when the credential store cannot be read or written."""
