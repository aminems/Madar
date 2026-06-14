#!/usr/bin/env bash
# Sync the MADAR project to the Linux build box and run a make target there.
# The repo lives on macOS (git home); RTL build/sim runs on Linux (Verilator).
#
#   scripts/remote-build.sh           # default: make test
#   scripts/remote-build.sh lint      # any make target
#   MADAR_HOST=user@host scripts/remote-build.sh test
set -euo pipefail

HOST="${MADAR_HOST:-amine@192.168.1.68}"
REMOTE_DIR="${MADAR_REMOTE_DIR:-madar-build}"
TARGET="${1:-test}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

rsync -az --delete --exclude obj_dir --exclude '*.vcd' \
  "$HERE/" "$HOST:$REMOTE_DIR/"

ssh "$HOST" "cd '$REMOTE_DIR' && make $TARGET"
