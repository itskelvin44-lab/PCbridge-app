#!/bin/bash
# Phase 4: put dist/pcbridge and the system files into one installable .deb.
#   bash packaging/build_deb.sh            (version 0.1.0)
#   bash packaging/build_deb.sh 0.2.0
set -e
cd "$(dirname "$0")/.."
VERSION="${1:-0.1.0}"
BUNDLE="dist/pcbridge"
[ -x "$BUNDLE/pcbridge" ] || { echo "Missing $BUNDLE/pcbridge. Run packaging/build_bundle.sh first."; exit 1; }

S="build/deb"
rm -rf "$S"
mkdir -p "$S/DEBIAN" "$S/opt" "$S/usr/bin" "$S/usr/share/applications" \
         "$S/usr/share/icons/hicolor/256x256/apps" "$S/usr/share/icons/hicolor/48x48/apps" \
         "$S/usr/lib/udev/rules.d" "$S/usr/lib/modules-load.d"

cp -r "$BUNDLE" "$S/opt/pcbridge"
ln -s /opt/pcbridge/pcbridge "$S/usr/bin/pcbridge"
install -m 644 packaging/pcbridge.desktop   "$S/usr/share/applications/pcbridge.desktop"
install -m 644 packaging/pcbridge-256.png   "$S/usr/share/icons/hicolor/256x256/apps/pcbridge.png"
install -m 644 packaging/pcbridge-48.png    "$S/usr/share/icons/hicolor/48x48/apps/pcbridge.png"
install -m 644 packaging/99-pcbridge.rules  "$S/usr/lib/udev/rules.d/99-pcbridge.rules"
install -m 644 packaging/pcbridge.conf      "$S/usr/lib/modules-load.d/pcbridge.conf"

install -m 755 packaging/debian/postinst "$S/DEBIAN/postinst"
install -m 755 packaging/debian/postrm   "$S/DEBIAN/postrm"
SIZE=$(du -sk "$S/opt" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" packaging/debian/control > "$S/DEBIAN/control"

OUT="pcbridge_${VERSION}_amd64.deb"
dpkg-deb --build --root-owner-group "$S" "$OUT"
echo
echo "Built: $OUT"
echo "Install with:  sudo apt install ./$OUT"
