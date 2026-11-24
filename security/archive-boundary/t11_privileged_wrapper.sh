#!/usr/bin/env bash
# Narrowly scoped privileged entrypoint for T11 (timestamp and content provenance).
# The only command T11 runs via sudo. It sets the confinement base and execs the
# scenario runner; it performs no other action and receives no credentials.
#
# Usage: t11_privileged_wrapper.sh <base> <helper-flags...>
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "t11_privileged_wrapper: expected <base>" >&2
  exit 2
fi
BASE="$1"; shift
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SELF_DIR/t11_privileged_helper.py"
[[ -f "$HELPER" ]] || { echo "helper not found: $HELPER" >&2; exit 2; }
PY="$(command -v python3)"
export ARCHIVE_BOUNDARY_BASE="$BASE"
exec "$PY" "$HELPER" "$@"
