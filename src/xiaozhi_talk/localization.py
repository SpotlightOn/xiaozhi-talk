"""Keep Qt's own strings English, whatever the desktop locale is.

Qt takes both its translations (standard buttons, context menus) and its
shortcut names ("Ctrl" versus "Strg") from the process locale, and it does so
while ``PySide6`` is being imported.  The variables therefore have to be set
before that import happens, which is why :func:`use_english_qt_strings` runs
from the package initialisation.
"""

from __future__ import annotations

import os

#: Locale that makes Qt fall back to its built in English texts.
QT_LOCALE = "C.UTF-8"


def use_english_qt_strings() -> None:
    """Force the English C locale for Qt's built in strings.

    Calling this again later is harmless but has no effect: Qt has already
    picked its translations by then.
    """
    os.environ["LANGUAGE"] = "C"
    os.environ["LC_ALL"] = QT_LOCALE
