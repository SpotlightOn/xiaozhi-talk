#!/usr/bin/env bash
# Start Xiaozhi Talk, preparing the virtual environment on first use.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$here/.venv"

if [ ! -x "$venv/bin/python" ]; then
    echo "Creating virtual environment in $venv"
    # Reuse distribution packages such as PySide6 when they are installed.
    python3 -m venv --system-site-packages "$venv"
    "$venv/bin/pip" install --quiet --upgrade pip
    "$venv/bin/pip" install --quiet -e "$here"
fi

exec "$venv/bin/python" -m xiaozhi_talk "$@"
