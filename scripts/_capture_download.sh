#!/usr/bin/env bash
# Move the most-recently-modified STL from ~/Downloads (newer than a sentinel)
# into models/tinkercad_sample/<NN_id>.stl. Retries for up to ~15s.
#
# Usage: _capture_download.sh <NN> <design-id-slug> <sentinel-file>
set -u
NN="$1"
SLUG="$2"
SENTINEL="$3"
DEST_DIR="${4:-models/tinkercad_single}"
DEST="${DEST_DIR}/${NN}_${SLUG}.stl"

for i in $(seq 1 15); do
  FOUND=$(find ~/Downloads -maxdepth 1 -name '*.stl' -newer "$SENTINEL" -print -quit 2>/dev/null)
  if [ -n "$FOUND" ] && [ -f "$FOUND" ]; then
    # Wait briefly for write to settle
    sleep 0.5
    SIZE1=$(stat -f %z "$FOUND" 2>/dev/null || echo 0)
    sleep 0.4
    SIZE2=$(stat -f %z "$FOUND" 2>/dev/null || echo 0)
    if [ "$SIZE1" = "$SIZE2" ] && [ "$SIZE2" -gt 200 ]; then
      mv "$FOUND" "$DEST"
      echo "OK $DEST ($SIZE2 bytes)"
      exit 0
    fi
  fi
  sleep 1
done
echo "FAIL no new STL detected for $NN $SLUG" >&2
exit 1
