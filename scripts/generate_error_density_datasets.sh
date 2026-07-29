#!/usr/bin/env bash
#
# generate_error_density_datasets.sh -- controlled error-density datasets (issue #44).
#
#     ./scripts/generate_error_density_datasets.sh                 # full matrix
#     ./scripts/generate_error_density_datasets.sh --quick         # small sizes only
#     ./scripts/generate_error_density_datasets.sh --sizes 64KiB,1MiB --densities 0,1,5
#     ./scripts/generate_error_density_datasets.sh --overwrite
#
# Writes deterministic UTF-16LE and UTF-16BE datasets with an exact number of ill-formed
# surrogate code units into datasets/error_density/, plus a manifest.csv describing them.
# Every dataset is verified against the scalar validator, the SIMD validator and
# scripts/utf16_oracle.py before it is accepted; any disagreement aborts the run.
#
# These are INPUTS for later benchmarking and validation work -- this script measures
# nothing and touches no benchmark result.
#
# A thin wrapper around scripts/generate_error_density_datasets.py: it locates the built
# binary the same way the other scripts do and forwards every argument. The Python entry
# point works standalone too (set UTF16VALIDATE_BIN or PARABIX_DIR, or pass --bin).

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

exec python3 "$SCRIPT_DIR/generate_error_density_datasets.py" --bin "$BIN" "$@"
