#!/usr/bin/env bash
# Narrowly scoped privileged entrypoint for the archive-boundary suite.
#
# This is the ONLY command the suite runs via sudo. A sudoers rule grants the
# unprivileged orchestrator permission to run just this script as root and
# nothing else (see sudoers.d/archive-boundary). The script runs strace (when
# available) around the Python extraction helper and does nothing else: no
# package installs, no shell expansion of archive contents, no other privileged
# action. All policy (allowlisted extractor, destination confined to a lab under
# the temp base) is enforced by privileged_extract.py.
#
# Usage: privileged_extract_wrapper.sh <extractor> <lab> <zip> <base>
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "privileged_extract_wrapper: expected 4 args, got $#" >&2
  exit 2
fi

EXTRACTOR="$1"
LAB="$2"
ZIP="$3"
BASE="$4"

# Resolve the helper next to this wrapper (both root-owned, not writable by the
# unprivileged caller).
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SELF_DIR/privileged_extract.py"

if [[ ! -f "$HELPER" ]]; then
  echo "privileged_extract_wrapper: helper not found: $HELPER" >&2
  exit 2
fi

export ARCHIVE_BOUNDARY_BASE="$BASE"
mkdir -p "$LAB/evidence"
STRACE_LOG="$LAB/evidence/strace.log"

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "privileged_extract_wrapper: python3 not found on PATH" >&2
  exit 2
fi

if command -v strace >/dev/null 2>&1; then
  exec strace -f -e trace=file -o "$STRACE_LOG" -- \
    "$PY" "$HELPER" --extractor "$EXTRACTOR" --lab "$LAB" --zip "$ZIP"
else
  echo "strace unavailable; running helper without syscall trace" >&2
  exec "$PY" "$HELPER" --extractor "$EXTRACTOR" --lab "$LAB" --zip "$ZIP"
fi
