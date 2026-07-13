#!/usr/bin/env python3
"""Summarize a benchmark CSV into a Markdown report.

Reads the CSV produced by run_utf16_benchmark.py and writes a conservative, factual
summary organised by the comparison groups defined in docs/benchmark_methodology.md:

  A  scalar vs Parabix SIMD (single thread)
  B  Parabix SIMD (single thread) vs Clausecker-Lemire/simdutf, valid input only
  C  Parabix thread scaling
  D  malformed accept/reject correctness (NOT a throughput comparison)

Rows whose result_ok is false are reported but never used for speedup claims.
Older CSVs without the newer columns are handled without failing.

Usage:
    python3 benchmarks/summarize_utf16_benchmark.py \\
        --input results/utf16_benchmark.csv \\
        --output results/utf16_benchmark_summary.md
"""

import argparse
import csv
import os
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_rows(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def num(row, key):
    try:
        return float(row.get(key, "") or "")
    except ValueError:
        return None


def is_ok(row):
    return str(row.get("result_ok", "True")).strip().lower() in ("true", "1", "yes")


def mode_of(row):
    # Newer CSVs carry `mode`; older ones only had mode/thread_count pairs.
    mode = row.get("mode") or row.get("tool") or "?"
    if mode in ("simd", "scalar") and row.get("thread_count"):
        return "%s(%s)" % (mode, row["thread_count"])
    return mode


def groups_of(row):
    raw = (row.get("comparison_group") or "").strip()
    return [g for g in raw.split(",") if g] if raw else []


def size_key(row):
    return num(row, "mib") or 0.0


def fmt(value, spec="%.2f", suffix=""):
    return (spec % value) + suffix if isinstance(value, float) else "-"


def table(lines, header, body):
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    lines.append("")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=os.path.join(REPO_ROOT, "results", "utf16_benchmark.csv"))
    parser.add_argument("--output", default=os.path.join(REPO_ROOT, "results", "utf16_benchmark_summary.md"))
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        raise SystemExit("ERROR: benchmark CSV not found: %s\n"
                         "       Run ./scripts/benchmark_utf16validate.sh first." % args.input)
    rows = read_rows(args.input)
    if not rows:
        raise SystemExit("ERROR: benchmark CSV is empty: %s" % args.input)

    first = rows[0]
    lines = []
    lines.append("# UTF-16 validation benchmark summary")
    lines.append("")
    lines.append("- Generated: %s" % datetime.now(timezone.utc).isoformat())
    lines.append("- Platform: %s" % first.get("platform", "?"))
    lines.append("- Machine: %s" % first.get("machine", "?"))
    lines.append("- Processor: %s" % (first.get("processor", "") or "(unspecified)"))
    lines.append("- Python: %s" % first.get("python_version", "?"))
    lines.append("- Parabix commit: %s" % (first.get("parabix_commit", "") or "(unrecorded)"))
    if first.get("simdutf_commit"):
        lines.append("- simdutf commit: %s (implementation: %s)"
                     % (first["simdutf_commit"], first.get("simdutf_impl", "?")))
    lines.append("- Timing scope: %s" % (first.get("timing_scope", "") or "whole_process"))
    lines.append("- Warmups: %s, measured repetitions: %s"
                 % (first.get("warmups", "?"), first.get("repetitions", "?")))
    lines.append("")
    lines.append("Throughput is computed from the **median** time. Speedups are stated "
                 "against an explicit baseline. Rows that reported an unexpected result "
                 "are excluded from every speedup.")
    lines.append("")

    # --- correctness gate ---
    bad = [r for r in rows if not is_ok(r)]
    lines.append("## Correctness gate")
    lines.append("")
    if bad:
        lines.append("**%d row(s) reported an unexpected result** and are excluded from "
                     "speedup claims:" % len(bad))
        lines.append("")
        table(lines, ["Dataset", "Mode", "Expected", "Reported"],
              [[r.get("dataset_type", "?"), mode_of(r),
                r.get("expected_result", "?"), r.get("reported_result", "?")] for r in bad])
    else:
        lines.append("All %d timed runs reported the expected result." % len(rows))
        lines.append("")

    # --- fixed overhead ---
    overhead = {}
    for row in rows:
        value = num(row, "fixed_overhead_seconds")
        if value is not None:
            overhead[row.get("tool", "?")] = value
    if overhead:
        lines.append("## Fixed per-process overhead")
        lines.append("")
        lines.append("Measured on a tiny input. This constant cost (process startup, and "
                     "for Parabix loading the compiled pipeline) is **not the same for "
                     "each tool** and dominates small inputs, so small-input throughput "
                     "must not be read as a performance conclusion.")
        lines.append("")
        table(lines, ["Tool", "Fixed overhead (s)"],
              [[tool, "%.4f" % value] for tool, value in sorted(overhead.items())])

    valid_rows = [r for r in rows if not (r.get("error_pattern") or "")]

    def perf_table(title, note, selected, speedup_col, speedup_label):
        if not selected:
            return
        lines.append("## %s" % title)
        lines.append("")
        lines.append(note)
        lines.append("")
        body = []
        for row in sorted(selected, key=lambda r: (r.get("dataset_type", ""), size_key(r), mode_of(r))):
            body.append([
                row.get("dataset_type", "?"),
                "%.0f" % (num(row, "mib") or 0),
                mode_of(row),
                fmt(num(row, "median_seconds"), "%.4f"),
                fmt(num(row, "stdev_seconds"), "%.4f"),
                fmt(num(row, "throughput_mib_s"), "%.1f"),
                fmt(num(row, "throughput_adjusted_mib_s"), "%.1f"),
                fmt(num(row, speedup_col), "%.2f", "x"),
                "yes" if is_ok(row) else "**NO**",
            ])
        table(lines, ["Dataset", "MiB", "Mode", "Median (s)", "Stdev (s)",
                      "Throughput (MiB/s)", "Adjusted (MiB/s)", speedup_label, "OK"], body)

    perf_table(
        "Group A - scalar vs Parabix SIMD (single thread)",
        "Same tool, same process model, both single-threaded: this isolates the SIMD "
        "kernel. Baseline: scalar.",
        [r for r in valid_rows if "A" in groups_of(r)],
        "speedup_vs_scalar", "Speedup vs scalar")

    perf_table(
        "Group B - Parabix SIMD (1 thread) vs Clausecker-Lemire/simdutf",
        "Valid input only, both single-threaded. simdutf reports validity, not an error "
        "count; on valid input both tools must scan every code unit, so the work is "
        "equivalent. Baseline: parabix_simd_t1.",
        [r for r in valid_rows if "B" in groups_of(r)],
        "speedup_vs_parabix_t1", "Speedup vs simd_t1")

    perf_table(
        "Group C - Parabix thread scaling",
        "Only the thread count varies. Baseline: parabix_simd_t1. Thread efficiency is "
        "speedup / threads; efficiency well below 1 is a normal result for a "
        "memory-bandwidth-bound streaming scan.",
        [r for r in valid_rows if "C" in groups_of(r)],
        "speedup_vs_parabix_t1", "Speedup vs simd_t1")

    # --- Group D: malformed accept/reject ---
    d_rows = [r for r in rows if "D" in groups_of(r)]
    if d_rows:
        lines.append("## Group D - malformed input (accept/reject correctness)")
        lines.append("")
        lines.append("**Not a throughput comparison.** simdutf may exit early at the first "
                     "ill-formed code unit while the Parabix validators count every error, "
                     "so malformed-input timings are not comparable across tools. These rows "
                     "verify that each tool reports the expected result.")
        lines.append("")
        body = []
        for row in sorted(d_rows, key=lambda r: (r.get("dataset_type", ""), r.get("error_pattern", ""),
                                                 size_key(r), mode_of(r))):
            body.append([
                row.get("dataset_type", "?"),
                row.get("error_pattern", "?"),
                str(row.get("error_rate_percent", "")),
                mode_of(row),
                row.get("expected_result", "?"),
                row.get("reported_result", "?"),
                "yes" if is_ok(row) else "**NO**",
            ])
        table(lines, ["Dataset", "Pattern", "Rate %", "Mode", "Expected", "Reported", "OK"], body)

    lines.append("## Notes")
    lines.append("")
    lines.append("- Timings are whole-process wall clock and include process startup; for "
                 "Parabix they also include loading the compiled pipeline. See the fixed "
                 "overhead table above and `docs/benchmark_methodology.md` section 5.")
    lines.append("- Cross-tool conclusions should be drawn from large inputs on valid data, "
                 "with both sides single-threaded.")
    lines.append("- Results are machine-specific and must be reproduced elsewhere before "
                 "being treated as general.")
    lines.append("")

    out_dir = os.path.dirname(os.path.abspath(args.output))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w") as handle:
        handle.write("\n".join(lines))
    print("Wrote summary to %s" % args.output)


if __name__ == "__main__":
    main()
