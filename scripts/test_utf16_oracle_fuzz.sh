#!/usr/bin/env bash
#
# test_utf16_oracle_fuzz.sh -- deterministic fuzz / property suite (issue #42).
#
#     ./scripts/test_utf16_oracle_fuzz.sh              # default run (~1.5 min)
#     ./scripts/test_utf16_oracle_fuzz.sh --quick      # fast subset (~20 s)
#     ./scripts/test_utf16_oracle_fuzz.sh --seed 1234 --cases 400
#     ./scripts/test_utf16_oracle_fuzz.sh --seed 479 --only-case 137
#
# A thin wrapper around scripts/test_utf16_oracle_fuzz.py: it locates the built binary the
# same way the other suites do and forwards every argument. The Python entry point works
# standalone too (set UTF16VALIDATE_BIN or PARABIX_DIR, or pass --bin).
#
# Cases are generated deterministically from --seed and compared against
# scripts/utf16_oracle.py -- an independent oracle written from the definition of UTF-16
# well-formedness -- on every validator path, in UTF-16LE and UTF-16BE. See the README
# section "Independent oracle and deterministic fuzzing" and the module docstrings.
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

exec python3 "$SCRIPT_DIR/test_utf16_oracle_fuzz.py" --bin "$BIN" "$@"
