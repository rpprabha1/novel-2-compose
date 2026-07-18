#!/usr/bin/env bash
# Thin wrapper around scripts/setup.py for Unix users who don't want `make`.
# See that file for the actual hardware-detection/install logic.
# Usage: ./setup.sh [--core-only|--dry-run|--apply-model-config|...]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "No python interpreter found on PATH (tried python3, python). Install Python 3 first." >&2
    exit 1
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/setup.py" "$@"
