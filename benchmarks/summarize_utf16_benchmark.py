#!/usr/bin/env python3
"""Summarize results/utf16_benchmark.csv into a Markdown report.

Reads the CSV produced by run_utf16_benchmark.py and writes a conservative,
factual Markdown summary (date/machine, settings, a results table, the fastest
measured configuration per file size, and a short observations section).

Usage:
    python3 summarize_utf16_benchmark.py \\
        --input results/utf16_benchmark.csv \\
        --output results/utf16_benchmark_summary.md
"""

import argparse
import csv
import os
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_label(mode, thread_count):
    if mode == "scalar":
        return "scalar"
    if not thread_count or thread_count == "default":
        return "simd (default threads)"
    return "simd (--thread-num=%s)" % thread_count


def read_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input",
                        default=os.path.join(REPO_ROOT, "results", "utf16_benchmark.csv"))
    parser.add_argument("--output",
                        default=os.path.join(REPO_ROOT, "results", "utf16_benchmark_summary.md"))
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        raise SystemExit("ERROR: benchmark CSV not found: %s\n"
                         "       Run ./scripts/benchmark_utf16validate.sh first." % args.input)

    rows = read_rows(args.input)
    if not rows:
        raise SystemExit("ERROR: benchmark CSV is empty: %s" % args.input)

    first = rows[0]
    warmups = first.get("warmups", "?")
    repetitions = first.get("repetitions", "?")

    # Group rows by dataset in first-seen order.
    order = []
    by_dataset = {}
    for row in rows:
        ds = row["dataset"]
        if ds not in by_dataset:
            by_dataset[ds] = []
            order.append(ds)
        by_dataset[ds].append(row)

    def mib_of(row):
        try:
            return float(row["mib"])
        except (KeyError, ValueError):
            return 0.0

    order.sort(key=lambda ds: mib_of(by_dataset[ds][0]))

    lines = []
    lines.append("# UTF-16LE validation -- preliminary benchmark summary")
    lines.append("")
    lines.append("- Generated: %s" % datetime.now(timezone.utc).isoformat())
    lines.append("- Platform: %s" % first.get("platform", "?"))
    lines.append("- Machine: %s" % first.get("machine", "?"))
    lines.append("- Processor: %s" % (first.get("processor", "") or "(unspecified)"))
    lines.append("- Python: %s" % first.get("python_version", "?"))
    # Only the binary name -- the full path is machine-specific and stays in the CSV.
    lines.append("- Binary: %s" % os.path.basename(first.get("binary", "?")))
    lines.append("")
    lines.append("## Benchmark settings")
    lines.append("")
    lines.append("- Warmups per configuration: %s" % warmups)
    lines.append("- Measured repetitions per configuration: %s" % repetitions)
    lines.append("- Throughput uses the median time; speedup is scalar median / configuration median.")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Dataset | Size (MiB) | Configuration | Median (s) | Throughput (MiB/s) | Speedup vs scalar |")
    lines.append("| --- | --- | --- | --- | --- | --- |")

    fastest = []  # (dataset, size_mib, best_label, best_throughput)

    for ds in order:
        group = by_dataset[ds]
        best = None
        for row in group:
            label = config_label(row["mode"], row.get("thread_count", ""))
            median = float(row["median_seconds"])
            throughput = float(row["throughput_mib_s"])
            speedup_raw = row.get("speedup_vs_scalar", "")
            speedup = "%.2fx" % float(speedup_raw) if speedup_raw not in ("", None) else "-"
            lines.append("| %s | %.0f | %s | %.4f | %.1f | %s |"
                         % (ds, mib_of(row), label, median, throughput, speedup))
            if best is None or throughput > best[1]:
                best = (label, throughput)
        fastest.append((ds, mib_of(group[0]), best[0], best[1]))

    lines.append("")
    lines.append("## Fastest measured configuration per size")
    lines.append("")
    for ds, size_mib, label, throughput in fastest:
        lines.append("- **%s** (%.0f MiB): highest measured median throughput was `%s` "
                     "at %.1f MiB/s." % (ds, size_mib, label, throughput))

    lines.append("")
    lines.append("## Observations")
    lines.append("")
    for ds, size_mib, label, throughput in fastest:
        lines.append("- For the %.0f MiB input, configuration `%s` had the highest measured "
                     "median throughput (%.1f MiB/s)." % (size_mib, label, throughput))
    lines.append("- These results are preliminary and apply only to this machine and this "
                 "benchmark configuration.")
    lines.append("- Timings include process startup and Parabix pipeline compilation; warmup "
                 "runs populate Parabix's on-disk object cache before measurement.")
    lines.append("- More repetitions and additional machines are required before drawing final "
                 "conclusions.")
    lines.append("- The Clausecker-Lemire comparison is not included here and is planned for a "
                 "later project update.")
    lines.append("")

    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w") as f:
        f.write("\n".join(lines))

    print("Wrote summary to %s" % args.output)


if __name__ == "__main__":
    main()
