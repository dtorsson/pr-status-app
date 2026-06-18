#!/usr/bin/env bash
# Build PR Status.app as an arm64-only (Apple silicon) bundle.
#
# py2app's arch=arm64 only thins the apploader stub; the Python framework and
# every bundled .so/.dylib are copied in as universal2 (x86_64 + arm64). The
# leftover x86_64 slices are what make macOS show the "Support Ending for
# Intel-based Apps" notice. So after py2app we lipo-thin every Mach-O to arm64
# and re-sign (thinning invalidates the signatures).
set -euo pipefail

cd "$(dirname "$0")"
PY="${PYTHON:-.venv/bin/python3}"
APP="dist/PR Status.app"

echo "==> clean"
rm -rf build dist

echo "==> py2app (arch=arm64)"
"$PY" setup.py py2app --arch arm64

echo "==> thin every Mach-O to arm64"
thinned=0
while IFS= read -r f; do
  archs=$(lipo -archs "$f" 2>/dev/null) || continue
  [ -z "$archs" ] && continue
  case "$archs" in
    *x86_64*)
      lipo "$f" -thin arm64 -output "$f.__thin"
      cat "$f.__thin" > "$f"      # preserve mode/exec bit by overwriting in place
      rm -f "$f.__thin"
      thinned=$((thinned+1)) ;;
  esac
done < <(find "$APP" -type f ! -path '*/__pycache__/*')
echo "    thinned $thinned binaries"

echo "==> ad-hoc re-sign"
codesign --force --deep -s - "$APP"
codesign --verify --deep --strict "$APP" && echo "    signature OK"

echo "==> verify no x86_64 remains"
if find "$APP" -type f ! -path '*/__pycache__/*' -exec sh -c \
   'lipo -archs "$1" 2>/dev/null | grep -q x86_64' _ {} \; -print | grep -q .; then
  echo "    FAIL: x86_64 slices still present" >&2; exit 1
fi
echo "    arm64-only ✓  ($(du -sh "$APP" | cut -f1))"
