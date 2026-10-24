#!/bin/bash
# Phase 3: turn the Python program into a folder that runs with no Python environment.
# Run ON THE LAPTOP (the bundle depends on the system it is built on).
#   cd ~/bluetooth-bridge && bash packaging/build_bundle.sh
set -e
cd "$(dirname "$0")/.."

source "$(dirname "$0")/../.venv/bin/activate"
pip install pyinstaller

echo "--- libraries the bundle will contain ---"
python -c "import bless, evdev, tkinter; print('bless, evdev, tkinter import OK')"
pip list 2>/dev/null | grep -i -E "bless|evdev|dbus|bleak|pyinstaller" || true
echo "-----------------------------------------"

# bless talks to BlueZ over D-Bus through a library PyInstaller cannot see by itself
# (dbus_next in older versions, dbus_fast in newer). The one that is not installed
# just prints a harmless warning.
pyinstaller --noconfirm --clean --onedir --name pcbridge \
  --collect-all evdev \
  --collect-all bless \
  --collect-submodules dbus_next \
  --collect-submodules dbus_fast \
  --hidden-import injector_linux \
  --hidden-import keymap_linux \
  gui.py

echo
echo "Built: dist/pcbridge/pcbridge"
echo "Test it with NO environment active:  deactivate; ./dist/pcbridge/pcbridge"
