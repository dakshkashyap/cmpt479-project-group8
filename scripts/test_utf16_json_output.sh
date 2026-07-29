#!/usr/bin/env bash
#
# test_utf16_json_output.sh -- machine-readable JSON diagnostics suite (issue #46).
#
#     ./scripts/test_utf16_json_output.sh
#
# Checks that --json / --json-pretty emit one parseable JSON document per input, that the
# schema is consistent and keys are stable, that counts stay numeric and flags stay boolean,
# that validation/positions/repair/error-mark fields agree with scripts/utf16_oracle.py, that
# arrays are empty rather than missing when there is nothing to report, and that the
# human-readable output is unchanged when neither flag is given.
#
# A thin wrapper around scripts/test_utf16_json_output.py; all fixtures live in a temporary
# directory that the Python driver removes on exit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
BIN="$PARABIX_DIR/build/bin/utf16validate"

[ -x "$BIN" ] || {
    echo "ERROR: utf16validate not found at $BIN" >&2
    echo "       Run ./scripts/setup_parabix.sh first." >&2
    exit 1
}
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }

UTF16VALIDATE_BIN="$BIN" exec python3 "$SCRIPT_DIR/test_utf16_json_output.py" "$@"
