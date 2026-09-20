#!/usr/bin/env bash
# Convenience wrapper — just runs the zero-dependency Python setup wizard.
# Usage: ./setup.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required (no other dependencies) — install it and re-run this script." >&2
    exit 1
fi

exec python3 setup_wizard.py
