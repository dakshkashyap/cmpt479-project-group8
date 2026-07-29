#!/usr/bin/env bash
#
# benchmark_utf16_pipeline.sh -- UTF-16 pipeline benchmark campaign (issue #45).
#
#     ./benchmarks/benchmark_utf16_pipeline.sh --quick --overwrite     # fast subset
#     ./benchmarks/benchmark_utf16_pipeline.sh --overwrite             # full matrix (slow)
#     ./benchmarks/benchmark_utf16_pipeline.sh --quick --estimate-only # gate + estimate only
#
# Measures validation, errorMarks generation, linear and two-level-scan error location, and
# repair, over the controlled error-density corpus from issue #44, in UTF-16LE and UTF-16BE.
# Every dataset passes a correctness gate before any of its paths are timed.
#
# The datasets must already exist; this driver never generates them:
#     ./scripts/generate_error_density_datasets.sh --quick
#
# A thin wrapper around benchmarks/benchmark_utf16_pipeline.py that locates the built binary
# the same way the other scripts do and forwards every argument.

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

exec python3 "$SCRIPT_DIR/benchmark_utf16_pipeline.py" --bin "$BIN" "$@"
