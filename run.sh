#!/usr/bin/env bash
# Start Xiaozhi Talk, preparing the virtual environment on first use.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$here/.venv"

prepare_venv() {
    # A virtual environment is bound to its directory. If the checkout was
    # moved or copied, or the environment is damaged, build it again instead of
    # failing with an import error.
    if [ -d "$venv" ] && ! "$venv/bin/python" -c "import xiaozhi_talk" >/dev/null 2>&1; then
        echo "Refreshing the virtual environment in $venv"
        rm -rf "$venv"
    fi

    if [ ! -x "$venv/bin/python" ]; then
        echo "Creating virtual environment in $venv"
        # Reuse distribution packages such as PySide6 when they are installed.
        python3 -m venv --system-site-packages "$venv"
        "$venv/bin/python" -m pip install --quiet --upgrade pip
        "$venv/bin/python" -m pip install --quiet -e "$here"
    fi
}

prepare_venv

exec "$venv/bin/python" -m xiaozhi_talk "$@"
