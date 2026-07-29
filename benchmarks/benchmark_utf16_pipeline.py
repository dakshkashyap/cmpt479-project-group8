#!/usr/bin/env python3
"""Benchmark the UTF-16 pipeline: validation, error location, scan and repair (issue #45).

benchmarks/run_utf16_benchmark.py measures ONE question -- scalar vs Parabix SIMD vs simdutf
validation throughput on the synthetic benchmark datasets. This driver is a separate campaign
over a different axis: it measures every major processing PATH (validate, mark, locate,
scan-locate, repair) against the controlled error-density corpus from issue #44, so the cost
of each stage can be compared at a known, exact malformed-unit density.

Operations (each timed independently -- never combined into one measurement)

    validate_scalar     utf16validate FILE                          validation only
    validate_simd       utf16validate --simd FILE                   validation only
    emit_error_marks    utf16validate --emit-error-marks FILE       marker generation
    locate_linear       ... --print-positions                       location materialization
    locate_scan         ... --scan-error-marks                      two-level scan location
    repair              utf16validate --repair FILE                 repair output
    simdutf_validate    simdutf validate_utf16{le,be}               validation only
    simdutf_repair      simdutf to_well_formed_utf16{le,be}         repair output

Position-printing and repair paths write to stdout; their stdout is redirected to os.devnull
so terminal I/O never dominates a measurement, while the implementation still performs and
writes all of its output.

Timing
------
Whole-process wall clock via time.perf_counter_ns() -- the same scope the existing benchmark
uses, so numbers stay comparable in kind. Every raw iteration is recorded. The median is the
headline statistic; min/max/mean/stddev are recorded too. Throughput is computed from the
INPUT BYTES on disk, never from code units. Each implementation's fixed per-process cost
(startup, and for Parabix loading the compiled pipeline) is measured once on a tiny input and
reported alongside an overhead-adjusted throughput, because that cost dominates small inputs
and differs per tool.

Correctness gate
----------------
No timing row is ever written for a path that has not first produced the right answer on that
exact dataset: scalar, --simd, --emit-error-marks, the oracle, the linear positions and the
scan positions must all agree with the manifest's actual_error_count (and, for positions, with
scripts/utf16_oracle.py), and repair output must equal the oracle's repaired bytes and
re-validate clean. A path that fails its gate is recorded as skipped, with the reason, and is
not timed.

This is measurement on one machine, at one moment, with other processes running. It is not a
controlled laboratory environment and no such claim is made.
"""

import argparse
import csv
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import utf16_oracle as oracle                                   # noqa: E402

DEFAULT_PARABIX = os.environ.get("PARABIX_DIR", os.path.join(REPO_ROOT, ".deps", "parabix"))
DEFAULT_BIN = os.path.join(DEFAULT_PARABIX, "build", "bin", "utf16validate")
SIMDUTF_SINGLEHEADER = os.path.join(os.path.dirname(DEFAULT_PARABIX), "simdutf", "singleheader")
DEFAULT_DATASET_DIR = os.path.join(REPO_ROOT, "datasets", "error_density")
DEFAULT_OUTPUT = os.path.join(REPO_ROOT, "results", "utf16_pipeline_benchmark.csv")
DEFAULT_SUMMARY = os.path.join(REPO_ROOT, "results", "utf16_pipeline_benchmark_summary.md")

MIB = 1024 * 1024
DEFAULT_SEED = 479
DEFAULT_ITERATIONS = 7
DEFAULT_WARMUPS = 2
DEFAULT_TIMEOUT = 300.0
QUICK_ITERATIONS = 3
QUICK_WARMUPS = 1
# Overhead subtraction is only reported when the measurement is at least this many times
# the fixed per-process cost; below that the corrected value is noise, not a measurement.
ADJUSTED_MIN_RATIO = 3.0
# Printed wherever an overhead-adjusted figure would be reported but the
# measurement did not clear ADJUSTED_MIN_RATIO x the fixed per-process cost.
UNRESOLVED = "not resolved by this timing scope"
QUICK_SIZES = ["4KiB", "64KiB", "1MiB"]
QUICK_DENSITIES = ["0", "1"]

# (operation, implementation, category, argument builder)
OPERATIONS = [
    ("validate_scalar", "parabix", "validation", lambda be: (["-be"] if be else [])),
    ("validate_simd", "parabix", "validation",
     lambda be: (["-be"] if be else []) + ["--simd"]),
    ("emit_error_marks", "parabix", "marker_generation",
     lambda be: (["-be"] if be else []) + ["--emit-error-marks"]),
    ("locate_linear", "parabix", "location",
     lambda be: (["-be"] if be else [])
     + ["--emit-error-marks", "--print-positions", "-thread-num=1"]),
    ("locate_scan", "parabix", "location_scan",
     lambda be: (["-be"] if be else [])
     + ["--emit-error-marks", "--scan-error-marks", "-thread-num=1"]),
    ("repair", "parabix", "repair", lambda be: (["-be"] if be else []) + ["--repair"]),
    ("simdutf_validate", "simdutf", "validation", None),
    ("simdutf_repair", "simdutf", "repair", None),
]
PARABIX_OPERATIONS = [name for name, impl, _, _ in OPERATIONS if impl == "parabix"]
SIMDUTF_OPERATIONS = [name for name, impl, _, _ in OPERATIONS if impl == "simdutf"]

RAW_COLUMNS = ["run_id", "timestamp", "git_commit", "branch", "dirty", "dataset", "encoding",
               "size_bytes", "code_units", "target_density", "actual_error_count",
               "operation", "implementation", "category", "iteration", "elapsed_seconds",
               "throughput_mib_s", "exit_code", "status", "skip_reason", "command"]

SUMMARY_COLUMNS = ["dataset", "encoding", "size_bytes", "target_density",
                   "actual_error_count", "operation", "implementation", "category",
                   "count", "median_seconds", "mean_seconds", "min_seconds", "max_seconds",
                   "stddev_seconds", "median_mib_s", "mean_mib_s",
                   "fixed_overhead_seconds", "adjusted_median_mib_s", "status",
                   "skip_reason"]


# --- simdutf helper ------------------------------------------------------------
# One binary, three modes, so validation and repair are never timed together. Repair writes
# its output to stdout exactly as `utf16validate --repair` does, so both pay the same write
# cost when stdout is redirected to the null sink.

SIMDUTF_PROGRAM = r"""
#include "simdutf.h"
#include <cstdio>
#include <cstring>
#include <vector>
int main(int argc, char ** argv) {
    if (argc < 4) return 2;                       // <file> <le|be> <validate|repair>
    FILE * f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<char> buf(n > 0 ? n : 1);
    if (n > 0 && fread(buf.data(), 1, (size_t) n, f) != (size_t) n) { fclose(f); return 2; }
    fclose(f);
    const bool be = strcmp(argv[2], "be") == 0;
    const size_t units = (size_t) n / 2;          // complete code units only
    const char16_t * in = reinterpret_cast<const char16_t *>(buf.data());
    if (strcmp(argv[3], "validate") == 0) {
        bool ok = be ? simdutf::validate_utf16be(in, units)
                     : simdutf::validate_utf16le(in, units);
        fprintf(stderr, "valid = %s\n", ok ? "true" : "false");
        return 0;
    }
    if (strcmp(argv[3], "repair") == 0) {
        std::vector<char16_t> out(units ? units : 1);
        if (be) simdutf::to_well_formed_utf16be(in, units, out.data());
        else    simdutf::to_well_formed_utf16le(in, units, out.data());
        if (units) fwrite(out.data(), 2, units, stdout);
        return 0;
    }
    return 2;
}
"""


def build_simdutf(workdir):
    """Compile the simdutf helper ONCE, before any measured run. Returns (path, reason)."""
    source = os.path.join(SIMDUTF_SINGLEHEADER, "simdutf.cpp")
    if not os.path.isfile(source):
        return None, ("simdutf singleheader not found at %s (run "
                      "./scripts/setup_clausecker_lemire.sh to enable it; nothing is "
                      "downloaded automatically)" % SIMDUTF_SINGLEHEADER)
    if not shutil.which("c++"):
        return None, "no c++ compiler on PATH"
    program = os.path.join(workdir, "simdutf_bench.cpp")
    with open(program, "w") as handle:
        handle.write(SIMDUTF_PROGRAM)
    binary = os.path.join(workdir, "simdutf_bench")
    proc = subprocess.run(["c++", "-O2", "-std=c++17", "-I", SIMDUTF_SINGLEHEADER,
                           program, source, "-o", binary],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        return None, "simdutf helper did not compile: %s" % (tail or "unknown error")
    return binary, None


def compiler_version():
    if not shutil.which("c++"):
        return "(no c++ on PATH)"
    try:
        out = subprocess.run(["c++", "--version"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=10).stdout
        return out.decode("utf-8", "replace").splitlines()[0].strip()
    except Exception:
        return "(unknown)"


# --- Environment ---------------------------------------------------------------

def git_info():
    def run(args, default=""):
        try:
            out = subprocess.run(["git", "-C", REPO_ROOT] + args, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=10)
            return out.stdout.decode("utf-8", "replace").strip() if out.returncode == 0 \
                else default
        except Exception:
            return default
    commit = run(["rev-parse", "HEAD"], "unknown")
    branch = run(["rev-parse", "--abbrev-ref", "HEAD"], "unknown")
    dirty = bool(run(["status", "--porcelain"]))
    return commit, branch, dirty


def cpu_model():
    system = platform.system()
    try:
        if system == "Darwin":
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 stdout=subprocess.PIPE, timeout=10)
            return out.stdout.decode("utf-8", "replace").strip()
        if system == "Linux":
            with open("/proc/cpuinfo") as handle:
                for line in handle:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or "unknown"


def apply_cpu_affinity(requested):
    """Pin to one CPU where the platform supports it. Returns (applied, note)."""
    if requested is None:
        return False, "not requested"
    if not hasattr(os, "sched_setaffinity"):
        return False, ("os.sched_setaffinity is unavailable on %s; no affinity or "
                       "cache-control was applied" % platform.system())
    try:
        os.sched_setaffinity(0, {requested})
        return True, "pinned to CPU %d via os.sched_setaffinity" % requested
    except OSError as ex:
        return False, "sched_setaffinity failed: %s" % ex


def environment(args, simdutf_status, affinity_applied, affinity_note):
    commit, branch, dirty = git_info()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "os": "%s %s" % (platform.system(), platform.release()),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "cpu_model": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "python_version": platform.python_version(),
        "compiler": compiler_version(),
        "git_commit": commit,
        "branch": branch,
        "dirty": dirty,
        "validator_binary": args.bin,
        "simdutf": simdutf_status,
        "warmups": args.warmups,
        "iterations": args.iterations,
        "timeout_seconds": args.timeout,
        "seed": args.seed,
        "cpu_affinity_applied": affinity_applied,
        "cpu_affinity_note": affinity_note,
        "timing_scope": "whole_process wall clock (time.perf_counter_ns)",
        "throughput_basis": "input bytes on disk",
        "command_line": " ".join(sys.argv),
    }


# --- Manifest ------------------------------------------------------------------

class Dataset(object):
    def __init__(self, row, dataset_dir):
        self.filename = row["filename"]
        self.encoding = row["encoding"]
        self.big_endian = row["encoding"] == "UTF-16BE"
        self.size_bytes = int(row["size_bytes"])
        self.code_units = int(row["code_units"])
        self.target_density = row["target_density"]
        self.actual_error_count = int(row["actual_error_count"])
        folder = "utf16be" if self.big_endian else "utf16le"
        self.path = os.path.join(dataset_dir, folder, self.filename)
        self.size_label = self.filename.split("_")[1]

    def __repr__(self):
        return self.filename


def load_manifest(manifest_path, dataset_dir):
    if not os.path.isfile(manifest_path):
        raise SystemExit(
            "ERROR: no manifest at %s\n"
            "       Generate the corpus first:\n"
            "         ./scripts/generate_error_density_datasets.sh --quick\n"
            "       (this driver never regenerates datasets on its own)" % manifest_path)
    with open(manifest_path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("ERROR: manifest %s has no rows" % manifest_path)
    return [Dataset(row, dataset_dir) for row in rows]


def select_datasets(datasets, sizes, densities, encodings):
    chosen = []
    for dataset in datasets:
        if sizes and dataset.size_label not in sizes:
            continue
        if densities and dataset.target_density not in densities:
            continue
        if encodings and dataset.encoding not in encodings:
            continue
        chosen.append(dataset)
    missing = [d.filename for d in chosen if not os.path.isfile(d.path)]
    if missing:
        raise SystemExit(
            "ERROR: %d dataset file(s) named in the manifest are missing, e.g. %s\n"
            "       Regenerate them with ./scripts/generate_error_density_datasets.sh"
            % (len(missing), ", ".join(missing[:3])))
    if not chosen:
        raise SystemExit("ERROR: no manifest rows match the selected sizes/densities/"
                         "encodings")
    return chosen


# --- Running -------------------------------------------------------------------

def command_for(operation, dataset, binary, simdutf_bin):
    for name, implementation, _, builder in OPERATIONS:
        if name != operation:
            continue
        if implementation == "simdutf":
            mode = "validate" if operation == "simdutf_validate" else "repair"
            return [simdutf_bin, dataset.path, "be" if dataset.big_endian else "le", mode]
        return [binary] + builder(dataset.big_endian) + [dataset.path]
    raise KeyError(operation)


def timed_run(cmd, timeout, devnull):
    """One measured run. stdout/stderr go to the null sink: no terminal I/O is timed."""
    start = time.perf_counter_ns()
    proc = subprocess.run(cmd, stdout=devnull, stderr=devnull, timeout=timeout)
    end = time.perf_counter_ns()
    return (end - start) / 1e9, proc.returncode


def measure(cmd, warmups, iterations, timeout, devnull):
    for _ in range(warmups):
        _, code = timed_run(cmd, timeout, devnull)
        if code != 0:
            return [], code
    times = []
    last = 0
    for _ in range(iterations):
        elapsed, last = timed_run(cmd, timeout, devnull)
        if last != 0:
            return times, last
        times.append(elapsed)
    return times, last


def measure_fixed_overhead(binary, simdutf_bin, warmups, iterations, timeout, devnull,
                           workdir):
    """Per-implementation constant process cost, measured on a two-code-unit input.

    Measured once, outside the timed matrix, and never mixed into an operation's timing --
    it is reported separately so small-input numbers can be read honestly.
    """
    tiny = os.path.join(workdir, "tiny.bin")
    with open(tiny, "wb") as handle:
        handle.write(oracle.encode_code_units([0x0041, 0x0042], False))
    overhead = {}
    for operation, implementation, _, builder in OPERATIONS:
        if implementation == "simdutf":
            if simdutf_bin is None:
                continue
            cmd = [simdutf_bin, tiny, "le",
                   "validate" if operation == "simdutf_validate" else "repair"]
        else:
            cmd = [binary] + builder(False) + [tiny]
        times, code = measure(cmd, warmups, max(3, iterations // 2), timeout, devnull)
        overhead[operation] = statistics.median(times) if times and code == 0 else None
    return overhead


# --- Correctness gate ----------------------------------------------------------

def parabix_count(binary, args, path, timeout):
    proc = subprocess.run([binary] + args + [path], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("rc=%d: %s" % (proc.returncode,
                                          proc.stderr.decode("utf-8", "replace").strip()))
    return int(proc.stdout.decode().split("errorCount = ")[1].split()[0])


def parabix_positions(binary, args, path, timeout):
    proc = subprocess.run([binary] + args + [path], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("rc=%d" % proc.returncode)
    text = proc.stdout.decode("utf-8", "replace")
    return sorted(int(line.split("=")[-1].strip(), 16)
                  for line in text.splitlines() if line.startswith("errpos"))


# The one scan-consumer symptom this benchmark tolerates, stated as a pure function so it can
# be tested without running anything.
#
# Issue #42's fuzz campaign first exposed the defect on inputs whose code-unit count is an
# exact multiple of the 4096-unit scan stride, and its regression cases are pinned to those.
# The controlled-density corpus shows the trigger is broader and depends on the error
# distribution, not only the length: a 2048-code-unit dataset reproduces it while a
# 32768-code-unit one does not. So the test here is on the SYMPTOM, never on the size.
#
# Accepted ONLY when all of these hold:
#     * the linear printer agrees with the oracle exactly;
#     * the scan reports every real position (none missing);
#     * every extra scan position is at or beyond the end of the input (>= code_units);
#     * there is at least one such extra position;
#     * the scan's own errorCount still equals the expected count.
# Anything else -- a missing position, an in-range extra, a count disagreement, a wrong
# linear result -- is rejected, and the caller aborts the whole run.

def classify_scan_symptom(oracle_positions, linear_positions, scan_positions, code_units,
                          scan_count, expected_count):
    """Return (exclude_locate_scan, reason). Pure: no subprocesses, no timing."""
    if list(linear_positions) != list(oracle_positions):
        return False, "linear positions disagree with the oracle"
    if list(scan_positions) == list(oracle_positions):
        return False, "scan agrees with the oracle, nothing to exclude"
    if scan_count != expected_count:
        return False, ("scan errorCount %d does not equal the expected %d"
                       % (scan_count, expected_count))
    # Both sets are hoisted out of the comprehensions below: these lists can hold a million
    # positions on a dense 4 MiB input, and rebuilding a set per element is quadratic.
    expected_set = set(oracle_positions)
    scan_set = set(scan_positions)
    missing = [p for p in oracle_positions if p not in scan_set]
    if missing:
        return False, "scan is missing real position(s) %s" % missing[:4]
    extras = [p for p in scan_positions if p not in expected_set]
    in_range = [p for p in extras if p < code_units]
    if in_range:
        return False, ("extra scan position(s) %s lie inside the valid code-unit range "
                       "0..%d" % (in_range[:4], code_units - 1))
    if not extras:
        return False, "scan differs but reports no extra position"
    return True, ("%d extra scan position(s) beyond EOF, e.g. %s, on a %d-code-unit input; "
                  "oracle and linear agree, no real position missing, errorCount correct"
                  % (len(extras), extras[:3], code_units))


# (name, oracle, linear, scan, code_units, scan_count, expected_count, should_exclude)
GATE_SELF_TESTS = [
    ("accept: extras beyond EOF only",
     [10, 20], [10, 20], [10, 20, 2048, 2050], 2048, 2, 2, True),
    ("accept: single extra at exactly code_units",
     [5], [5], [5, 4096], 4096, 1, 1, True),
    ("reject: a real position is missing",
     [10, 20], [10, 20], [10, 2048], 2048, 2, 2, False),
    ("reject: extra inside the valid range",
     [10], [10], [10, 99], 2048, 1, 1, False),
    ("reject: scan errorCount disagrees",
     [10], [10], [10, 2048], 2048, 99, 1, False),
    ("reject: linear disagrees with the oracle",
     [10, 20], [10], [10, 20, 2048], 2048, 2, 2, False),
    ("reject: scan agrees (nothing to exclude)",
     [10], [10], [10], 2048, 1, 1, False),
    ("reject: differs but no extra position",
     [10, 20], [10, 20], [10], 2048, 2, 2, False),
]


def self_test_gate(binary, dataset_dir, timeout):
    """Prove the exclusion predicate accepts only the known symptom, and check it against
    real datasets of 2048 and 32768 code units in both encodings."""
    passed = failed = 0
    print("== exclusion predicate (pure, no timing) ==")
    for name, oracle_pos, linear, scan, units, scan_count, expected, want in \
            GATE_SELF_TESTS:
        got, reason = classify_scan_symptom(oracle_pos, linear, scan, units, scan_count,
                                            expected)
        if got == want:
            passed += 1
            print("  PASS %-44s -> %s" % (name, "exclude" if got else "abort"))
        else:
            failed += 1
            print("  FAIL %-44s wanted %s, got %s (%s)"
                  % (name, want, got, reason))

    print()
    print("== real datasets (gate only, no timing) ==")
    # 2048 code units is expected to show the symptom; 32768 is expected not to. Both are
    # checked rather than assumed, and neither expectation is a size rule -- it is a record
    # of what these particular generated streams do.
    cases = [("errdens_4KiB_d1pct", 2048, True), ("errdens_64KiB_d1pct", 32768, False)]
    for stem, units, expect_symptom in cases:
        for folder, encoding, be_flag in (("utf16le", "UTF-16LE", []),
                                          ("utf16be", "UTF-16BE", ["-be"])):
            path = os.path.join(dataset_dir, folder, "%s.%s.bin" % (stem, folder))
            if not os.path.isfile(path):
                print("  SKIP %-44s dataset not generated" % os.path.basename(path))
                continue
            data = open(path, "rb").read()
            code_units = oracle.decode_code_units(data, bool(be_flag))
            oracle_pos = oracle.malformed_positions(code_units)
            expected = len(oracle_pos)
            marks = be_flag + ["--emit-error-marks"]
            linear = parabix_positions(binary, marks + ["--print-positions",
                                                        "-thread-num=1"], path, timeout)
            scan_args = marks + ["--scan-error-marks", "-thread-num=1"]
            scan = parabix_positions(binary, scan_args, path, timeout)
            scan_count = parabix_count(binary, scan_args, path, timeout)
            symptom = scan != oracle_pos
            excluded, reason = (classify_scan_symptom(oracle_pos, linear, scan,
                                                      len(code_units), scan_count, expected)
                                if symptom else (False, "scan agrees with the oracle"))
            ok = (len(code_units) == units
                  and (excluded if expect_symptom else not symptom))
            if ok:
                passed += 1
                print("  PASS %-30s %s %5d units  %s"
                      % (stem, encoding, len(code_units),
                         "symptom excluded" if excluded else "clean, timed normally"))
            else:
                failed += 1
                print("  FAIL %-30s %s %5d units  expected %s, got %s"
                      % (stem, encoding, len(code_units),
                         "symptom" if expect_symptom else "clean", reason))
    print()
    print("%d passed, %d failed" % (passed, failed))
    return failed


def gate_dataset(dataset, binary, simdutf_bin, timeout):
    """Check every path on this dataset. Returns (skips, notes).

    `skips` maps operation -> reason; those operations are not timed for this dataset.
    A disagreement that is not a known, documented one aborts the whole run.
    """
    be = ["-be"] if dataset.big_endian else []
    expected = dataset.actual_error_count
    data = open(dataset.path, "rb").read()
    skips = {}
    notes = []

    # The oracle, independent of every kernel.
    units = oracle.decode_code_units(data, dataset.big_endian)
    oracle_positions = oracle.malformed_positions(units)
    oracle_count = len(oracle_positions) + (1 if oracle.has_odd_trailing_byte(data) else 0)
    if oracle_count != expected:
        raise SystemExit("CORRECTNESS GATE FAILED: %s: oracle reports %d errors, manifest "
                         "says %d" % (dataset.filename, oracle_count, expected))

    for operation, args in (("validate_scalar", be),
                            ("validate_simd", be + ["--simd"]),
                            ("emit_error_marks", be + ["--emit-error-marks"])):
        got = parabix_count(binary, args, dataset.path, timeout)
        if got != expected:
            raise SystemExit("CORRECTNESS GATE FAILED: %s: %s reports %d, expected %d"
                             % (dataset.filename, operation, got, expected))

    linear = parabix_positions(binary, be + ["--emit-error-marks", "--print-positions",
                                             "-thread-num=1"], dataset.path, timeout)
    if linear != oracle_positions:
        raise SystemExit("CORRECTNESS GATE FAILED: %s: --print-positions disagrees with the "
                         "oracle (%d vs %d positions)"
                         % (dataset.filename, len(linear), len(oracle_positions)))

    # The two-level scan. Issue #42 documented a defect in which it prints positions PAST THE
    # END of the input. That issue characterised the trigger as an input whose code-unit count
    # is an exact multiple of the 4096-unit scan stride; this corpus shows the trigger is
    # wider than that (a dense 2048-code-unit input reproduces it), so the check here is on
    # the SYMPTOM rather than on any assumed size rule:
    #
    #     every unexpected position is outside the valid code-unit range,
    #     no real position is missing, and the reported errorCount is still correct.
    #
    # Anything else -- a missing position, an in-range spurious position, a wrong count --
    # is a different failure and aborts the whole run rather than being excluded.
    scan_args = be + ["--emit-error-marks", "--scan-error-marks", "-thread-num=1"]
    scan = parabix_positions(binary, scan_args, dataset.path, timeout)
    if scan != oracle_positions:
        scan_count = parabix_count(binary, scan_args, dataset.path, timeout)
        excluded, reason = classify_scan_symptom(oracle_positions, linear, scan,
                                                 len(units), scan_count, expected)
        if excluded:
            skips["locate_scan"] = (
                "known scan-consumer symptom (%s); the position output is incorrect, so "
                "only locate_scan is excluded for this dataset" % reason)
            notes.append("`locate_scan` excluded for %s (%s)" % (dataset.filename, reason))
        else:
            raise SystemExit(
                "CORRECTNESS GATE FAILED: %s: --scan-error-marks disagrees with the oracle "
                "and it is NOT the known out-of-range-only symptom: %s. Stopping."
                % (dataset.filename, reason))

    # Repair: exact bytes against the oracle, and the repaired output must re-validate clean.
    # Checked once per dataset, outside the timed loop.
    proc = subprocess.run([binary] + be + ["--repair", dataset.path],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit("CORRECTNESS GATE FAILED: %s: --repair exited %d"
                         % (dataset.filename, proc.returncode))
    expected_repair = oracle.analyze(data, dataset.big_endian).repaired
    if proc.stdout != expected_repair:
        raise SystemExit("CORRECTNESS GATE FAILED: %s: repair bytes differ from the oracle"
                         % dataset.filename)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".rep") as handle:
        handle.write(proc.stdout)
        repaired_path = handle.name
    try:
        after = parabix_count(binary, be, repaired_path, timeout)
    finally:
        os.remove(repaired_path)
    if after != 0:
        raise SystemExit("CORRECTNESS GATE FAILED: %s: validate(repair(x)) = %d"
                         % (dataset.filename, after))

    if simdutf_bin is not None:
        # simdutf repair is only comparable on complete code units; every dataset here is
        # even-length, but check rather than assume.
        if dataset.size_bytes % 2:
            skips["simdutf_repair"] = ("odd-length input: simdutf's char16_t API has no "
                                       "odd-trailing-byte concept")
    return skips, notes


# --- Aggregation and reporting -------------------------------------------------

def aggregate(raw_rows, overhead):
    groups = {}
    for row in raw_rows:
        if row["status"] != "ok":
            continue
        key = (row["dataset"], row["encoding"], row["operation"])
        groups.setdefault(key, []).append(row)

    summaries = []
    for key, rows in sorted(groups.items()):
        times = [float(r["elapsed_seconds"]) for r in rows]
        rates = [float(r["throughput_mib_s"]) for r in rows]
        first = rows[0]
        fixed = overhead.get(first["operation"])
        median_time = statistics.median(times)
        # Subtracting the fixed per-process cost is only meaningful when the measurement is
        # substantially larger than that cost. When elapsed is close to the overhead the
        # difference is dominated by its own noise and the derived throughput explodes, so it
        # is left blank rather than published as a number.
        adjusted = ""
        if fixed is not None and median_time >= ADJUSTED_MIN_RATIO * fixed:
            adjusted = "%.2f" % ((int(first["size_bytes"]) / MIB) / (median_time - fixed))
        summaries.append({
            "dataset": first["dataset"], "encoding": first["encoding"],
            "size_bytes": first["size_bytes"], "target_density": first["target_density"],
            "actual_error_count": first["actual_error_count"],
            "operation": first["operation"], "implementation": first["implementation"],
            "category": first["category"], "count": len(times),
            "median_seconds": "%.9f" % median_time,
            "mean_seconds": "%.9f" % statistics.mean(times),
            "min_seconds": "%.9f" % min(times), "max_seconds": "%.9f" % max(times),
            "stddev_seconds": "%.9f" % (statistics.stdev(times) if len(times) > 1 else 0.0),
            "median_mib_s": "%.2f" % statistics.median(rates),
            "mean_mib_s": "%.2f" % statistics.mean(rates),
            "fixed_overhead_seconds": "" if fixed is None else "%.9f" % fixed,
            "adjusted_median_mib_s": adjusted,
            "status": "ok", "skip_reason": "",
        })

    # Skipped rows are reported too -- never silently dropped.
    for row in raw_rows:
        if row["status"] == "ok":
            continue
        summaries.append({
            "dataset": row["dataset"], "encoding": row["encoding"],
            "size_bytes": row["size_bytes"], "target_density": row["target_density"],
            "actual_error_count": row["actual_error_count"], "operation": row["operation"],
            "implementation": row["implementation"], "category": row["category"],
            "count": 0, "median_seconds": "", "mean_seconds": "", "min_seconds": "",
            "max_seconds": "", "stddev_seconds": "", "median_mib_s": "", "mean_mib_s": "",
            "fixed_overhead_seconds": "", "adjusted_median_mib_s": "",
            "status": row["status"], "skip_reason": row["skip_reason"],
        })
    return summaries


def median_by(summaries, predicate, key="median_mib_s"):
    values = [float(s[key]) for s in summaries
              if s["status"] == "ok" and s[key] and predicate(s)]
    return statistics.median(values) if values else None


OPERATION_DEFINITIONS = [
    ("validate_scalar", "validation", "scalar kernel; reports errorCount only"),
    ("validate_simd", "validation", "`--simd` byte-oriented Parabix kernel; errorCount only"),
    ("emit_error_marks", "marker generation",
     "`--emit-error-marks`: builds the one-bit-per-code-unit stream, still reports a count"),
    ("locate_linear", "location materialization",
     "`--print-positions`: visits every block and prints each ill-formed index"),
    ("locate_scan", "scan-based location",
     "`--scan-error-marks`: TwoLevelScanKernel, skips clean 4096-unit regions"),
    ("repair", "repair output", "`--repair`: writes repaired UTF-16 to stdout"),
    ("simdutf_validate", "validation", "`simdutf::validate_utf16{le,be}`"),
    ("simdutf_repair", "repair output", "`simdutf::to_well_formed_utf16{le,be}`"),
]


def write_summary(path, env, summaries, selected, gate_notes, simdutf_status, args,
                  chart_note):
    def table(rows, columns, headers):
        lines = ["| " + " | ".join(headers) + " |",
                 "| " + " | ".join(["---"] * len(headers)) + " |"]
        for row in rows:
            lines.append("| " + " | ".join(str(row[c]) for c in columns) + " |")
        return "\n".join(lines)

    ok = [s for s in summaries if s["status"] == "ok"]
    skipped = [s for s in summaries if s["status"] != "ok"]
    sizes = sorted(set(int(s["size_bytes"]) for s in ok))
    densities = sorted(set(float(s["target_density"]) for s in ok))

    lines = []
    lines.append("# UTF-16 pipeline benchmark (issue #45)")
    lines.append("")
    lines.append("Validation, error location, two-level scan and repair, measured over the "
                 "controlled error-density corpus from issue #44.")
    lines.append("")
    lines.append("**These numbers are machine-specific evidence from one run on one "
                 "machine, not universal claims.** Other processes were running, no "
                 "laboratory isolation was applied, and the figures should be reproduced "
                 "locally before being relied on.")
    lines.append("")

    lines.append("## Environment")
    lines.append("")
    for key in ("timestamp", "os", "architecture", "cpu_model", "logical_cpus",
                "python_version", "compiler", "git_commit", "branch", "dirty",
                "validator_binary", "simdutf", "cpu_affinity_applied", "cpu_affinity_note"):
        lines.append("- **%s**: %s" % (key, env[key]))
    lines.append("- **command**: `%s`" % env["command_line"])
    lines.append("")

    lines.append("## Methodology")
    lines.append("")
    lines.append("- Whole-process wall clock (`time.perf_counter_ns`), %d warm-up run(s) "
                 "and %d measured iteration(s) per command; every raw iteration is kept in "
                 "the CSV." % (env["warmups"], env["iterations"]))
    lines.append("- The **median** is the headline statistic; min, max, mean and standard "
                 "deviation are recorded alongside it.")
    lines.append("- Throughput is computed from **input bytes on disk**, not code units.")
    lines.append("- stdout and stderr of every measured command are redirected to the null "
                 "sink, so terminal I/O is never timed while the implementation still does "
                 "all of its work.")
    lines.append("- Operation order is rotated per dataset, deterministically from "
                 "`--seed %s`, to spread systematic order and thermal bias." % env["seed"])
    lines.append("- Each implementation's fixed per-process cost was measured separately on "
                 "a two-code-unit input; `adjusted_median_mib_s` in the CSV subtracts it. "
                 "That cost dominates the smallest inputs.")
    lines.append("- Dataset generation and helper compilation happen before any measurement "
                 "and are never timed.")
    lines.append("- A timeout of %s s applies to every run; a non-zero exit marks the row "
                 "failed rather than being silently dropped." % env["timeout_seconds"])
    lines.append("")

    lines.append("## Correctness gate")
    lines.append("")
    lines.append("Before any dataset is timed, scalar, `--simd`, `--emit-error-marks`, the "
                 "Python oracle, `--print-positions` and `--scan-error-marks` must all agree "
                 "with the manifest's `actual_error_count`, positions must match the oracle "
                 "exactly, and `--repair` output must equal the oracle's repaired bytes and "
                 "re-validate to zero errors. No timing row is written for a path that fails "
                 "its gate.")
    lines.append("")
    if gate_notes:
        lines.append("Excluded by the gate:")
        lines.append("")
        for note in sorted(set(gate_notes)):
            lines.append("- %s" % note)
        lines.append("")
    else:
        lines.append("No path was excluded on this run.")
        lines.append("")

    lines.append("## Selected matrix")
    lines.append("")
    lines.append("- datasets timed: %d (from `datasets/error_density/manifest.csv`)"
                 % len(set(s["dataset"] for s in ok)))
    lines.append("- sizes: %s" % ", ".join("%d B" % s for s in sizes))
    lines.append("- densities: %s" % ", ".join("%g%%" % d for d in densities))
    lines.append("- encodings: %s" % ", ".join(sorted(set(s["encoding"] for s in ok))))
    lines.append("")

    lines.append("## Operations")
    lines.append("")
    lines.append("| operation | category | definition |")
    lines.append("| --- | --- | --- |")
    for name, category, definition in OPERATION_DEFINITIONS:
        lines.append("| `%s` | %s | %s |" % (name, category, definition))
    lines.append("")

    lines.append("## Median throughput by operation")
    lines.append("")
    lines.append("Median of the per-dataset medians, over every dataset that passed its "
                 "gate. Small inputs are dominated by process start-up; see the adjusted "
                 "column in the CSV.")
    lines.append("")
    lines.append("`adjusted` subtracts that implementation's measured fixed per-process "
                 "cost, which differs per tool. It is reported only where the measurement is "
                 "at least %gx that cost; below that the corrected value would be dominated "
                 "by its own noise, so it is left as n/a rather than published."
                 % ADJUSTED_MIN_RATIO)
    lines.append("")
    lines.append("| operation | datasets | median MiB/s (raw) | median MiB/s (adjusted) |")
    lines.append("| --- | --- | --- | --- |")
    for name, _, _ in OPERATION_DEFINITIONS:
        rows = [s for s in ok if s["operation"] == name]
        raw = median_by(summaries, lambda s, n=name: s["operation"] == n)
        adj = median_by(summaries, lambda s, n=name: s["operation"] == n,
                        key="adjusted_median_mib_s")
        lines.append("| `%s` | %d | %s | %s |"
                     % (name, len(rows), "no data" if raw is None else "%.1f" % raw,
                        UNRESOLVED if adj is None else "%.1f" % adj))
    lines.append("")

    for label, key in (("raw (whole process)", "median_mib_s"),
                       ("overhead-adjusted", "adjusted_median_mib_s")):
        lines.append("## Throughput by size and operation -- %s" % label)
        lines.append("")
        lines.append("| size (bytes) | " + " | ".join("`%s`" % n for n, _, _ in
                                                      OPERATION_DEFINITIONS) + " |")
        lines.append("| --- | " + " | ".join(["---"] * len(OPERATION_DEFINITIONS)) + " |")
        for size in sizes:
            cells = []
            for name, _, _ in OPERATION_DEFINITIONS:
                value = median_by(summaries,
                                  lambda s, n=name, z=size: (s["operation"] == n
                                                             and int(s["size_bytes"]) == z),
                                  key=key)
                blank = UNRESOLVED if key == "adjusted_median_mib_s" else "no data"
                cells.append(blank if value is None else "%.1f" % value)
            lines.append("| %d | %s |" % (size, " | ".join(cells)))
        lines.append("")
    lines.append("At the smallest sizes the raw table is dominated by process start-up, so "
                 "the operations look identical there; that is a property of whole-process "
                 "timing, not of the kernels.")
    lines.append("")

    # State the start-up domination as a measured fact rather than a hunch. The fixed cost is
    # read back from the summary rows, which already carry it per operation.
    fixed_by_operation = {}
    for row in ok:
        if row["fixed_overhead_seconds"]:
            fixed_by_operation[row["operation"]] = float(row["fixed_overhead_seconds"])
    dominated = []
    for size in sizes:
        for name, _, _ in OPERATION_DEFINITIONS:
            rows = [s for s in ok if s["operation"] == name
                    and int(s["size_bytes"]) == size and s["median_seconds"]]
            fixed = fixed_by_operation.get(name)
            if not rows or fixed is None:
                continue
            median_time = statistics.median(float(r["median_seconds"]) for r in rows)
            if median_time < ADJUSTED_MIN_RATIO * fixed:
                dominated.append((size, name, median_time, fixed))
    if dominated:
        largest_dominated = max(size for size, _, _, _ in dominated)
        lines.append("**Resolution limit measured on this machine.** %d of the "
                     "size/operation combinations timed here have a median wall time below "
                     "%gx their implementation's fixed per-process cost, including most "
                     "operations at %d bytes, the largest size in this run. Across those "
                     "combinations the median wall time is about %.3f s against a median "
                     "measured start-up of about %.3f s. At this timing scope the work is "
                     "not separable from process start-up at these sizes, so the raw "
                     "throughput columns above should be read as *process* throughput, not "
                     "kernel throughput, and no per-kernel ranking is claimed from them."
                     % (len(dominated), ADJUSTED_MIN_RATIO, largest_dominated,
                        statistics.median([t for z, _, t, _ in dominated
                                           if z == largest_dominated]),
                        statistics.median([f for z, _, _, f in dominated
                                           if z == largest_dominated])))
        lines.append("")
        lines.append("Larger inputs (the 4 MiB tier, and repeating with `--sizes 4MiB`) are "
                     "where this matrix starts to measure the kernels rather than the "
                     "process. That is a property of the measurement, not of the code.")
        lines.append("")

    lines.append("## Density sensitivity")
    lines.append("")
    lines.append("Median MiB/s at each malformed-unit density, per operation.")
    lines.append("")
    lines.append("| density | " + " | ".join("`%s`" % n for n, _, _ in
                                             OPERATION_DEFINITIONS) + " |")
    lines.append("| --- | " + " | ".join(["---"] * len(OPERATION_DEFINITIONS)) + " |")
    for density in densities:
        cells = []
        for name, _, _ in OPERATION_DEFINITIONS:
            value = median_by(summaries,
                              lambda s, n=name, d=density: (
                                  s["operation"] == n
                                  and float(s["target_density"]) == d))
            cells.append("no data" if value is None else "%.1f" % value)
        lines.append("| %g%% | %s |" % (density, " | ".join(cells)))
    lines.append("")
    lines.append("A gap means every dataset at that density/operation was excluded by the "
                 "correctness gate (see above), not that the run failed.")
    lines.append("")
    lines.append("Unlike the cross-operation tables, a **trend down a single column is "
                 "meaningful even at this timing scope**: the size is fixed and the same "
                 "per-process start-up is present at every density, so it cancels out of the "
                 "comparison. Differences *between* columns remain confounded by start-up "
                 "and are not ranked here.")
    lines.append("")

    lines.append("## Pairwise comparisons")
    lines.append("")
    pairs = [("scalar vs SIMD validation", "validate_scalar", "validate_simd"),
             ("validation vs errorMarks generation", "validate_simd", "emit_error_marks"),
             ("linear vs two-level scan location", "locate_linear", "locate_scan"),
             ("Parabix vs simdutf validation", "validate_simd", "simdutf_validate"),
             ("Parabix vs simdutf repair", "repair", "simdutf_repair")]
    lines.append("Adjusted medians (fixed per-process cost subtracted), because at these "
                 "sizes the raw numbers are mostly process start-up.")
    lines.append("")
    lines.append("| comparison | A adjusted MiB/s | B adjusted MiB/s | B / A |")
    lines.append("| --- | --- | --- | --- |")
    for label, first, second in pairs:
        a = median_by(summaries, lambda s, n=first: s["operation"] == n,
                      key="adjusted_median_mib_s")
        b = median_by(summaries, lambda s, n=second: s["operation"] == n,
                      key="adjusted_median_mib_s")
        ratio = UNRESOLVED if not a or not b else "%.2fx" % (b / a)
        lines.append("| %s | %s | %s | %s |"
                     % (label, UNRESOLVED if a is None else "%.1f" % a,
                        UNRESOLVED if b is None else "%.1f" % b, ratio))
    lines.append("")
    lines.append("Ratios are medians on this machine over the datasets that passed the gate. "
                 "The overhead subtraction is an estimate, the two tools do not perform "
                 "identical work, and `locate_scan` is measured on a different (smaller) set "
                 "of datasets than the other paths -- see the exclusions above. These are "
                 "directional observations, not kernel-only speedups.")
    lines.append("")

    lines.append("## UTF-16LE vs UTF-16BE")
    lines.append("")
    lines.append("| operation | LE median MiB/s | BE median MiB/s |")
    lines.append("| --- | --- | --- |")
    for name, _, _ in OPERATION_DEFINITIONS:
        le = median_by(summaries, lambda s, n=name: (s["operation"] == n
                                                     and s["encoding"] == "UTF-16LE"))
        be = median_by(summaries, lambda s, n=name: (s["operation"] == n
                                                     and s["encoding"] == "UTF-16BE"))
        lines.append("| `%s` | %s | %s |"
                     % (name, "n/a" if le is None else "%.1f" % le,
                        "n/a" if be is None else "%.1f" % be))
    lines.append("")

    lines.append("## simdutf")
    lines.append("")
    lines.append("- status: %s" % simdutf_status)
    lines.append("- simdutf validation returns a boolean; Parabix reports an error count. "
                 "Both read the whole input, but they are not doing identical work.")
    lines.append("- simdutf repair is compared only on even-length inputs. Its `char16_t` "
                 "API has no notion of an odd trailing byte, so this project's "
                 "\"drop the byte and append one U+FFFD\" policy has no simdutf equivalent.")
    lines.append("")

    if skipped:
        lines.append("## Skipped rows")
        lines.append("")
        lines.append("| dataset | operation | reason |")
        lines.append("| --- | --- | --- |")
        for row in skipped[:40]:
            lines.append("| %s | `%s` | %s |"
                         % (row["dataset"], row["operation"], row["skip_reason"]))
        if len(skipped) > 40:
            lines.append("| ... | | %d more, see the CSV |" % (len(skipped) - 40))
        lines.append("")

    lines.append("## Charts")
    lines.append("")
    lines.append("- %s" % chart_note)
    lines.append("")

    lines.append("## Limitations and threats to validity")
    lines.append("")
    lines.append("- Whole-process timing includes start-up and, for Parabix, loading the "
                 "compiled pipeline. At 4 KiB that cost is most of the measurement; the "
                 "adjusted column exists for exactly this reason and is still an estimate.")
    lines.append("- One machine, one run, shared with other processes. No CPU pinning, no "
                 "cache control, no frequency pinning on this platform.")
    lines.append("- Operation order is rotated but the machine's thermal state still drifts "
                 "over a long run.")
    lines.append("- simdutf and Parabix are different tools solving overlapping but "
                 "non-identical problems; the ratios above are directional only.")
    lines.append("- The corpus is synthetic: uniformly spread malformed units, not the "
                 "clustering of real-world damaged data.")
    lines.append("- Densities are exact by construction, but each density is one sample of "
                 "one generated stream, not an average over many streams.")
    lines.append("")

    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")


# --- Main ----------------------------------------------------------------------

def parse_csv_list(text):
    if text is None:
        return None
    return [part.strip() for part in text.split(",") if part.strip()]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-dir", default=DEFAULT_DATASET_DIR)
    parser.add_argument("--manifest", default=None,
                        help="default: <dataset-dir>/manifest.csv")
    parser.add_argument("--bin", default=os.environ.get("UTF16VALIDATE_BIN", DEFAULT_BIN))
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="raw CSV path")
    parser.add_argument("--summary", default=DEFAULT_SUMMARY, help="Markdown summary path")
    parser.add_argument("--sizes", default=None,
                        help="comma-separated size labels, e.g. 4KiB,1MiB")
    parser.add_argument("--densities", default=None,
                        help="comma-separated target densities, e.g. 0,1,10")
    parser.add_argument("--encodings", default=None,
                        help="comma-separated: UTF-16LE,UTF-16BE")
    parser.add_argument("--operations", default=None,
                        help="comma-separated subset of: %s"
                             % ",".join(name for name, _, _, _ in OPERATIONS))
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--warmups", type=int, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--quick", action="store_true",
                        help="a small documented subset: sizes %s, densities %s, %d "
                             "iteration(s)" % (",".join(QUICK_SIZES),
                                               ",".join(QUICK_DENSITIES), QUICK_ITERATIONS))
    parser.add_argument("--no-simdutf", action="store_true")
    parser.add_argument("--cpu-affinity", type=int, default=None,
                        help="pin to this logical CPU where the platform supports it")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--overwrite", action="store_true",
                        help="replace existing result files instead of refusing")
    parser.add_argument("--self-test-gate", action="store_true",
                        help="check the locate_scan exclusion predicate (pure accept/reject "
                             "cases plus real 2048- and 32768-code-unit datasets) and exit; "
                             "nothing is timed")
    parser.add_argument("--estimate-only", action="store_true",
                        help="run the correctness gate and print a runtime estimate, then "
                             "stop without timing anything")
    args = parser.parse_args()

    args.iterations = args.iterations if args.iterations is not None else (
        QUICK_ITERATIONS if args.quick else DEFAULT_ITERATIONS)
    args.warmups = args.warmups if args.warmups is not None else (
        QUICK_WARMUPS if args.quick else DEFAULT_WARMUPS)
    manifest_path = args.manifest or os.path.join(args.dataset_dir, "manifest.csv")

    if not os.access(args.bin, os.X_OK):
        raise SystemExit("ERROR: utf16validate not found at %s\n"
                         "       Run ./scripts/setup_parabix.sh first." % args.bin)

    if args.self_test_gate:
        print("locate_scan exclusion predicate self-test (issue #45 correctness gate)")
        print("  binary : %s" % args.bin)
        print("  data   : %s" % args.dataset_dir)
        print()
        return 1 if self_test_gate(args.bin, args.dataset_dir, args.timeout) else 0

    sizes = parse_csv_list(args.sizes) or (QUICK_SIZES if args.quick else None)
    densities = parse_csv_list(args.densities) or (QUICK_DENSITIES if args.quick else None)
    encodings = parse_csv_list(args.encodings)
    operations = parse_csv_list(args.operations) or [n for n, _, _, _ in OPERATIONS]
    unknown = [o for o in operations if o not in [n for n, _, _, _ in OPERATIONS]]
    if unknown:
        raise SystemExit("ERROR: unknown operation(s): %s" % ", ".join(unknown))

    datasets = select_datasets(load_manifest(manifest_path, args.dataset_dir),
                               sizes, densities, encodings)

    for path in (args.output, os.path.splitext(args.output)[0] + ".json", args.summary):
        if os.path.exists(path) and not args.overwrite:
            raise SystemExit("ERROR: %s already exists.\n"
                             "       Pass --overwrite, or --output/--summary with a "
                             "different path (for example a timestamped run directory)."
                             % path)

    affinity_applied, affinity_note = apply_cpu_affinity(args.cpu_affinity)
    workdir = tempfile.mkdtemp(prefix="utf16-pipeline-bench-")
    devnull = open(os.devnull, "wb")
    raw_rows = []
    gate_notes = []

    try:
        simdutf_bin, simdutf_reason = (None, "disabled with --no-simdutf") \
            if args.no_simdutf else build_simdutf(workdir)
        if simdutf_bin is None:
            simdutf_status = "unavailable (%s)" % simdutf_reason
        else:
            simdutf_status = "available (compiled once, before any measured run)"

        print("UTF-16 pipeline benchmark (issue #45)")
        print("  datasets   : %d selected from %s" % (len(datasets), manifest_path))
        print("  operations : %s" % ", ".join(operations))
        print("  timing     : %d warm-up(s) + %d iteration(s), median reported"
              % (args.warmups, args.iterations))
        print("  simdutf    : %s" % simdutf_status)
        print("  affinity   : %s" % affinity_note)
        print()

        active = [o for o in operations
                  if simdutf_bin is not None or o not in SIMDUTF_OPERATIONS]
        for operation in operations:
            if operation in SIMDUTF_OPERATIONS and simdutf_bin is None:
                print("  NOTE: %s skipped -- %s" % (operation, simdutf_reason))

        print("== correctness gate ==")
        gates = {}
        for dataset in datasets:
            skips, notes = gate_dataset(dataset, args.bin, simdutf_bin, args.timeout)
            gates[dataset.filename] = skips
            gate_notes.extend(notes)
            print("  PASS %-44s %s errors=%d%s"
                  % (dataset.filename, dataset.encoding, dataset.actual_error_count,
                     "" if not skips else "  [excluded: %s]" % ", ".join(sorted(skips))))
        print()

        print("== fixed per-process overhead (measured once, never mixed into a timing) ==")
        overhead = measure_fixed_overhead(args.bin, simdutf_bin, args.warmups,
                                          args.iterations, args.timeout, devnull, workdir)
        for operation in sorted(overhead):
            value = overhead[operation]
            print("  %-20s %s" % (operation,
                                  "n/a" if value is None else "%.6f s" % value))
        print()

        total_runs = sum(len([o for o in active
                              if o not in gates[d.filename]]) for d in datasets) \
            * (args.warmups + args.iterations)
        estimated = total_runs * (statistics.median([v for v in overhead.values()
                                                     if v is not None] or [0.05]))
        print("planned measured runs: %d (rough lower-bound estimate %.0f s, excluding the "
              "per-dataset work itself)" % (total_runs, estimated))
        print()
        if args.estimate_only:
            print("--estimate-only: stopping before any timing.")
            return 0

        print("== timing ==")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        commit, branch, dirty = git_info()
        rng = random.Random("utf16-pipeline|%d" % args.seed)

        for index, dataset in enumerate(datasets):
            # Rotate the operation order per dataset, deterministically, so no operation is
            # always measured first on a cold machine.
            order = list(active)
            rng.shuffle(order)
            for operation in order:
                spec = [o for o in OPERATIONS if o[0] == operation][0]
                _, implementation, category, _ = spec
                base = {"run_id": run_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "git_commit": commit, "branch": branch, "dirty": dirty,
                        "dataset": dataset.filename, "encoding": dataset.encoding,
                        "size_bytes": dataset.size_bytes, "code_units": dataset.code_units,
                        "target_density": dataset.target_density,
                        "actual_error_count": dataset.actual_error_count,
                        "operation": operation, "implementation": implementation,
                        "category": category}

                skip_reason = gates[dataset.filename].get(operation)
                if skip_reason:
                    raw_rows.append(dict(base, iteration="", elapsed_seconds="",
                                         throughput_mib_s="", exit_code="",
                                         status="skipped", skip_reason=skip_reason,
                                         command=""))
                    continue

                cmd = command_for(operation, dataset, args.bin, simdutf_bin)
                try:
                    times, code = measure(cmd, args.warmups, args.iterations,
                                          args.timeout, devnull)
                except subprocess.TimeoutExpired:
                    raw_rows.append(dict(base, iteration="", elapsed_seconds="",
                                         throughput_mib_s="", exit_code="timeout",
                                         status="failed",
                                         skip_reason="exceeded --timeout %ss"
                                                     % args.timeout,
                                         command=" ".join(cmd)))
                    print("  FAIL %-44s %-18s timeout" % (dataset.filename, operation))
                    continue

                if code != 0 or not times:
                    raw_rows.append(dict(base, iteration="", elapsed_seconds="",
                                         throughput_mib_s="", exit_code=code,
                                         status="failed",
                                         skip_reason="non-zero exit code",
                                         command=" ".join(cmd)))
                    print("  FAIL %-44s %-18s exit=%d"
                          % (dataset.filename, operation, code))
                    continue

                mib = dataset.size_bytes / MIB
                for iteration, elapsed in enumerate(times):
                    raw_rows.append(dict(base, iteration=iteration,
                                         elapsed_seconds="%.9f" % elapsed,
                                         throughput_mib_s="%.4f" % (mib / elapsed),
                                         exit_code=0, status="ok", skip_reason="",
                                         command=" ".join(cmd)))
            print("  [%3d/%3d] %-44s %s"
                  % (index + 1, len(datasets), dataset.filename, dataset.encoding))
    finally:
        devnull.close()
        shutil.rmtree(workdir, ignore_errors=True)

    summaries = aggregate(raw_rows, overhead)
    env = environment(args, simdutf_status, affinity_applied, affinity_note)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_COLUMNS)
        writer.writeheader()
        for row in raw_rows:
            writer.writerow(row)

    summary_csv = os.path.splitext(args.output)[0] + "_aggregate.csv"
    with open(summary_csv, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        for row in summaries:
            writer.writerow(row)

    json_path = os.path.splitext(args.output)[0] + ".json"
    with open(json_path, "w") as handle:
        json.dump({"environment": env, "raw": raw_rows, "aggregate": summaries,
                   "fixed_overhead_seconds": overhead,
                   "gate_exclusions": sorted(set(gate_notes))}, handle, indent=2)
        handle.write("\n")

    chart_note = ("regenerate with `python3 benchmarks/plot_utf16_pipeline_benchmark.py "
                  "--input %s`" % os.path.relpath(args.output, REPO_ROOT))
    write_summary(args.summary, env, summaries, datasets, gate_notes, simdutf_status,
                  args, chart_note)

    ok = len([r for r in raw_rows if r["status"] == "ok"])
    failed = len([r for r in raw_rows if r["status"] == "failed"])
    skipped = len([r for r in raw_rows if r["status"] == "skipped"])
    print()
    print("%d raw timing rows, %d skipped, %d failed" % (ok, skipped, failed))
    print("raw     : %s" % args.output)
    print("aggregate: %s" % summary_csv)
    print("json    : %s" % json_path)
    print("summary : %s" % args.summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
