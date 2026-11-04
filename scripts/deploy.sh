#!/usr/bin/env bash
# Local entry point; all paths are resolved relative to this script, not $PWD.
set -euo pipefail
command -v python3 >/dev/null 2>&1 || { echo '需要本地 Python 3。' >&2; exit 1; }
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/deploy.py" "$@"
