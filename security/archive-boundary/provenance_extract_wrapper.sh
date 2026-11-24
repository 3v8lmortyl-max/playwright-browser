#!/usr/bin/env bash
# Narrowly scoped privileged entrypoint for the T9 ownership-provenance helper.
#
# This is the only command T9 runs via sudo. A sudoers rule can grant the
# unprivileged orchestrator permission to run just this script as root (see
# sudoers.d/archive-boundary.example). It sets the confinement base and execs the
# Python helper; it performs no other action. All policy (confinement, allowlisted
# extractor, destination setup, optional privilege drop) is enforced by
# provenance_extract_helper.py.
#
# Usage: provenance_extract_wrapper.sh <base> <helper-flags...>
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "provenance_extract_wrapper: expected at least <base>" >&2
  exit 2
fi

BASE="$1"
shift

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SELF_DIR/provenance_extract_helper.py"
if [[ ! -f "$HELPER" ]]; then
  echo "provenance_extract_wrapper: helper not found: $HELPER" >&2
  exit 2
fi

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "provenance_extract_wrapper: python3 not found on PATH" >&2
  exit 2
fi

export ARCHIVE_BOUNDARY_BASE="$BASE"
exec "$PY" "$HELPER" "$@"
