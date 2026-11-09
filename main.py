"""Entry point for PyInstaller builds and ``python main.py``."""

from __future__ import annotations

import sys
from pathlib import Path

# Running straight from a checkout: prefer src/ over any older installed copy.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

from xiaozhi_talk.app import main  # noqa: E402 - src/ was put on the path above

if __name__ == "__main__":
    raise SystemExit(main())
