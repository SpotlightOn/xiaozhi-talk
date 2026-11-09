#!/usr/bin/env bash
# Install Xiaozhi Talk for the current user; no root and no system paths.
#
#   ./install.sh              install or update the app, menu entry and launcher
#   ./install.sh --uninstall  remove the app, menu entry, icon and launcher
#
# The application gets its own virtual environment below
# $XDG_DATA_HOME/xiaozhi-talk and is copied there by pip. Nothing in the home
# directory points back at this checkout, so it can be moved or deleted once
# the installation is done. Run this script again to update the installation.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_home="${XDG_BIN_HOME:-$HOME/.local/bin}"
app_root="$data_home/xiaozhi-talk"
venv="$app_root/venv"
app_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/scalable/apps"
desktop_file="$app_dir/xiaozhi-talk.desktop"
icon_file="$icon_dir/xiaozhi-talk.svg"
launcher="$bin_home/xiaozhi-talk"

usage() {
    echo "usage: $0 [--uninstall]" >&2
    exit 2
}

refresh_menu() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$app_dir" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q "$data_home/icons/hicolor" >/dev/null 2>&1 || true
    fi
}

check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        echo "python3 was not found on PATH." >&2
        exit 1
    fi
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        echo "Python 3.10 or newer is required, found: $(python3 -V 2>&1)" >&2
        exit 1
    fi
}

prepare_venv() {
    if [ -x "$venv/bin/python" ] && "$venv/bin/python" -c "import sys" >/dev/null 2>&1; then
        return
    fi
    if [ -d "$venv" ]; then
        echo "Replacing the unusable virtual environment in $venv"
        rm -rf "$venv"
    fi
    echo "Creating the virtual environment in $venv"
    # Distribution packages such as PySide6 are reused when they are installed.
    python3 -m venv --system-site-packages "$venv"
}

uninstall() {
    rm -f "$desktop_file" "$icon_file" "$launcher"
    if [ -d "$app_root" ]; then
        rm -rf "$app_root"
        echo "Removed the installation in $app_root"
    fi
    refresh_menu
    echo "Removed the menu entry, the icon and the launcher."
}

install_app() {
    check_python
    prepare_venv

    echo "Installing Xiaozhi Talk into $venv"
    "$venv/bin/python" -m pip install --quiet --upgrade pip

    # Build a wheel and install that copy. Installing the directory directly
    # would record this checkout in the environment metadata; a wheel install
    # leaves no reference to it behind.
    build_dir="$(mktemp -d)"
    trap 'rm -rf "$build_dir"' EXIT
    "$venv/bin/python" -m pip wheel --quiet --no-deps --wheel-dir "$build_dir" "$here"
    wheel="$(find "$build_dir" -maxdepth 1 -name 'xiaozhi_talk-*.whl' -print -quit)"
    if [ -z "$wheel" ]; then
        echo "Could not build a wheel from $here" >&2
        exit 1
    fi
    # Reinstalling on every run keeps updates predictable.
    "$venv/bin/python" -m pip uninstall --quiet --yes xiaozhi-talk >/dev/null 2>&1 || true
    "$venv/bin/python" -m pip install --quiet "${wheel}[keyring]"
    rm -rf "$build_dir"
    trap - EXIT

    mkdir -p "$app_dir" "$icon_dir" "$bin_home"

    cat > "$launcher" <<EOF
#!/usr/bin/env bash
# Created by install.sh; runs the copy installed in $venv
exec "$venv/bin/xiaozhi-talk" "\$@"
EOF
    chmod +x "$launcher"

    cp "$here/assets/xiaozhi-talk.svg" "$icon_file"
    sed -e "s|@EXEC@|$launcher|g" \
        -e "s|@ICON@|xiaozhi-talk|g" \
        "$here/packaging/xiaozhi-talk.desktop" > "$desktop_file"
    chmod 644 "$desktop_file"

    refresh_menu

    echo "Installed Xiaozhi Talk for $(id -un):"
    echo "  environment: $venv"
    echo "  menu entry:  $desktop_file"
    echo "  icon:        $icon_file"
    echo "  launcher:    $launcher"
    echo
    echo "Start it from the application menu or with: xiaozhi-talk"
    echo "Run this script again to update the installation."
}

case "${1:-}" in
    "") install_app ;;
    --uninstall) uninstall ;;
    -h|--help) usage ;;
    *) usage ;;
esac
