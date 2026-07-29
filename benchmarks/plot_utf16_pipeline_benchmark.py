#!/usr/bin/env python3
"""Charts for the UTF-16 pipeline benchmark (issue #45).

Every figure is derived ONLY from the raw result CSV written by
benchmarks/benchmark_utf16_pipeline.py -- nothing here re-runs a measurement, and no number
is transcribed by hand.

    python3 benchmarks/plot_utf16_pipeline_benchmark.py
    python3 benchmarks/plot_utf16_pipeline_benchmark.py --input results/utf16_pipeline_benchmark.csv

Figures (written to results/utf16_pipeline_graphs/):

    throughput_vs_size.png       median MiB/s against input size, one line per operation
    throughput_vs_density.png    median MiB/s against malformed-unit density
    scalar_vs_simd.png           scalar against --simd validation
    linear_vs_scan.png           linear against two-level scan location
    repair_throughput.png        repair, Parabix against simdutf
    le_vs_be.png                 UTF-16LE against UTF-16BE per operation

matplotlib is not a dependency of this repository. If it is missing, this script reports the
chart step as SKIPPED and exits 0 -- it never installs anything.

Axes are labelled with units, y-axes start at zero so bar heights are proportional, and rows
that were skipped or failed in the benchmark are reported in the console output and in the
figure captions rather than being silently dropped.
"""

import argparse
import csv
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(REPO_ROOT, "results", "utf16_pipeline_benchmark.csv")
DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, "results", "utf16_pipeline_graphs")

OPERATIONS = ["validate_scalar", "validate_simd", "emit_error_marks", "locate_linear",
              "locate_scan", "repair", "simdutf_validate", "simdutf_repair"]


def read_rows(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def median_of(rows, predicate):
    values = [float(r["throughput_mib_s"]) for r in rows
              if r["status"] == "ok" and r["throughput_mib_s"] and predicate(r)]
    return statistics.median(values) if values else None


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print("SKIPPED: no raw results at %s" % args.input)
        print("         Run benchmarks/benchmark_utf16_pipeline.py first.")
        return 0

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("SKIPPED: matplotlib is not installed, so no charts were generated.")
        print("         matplotlib is not a dependency of this repository and this script")
        print("         does not install anything. The raw CSV/JSON and the Markdown")
        print("         summary are unaffected: %s" % args.input)
        return 0

    rows = read_rows(args.input)
    ok = [r for r in rows if r["status"] == "ok"]
    skipped = [r for r in rows if r["status"] == "skipped"]
    failed = [r for r in rows if r["status"] == "failed"]
    if not ok:
        print("SKIPPED: %s has no successful rows to plot." % args.input)
        return 0

    os.makedirs(args.output_dir, exist_ok=True)
    caption = ("From %s. %d measured rows; %d skipped, %d failed (shown in the benchmark "
               "summary, not hidden)." % (os.path.basename(args.input), len(ok),
                                          len(skipped), len(failed)))
    written = []

    def save(fig, name):
        path = os.path.join(args.output_dir, name)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)
        written.append(path)

    sizes = sorted(set(int(r["size_bytes"]) for r in ok))
    densities = sorted(set(float(r["target_density"]) for r in ok))
    present = [o for o in OPERATIONS if any(r["operation"] == o for r in ok)]

    # 1. throughput vs size
    fig, ax = plt.subplots(figsize=(8, 5))
    for operation in present:
        ys = [median_of(ok, lambda r, o=operation, z=s: (r["operation"] == o
                                                         and int(r["size_bytes"]) == z))
              for s in sizes]
        points = [(s, y) for s, y in zip(sizes, ys) if y is not None]
        if points:
            ax.plot([p[0] / 1024.0 for p in points], [p[1] for p in points],
                    marker="o", label=operation)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("input size (KiB, log2 scale)")
    ax.set_ylabel("median throughput (MiB/s, whole process)")
    ax.set_ylim(bottom=0)
    ax.set_title("UTF-16 pipeline: throughput vs input size")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.text(0.01, 0.01, caption, fontsize=6)
    save(fig, "throughput_vs_size.png")

    # 2. throughput vs density (largest size only, so size does not confound it)
    largest = sizes[-1]
    fig, ax = plt.subplots(figsize=(8, 5))
    for operation in present:
        ys = [median_of(ok, lambda r, o=operation, d=d0: (
            r["operation"] == o and float(r["target_density"]) == d
            and int(r["size_bytes"]) == largest)) for d0 in densities for d in [d0]]
        points = [(d, y) for d, y in zip(densities, ys) if y is not None]
        if points:
            ax.plot([p[0] for p in points], [p[1] for p in points],
                    marker="o", label=operation)
    ax.set_xlabel("malformed code-unit density (%% of code units)")
    ax.set_ylabel("median throughput (MiB/s, whole process)")
    ax.set_ylim(bottom=0)
    ax.set_title("Throughput vs malformed density (%d KiB inputs)" % (largest // 1024))
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.text(0.01, 0.01, caption, fontsize=6)
    save(fig, "throughput_vs_density.png")

    # 3-5. paired bar charts
    for name, title, pair in (
            ("scalar_vs_simd.png", "Scalar vs --simd validation",
             ("validate_scalar", "validate_simd")),
            ("linear_vs_scan.png", "Linear vs two-level scan location",
             ("locate_linear", "locate_scan")),
            ("repair_throughput.png", "Repair throughput", ("repair", "simdutf_repair"))):
        left, right = pair
        labels, lefts, rights = [], [], []
        for size in sizes:
            a = median_of(ok, lambda r, o=left, z=size: (r["operation"] == o
                                                         and int(r["size_bytes"]) == z))
            b = median_of(ok, lambda r, o=right, z=size: (r["operation"] == o
                                                          and int(r["size_bytes"]) == z))
            labels.append("%d KiB" % (size // 1024))
            lefts.append(a or 0.0)
            rights.append(b or 0.0)
        fig, ax = plt.subplots(figsize=(8, 5))
        positions = range(len(labels))
        ax.bar([p - 0.2 for p in positions], lefts, width=0.4, label=left)
        ax.bar([p + 0.2 for p in positions], rights, width=0.4, label=right)
        ax.set_xticks(list(positions))
        ax.set_xticklabels(labels)
        ax.set_xlabel("input size")
        ax.set_ylabel("median throughput (MiB/s, whole process)")
        ax.set_ylim(bottom=0)
        ax.set_title(title)
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3, axis="y")
        fig.text(0.01, 0.01, caption + " A zero bar means no gated-clean measurement.",
                 fontsize=6)
        save(fig, name)

    # 6. LE vs BE
    fig, ax = plt.subplots(figsize=(9, 5))
    positions = range(len(present))
    le = [median_of(ok, lambda r, o=o: (r["operation"] == o
                                        and r["encoding"] == "UTF-16LE")) or 0.0
          for o in present]
    be = [median_of(ok, lambda r, o=o: (r["operation"] == o
                                        and r["encoding"] == "UTF-16BE")) or 0.0
          for o in present]
    ax.bar([p - 0.2 for p in positions], le, width=0.4, label="UTF-16LE")
    ax.bar([p + 0.2 for p in positions], be, width=0.4, label="UTF-16BE")
    ax.set_xticks(list(positions))
    ax.set_xticklabels(present, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("median throughput (MiB/s, whole process)")
    ax.set_ylim(bottom=0)
    ax.set_title("UTF-16LE vs UTF-16BE by operation")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.text(0.01, 0.01, caption, fontsize=6)
    save(fig, "le_vs_be.png")

    readme = os.path.join(args.output_dir, "README.md")
    with open(readme, "w") as handle:
        handle.write("# UTF-16 pipeline benchmark charts\n\n")
        handle.write("Generated by `benchmarks/plot_utf16_pipeline_benchmark.py` from "
                     "`%s`.\n\n" % os.path.relpath(args.input, REPO_ROOT))
        handle.write("%s\n\n" % caption)
        handle.write("These are measurements from one machine and one run. Whole-process "
                     "timing includes per-process start-up, which dominates the smallest "
                     "inputs. See the benchmark summary for the correctness gate, the "
                     "excluded paths and the limitations.\n\n")
        for path in written:
            handle.write("- `%s`\n" % os.path.basename(path))

    print("wrote %d figure(s) to %s" % (len(written), args.output_dir))
    for path in written:
        print("  %s" % os.path.relpath(path, REPO_ROOT))
    if skipped or failed:
        print("note: %d skipped and %d failed benchmark row(s) are not plotted; they are "
              "listed in the benchmark summary." % (len(skipped), len(failed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
