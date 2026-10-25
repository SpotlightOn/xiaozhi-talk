"""Xiaozhi Talk - a desktop chat client for Xiaozhi servers."""

# The locale must be English before Qt is imported, so this comes first.
from .localization import use_english_qt_strings

use_english_qt_strings()

# The compatibility shim must run before any module imports ``xiaozhi_sdk``.
from . import _compat  # noqa: F401, E402

__version__ = "0.1.0"
