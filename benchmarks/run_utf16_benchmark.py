#!/usr/bin/env python3
"""Run the preliminary UTF-16LE validation benchmark and write a CSV.

Compares, for each dataset:
  - scalar validator
  - SIMD validator (plain --simd, i.e. Parabix default threading)
  - SIMD validator with --thread-num=N for each requested thread count

Wall-clock time is measured with time.perf_counter_ns(). Every run must exit
successfully and report "errorCount = 0"; otherwise the benchmark stops.

Note: each run is a separate process, so timings include process startup and
Parabix pipeline compilation. Warmup runs populate Parabix's on-disk object
cache so the measured repetitions load the compiled pipeline instead of
recompiling it.
"""

import argparse
import csv
import os
import platform
import re
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

MIB = 1024 * 1024
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_int_list(text, what):
    values = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value <= 0:
            raise ValueError("%s must be positive integers (got %r)" % (what, part))
        values.append(value)
    if not values:
        raise ValueError("no %s provided" % what)
    return values


def build_configs(thread_counts):
    """Return a list of (mode, thread_label, extra_args).

    Plain --simd is kept as its own configuration because it represents the
    Parabix default threading behaviour, which may differ from an explicit
    --thread-num=1.
    """
    configs = [
        ("scalar", "", []),
        ("simd", "default", ["--simd"]),
    ]
    for t in thread_counts:
        configs.append(("simd", str(t), ["--simd", "--thread-num=%d" % t]))
    return configs


def run_once(cmd):
    """Run one validation and return elapsed seconds; raise on any problem."""
    start = time.perf_counter_ns()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    end = time.perf_counter_ns()
    if proc.returncode != 0:
        raise RuntimeError("command failed (rc=%d): %s\n%s"
                           % (proc.returncode, " ".join(cmd), proc.stderr.strip()))
    counts = re.findall(r"errorCount\s*=\s*(\d+)", proc.stdout)
    if not counts or any(int(count) != 0 for count in counts):
        raise RuntimeError("expected only errorCount = 0 results but got: %r for %s"
                           % (proc.stdout.strip(), " ".join(cmd)))
    return (end - start) / 1e9


def measure(binary, path, extra_args, warmups, repetitions):
    cmd = [binary] + extra_args + [path]
    for _ in range(warmups):
        run_once(cmd)
    times = []
    for _ in range(repetitions):
        times.append(run_once(cmd))
    return times


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary",
                        default=os.path.join(REPO_ROOT, ".deps", "parabix", "build", "bin", "utf16validate"))
    parser.add_argument("--data-dir",
                        default=os.path.join(REPO_ROOT, "benchmarks", "data"))
    parser.add_argument("--output",
                        default=os.path.join(REPO_ROOT, "results", "utf16_benchmark.csv"))
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--thread-counts", default="1,2,3")
    parser.add_argument("--sizes-mb", default="1,8,32,64")
    args = parser.parse_args()

    if args.warmups < 0 or args.repetitions <= 0:
        raise SystemExit("ERROR: --warmups must be >= 0 and --repetitions must be > 0")

    try:
        thread_counts = parse_int_list(args.thread_counts, "thread counts")
        sizes = parse_int_list(args.sizes_mb, "sizes")
    except ValueError as ex:
        raise SystemExit("ERROR: %s" % ex)

    binary = os.path.abspath(args.binary)
    if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
        raise SystemExit("ERROR: validator binary not found or not executable: %s\n"
                         "       Run ./scripts/setup_parabix.sh first." % binary)

    configs = build_configs(thread_counts)

    fixed = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "binary": binary,
    }

    columns = [
        "timestamp", "host", "platform", "machine", "processor", "python_version",
        "binary", "dataset", "bytes", "mib", "mode", "thread_count",
        "warmups", "repetitions", "min_seconds", "median_seconds", "mean_seconds",
        "stdev_seconds", "throughput_mib_s", "speedup_vs_scalar",
        "individual_times_seconds",
    ]

    rows = []
    table = []  # (dataset, mode, thread_label, median, throughput, speedup)

    for size_mb in sizes:
        name = "valid_utf16le_%dMiB.bin" % size_mb
        path = os.path.join(args.data_dir, name)
        if not os.path.isfile(path):
            raise SystemExit("ERROR: dataset not found: %s\n"
                             "       Generate it first with generate_utf16_benchmark.py." % path)
        nbytes = os.path.getsize(path)
        mib = nbytes / MIB

        print("== %s (%d bytes) ==" % (name, nbytes))
        size_results = []  # (mode, thread_label, times)
        for mode, thread_label, extra_args in configs:
            label = mode if not thread_label else "%s(%s)" % (mode, thread_label)
            print("   measuring %-14s ..." % label, end="", flush=True)
            times = measure(binary, path, extra_args, args.warmups, args.repetitions)
            print(" median=%.4fs" % statistics.median(times))
            size_results.append((mode, thread_label, extra_args, times))

        # scalar median for this dataset is the speedup baseline
        scalar_median = None
        for mode, thread_label, extra_args, times in size_results:
            if mode == "scalar":
                scalar_median = statistics.median(times)
                break

        for mode, thread_label, extra_args, times in size_results:
            tmin = min(times)
            tmed = statistics.median(times)
            tmean = statistics.fmean(times)
            tstd = statistics.pstdev(times)
            throughput = mib / tmed if tmed > 0 else 0.0
            speedup = (scalar_median / tmed) if (scalar_median and tmed > 0) else ""
            row = dict(fixed)
            row.update({
                "dataset": name,
                "bytes": nbytes,
                "mib": mib,
                "mode": mode,
                "thread_count": thread_label,
                "warmups": args.warmups,
                "repetitions": args.repetitions,
                "min_seconds": tmin,
                "median_seconds": tmed,
                "mean_seconds": tmean,
                "stdev_seconds": tstd,
                "throughput_mib_s": throughput,
                "speedup_vs_scalar": speedup,
                "individual_times_seconds": ";".join(repr(t) for t in times),
            })
            rows.append(row)
            table.append((name, mode, thread_label, tmed, throughput, speedup))

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print()
    print("Wrote %d rows to %s" % (len(rows), args.output))
    print()
    header = "%-26s | %-6s | %-8s | %-11s | %-18s | %-8s" % (
        "Dataset", "Mode", "Threads", "Median (s)", "Throughput (MiB/s)", "Speedup")
    print(header)
    print("-" * len(header))
    for name, mode, thread_label, tmed, throughput, speedup in table:
        speedup_text = "%.2fx" % speedup if isinstance(speedup, float) else "-"
        print("%-26s | %-6s | %-8s | %11.4f | %18.1f | %-8s" % (
            name, mode, (thread_label or "-"), tmed, throughput, speedup_text))


if __name__ == "__main__":
    main()
