#!/usr/bin/env python3
"""Run the UTF-16 validation benchmark matrix and write a CSV.

Modes (see docs/benchmark_methodology.md):

  scalar                 utf16validate <file>                       -> errorCount = N
  parabix_simd_t1        utf16validate --simd --thread-num=1        -> errorCount = N
  parabix_simd_t2        utf16validate --simd --thread-num=2        -> errorCount = N
  parabix_simd_t3        utf16validate --simd --thread-num=3        -> errorCount = N
  parabix_simd_default   utf16validate --simd                       -> errorCount = N
  simdutf                utf16validate_cl <file>   (--include-simdutf)  -> valid = true/false

The Clausecker-Lemire/simdutf baseline is single-threaded and reports validity, not an
error count. It is therefore only comparable against parabix_simd_t1, on valid input
(comparison group B). Malformed rows are recorded for accept/reject correctness
(group D) and are NOT used for cross-tool throughput claims.

Every timed run is checked against the dataset's expected result (from the generator's
JSON sidecar). A run that reports the wrong answer is marked result_ok=false and is
never used as a speedup baseline.

Timing is whole-process wall clock (timing_scope=whole_process), so it includes process
startup and, for Parabix, loading the compiled pipeline. Each tool's fixed cost is
measured separately on a tiny input and reported as fixed_overhead_seconds, with an
overhead-adjusted throughput alongside the raw one.

Examples:
    # smoke test
    python3 benchmarks/run_utf16_benchmark.py --datasets mixed_multilingual \\
        --sizes-mb 1 --warmups 1 --repetitions 2 --include-simdutf \\
        --output /tmp/smoke.csv

    # final matrix
    python3 benchmarks/run_utf16_benchmark.py --datasets all --sizes-mb 128,256,512 \\
        --include-simdutf --output results/utf16_final.csv
"""

import argparse
import csv
import json
import os
import platform
import re
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

MIB = 1024 * 1024
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATASETS = ["default", "english_ascii_heavy", "european_accented", "south_asian",
            "cjk", "emoji_heavy", "mixed_multilingual"]

# (mode, tool, extra args, thread_count, comparison_group)
PARABIX_MODES = [
    ("scalar",               "scalar",       [],                            "1",       "A"),
    ("parabix_simd_t1",      "parabix_simd", ["--simd", "--thread-num=1"],  "1",       "A,B,C"),
    ("parabix_simd_t2",      "parabix_simd", ["--simd", "--thread-num=2"],  "2",       "C"),
    ("parabix_simd_t3",      "parabix_simd", ["--simd", "--thread-num=3"],  "3",       "C"),
    ("parabix_simd_default", "parabix_simd", ["--simd"],                    "default", "C"),
]
SIMDUTF_MODE = ("simdutf", "simdutf", [], "1", "B")

COLUMNS = [
    "timestamp", "host", "platform", "machine", "processor", "python_version",
    "tool", "mode", "binary", "dataset_type", "error_pattern", "error_rate_percent",
    "bytes", "mib", "thread_count", "warmups", "repetitions",
    "min_seconds", "median_seconds", "mean_seconds", "stdev_seconds",
    "throughput_mib_s", "fixed_overhead_seconds", "throughput_adjusted_mib_s",
    "speedup_vs_scalar", "speedup_vs_parabix_t1",
    "expected_result", "reported_result", "result_ok",
    "comparison_group", "timing_scope",
    "parabix_commit", "simdutf_commit", "simdutf_impl",
    "individual_times_seconds",
]


# --- helpers -------------------------------------------------------------------

def parse_list(text, what, valid=None):
    values = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "all" and valid is not None:
            values.extend(valid)
            continue
        if valid is not None and part not in valid:
            raise ValueError("unknown %s %r (choose from: %s, all)"
                             % (what, part, ", ".join(valid)))
        values.append(part)
    if not values:
        raise ValueError("no %s provided" % what)
    seen, unique = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique


def parse_ints(text, what):
    values = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value <= 0:
            raise ValueError("%s must be positive (got %r)" % (what, part))
        values.append(value)
    if not values:
        raise ValueError("no %s provided" % what)
    return values


def rate_tag(rate):
    return "%g" % float(rate)


def valid_name(dataset, size_mb):
    if dataset == "default":
        return "valid_utf16le_%dMiB.bin" % size_mb
    return "valid_utf16le_%s_%dMiB.bin" % (dataset, size_mb)


def malformed_name(dataset, pattern, rate, size_mb):
    return "malformed_utf16le_%s_%s_err%s_%dMiB.bin" % (
        dataset, pattern, rate_tag(rate), size_mb)


def grep_constant(path, key):
    """Read a shell constant like KEY="value" from a setup script."""
    try:
        with open(path) as handle:
            for line in handle:
                match = re.match(r'\s*%s="([^"]+)"' % re.escape(key), line)
                if match:
                    return match.group(1)
    except OSError:
        pass
    return ""


def expected_error_count(bin_path):
    """Expected count from the generator's sidecar; valid files default to 0.

    Sidecars written before the malformed generator existed have no
    expected_error_count key, so fall back to 0 for a `valid_` dataset.
    """
    side = bin_path + ".json"
    if os.path.isfile(side):
        with open(side) as handle:
            meta = json.load(handle)
        if "expected_error_count" in meta:
            return int(meta["expected_error_count"])
    if os.path.basename(bin_path).startswith("valid_"):
        return 0
    raise SystemExit("ERROR: no expected_error_count for %s (regenerate the dataset)" % bin_path)


# --- running -------------------------------------------------------------------

PARABIX_RE = re.compile(r"errorCount\s*=\s*(\d+)")
SIMDUTF_RE = re.compile(r"valid\s*=\s*(true|false)")


def run_once(cmd, tool):
    """Run one validation. Returns (elapsed_seconds, reported_result_string)."""
    start = time.perf_counter_ns()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    end = time.perf_counter_ns()
    if proc.returncode != 0:
        raise RuntimeError("command failed (rc=%d): %s\n%s"
                           % (proc.returncode, " ".join(cmd), proc.stderr.strip()))
    if tool == "simdutf":
        match = SIMDUTF_RE.search(proc.stdout)
        if not match:
            raise RuntimeError("no 'valid =' in output of %s: %r" % (" ".join(cmd), proc.stdout))
        reported = "valid = %s" % match.group(1)
    else:
        match = PARABIX_RE.search(proc.stdout)
        if not match:
            raise RuntimeError("no 'errorCount =' in output of %s: %r" % (" ".join(cmd), proc.stdout))
        reported = "errorCount = %s" % match.group(1)
    return (end - start) / 1e9, reported


def measure(cmd, tool, warmups, repetitions):
    for _ in range(warmups):
        run_once(cmd, tool)
    times, reported = [], None
    for _ in range(repetitions):
        elapsed, reported = run_once(cmd, tool)
        times.append(elapsed)
    return times, reported


def expected_string(tool, expected_errors):
    if tool == "simdutf":
        return "valid = %s" % ("true" if expected_errors == 0 else "false")
    return "errorCount = %d" % expected_errors


def measure_fixed_overhead(binaries, warmups, repetitions):
    """Median wall-clock of each tool on a tiny valid input.

    This approximates the constant per-process cost (startup, and for Parabix loading
    the compiled pipeline), which dominates small inputs and is NOT the same for both
    tools. See docs/benchmark_methodology.md section 5.
    """
    overhead = {}
    with tempfile.TemporaryDirectory() as tmp:
        tiny = os.path.join(tmp, "tiny_valid.bin")
        with open(tiny, "wb") as handle:
            handle.write(struct.pack("<2H", 0x0041, 0x0042))    # two BMP code units
        for tool, binary in binaries.items():
            cmd = [binary] + (["--simd"] if tool == "parabix_simd" else []) + [tiny]
            times, _ = measure(cmd, tool, warmups, repetitions)
            overhead[tool] = statistics.median(times)
    return overhead


# --- main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary",
                        default=os.path.join(REPO_ROOT, ".deps", "parabix", "build", "bin", "utf16validate"))
    parser.add_argument("--simdutf-binary",
                        default=os.path.join(REPO_ROOT, ".deps", "baselines", "bin", "utf16validate_cl"))
    parser.add_argument("--include-simdutf", action="store_true",
                        help="also benchmark the Clausecker-Lemire/simdutf baseline")
    parser.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "benchmarks", "data"))
    parser.add_argument("--output", default=os.path.join(REPO_ROOT, "results", "utf16_benchmark.csv"))
    parser.add_argument("--datasets", default="default",
                        help="comma-separated: %s, or 'all'" % ", ".join(DATASETS))
    parser.add_argument("--sizes-mb", default="1,8,32,64",
                        help="comma-separated sizes in MiB (final runs: 128,256,512)")
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--malformed-patterns", default=None,
                        help="also run malformed datasets (accept/reject, group D)")
    parser.add_argument("--malformed-rates", default="0.01")
    parser.add_argument("--skip-overhead", action="store_true",
                        help="skip the fixed-overhead measurement")
    args = parser.parse_args()

    if args.warmups < 0 or args.repetitions <= 0:
        raise SystemExit("ERROR: --warmups must be >= 0 and --repetitions must be > 0")

    try:
        datasets = parse_list(args.datasets, "dataset", DATASETS)
        sizes = parse_ints(args.sizes_mb, "sizes")
        patterns = parse_list(args.malformed_patterns, "error pattern") if args.malformed_patterns else []
        rates = [float(r) for r in args.malformed_rates.split(",") if r.strip()] if patterns else []
    except ValueError as ex:
        raise SystemExit("ERROR: %s" % ex)

    parabix = os.path.abspath(args.binary)
    if not (os.path.isfile(parabix) and os.access(parabix, os.X_OK)):
        raise SystemExit("ERROR: Parabix validator not found: %s\n"
                         "       Run ./scripts/setup_parabix.sh first." % parabix)

    modes = list(PARABIX_MODES)
    simdutf = os.path.abspath(args.simdutf_binary)
    simdutf_impl = ""
    if args.include_simdutf:
        if not (os.path.isfile(simdutf) and os.access(simdutf, os.X_OK)):
            raise SystemExit("ERROR: simdutf baseline not found: %s\n"
                             "       Run ./scripts/setup_clausecker_lemire.sh first." % simdutf)
        modes.append(SIMDUTF_MODE)
        out = subprocess.run([simdutf, "--impl"], capture_output=True, text=True)
        simdutf_impl = out.stdout.split("=")[-1].strip() if out.returncode == 0 else ""

    binary_for = {"scalar": parabix, "parabix_simd": parabix, "simdutf": simdutf}

    overhead = {}
    if not args.skip_overhead:
        tools = {"scalar": parabix, "parabix_simd": parabix}
        if args.include_simdutf:
            tools["simdutf"] = simdutf
        print("== measuring fixed per-process overhead (tiny input) ==")
        overhead = measure_fixed_overhead(tools, args.warmups, args.repetitions)
        for tool, value in sorted(overhead.items()):
            print("   %-14s %.4f s" % (tool, value))
        print()

    fixed = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "parabix_commit": grep_constant(
            os.path.join(REPO_ROOT, "scripts", "setup_parabix.sh"), "PARABIX_COMMIT"),
        "simdutf_commit": grep_constant(
            os.path.join(REPO_ROOT, "scripts", "setup_clausecker_lemire.sh"),
            "SIMDUTF_COMMIT") if args.include_simdutf else "",
        "simdutf_impl": simdutf_impl,
        "timing_scope": "whole_process",
    }

    # Build the work list: valid datasets first (the primary workload), then malformed.
    work = []
    for dataset in datasets:
        for size_mb in sizes:
            work.append((dataset, "", "", valid_name(dataset, size_mb), size_mb))
        for pattern in patterns:
            for rate in rates:
                for size_mb in sizes:
                    work.append((dataset, pattern, rate,
                                 malformed_name(dataset, pattern, rate, size_mb), size_mb))

    rows, table = [], []

    for dataset, pattern, rate, name, size_mb in work:
        path = os.path.join(args.data_dir, name)
        if not os.path.isfile(path):
            raise SystemExit("ERROR: dataset not found: %s\n"
                             "       Generate it with benchmarks/generate_utf16_benchmark.py" % path)
        nbytes = os.path.getsize(path)
        mib = nbytes / MIB
        expected_errors = expected_error_count(path)
        malformed = bool(pattern)

        print("== %s (%d bytes) ==" % (name, nbytes))
        results = []
        for mode, tool, extra, threads, group in modes:
            cmd = [binary_for[tool]] + extra + [path]
            times, reported = measure(cmd, tool, args.warmups, args.repetitions)
            expected = expected_string(tool, expected_errors)
            ok = (reported == expected)
            median = statistics.median(times)
            flag = "" if ok else "   !! expected %s, got %s" % (expected, reported)
            print("   %-22s median=%.4fs%s" % (mode, median, flag))
            results.append((mode, tool, extra, threads, group, times, reported, expected, ok))

        def median_of(target_mode):
            for mode, _t, _e, _th, _g, times, _r, _x, ok in results:
                if mode == target_mode and ok:
                    return statistics.median(times)
            return None

        # Baselines only come from rows that produced the correct answer.
        scalar_median = median_of("scalar")
        t1_median = median_of("parabix_simd_t1")

        for mode, tool, extra, threads, group, times, reported, expected, ok in results:
            tmed = statistics.median(times)
            throughput = mib / tmed if tmed > 0 else 0.0
            over = overhead.get(tool, "")
            adjusted = ""
            if over != "" and tmed > over:
                adjusted = mib / (tmed - over)

            # Malformed rows are group D: accept/reject correctness, not a throughput
            # comparison (simdutf may exit early at the first error). No speedups.
            if malformed:
                group_out = "D"
                sp_scalar = sp_t1 = ""
            else:
                group_out = group
                sp_scalar = (scalar_median / tmed) if (ok and scalar_median and tmed > 0) else ""
                # Cross-tool speedup is only meaningful against our own single-thread SIMD.
                sp_t1 = (t1_median / tmed) if (ok and t1_median and tmed > 0) else ""

            row = dict(fixed)
            row.update({
                "tool": tool,
                "mode": mode,
                "binary": binary_for[tool],
                "dataset_type": dataset,
                "error_pattern": pattern,
                "error_rate_percent": rate if malformed else "",
                "bytes": nbytes,
                "mib": mib,
                "thread_count": threads,
                "warmups": args.warmups,
                "repetitions": args.repetitions,
                "min_seconds": min(times),
                "median_seconds": tmed,
                "mean_seconds": statistics.fmean(times),
                "stdev_seconds": statistics.pstdev(times),
                "throughput_mib_s": throughput,
                "fixed_overhead_seconds": over,
                "throughput_adjusted_mib_s": adjusted,
                "speedup_vs_scalar": sp_scalar,
                "speedup_vs_parabix_t1": sp_t1,
                "expected_result": expected,
                "reported_result": reported,
                "result_ok": ok,
                "comparison_group": group_out,
                "individual_times_seconds": ";".join(repr(t) for t in times),
            })
            rows.append(row)
            table.append((name, mode, threads, tmed, throughput, sp_t1, ok, group_out))

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print()
    print("Wrote %d rows to %s" % (len(rows), args.output))
    print()
    header = ("%-34s | %-22s | %-7s | %-11s | %-18s | %-10s | %-2s | %s"
              % ("Dataset", "Mode", "Threads", "Median (s)", "Throughput (MiB/s)",
                 "vs simd_t1", "Gp", "OK"))
    print(header)
    print("-" * len(header))
    for name, mode, threads, tmed, throughput, sp_t1, ok, group in table:
        sp = "%.2fx" % sp_t1 if isinstance(sp_t1, float) else "-"
        print("%-34s | %-22s | %-7s | %11.4f | %18.1f | %-10s | %-2s | %s"
              % (name[:34], mode, threads, tmed, throughput, sp, group,
                 "yes" if ok else "NO"))

    bad = [r for r in rows if not r["result_ok"]]
    if bad:
        print()
        print("WARNING: %d row(s) reported an unexpected result and are excluded from "
              "speedup baselines." % len(bad))


if __name__ == "__main__":
    main()
