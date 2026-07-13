#!/usr/bin/env python3
"""Analyze Parabix thread scaling from a benchmark CSV.

Reads the CSV produced by benchmarks/run_utf16_benchmark.py and reports, per dataset
and input size:

  - throughput for parabix_simd_t1 / t2 / t3 / default
  - speedup over parabix_simd_t1  (the only correct baseline for thread scaling)
  - thread efficiency = speedup / thread_count
  - the plateau point: the thread count beyond which adding a thread buys < 5%

This is comparison group C in docs/benchmark_methodology.md. It deliberately looks at
Parabix modes ONLY. The Clausecker-Lemire/simdutf baseline is single-threaded and is
never included here: comparing multi-threaded Parabix against it would conflate
parallelism with SIMD quality.

Rows whose result_ok is false are ignored (a run that produced the wrong answer must
not be used for a performance claim).

Usage:
    python3 benchmarks/analyze_thread_scaling.py --input /tmp/bench.csv
    python3 benchmarks/analyze_thread_scaling.py --input /tmp/bench.csv \\
        --output /tmp/thread_scaling.md
"""

import argparse
import csv
import os
import sys

# Thread scaling is a Parabix-only question.
THREAD_MODES = ["parabix_simd_t1", "parabix_simd_t2", "parabix_simd_t3",
                "parabix_simd_default"]
THREADS = {"parabix_simd_t1": 1, "parabix_simd_t2": 2, "parabix_simd_t3": 3}

# Adding a thread must buy at least this much to count as "still scaling".
PLATEAU_GAIN = 1.05


def is_ok(row):
    return str(row.get("result_ok", "True")).strip().lower() in ("true", "1", "yes")


def num(row, key):
    try:
        return float(row.get(key, "") or "")
    except ValueError:
        return None


def load(path):
    if not os.path.isfile(path):
        raise SystemExit("ERROR: CSV not found: %s\n"
                         "       Run the benchmark harness first "
                         "(see docs/threading_analysis.md)." % path)
    with open(path, newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("ERROR: CSV is empty: %s" % path)
    return rows


def plateau_point(speedups):
    """Smallest thread count after which another thread buys less than PLATEAU_GAIN."""
    ordered = [1, 2, 3]
    for i, n in enumerate(ordered[:-1]):
        nxt = ordered[i + 1]
        if speedups.get(n) is None or speedups.get(nxt) is None:
            continue
        if speedups[nxt] < speedups[n] * PLATEAU_GAIN:
            return n
    if speedups.get(3) is not None:
        return 3            # still improving at the highest count we tested
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", required=True, help="benchmark CSV")
    parser.add_argument("--output", help="write the Markdown report here (default: stdout)")
    args = parser.parse_args()

    rows = load(args.input)

    # Index the usable Parabix rows by (dataset, MiB, mode).
    data = {}
    skipped = 0
    for row in rows:
        mode = row.get("mode", "")
        if mode not in THREAD_MODES:
            continue
        if row.get("error_pattern"):
            continue                      # malformed rows are group D, not a throughput study
        if not is_ok(row):
            skipped += 1
            continue
        key = (row.get("dataset_type", "?"), num(row, "mib") or 0.0)
        data.setdefault(key, {})[mode] = row

    if not data:
        raise SystemExit("ERROR: no usable Parabix thread rows in %s\n"
                         "       Expected modes: %s" % (args.input, ", ".join(THREAD_MODES)))

    lines = []
    lines.append("# Parabix thread scaling")
    lines.append("")
    first = rows[0]
    lines.append("- Source CSV: `%s`" % os.path.basename(args.input))
    lines.append("- Platform: %s" % first.get("platform", "?"))
    lines.append("- Warmups: %s, repetitions: %s"
                 % (first.get("warmups", "?"), first.get("repetitions", "?")))
    lines.append("- Timing scope: %s" % (first.get("timing_scope", "") or "whole_process"))
    if skipped:
        lines.append("- **%d row(s) skipped** (result_ok=false)" % skipped)
    lines.append("")
    lines.append("Baseline for every speedup is `parabix_simd_t1`. "
                 "Efficiency = speedup / thread count.")
    lines.append("")
    lines.append("| Dataset | MiB | Mode | Threads | Throughput (MiB/s) | Speedup vs t1 | Efficiency |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")

    summary = []
    for (dataset, mib), modes in sorted(data.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        base = modes.get("parabix_simd_t1")
        base_median = num(base, "median_seconds") if base else None

        speedups = {}
        for mode in THREAD_MODES:
            row = modes.get(mode)
            if row is None:
                continue
            median = num(row, "median_seconds")
            thr = num(row, "throughput_mib_s")
            speedup = (base_median / median) if (base_median and median) else None
            n = THREADS.get(mode)
            if n is not None:
                speedups[n] = speedup
            eff = (speedup / n) if (speedup is not None and n) else None
            lines.append("| %s | %.0f | %s | %s | %s | %s | %s |" % (
                dataset, mib, mode, row.get("thread_count", "?"),
                "%.1f" % thr if thr is not None else "-",
                "%.2fx" % speedup if speedup is not None else "-",
                "%.2f" % eff if eff is not None else "-"))

        point = plateau_point(speedups)
        best = max((s for s in speedups.values() if s is not None), default=None)
        summary.append((dataset, mib, point, best, speedups))

    lines.append("")
    lines.append("## Plateau")
    lines.append("")
    lines.append("| Dataset | MiB | Best speedup vs t1 | Plateau at |")
    lines.append("| --- | --- | --- | --- |")
    for dataset, mib, point, best, _s in summary:
        if point is None:
            verdict = "not determined"
        elif point == 1:
            verdict = "**1 thread (no benefit from more)**"
        elif point == 3:
            verdict = "still improving at 3 threads"
        else:
            verdict = "%d threads" % point
        lines.append("| %s | %.0f | %s | %s |" % (
            dataset, mib, "%.2fx" % best if best is not None else "-", verdict))
    lines.append("")
    lines.append("A plateau at 1 thread means extra threads did not pay for themselves on "
                 "this input. Efficiency well below 1.0 is a normal outcome for a "
                 "streaming, low-arithmetic-intensity scan; see "
                 "`docs/threading_analysis.md` for the candidate explanations.")
    lines.append("")

    report = "\n".join(lines)
    if args.output:
        out_dir = os.path.dirname(os.path.abspath(args.output))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.output, "w") as handle:
            handle.write(report)
        print("Wrote thread-scaling report to %s" % args.output)
    else:
        sys.stdout.write(report)


if __name__ == "__main__":
    main()
