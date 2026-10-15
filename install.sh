#!/bin/sh
# Puts AIcove on this machine: checks what it needs, links the aicove command onto the PATH,
# adds a menu entry and a desktop icon. Nothing is copied: the program runs from this folder,
# so "git pull" here is the update. Run it again after moving the folder.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)

python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
    echo "AIcove needs Python 3.11 or newer (this is $(python3 --version 2>&1))."; exit 1; }
python3 -c 'import PyQt6.QtWidgets' 2>/dev/null || {
    echo "PyQt6 for Python is missing."
    echo "  Manjaro / Arch:  sudo pacman -S python-pyqt6"
    echo "  Debian / Ubuntu: sudo apt install python3-pyqt6"
    echo "  Fedora:          sudo dnf install python3-pyqt6"
    echo "  Slackware:       python3-PyQt6 from SlackBuilds.org (sbopkg)"
    exit 1; }
python3 -c 'import markdown, pygments' 2>/dev/null || {
    echo "python-markdown and Pygments are missing (the chat renders Markdown and code with them)."
    echo "  Arch: sudo pacman -S python-markdown python-pygments   Debian: sudo apt install python3-markdown python3-pygments"
    exit 1; }
command -v curl >/dev/null || echo "Note: curl is missing; \"Add model\" needs it for downloads."

# The command: ~/bin if that is on the PATH, else ~/.local/bin.
case ":$PATH:" in
    *":$HOME/bin:"*) BIN="$HOME/bin" ;;
    *) BIN="$HOME/.local/bin" ;;
esac
mkdir -p "$BIN"
ln -sf "$HERE/aicove" "$BIN/aicove"
echo "Command:      $BIN/aicove"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "              $BIN is not on your PATH; add it to use aicove in a terminal." ;;
esac

entry() {
    printf '[Desktop Entry]\nType=Application\nName=%s\nComment=Local AI models and personas: start, stop, settings\nExec=%s\nIcon=%s\nTerminal=false\nCategories=Utility;\nStartupWMClass=aicove\n' "$1" "$HERE/aicove" "$HERE/cove/data/aicove.svg"
}

APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
DESKTOP=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")
entry "AIcove" > "$APPS/aicove.desktop"
echo "Menu entry:   $APPS/aicove.desktop"

if [ -d "$DESKTOP" ]; then
    NAME="AIcove"
    # An icon of that name that starts something else is left alone.
    if [ -e "$DESKTOP/$NAME.desktop" ] && ! grep -q "^Exec=$HERE/aicove\$" "$DESKTOP/$NAME.desktop"; then
        NAME="AIcove (new)"
    fi
    entry "$NAME" > "$DESKTOP/$NAME.desktop"
    chmod +x "$DESKTOP/$NAME.desktop"
    # Xfce asks "untrusted launcher?" on the first click unless the file carries its checksum.
    if command -v gio >/dev/null && command -v sha256sum >/dev/null; then
        gio set -t string "$DESKTOP/$NAME.desktop" metadata::xfce-exe-checksum \
            "$(sha256sum "$DESKTOP/$NAME.desktop" | cut -d' ' -f1)" 2>/dev/null || true
    fi
    echo "Desktop icon: $DESKTOP/$NAME.desktop"
fi

echo
echo "Start it with the icon or with: aicove"
echo "The first start opens the Settings tab with what it found on this machine."
