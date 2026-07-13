#!/usr/bin/env bash
#
# benchmark_utf16validate.sh -- run the full benchmark workflow.
#
# It runs the correctness suite, generates deterministic valid UTF-16LE datasets, runs
# the benchmark matrix (scalar, Parabix SIMD at several thread counts, and optionally
# the Clausecker-Lemire/simdutf baseline), and writes a CSV plus a Markdown summary.
#
# Follows docs/benchmark_methodology.md.
#
# Environment overrides (all optional):
#     BENCH_DATASETS=default          dataset types, or 'all'
#     BENCH_SIZES_MB=1,8,32,64        sizes in MiB (final runs: 128,256,512)
#     BENCH_WARMUPS=2                 warmup runs per configuration
#     BENCH_REPETITIONS=7             measured runs per configuration
#     BENCH_INCLUDE_SIMDUTF=0         set to 1 to include the simdutf baseline
#     BENCH_RESULTS_DIR=<repo>/results  where the CSV/summary are written
#     BENCH_LABEL=utf16_benchmark     output basename
#     BENCH_SMOKE=0                   set to 1 for a fast harness check (see below)
#     PARABIX_DIR=/path               Parabix checkout
#
# Smoke mode (fast; validates the harness without a long run):
#     BENCH_SMOKE=1 ./scripts/benchmark_utf16validate.sh
# It uses 1 MiB / mixed_multilingual / 1 warmup / 2 repetitions, includes simdutf, and
# writes into a temporary directory so it can never overwrite committed results.
#
# NOTE: a normal (non-smoke) run writes results/<label>_summary.md, which is a tracked
# file. That is intentional -- it is how the committed summary is regenerated -- but it
# means a casual run will show up in `git status`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
BIN="$PARABIX_DIR/build/bin/utf16validate"
CL_BIN="$REPO_ROOT/.deps/baselines/bin/utf16validate_cl"

SMOKE="${BENCH_SMOKE:-0}"

if [ "$SMOKE" = "1" ]; then
    DATASETS="${BENCH_DATASETS:-mixed_multilingual}"
    SIZES_MB="${BENCH_SIZES_MB:-1}"
    WARMUPS="${BENCH_WARMUPS:-1}"
    REPETITIONS="${BENCH_REPETITIONS:-2}"
    INCLUDE_SIMDUTF="${BENCH_INCLUDE_SIMDUTF:-1}"
    # Never write into results/ from a smoke run.
    RESULTS_DIR="${BENCH_RESULTS_DIR:-$(mktemp -d)}"
    LABEL="${BENCH_LABEL:-utf16_benchmark_smoke}"
else
    DATASETS="${BENCH_DATASETS:-default}"
    SIZES_MB="${BENCH_SIZES_MB:-1,8,32,64}"
    WARMUPS="${BENCH_WARMUPS:-2}"
    REPETITIONS="${BENCH_REPETITIONS:-7}"
    INCLUDE_SIMDUTF="${BENCH_INCLUDE_SIMDUTF:-0}"
    RESULTS_DIR="${BENCH_RESULTS_DIR:-$REPO_ROOT/results}"
    LABEL="${BENCH_LABEL:-utf16_benchmark}"
fi

DATA_DIR="$REPO_ROOT/benchmarks/data"
CSV="$RESULTS_DIR/${LABEL}.csv"
SUMMARY="$RESULTS_DIR/${LABEL}_summary.md"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

command -v python3 >/dev/null 2>&1 || err "python3 not found in PATH."

[ -x "$BIN" ] || err "validator binary not found at $BIN
  Build it first:  ./scripts/setup_parabix.sh"

SIMDUTF_ARGS=()
if [ "$INCLUDE_SIMDUTF" = "1" ]; then
    [ -x "$CL_BIN" ] || err "simdutf baseline not found at $CL_BIN
  Build it first:  ./scripts/setup_clausecker_lemire.sh
  (or set BENCH_INCLUDE_SIMDUTF=0 to benchmark Parabix only)"
    SIMDUTF_ARGS+=(--include-simdutf)
    info "Including Clausecker-Lemire/simdutf baseline ($("$CL_BIN" --impl | sed 's/^.*= //'))"
fi

info "Running correctness suite before benchmarking"
"$REPO_ROOT/scripts/test_utf16validate.sh"

info "Generating valid datasets (datasets: $DATASETS, sizes: $SIZES_MB MiB)"
python3 "$REPO_ROOT/benchmarks/generate_utf16_benchmark.py" \
    --output-dir "$DATA_DIR" \
    --datasets "$DATASETS" \
    --sizes-mb "$SIZES_MB"

mkdir -p "$RESULTS_DIR"

info "Running benchmark matrix (warmups=$WARMUPS, repetitions=$REPETITIONS)"
python3 "$REPO_ROOT/benchmarks/run_utf16_benchmark.py" \
    --binary "$BIN" \
    --simdutf-binary "$CL_BIN" \
    --data-dir "$DATA_DIR" \
    --output "$CSV" \
    --datasets "$DATASETS" \
    --sizes-mb "$SIZES_MB" \
    --warmups "$WARMUPS" \
    --repetitions "$REPETITIONS" \
    ${SIMDUTF_ARGS[@]+"${SIMDUTF_ARGS[@]}"}

info "Writing Markdown summary"
python3 "$REPO_ROOT/benchmarks/summarize_utf16_benchmark.py" \
    --input "$CSV" \
    --output "$SUMMARY"

echo
info "Benchmark complete."
echo "    CSV     : $CSV"
echo "    Summary : $SUMMARY"
