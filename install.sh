#!/usr/bin/env bash
# Install Xiaozhi Talk for the current user; no root and no system paths.
#
#   ./install.sh              install or update the menu entry
#   ./install.sh --uninstall  remove the menu entry, icon and launcher
#
# The application itself stays in this directory: the launcher and the menu
# entry point back at run.sh, so updating the checkout is enough.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_home="${XDG_BIN_HOME:-$HOME/.local/bin}"
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

uninstall() {
    rm -f "$desktop_file" "$icon_file" "$launcher"
    refresh_menu
    echo "Removed the menu entry and the launcher."
    echo "The application directory is untouched: $here"
}

install_app() {
    if [ ! -x "$here/.venv/bin/python" ]; then
        echo "Preparing the virtual environment in $here/.venv"
        python3 -m venv --system-site-packages "$here/.venv"
        "$here/.venv/bin/pip" install --quiet --upgrade pip
        "$here/.venv/bin/pip" install --quiet -e "$here"
    fi

    mkdir -p "$app_dir" "$icon_dir" "$bin_home"

    cat > "$launcher" <<EOF
#!/usr/bin/env bash
# Created by install.sh in $here
exec "$here/run.sh" "\$@"
EOF
    chmod +x "$launcher"

    cp "$here/assets/xiaozhi-talk.svg" "$icon_file"
    sed -e "s|@EXEC@|$launcher|g" \
        -e "s|@ICON@|xiaozhi-talk|g" \
        "$here/packaging/xiaozhi-talk.desktop" > "$desktop_file"
    chmod 644 "$desktop_file"

    refresh_menu

    echo "Installed Xiaozhi Talk for $(id -un):"
    echo "  menu entry: $desktop_file"
    echo "  icon:       $icon_file"
    echo "  launcher:   $launcher"
    echo
    echo "Start it from the application menu or with: xiaozhi-talk"
}

case "${1:-}" in
    "") install_app ;;
    --uninstall) uninstall ;;
    -h|--help) usage ;;
    *) usage ;;
esac
