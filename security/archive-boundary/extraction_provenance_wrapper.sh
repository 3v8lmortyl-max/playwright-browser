#!/usr/bin/env bash
# Narrowly scoped privileged entrypoint for T10 (extraction provenance).
# The only command T10 runs via sudo. Sets the confinement base and execs the
# scenario runner; it performs no other action.
#
# Usage: extraction_provenance_wrapper.sh <base> <helper-flags...>
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "extraction_provenance_wrapper: expected <base>" >&2
  exit 2
fi
BASE="$1"; shift
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SELF_DIR/extraction_provenance_helper.py"
[[ -f "$HELPER" ]] || { echo "helper not found: $HELPER" >&2; exit 2; }
PY="$(command -v python3)"
export ARCHIVE_BOUNDARY_BASE="$BASE"
exec "$PY" "$HELPER" "$@"
