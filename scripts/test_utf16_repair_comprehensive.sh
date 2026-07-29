#!/usr/bin/env bash
#
# test_utf16_repair_comprehensive.sh -- comprehensive U+FFFD repair campaign (issue #43).
#
#     ./scripts/test_utf16_repair_comprehensive.sh            # default (~2 min)
#     ./scripts/test_utf16_repair_comprehensive.sh --quick    # fast subset
#     ./scripts/test_utf16_repair_comprehensive.sh --seed 1234 --cases 200
#     ./scripts/test_utf16_repair_comprehensive.sh --only-case 57
#     ./scripts/test_utf16_repair_comprehensive.sh --no-simdutf
#
# scripts/test_utf16_repair.sh remains the focused smoke/regression gate for --repair.
# This is the broad campaign on top of it: hand-curated exact-byte cases, boundary and
# forced-segment-size cases, a deterministic generated campaign, large-file stress streams,
# and a simdutf differential -- all in UTF-16LE and UTF-16BE, with
# scripts/utf16_oracle.py as the exact-output oracle.
#
# A thin wrapper around scripts/test_utf16_repair_comprehensive.py: it locates the built
# binary the same way the other suites do and forwards every argument. The Python entry
# point works standalone too (set UTF16VALIDATE_BIN or PARABIX_DIR, or pass --bin).
#
# The simdutf differential is optional: if .deps/simdutf/singleheader is absent (or a C++
# compiler is not on PATH) it is reported as skipped with the reason and the rest of the
# campaign still runs. Nothing is downloaded or installed by this script.
#
# All generated fixtures live in a temporary directory that the Python driver removes on
# exit; nothing is written into the repository.

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

exec python3 "$SCRIPT_DIR/test_utf16_repair_comprehensive.py" --bin "$BIN" "$@"
