#!/usr/bin/env bash
#
# reproduce_research.sh -- one-command reproduction of the whole research artifact (issue #47).
#
#     ./scripts/reproduce_research.sh --quick     # representative evidence (~5 min)
#     ./scripts/reproduce_research.sh --full      # the complete published evaluation (hours)
#     ./scripts/reproduce_research.sh --help
#
# This adds no UTF-16 functionality. It runs the workflow that already exists -- environment
# checks, dataset verification, the regression suites, the benchmark campaign -- and collects
# the results into one evidence directory with machine-readable metadata and a report.
#
# Nothing is downloaded, nothing is installed, and datasets are never silently regenerated.
#
# Stages
#   1. environment   python3, C++ compiler, validator binary, repository layout, patch, space
#   2. datasets      verify datasets/error_density and its manifest (regenerate only on --force)
#   3. tests         every regression suite; stops immediately on an unexpected failure
#   4. benchmark     the representative campaign (quick) or the full matrix (full)
#   5. evidence      results/reproduction/: environment, system info, raw + summary, report
#
# Exit status is 0 only when every stage that ran succeeded.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
BIN="$PARABIX_DIR/build/bin/utf16validate"
SIMDUTF_SH="$(cd "$PARABIX_DIR/.." 2>/dev/null && pwd)/simdutf/singleheader"
DATASET_DIR="$REPO_ROOT/datasets/error_density"

MODE="quick"
SKIP_TESTS=0
SKIP_BENCHMARKS=0
SKIP_DATASETS=0
FORCE=0
SEED=479
OUTPUT_DIR="$REPO_ROOT/results/reproduction"

START_EPOCH=$(date +%s)
STAGE_FAILED=""

usage() {
    cat <<'USAGE'
reproduce_research.sh -- one-command reproduction of the research artifact.

Usage: ./scripts/reproduce_research.sh [options]

  --quick             representative evidence; practical runtime (default)
  --full              the complete published evaluation (hours; see the README)
  --skip-tests        do not run the regression suites
  --skip-benchmarks   do not run the benchmark campaign
  --skip-datasets     do not verify the controlled datasets
  --force             regenerate missing datasets, and overwrite existing evidence
  --output-dir DIR    where to write the evidence (default: results/reproduction)
  --seed N            seed for dataset generation and the benchmark (default: 479)
  --help              show this message

Without --force nothing existing is deleted or regenerated: the script reports what is
missing and stops. Nothing is ever downloaded or installed.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --quick) MODE="quick" ;;
        --full) MODE="full" ;;
        --skip-tests) SKIP_TESTS=1 ;;
        --skip-benchmarks) SKIP_BENCHMARKS=1 ;;
        --skip-datasets) SKIP_DATASETS=1 ;;
        --force) FORCE=1 ;;
        --seed) shift; SEED="${1:-479}" ;;
        --output-dir) shift; OUTPUT_DIR="${1:-$OUTPUT_DIR}" ;;
        --help|-h) usage; exit 0 ;;
        *) echo "ERROR: unknown option '$1' (try --help)" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
info() { printf '   %s\n' "$1"; }
ok()   { printf '   \033[32mOK\033[0m   %s\n' "$1"; }
warn() { printf '   \033[33mNOTE\033[0m %s\n' "$1"; }
bad()  { printf '   \033[31mFAIL\033[0m %s\n' "$1"; }

die() {
    bad "$1"
    printf '\nReproduction stopped. No evidence was written for the incomplete run.\n'
    exit "${2:-1}"
}

# --- stage 1: environment ------------------------------------------------------

say "stage 1/5  environment"

command -v python3 >/dev/null 2>&1 || die "python3 is required but was not found on PATH."
PYTHON_VERSION="$(python3 -c 'import platform;print(platform.python_version())')"
python3 - <<'PY' || die "Python 3.6 or newer is required."
import sys
sys.exit(0 if sys.version_info >= (3, 6) else 1)
PY
ok "python3 $PYTHON_VERSION"

if command -v c++ >/dev/null 2>&1; then
    CXX_VERSION="$(c++ --version 2>&1 | head -1)"
    ok "C++ compiler: $CXX_VERSION"
else
    CXX_VERSION="(none on PATH)"
    warn "no C++ compiler on PATH; the simdutf comparison will be reported as skipped"
fi

[ -x "$BIN" ] || die "utf16validate not found at $BIN
        Build it first:  ./scripts/setup_parabix.sh"
ok "validator binary: $BIN"

for required in \
    scripts/utf16_oracle.py \
    scripts/test_utf16validate.sh \
    scripts/test_utf16be.sh \
    scripts/test_errormarks.sh \
    scripts/test_scan_consumer.sh \
    scripts/test_utf16_repair.sh \
    scripts/test_utf16_json_output.sh \
    scripts/test_utf16_oracle_fuzz.sh \
    scripts/generate_error_density_datasets.sh \
    benchmarks/benchmark_utf16_pipeline.sh \
    benchmarks/plot_utf16_pipeline_benchmark.py \
    patches/utf16-simd-milestone.patch
do
    [ -e "$REPO_ROOT/$required" ] || die "required file missing: $required"
done
ok "repository layout and milestone patch present"

if [ -f "$SIMDUTF_SH/simdutf.cpp" ]; then
    SIMDUTF_STATUS="available at $SIMDUTF_SH"
    ok "simdutf: $SIMDUTF_STATUS"
else
    SIMDUTF_STATUS="unavailable (run ./scripts/setup_clausecker_lemire.sh to enable it)"
    warn "simdutf $SIMDUTF_STATUS; comparison rows will be reported as skipped"
fi

# A rough free-space check. The full matrix needs the ~117 MiB corpus plus results.
FREE_MIB="$(df -Pm "$REPO_ROOT" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "${FREE_MIB:-}" ]; then
    if [ "$FREE_MIB" -lt 512 ]; then
        warn "only ${FREE_MIB} MiB free on this filesystem; the full corpus needs ~117 MiB"
    else
        ok "free disk space: ${FREE_MIB} MiB"
    fi
else
    FREE_MIB="unknown"
    warn "could not determine free disk space"
fi

GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then
    GIT_DIRTY="true"
else
    GIT_DIRTY="false"
fi
info "repository $GIT_BRANCH @ ${GIT_COMMIT:0:12} (dirty: $GIT_DIRTY)"

# --- evidence directory --------------------------------------------------------

if [ -d "$OUTPUT_DIR" ] && [ -n "$(ls -A "$OUTPUT_DIR" 2>/dev/null)" ]; then
    if [ "$FORCE" -eq 1 ]; then
        warn "--force: existing evidence in $OUTPUT_DIR will be replaced once this run succeeds"
    else
        die "evidence already exists in $OUTPUT_DIR
        Pass --force to replace it, or --output-dir to write somewhere else.
        Existing evidence is never deleted without --force."
    fi
fi

# Everything this run produces is written to a staging directory first and moved into place
# only after the whole workflow succeeds. A failed run therefore never replaces evidence that
# a previous successful run left behind, and never leaves a half-written report.
STAGING="${OUTPUT_DIR}.partial"
rm -rf "$STAGING"
mkdir -p "$STAGING" || die "could not create the staging directory $STAGING"
cleanup_staging() { rm -rf "$STAGING"; }
trap cleanup_staging EXIT
ok "evidence directory: $OUTPUT_DIR (staged in $(basename "$STAGING"))"

# --- stage 2: datasets ---------------------------------------------------------

say "stage 2/5  controlled error-density datasets"
DATASET_STATUS="skipped"
if [ "$SKIP_DATASETS" -eq 1 ]; then
    warn "--skip-datasets given; not verified"
else
    MISSING=""
    [ -d "$DATASET_DIR" ] || MISSING="$MISSING\n     - directory $DATASET_DIR"
    [ -f "$DATASET_DIR/manifest.csv" ] || MISSING="$MISSING\n     - $DATASET_DIR/manifest.csv"
    if [ -n "$MISSING" ]; then
        if [ "$FORCE" -eq 1 ]; then
            warn "missing dataset material; regenerating because --force was given"
            "$REPO_ROOT/scripts/generate_error_density_datasets.sh" --seed "$SEED" \
                || die "dataset generation failed"
            DATASET_STATUS="regenerated (--force)"
        else
            die "controlled datasets are missing:$(printf "$MISSING")
        Datasets are never regenerated silently. Generate them with:
          ./scripts/generate_error_density_datasets.sh
        or re-run this script with --force."
        fi
    else
        DATASET_ROWS="$(( $(wc -l < "$DATASET_DIR/manifest.csv") - 1 ))"
        DATASET_FILES="$(find "$DATASET_DIR" -name '*.bin' | wc -l | tr -d ' ')"
        ok "manifest describes $DATASET_ROWS datasets; $DATASET_FILES files present"
        DATASET_STATUS="verified: $DATASET_ROWS manifest rows, $DATASET_FILES files"
    fi
fi

# --- stage 3: tests ------------------------------------------------------------

say "stage 3/5  regression suites"
: > "$STAGING/.test_rows"
run_suite() {
    local label="$1"; shift
    local log; log="$(mktemp)"
    printf '   %-34s ' "$label"
    if "$@" > "$log" 2>&1; then
        local counts xfail status
        counts="$(grep -oE '[0-9]+ passed, [0-9]+ (known-xfail, [0-9]+ )?failed' "$log" | tail -1)"
        [ -n "$counts" ] || counts="$(grep -oE '[0-9]+ passed, [0-9]+ failed' "$log" | tail -1)"
        [ -n "$counts" ] || counts="completed"
        if grep -q "known-xfail" "$log"; then
            xfail="$(grep -oE '[0-9]+ known-xfail' "$log" | tail -1)"
            status="KNOWN-XFAIL"
            printf '\033[33mPASS (%s)\033[0m  %s\n' "$xfail" "$counts"
        else
            status="PASS"
            printf '\033[32mPASS\033[0m  %s\n' "$counts"
        fi
        printf '%s\t%s\t%s\n' "$label" "$status" "$counts" >> "$STAGING/.test_rows"
        rm -f "$log"
        return 0
    fi
    printf '\033[31mFAIL\033[0m\n'
    printf '%s\t%s\t%s\n' "$label" "FAIL" "see output above" >> "$STAGING/.test_rows"
    echo "----- last 20 lines of $label -----"
    tail -20 "$log"
    rm -f "$log"
    return 1
}

TEST_STATUS="skipped"
if [ "$SKIP_TESTS" -eq 1 ]; then
    warn "--skip-tests given; suites not run"
else
    run_suite "utf16_oracle self-test"   python3 "$REPO_ROOT/scripts/utf16_oracle.py" --self-test || STAGE_FAILED="tests"
    [ -z "$STAGE_FAILED" ] && { run_suite "test_utf16validate"      "$REPO_ROOT/scripts/test_utf16validate.sh" || STAGE_FAILED="tests"; }
    [ -z "$STAGE_FAILED" ] && { run_suite "test_utf16be"            "$REPO_ROOT/scripts/test_utf16be.sh" || STAGE_FAILED="tests"; }
    [ -z "$STAGE_FAILED" ] && { run_suite "test_errormarks"         "$REPO_ROOT/scripts/test_errormarks.sh" || STAGE_FAILED="tests"; }
    [ -z "$STAGE_FAILED" ] && { run_suite "test_scan_consumer"      "$REPO_ROOT/scripts/test_scan_consumer.sh" || STAGE_FAILED="tests"; }
    [ -z "$STAGE_FAILED" ] && { run_suite "test_utf16_repair"       "$REPO_ROOT/scripts/test_utf16_repair.sh" || STAGE_FAILED="tests"; }
    [ -z "$STAGE_FAILED" ] && { run_suite "test_utf16_json_output"  "$REPO_ROOT/scripts/test_utf16_json_output.sh" || STAGE_FAILED="tests"; }
    [ -z "$STAGE_FAILED" ] && { run_suite "utf16_oracle_fuzz quick" "$REPO_ROOT/scripts/test_utf16_oracle_fuzz.sh" --quick || STAGE_FAILED="tests"; }
    [ -z "$STAGE_FAILED" ] && { run_suite "benchmark self-test-gate" "$REPO_ROOT/benchmarks/benchmark_utf16_pipeline.sh" --self-test-gate || STAGE_FAILED="tests"; }

    if [ -n "$STAGE_FAILED" ]; then
        die "a regression suite failed unexpectedly; stopping before the benchmark stage."
    fi
    TEST_STATUS="all suites passed"
fi

# --- stage 4: benchmark --------------------------------------------------------

say "stage 4/5  benchmark campaign ($MODE)"
BENCH_STATUS="skipped"
BENCH_COMMAND="(not run)"
if [ "$SKIP_BENCHMARKS" -eq 1 ]; then
    warn "--skip-benchmarks given; campaign not run"
elif [ "$SKIP_DATASETS" -eq 1 ] && [ ! -f "$DATASET_DIR/manifest.csv" ]; then
    warn "datasets were not verified and no manifest is present; skipping the benchmark"
else
    # The benchmark writes its raw CSV, aggregate CSV, JSON and Markdown summary DIRECTLY
    # into this run's staging directory via the driver's own --output/--summary options. The
    # canonical issue #45 evidence under results/ is never read, rewritten or copied from, so
    # a reproduction run leaves it byte-for-byte untouched. Methodology is unchanged.
    BENCH_OUTPUT="$STAGING/benchmark.csv"
    BENCH_SUMMARY="$STAGING/benchmark_summary.md"
    if [ "$MODE" = "quick" ]; then
        # The representative campaign: 4 MiB inputs are the smallest size in this corpus at
        # which the measurement is not entirely process start-up. Methodology is unchanged.
        BENCH_ARGS=(--sizes 4MiB --densities 0,1,10,50 --warmups 1 --iterations 3 --seed "$SEED")
    else
        BENCH_ARGS=(--seed "$SEED")
    fi
    BENCH_ARGS+=(--output "$BENCH_OUTPUT" --summary "$BENCH_SUMMARY")
    BENCH_COMMAND="./benchmarks/benchmark_utf16_pipeline.sh ${BENCH_ARGS[*]}"
    info "$BENCH_COMMAND"
    if "$REPO_ROOT/benchmarks/benchmark_utf16_pipeline.sh" "${BENCH_ARGS[@]}"; then
        ok "benchmark completed"
        BENCH_STATUS="completed"
        # The driver derives the aggregate name from the raw CSV stem; give it the documented
        # evidence name.
        [ -f "$STAGING/benchmark_aggregate.csv" ] \
            && mv "$STAGING/benchmark_aggregate.csv" "$STAGING/aggregate.csv"
    else
        bad "benchmark reported failures"
        BENCH_STATUS="failed"
        STAGE_FAILED="benchmark"
    fi

    printf '   %-34s ' "charts"
    if python3 "$REPO_ROOT/benchmarks/plot_utf16_pipeline_benchmark.py" \
            --input "$BENCH_OUTPUT" --output-dir "$STAGING/graphs" \
            > "$STAGING/.charts.log" 2>&1; then
        if grep -q "SKIPPED" "$STAGING/.charts.log"; then
            printf '\033[33mSKIPPED\033[0m  %s\n' "$(head -1 "$STAGING/.charts.log")"
            CHART_STATUS="skipped: $(head -1 "$STAGING/.charts.log")"
        else
            printf '\033[32mOK\033[0m\n'
            CHART_STATUS="generated"
        fi
    else
        printf '\033[33mSKIPPED\033[0m\n'
        CHART_STATUS="skipped (chart step returned non-zero)"
    fi
    rm -f "$STAGING/.charts.log"
fi
CHART_STATUS="${CHART_STATUS:-not attempted}"

# --- stage 5: evidence ---------------------------------------------------------

say "stage 5/5  research evidence package"

for expected in benchmark.csv benchmark.json aggregate.csv benchmark_summary.md; do
    if [ -f "$STAGING/$expected" ]; then
        ok "produced $expected"
    else
        warn "$expected was not produced (the benchmark stage was skipped or failed)"
    fi
done

ELAPSED=$(( $(date +%s) - START_EPOCH ))

MODE="$MODE" SEED="$SEED" OUTPUT_DIR="$STAGING" REPO_ROOT="$REPO_ROOT" \
GIT_COMMIT="$GIT_COMMIT" GIT_BRANCH="$GIT_BRANCH" GIT_DIRTY="$GIT_DIRTY" \
PYTHON_VERSION="$PYTHON_VERSION" CXX_VERSION="$CXX_VERSION" BIN="$BIN" \
SIMDUTF_STATUS="$SIMDUTF_STATUS" DATASET_STATUS="$DATASET_STATUS" \
TEST_STATUS="$TEST_STATUS" BENCH_STATUS="$BENCH_STATUS" BENCH_COMMAND="$BENCH_COMMAND" \
CHART_STATUS="$CHART_STATUS" ELAPSED="$ELAPSED" FREE_MIB="$FREE_MIB" \
SKIP_TESTS="$SKIP_TESTS" SKIP_BENCHMARKS="$SKIP_BENCHMARKS" SKIP_DATASETS="$SKIP_DATASETS" \
STAGE_FAILED="$STAGE_FAILED" python3 - <<'PY'
import json, os, platform, subprocess, sys
from datetime import datetime, timezone

env = os.environ
out = env["OUTPUT_DIR"]


def cpu_model():
    try:
        if platform.system() == "Darwin":
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                  stdout=subprocess.PIPE, timeout=10
                                  ).stdout.decode().strip()
        if platform.system() == "Linux":
            with open("/proc/cpuinfo") as handle:
                for line in handle:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


timestamp = datetime.now(timezone.utc).isoformat()
environment = {
    "timestamp": timestamp,
    "git_commit": env["GIT_COMMIT"],
    "branch": env["GIT_BRANCH"],
    "dirty": env["GIT_DIRTY"] == "true",
    "mode": env["MODE"],
    "seed": int(env["SEED"]),
    "validator_binary": env["BIN"],
    "simdutf": env["SIMDUTF_STATUS"],
    "python_version": env["PYTHON_VERSION"],
    "compiler": env["CXX_VERSION"],
    "benchmark_command": env["BENCH_COMMAND"],
    "dataset_status": env["DATASET_STATUS"],
    "skipped": {
        "tests": env["SKIP_TESTS"] == "1",
        "benchmarks": env["SKIP_BENCHMARKS"] == "1",
        "datasets": env["SKIP_DATASETS"] == "1",
    },
    "elapsed_seconds": int(env["ELAPSED"]),
}
system = {
    "os": "%s %s" % (platform.system(), platform.release()),
    "platform": platform.platform(),
    "architecture": platform.machine(),
    "cpu_model": cpu_model(),
    "logical_cpus": os.cpu_count(),
    "python_version": platform.python_version(),
    "python_implementation": platform.python_implementation(),
    "free_disk_mib": env["FREE_MIB"],
}

rows = []
path = os.path.join(out, ".test_rows")
if os.path.exists(path):
    with open(path) as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                rows.append({"suite": parts[0], "status": parts[1], "detail": parts[2]})
    os.remove(path)

tests = {
    "status": env["TEST_STATUS"],
    "suites": rows,
    "passed": sum(1 for r in rows if r["status"] in ("PASS", "KNOWN-XFAIL")),
    "known_xfail": sum(1 for r in rows if r["status"] == "KNOWN-XFAIL"),
    "failed": sum(1 for r in rows if r["status"] == "FAIL"),
}

overall = "PASS" if not env["STAGE_FAILED"] and tests["failed"] == 0 else "FAIL"

# Write atomically: a partially-written report is worse than none.
def write(name, text):
    tmp = os.path.join(out, "." + name + ".partial")
    with open(tmp, "w") as handle:
        handle.write(text)
    os.replace(tmp, os.path.join(out, name))


write("environment.json", json.dumps(environment, indent=2) + "\n")
write("system_information.json", json.dumps(system, indent=2) + "\n")
write("test_summary.json", json.dumps(tests, indent=2) + "\n")

produced = sorted(n for n in os.listdir(out) if not n.startswith("."))

lines = []
lines.append("# Reproduction report")
lines.append("")
lines.append("Generated by `scripts/reproduce_research.sh --%s`. This report records what was "
             "run and what it produced on one machine; it draws no conclusions beyond the "
             "measured evidence copied alongside it." % environment["mode"])
lines.append("")
lines.append("**Overall result: %s**" % overall)
lines.append("")
lines.append("## Repository version")
lines.append("")
lines.append("- commit: `%s`" % environment["git_commit"])
lines.append("- branch: `%s`" % environment["branch"])
lines.append("- working tree dirty: %s" % environment["dirty"])
lines.append("")
lines.append("## Commands executed")
lines.append("")
lines.append("```")
lines.append("./scripts/reproduce_research.sh --%s" % environment["mode"])
for row in rows:
    lines.append("  %s" % row["suite"])
lines.append("  %s" % environment["benchmark_command"])
lines.append("  python3 benchmarks/plot_utf16_pipeline_benchmark.py")
lines.append("```")
lines.append("")
lines.append("## Environment")
lines.append("")
for key in ("timestamp", "python_version", "compiler", "validator_binary", "simdutf",
            "seed", "elapsed_seconds"):
    lines.append("- **%s**: %s" % (key, environment[key]))
for key in ("os", "architecture", "cpu_model", "logical_cpus", "free_disk_mib"):
    lines.append("- **%s**: %s" % (key, system[key]))
lines.append("")
lines.append("## Dataset status")
lines.append("")
lines.append("- %s" % environment["dataset_status"])
lines.append("- Datasets are never regenerated silently; `--force` is required.")
lines.append("")
lines.append("## Test summary")
lines.append("")
if rows:
    lines.append("| suite | status | detail |")
    lines.append("| --- | --- | --- |")
    for row in rows:
        lines.append("| `%s` | %s | %s |" % (row["suite"], row["status"], row["detail"]))
    lines.append("")
    lines.append("%d suite(s) passed, %d with known-xfail entries, %d failed."
                 % (tests["passed"], tests["known_xfail"], tests["failed"]))
else:
    lines.append("Tests were skipped for this run.")
lines.append("")
lines.append("## Benchmark summary")
lines.append("")
lines.append("- status: %s" % environment["benchmark_command"]
             if environment["benchmark_command"] == "(not run)"
             else "- command: `%s`" % environment["benchmark_command"])
lines.append("- result: %s" % env["BENCH_STATUS"])
lines.append("- charts: %s" % env["CHART_STATUS"])
lines.append("- Full measured tables, the correctness gate and the limitations are in "
             "`benchmark_summary.md` next to this report; the raw per-iteration rows are in "
             "`benchmark.csv` / `benchmark.json`.")
lines.append("")
lines.append("## Known limitations")
lines.append("")
lines.append("- Whole-process wall-clock timing includes process start-up, which on some "
             "machines exceeds the work for inputs of a few MiB. The benchmark summary "
             "reports where a measurement could not be separated from start-up rather than "
             "publishing a derived number.")
lines.append("- One machine, one run, with other processes running. No CPU pinning or cache "
             "control is claimed.")
lines.append("- The corpus is synthetic, with uniformly spread malformed units.")
lines.append("- simdutf and Parabix solve overlapping but non-identical problems; any "
             "comparison between them is directional only.")
lines.append("")
lines.append("## Known issue #42 scan-consumer note")
lines.append("")
lines.append("`--scan-error-marks` can print positions **past the end of the input**. The "
             "oracle and `--print-positions` remain correct, no real position is missing, and "
             "the reported `errorCount` is still correct, so only the two-level scan's "
             "position stream is affected.")
lines.append("")
lines.append("The trigger is **not** simply an exact multiple of the 4096-code-unit scan "
             "stride: controlled-density testing reproduces it on a 2048-code-unit input "
             "while a 32768-code-unit input passes, so it depends on the error distribution. "
             "The benchmark gate therefore classifies by symptom and excludes only "
             "`locate_scan` timing for an affected dataset; every other operation on that "
             "dataset is still measured. The fuzz suite's KNOWN-XFAIL entries are pinned to "
             "its own regression cases and are not evidence that other inputs are unaffected.")
lines.append("")
lines.append("## Files produced")
lines.append("")
for name in produced:
    lines.append("- `%s`" % name)
lines.append("")
lines.append("## Elapsed time")
lines.append("")
lines.append("- %d seconds" % environment["elapsed_seconds"])
lines.append("")

write("reproduction_report.md", "\n".join(lines) + "\n")
print("   wrote environment.json, system_information.json, test_summary.json, "
      "reproduction_report.md")
sys.exit(0 if overall == "PASS" else 1)
PY
REPORT_RC=$?

if [ -n "$STAGE_FAILED" ] || [ "$REPORT_RC" -ne 0 ]; then
    bad "reproduction FAILED (stage: ${STAGE_FAILED:-report})"
    info "staged output discarded; any previous evidence in $OUTPUT_DIR is untouched"
    exit 1
fi

# Promote the staged run only now that everything has succeeded.
mkdir -p "$OUTPUT_DIR" || die "could not create $OUTPUT_DIR"
rm -f "$OUTPUT_DIR"/*.json "$OUTPUT_DIR"/*.csv "$OUTPUT_DIR"/*.md
rm -rf "$OUTPUT_DIR/graphs"
for staged in "$STAGING"/*; do
    [ -e "$staged" ] || continue
    mv "$staged" "$OUTPUT_DIR/" || die "could not move $(basename "$staged") into $OUTPUT_DIR"
done

say "done"
info "evidence: $OUTPUT_DIR"
info "report:   $OUTPUT_DIR/reproduction_report.md"
info "elapsed:  ${ELAPSED}s"

ok "reproduction PASSED"
exit 0
