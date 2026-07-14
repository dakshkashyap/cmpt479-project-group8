#!/usr/bin/env bash
#
# run_llmask_prototype.sh -- build and exercise the issue #29 LLmask generation prototype.
#
#     ./scripts/run_llmask_prototype.sh                 # self-test + differential + benchmark
#     LLMASK_SIZES_MB=64 ./scripts/run_llmask_prototype.sh
#
# What it does, in order:
#   1. compiles benchmarks/prototype_llmask_generation.cpp into a temporary directory
#      (never into the repository -- no binary is ever produced under the working tree),
#   2. runs the prototype's self-test (the smoke cases from issue #29),
#   3. differentially checks the C++ LLmasks against benchmarks/llmask_reference.py, an
#      independently written Python reference, on generated valid and malformed files,
#   4. cross-checks the LLmasks against the already-trusted validator:
#         popcount(all LLmasks) + oddTrailingByte == utf16validate errorCount
#      (skipped with a notice if the validator has not been built),
#   5. benchmarks every strategy on one valid and one malformed file.
#
# Environment overrides (all optional):
#     LLMASK_SIZES_MB=32        size of the generated benchmark files, in MiB
#     LLMASK_DATASET=mixed_multilingual   dataset type for the generated files
#     LLMASK_WARMUPS=2          warmup runs per strategy
#     LLMASK_REPETITIONS=7      measured runs per strategy (median reported)
#     LLMASK_DATA_DIR=<repo>/benchmarks/data   where the generated files live (git-ignored)
#     PARABIX_DIR=<repo>/.deps/parabix
#
# Nothing is written to results/, no CSV is produced, and the generated datasets go to
# the git-ignored benchmarks/data/ directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
VALIDATOR="$PARABIX_DIR/build/bin/utf16validate"

DATA_DIR="${LLMASK_DATA_DIR:-$REPO_ROOT/benchmarks/data}"
SIZES_MB="${LLMASK_SIZES_MB:-32}"
DATASET="${LLMASK_DATASET:-mixed_multilingual}"
WARMUPS="${LLMASK_WARMUPS:-2}"
REPETITIONS="${LLMASK_REPETITIONS:-7}"

CXX_BIN="${CXX:-c++}"
command -v "$CXX_BIN" >/dev/null 2>&1 || { echo "ERROR: no C++ compiler ($CXX_BIN)." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
BIN="$WORK/prototype_llmask_generation"

echo "=============================================================="
echo " 1. build (temporary directory, nothing written to the repo)"
echo "=============================================================="
"$CXX_BIN" -O3 -std=c++17 -Wall -Wextra \
    -o "$BIN" "$REPO_ROOT/benchmarks/prototype_llmask_generation.cpp"
echo "built: $BIN"
echo

echo "=============================================================="
echo " 2. self-test (issue #29 smoke cases)"
echo "=============================================================="
"$BIN" --self-test
echo

echo "=============================================================="
echo " 3. datasets"
echo "=============================================================="
mkdir -p "$DATA_DIR"
VALID="$DATA_DIR/valid_utf16le_${DATASET}_${SIZES_MB}MiB.bin"
MALFORMED="$DATA_DIR/malformed_utf16le_${DATASET}_random_mix_err0.01_${SIZES_MB}MiB.bin"

if [ ! -f "$VALID" ]; then
    python3 "$REPO_ROOT/benchmarks/generate_utf16_benchmark.py" \
        --output-dir "$DATA_DIR" --datasets "$DATASET" --sizes-mb "$SIZES_MB" >/dev/null
fi
if [ ! -f "$MALFORMED" ]; then
    python3 "$REPO_ROOT/benchmarks/generate_utf16_benchmark.py" \
        --output-dir "$DATA_DIR" --datasets "$DATASET" --error-patterns random_mix \
        --error-rates 0.01 --sizes-mb "$SIZES_MB" >/dev/null
fi
ls -l "$VALID" "$MALFORMED" | sed "s|$REPO_ROOT/||"

# A small odd-length file: the odd trailing byte has no code unit, so it exercises the
# one case an LLmask structurally cannot represent.
ODD="$WORK/odd_trailing_byte.bin"
head -c 4097 "$MALFORMED" > "$ODD"
echo

echo "=============================================================="
echo " 4. differential vs benchmarks/llmask_reference.py (independent Python)"
echo "=============================================================="
diff_fail=0
for f in "$VALID" "$MALFORMED" "$ODD"; do
    name="$(basename "$f")"
    for strategy in scalar signmask_scalarized signmask_optimized vector_pack_reduce; do
        if diff -q <("$BIN" --dump "$f" --strategy "$strategy") \
                   <(python3 "$REPO_ROOT/benchmarks/llmask_reference.py" --dump "$f") >/dev/null; then
            echo "  PASS  $strategy  vs Python reference  ($name)"
        else
            echo "  FAIL  $strategy  vs Python reference  ($name)"
            diff_fail=1
        fi
    done
done
[ "$diff_fail" -eq 0 ] || { echo "differential check FAILED" >&2; exit 1; }
echo

echo "=============================================================="
echo " 5. cross-check against the validator"
echo "     popcount(LLmasks) + oddTrailingByte == utf16validate errorCount"
echo "=============================================================="
if [ -x "$VALIDATOR" ]; then
    for f in "$VALID" "$MALFORMED" "$ODD"; do
        name="$(basename "$f")"
        dumped="$("$BIN" --dump "$f")"
        bits="$(printf '%s\n' "$dumped" | sed -n 's/^errorbits=//p')"
        odd="$(printf '%s\n' "$dumped" | sed -n 's/^oddtrailingbyte=//p')"
        total=$((bits + odd))
        # The validator prints "<path>: errorCount = <n>".
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
echo " 6. benchmark"
echo "=============================================================="
for f in "$VALID" "$MALFORMED"; do
    "$BIN" --bench "$f" --warmups "$WARMUPS" --repetitions "$REPETITIONS"
    echo
done

echo "Done. Nothing was written to the repository working tree."
