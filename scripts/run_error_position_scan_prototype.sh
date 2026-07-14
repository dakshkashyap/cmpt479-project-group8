#!/usr/bin/env bash
#
# run_error_position_scan_prototype.sh -- build and exercise the issue #31 error-position
# scan prototype.
#
#     ./scripts/run_error_position_scan_prototype.sh
#     LLMASK_SIZES_MB=64 ./scripts/run_error_position_scan_prototype.sh
#
# This is POSITION SCANNING ONLY. There is no repair, no TwoLevelScanKernel subclass, and no
# change to the production validator. The throughput figures are prototype microbenchmarks on
# an in-memory buffer, not production validator throughput.
#
# The scan walks the two-level structure built by issues #29 and #30:
#
#     for each maskHL word j:
#         if maskHL[j] == 0: skip the whole 4096-code-unit region
#         while maskHL[j] != 0:
#             llIndex = ctz(maskHL[j]); m = llmasks[64*j + llIndex]
#             while m != 0:
#                 emit((64*j + llIndex) * 64 + ctz(m));  m &= m - 1
#             maskHL[j] &= maskHL[j] - 1
#
# What this script does, in order:
#   1. compiles benchmarks/prototype_error_position_scan.cpp into a temporary directory
#      (never into the repository -- no binary is ever produced under the working tree),
#   2. runs the self-test (the smoke cases from issue #31),
#   3. checks, on generated files, that the two-level scan, a one-level scan and a linear
#      scan all emit the identical, strictly ascending, in-range position list,
#   4. differentially checks those positions against benchmarks/llmask_reference.py, which
#      computes them straight from the definition with no LLmask and no maskHL,
#   5. cross-checks against the production validator:
#         number_of_positions + oddTrailingByte == utf16validate errorCount
#      (skipped with a notice if the validator has not been built),
#   6. benchmarks the scan as a delta on top of mask generation.
#
# Environment overrides (all optional):
#     LLMASK_SIZES_MB=32          size of the benchmarked files, in MiB
#     LLMASK_DATASET=mixed_multilingual
#     LLMASK_WARMUPS=2            warmup runs per stage
#     LLMASK_REPETITIONS=15       measured runs per stage (median reported)
#     LLMASK_DATA_DIR=<repo>/benchmarks/data   generated files (git-ignored)
#     PARABIX_DIR=<repo>/.deps/parabix
#
# Nothing is written to results/, no CSV is produced, and the generated datasets go to the
# git-ignored benchmarks/data/ directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
VALIDATOR="$PARABIX_DIR/build/bin/utf16validate"
GEN="$REPO_ROOT/benchmarks/generate_utf16_benchmark.py"
REF="$REPO_ROOT/benchmarks/llmask_reference.py"

DATA_DIR="${LLMASK_DATA_DIR:-$REPO_ROOT/benchmarks/data}"
SIZES_MB="${LLMASK_SIZES_MB:-32}"
DATASET="${LLMASK_DATASET:-mixed_multilingual}"
WARMUPS="${LLMASK_WARMUPS:-2}"
REPETITIONS="${LLMASK_REPETITIONS:-15}"

CXX_BIN="${CXX:-c++}"
command -v "$CXX_BIN" >/dev/null 2>&1 || { echo "ERROR: no C++ compiler ($CXX_BIN)." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/prototype_error_position_scan"

echo "=============================================================="
echo " 1. build (temporary directory, nothing written to the repo)"
echo "=============================================================="
"$CXX_BIN" -O3 -std=c++17 -Wall -Wextra \
    -o "$BIN" "$REPO_ROOT/benchmarks/prototype_error_position_scan.cpp"
echo "built: $BIN"
echo

echo "=============================================================="
echo " 2. self-test (issue #31 smoke cases)"
echo "=============================================================="
"$BIN" --self-test
echo

echo "=============================================================="
echo " 3. datasets"
echo "=============================================================="
mkdir -p "$DATA_DIR"
VALID="$DATA_DIR/valid_utf16le_${DATASET}_${SIZES_MB}MiB.bin"
RANDOM_MIX="$DATA_DIR/malformed_utf16le_${DATASET}_random_mix_err0.01_${SIZES_MB}MiB.bin"
CLUSTERED="$DATA_DIR/malformed_utf16le_${DATASET}_clustered_mix_err0.1_${SIZES_MB}MiB.bin"

[ -f "$VALID" ] || python3 "$GEN" --output-dir "$DATA_DIR" --datasets "$DATASET" \
    --sizes-mb "$SIZES_MB" >/dev/null
[ -f "$RANDOM_MIX" ] || python3 "$GEN" --output-dir "$DATA_DIR" --datasets "$DATASET" \
    --error-patterns random_mix --error-rates 0.01 --sizes-mb "$SIZES_MB" >/dev/null
[ -f "$CLUSTERED" ] || python3 "$GEN" --output-dir "$DATA_DIR" --datasets "$DATASET" \
    --error-patterns clustered_mix --error-rates 0.1 --sizes-mb "$SIZES_MB" >/dev/null

ls -l "$VALID" "$RANDOM_MIX" "$CLUSTERED" | sed "s|$REPO_ROOT/||"

# An odd-length file: the trailing byte is half a code unit, so it has no position at all.
ODD="$WORK/odd_trailing_byte.bin"
head -c 4097 "$RANDOM_MIX" > "$ODD"
echo

FILES=("$VALID" "$RANDOM_MIX" "$CLUSTERED" "$ODD")

echo "=============================================================="
echo " 4. scanner agreement (two_level == one_level == linear)"
echo "     positions strictly ascending, all in range; maskHL invariants hold"
echo "=============================================================="
for f in "${FILES[@]}"; do
    "$BIN" --check "$f" | grep -v '^positions=\|^oddtrailingbyte='
done
echo

echo "=============================================================="
echo " 5. differential vs benchmarks/llmask_reference.py --positions"
echo "     (the reference derives positions from the definition, with no LLmask/maskHL)"
echo "=============================================================="
for f in "${FILES[@]}"; do
    name="$(basename "$f")"
    if diff -q <("$BIN" --dump "$f") <(python3 "$REF" --positions "$f") >/dev/null; then
        echo "  PASS  identical position list  ($name)"
    else
        echo "  FAIL  position lists differ    ($name)"
        exit 1
    fi
done
echo

echo "=============================================================="
echo " 6. cross-check against the production validator"
echo "     positions + oddTrailingByte == utf16validate errorCount"
echo "=============================================================="
if [ -x "$VALIDATOR" ]; then
    for f in "${FILES[@]}"; do
        name="$(basename "$f")"
        out="$("$BIN" --check "$f")"
        pos="$(printf '%s\n' "$out" | sed -n 's/^positions=//p')"
        odd="$(printf '%s\n' "$out" | sed -n 's/^oddtrailingbyte=//p')"
        total=$((pos + odd))
        vcount="$("$VALIDATOR" -simd "$f" | sed -n 's/.*errorCount = \([0-9][0-9]*\).*/\1/p')"
        if [ "$total" = "$vcount" ]; then
            echo "  PASS  $name: positions=$pos + oddByte=$odd = $total == validator $vcount"
        else
            echo "  FAIL  $name: positions=$pos + oddByte=$odd = $total != validator $vcount"
            exit 1
        fi
    done
else
    echo "  SKIPPED: $VALIDATOR not built (run ./scripts/setup_parabix.sh to enable this check)."
fi
echo

echo "=============================================================="
echo " 7. benchmark: scan cost as a delta on top of mask generation"
echo "=============================================================="
for f in "$VALID" "$RANDOM_MIX" "$CLUSTERED"; do
    "$BIN" --bench "$f" --warmups "$WARMUPS" --repetitions "$REPETITIONS"
    echo
done

echo "Done. Nothing was written to the repository working tree."
echo "Reminder: position scanning only. No repair, no TwoLevelScanKernel subclass, and no"
echo "change to the production validator. These are prototype figures, not production numbers."
