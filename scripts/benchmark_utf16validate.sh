#!/usr/bin/env bash
#
# benchmark_utf16validate.sh -- run the full preliminary benchmark workflow.
#
# It runs the correctness suite, generates deterministic UTF-16LE datasets,
# benchmarks the scalar and SIMD validators (including several thread counts),
# and writes a CSV plus a Markdown summary under results/.
#
# Environment overrides (all optional):
#     BENCH_SIZES_MB=1,8,32,64        dataset sizes in MiB
#     BENCH_WARMUPS=2                 warmup runs per configuration
#     BENCH_REPETITIONS=7            measured runs per configuration
#     BENCH_THREAD_COUNTS=1,2,3       --thread-num values to benchmark
#     PARABIX_DIR=/path              Parabix checkout (default: <repo>/.deps/parabix)
#
# Example (shorter development run):
#     BENCH_SIZES_MB=1,8 BENCH_WARMUPS=1 BENCH_REPETITIONS=3 \
#         ./scripts/benchmark_utf16validate.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
BIN="$PARABIX_DIR/build/bin/utf16validate"

SIZES_MB="${BENCH_SIZES_MB:-1,8,32,64}"
WARMUPS="${BENCH_WARMUPS:-2}"
REPETITIONS="${BENCH_REPETITIONS:-7}"
THREAD_COUNTS="${BENCH_THREAD_COUNTS:-1,2,3}"

DATA_DIR="$REPO_ROOT/benchmarks/data"
CSV="$REPO_ROOT/results/utf16_benchmark.csv"
SUMMARY="$REPO_ROOT/results/utf16_benchmark_summary.md"

err() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

command -v python3 >/dev/null 2>&1 || err "python3 not found in PATH."

if [ ! -x "$BIN" ]; then
    err "validator binary not found at $BIN
  Build it first:  ./scripts/setup_parabix.sh"
fi

info "Running correctness suite before benchmarking"
"$REPO_ROOT/scripts/test_utf16validate.sh"

info "Generating deterministic datasets (sizes: $SIZES_MB MiB) in $DATA_DIR"
python3 "$REPO_ROOT/benchmarks/generate_utf16_benchmark.py" \
    --output-dir "$DATA_DIR" \
    --sizes-mb "$SIZES_MB"

info "Running benchmark (warmups=$WARMUPS, repetitions=$REPETITIONS, threads=$THREAD_COUNTS)"
python3 "$REPO_ROOT/benchmarks/run_utf16_benchmark.py" \
    --binary "$BIN" \
    --data-dir "$DATA_DIR" \
    --output "$CSV" \
    --warmups "$WARMUPS" \
    --repetitions "$REPETITIONS" \
    --thread-counts "$THREAD_COUNTS" \
    --sizes-mb "$SIZES_MB"

info "Writing Markdown summary"
python3 "$REPO_ROOT/benchmarks/summarize_utf16_benchmark.py" \
    --input "$CSV" \
    --output "$SUMMARY"

echo
info "Benchmark complete."
echo "    CSV     : $CSV"
echo "    Summary : $SUMMARY"
