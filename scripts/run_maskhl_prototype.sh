#!/usr/bin/env bash
#
# run_maskhl_prototype.sh -- build and exercise the issue #30 maskHL aggregation prototype.
#
#     ./scripts/run_maskhl_prototype.sh
#     LLMASK_SIZES_MB=64 ./scripts/run_maskhl_prototype.sh
#
# maskHL is the high level of the two-level scan: one 64-bit word per 64 LLmasks, i.e. per
# 4096 code units. Bit w of maskHL[j] is set iff LLmask[64j+w] is nonzero, so a future scan
# can skip an entire 4096-code-unit region with one compare when maskHL[j] == 0.
#
# What this script does, in order:
#   1. compiles benchmarks/prototype_maskhl_aggregation.cpp into a temporary directory
#      (never into the repository -- no binary is ever produced under the working tree),
#   2. runs the self-test (the smoke cases from issue #30),
#   3. checks the maskHL invariants on generated valid and malformed files, with all three
#      aggregation strategies required to agree,
#   4. cross-checks against the production validator:
#         popcount(all LLmasks) + oddTrailingByte == utf16validate errorCount
#      (skipped with a notice if the validator has not been built),
#   5. reports the clean-region skip rate across an error-rate sweep, for randomly scattered
#      errors and for clustered errors,
#   6. benchmarks LLmask generation with and without maskHL aggregation, so the aggregation
#      overhead is a measured delta.
#
# Environment overrides (all optional):
#     LLMASK_SIZES_MB=32          size of the benchmarked files, in MiB
#     MASKHL_SWEEP_MB=8           size of the skip-rate sweep files, in MiB
#     MASKHL_SWEEP_RATES=...      error rates (%) for the sweep
#     LLMASK_DATASET=mixed_multilingual
#     LLMASK_WARMUPS=2            warmup runs per stage
#     LLMASK_REPETITIONS=7        measured runs per stage (median reported)
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

DATA_DIR="${LLMASK_DATA_DIR:-$REPO_ROOT/benchmarks/data}"
SIZES_MB="${LLMASK_SIZES_MB:-32}"
SWEEP_MB="${MASKHL_SWEEP_MB:-8}"
SWEEP_RATES="${MASKHL_SWEEP_RATES:-0.0001,0.001,0.01,0.1,1}"
DATASET="${LLMASK_DATASET:-mixed_multilingual}"
WARMUPS="${LLMASK_WARMUPS:-2}"
REPETITIONS="${LLMASK_REPETITIONS:-7}"

CXX_BIN="${CXX:-c++}"
command -v "$CXX_BIN" >/dev/null 2>&1 || { echo "ERROR: no C++ compiler ($CXX_BIN)." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/prototype_maskhl_aggregation"

echo "=============================================================="
echo " 1. build (temporary directory, nothing written to the repo)"
echo "=============================================================="
"$CXX_BIN" -O3 -std=c++17 -Wall -Wextra \
    -o "$BIN" "$REPO_ROOT/benchmarks/prototype_maskhl_aggregation.cpp"
echo "built: $BIN"
echo

echo "=============================================================="
echo " 2. self-test (issue #30 smoke cases)"
echo "=============================================================="
"$BIN" --self-test
echo

echo "=============================================================="
echo " 3. datasets"
echo "=============================================================="
mkdir -p "$DATA_DIR"

VALID="$DATA_DIR/valid_utf16le_${DATASET}_${SIZES_MB}MiB.bin"
MALFORMED="$DATA_DIR/malformed_utf16le_${DATASET}_random_mix_err0.01_${SIZES_MB}MiB.bin"

[ -f "$VALID" ] || python3 "$GEN" --output-dir "$DATA_DIR" --datasets "$DATASET" \
    --sizes-mb "$SIZES_MB" >/dev/null
[ -f "$MALFORMED" ] || python3 "$GEN" --output-dir "$DATA_DIR" --datasets "$DATASET" \
    --error-patterns random_mix --error-rates 0.01 --sizes-mb "$SIZES_MB" >/dev/null

# Sweep files: the same errors scattered randomly vs concentrated in clusters, plus a valid
# file of the same size so every row of the skip-rate table is comparable.
[ -f "$DATA_DIR/valid_utf16le_${DATASET}_${SWEEP_MB}MiB.bin" ] || \
    python3 "$GEN" --output-dir "$DATA_DIR" --datasets "$DATASET" \
        --sizes-mb "$SWEEP_MB" >/dev/null
python3 "$GEN" --output-dir "$DATA_DIR" --datasets "$DATASET" \
    --error-patterns random_mix,clustered_mix --error-rates "$SWEEP_RATES" \
    --sizes-mb "$SWEEP_MB" >/dev/null

echo "benchmark files (${SIZES_MB} MiB):"
ls -l "$VALID" "$MALFORMED" | sed "s|$REPO_ROOT/||"
echo "sweep files (${SWEEP_MB} MiB, rates ${SWEEP_RATES}): $(ls "$DATA_DIR" | grep -c "_${SWEEP_MB}MiB.bin$") in $DATA_DIR"

ODD="$WORK/odd_trailing_byte.bin"
head -c 4097 "$MALFORMED" > "$ODD"
echo

echo "=============================================================="
echo " 4. maskHL invariants (all 3 strategies must agree)"
echo "     popcount(maskHL) == nonzero LLmasks; no stray or missing bits"
echo "=============================================================="
for f in "$VALID" "$MALFORMED" "$ODD"; do
    "$BIN" --check "$f" | grep -v '^errorbits=\|^oddtrailingbyte='
done
echo

echo "=============================================================="
echo " 5. cross-check against the production validator"
echo "     popcount(LLmasks) + oddTrailingByte == utf16validate errorCount"
echo "=============================================================="
if [ -x "$VALIDATOR" ]; then
    for f in "$VALID" "$MALFORMED" "$ODD"; do
        name="$(basename "$f")"
        out="$("$BIN" --check "$f")"
        bits="$(printf '%s\n' "$out" | sed -n 's/^errorbits=//p')"
        odd="$(printf '%s\n' "$out" | sed -n 's/^oddtrailingbyte=//p')"
        total=$((bits + odd))
        vcount="$("$VALIDATOR" -simd "$f" | sed -n 's/.*errorCount = \([0-9][0-9]*\).*/\1/p')"
        if [ "$total" = "$vcount" ]; then
            echo "  PASS  $name: LLmask bits=$bits + oddByte=$odd = $total == validator $vcount"
        else
            echo "  FAIL  $name: LLmask bits=$bits + oddByte=$odd = $total != validator $vcount"
            exit 1
        fi
    done
else
    echo "  SKIPPED: $VALIDATOR not built (run ./scripts/setup_parabix.sh to enable this check)."
fi
echo

echo "=============================================================="
echo " 6. clean-region skip rate  (one region = 4096 code units)"
echo "=============================================================="
echo "  All rows are ${SWEEP_MB} MiB of $DATASET text, so they are directly comparable."
echo
printf "  %-14s %-8s %9s %9s %9s %12s %12s\n" \
    "pattern" "rate%" "errors" "dirty HL" "dirty LL" "REGION SKIP" "LLMASK SKIP"
skip_row() {
    local label="$1" rate="$2" file="$3"
    [ -f "$file" ] || { printf "  %-14s %-8s   (file missing: %s)\n" "$label" "$rate" \
        "$(basename "$file")"; return; }
    "$BIN" --stats "$file" | awk -v L="$label" -v R="$rate" '
        /^  total error bits/           { err   = $5 }
        /^  nonzero \(dirty\) LLmasks/  { dll   = $5 }
        /^  nonzero maskHL words/       { dhl   = $5 }
        /^  CLEAN-REGION SKIP RATE/     { rskip = $5 }
        /^  clean-LLmask skip rate/     { lskip = $5 }
        END { printf "  %-14s %-8s %9s %9s %9s %12s %12s\n",
                     L, R, err, dhl, dll, rskip, lskip }'
}
skip_row "valid" "0" "$DATA_DIR/valid_utf16le_${DATASET}_${SWEEP_MB}MiB.bin"
for pattern in random_mix clustered_mix; do
    IFS=',' read -ra RATES <<< "$SWEEP_RATES"
    for rate in "${RATES[@]}"; do
        # The generator names files with "%g" % rate, which is what these rate strings are.
        f="$DATA_DIR/malformed_utf16le_${DATASET}_${pattern}_err${rate}_${SWEEP_MB}MiB.bin"
        skip_row "$pattern" "$rate" "$f"
    done
done
echo
echo "  REGION SKIP = fraction of 4096-code-unit regions whose maskHL word is zero -- regions a"
echo "                future scan could skip with a single compare and branch (level 1)."
echo "  LLMASK SKIP = fraction of 64-code-unit LLmasks that are zero -- LLmasks the scan never"
echo "                has to look at even inside a dirty region (level 2)."
echo "  Both are properties of the DATA, not measured scan speedups: no scan kernel and no"
echo "  repair exists yet. random_mix and clustered_mix at the same rate contain a comparable"
echo "  number of errors; only their distribution differs, which is what the skip rates are"
echo "  sensitive to. Note the two levels degrade at different rates."
echo

echo "=============================================================="
echo " 7. benchmark: cost of aggregation on top of LLmask generation"
echo "=============================================================="
for f in "$VALID" "$MALFORMED"; do
    "$BIN" --bench "$f" --warmups "$WARMUPS" --repetitions "$REPETITIONS"
    echo
done

echo "Done. Nothing was written to the repository working tree."
