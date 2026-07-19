#!/usr/bin/env python3
"""Generate the final research-quality benchmark graphs for the UTF-16 SIMD project.

    python3 benchmarks/plot_final_benchmark_graphs.py

Writes PNGs (and a compact curated CSV) into results/final_graphs/. Requires matplotlib
(no seaborn). Each figure has a title, labelled axes with units, a legend, and a caption in
results/final_graphs/README.md.

Data sources
------------
All performance numbers are the **adjusted (overhead-subtracted) MiB/s** already published in
the committed cross-architecture summaries; they are transcribed here (not re-run) with the
exact source row cited next to each value, because parsing the markdown tables is more fragile
than a small audited table:

  * results/utf16_benchmark_csil_x86_64_summary.md   (CSIL x86-64, simdutf=westmere/SSE4.2)
  * results/utf16_benchmark_apple_arm64_summary.md   (Apple M1 arm64, simdutf=arm64/NEON)
  * results/apple_arm64_toolchain.md                 (Apple Mac-only extended-mode table)

The Apple pipeline-ablation stage numbers (Graph 4) that include U+FFFD **repair** are a fresh
local Apple measurement (this branch has issue #40 repair), taken with the SAME methodology as
the summaries: whole-process wall time, valid `default` 128 MiB input, 2 warmups, median of 7,
per-tool fixed overhead subtracted, ALL Parabix stages pinned to -thread-num=1 so the per-stage
kernel costs are directly comparable. The command is documented in the README.

Correctness counts (Graph 5) are the current pass counts from the repo's test suites.

Nothing here re-runs a massive benchmark; the heavy matrices live in the committed summaries.
"""

import csv
import os

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt

OUT = os.path.join(os.path.dirname(__file__), os.pardir, "results", "final_graphs")
OUT = os.path.abspath(OUT)

# Consistent palette (colour-blind friendly-ish, distinct in grayscale too).
C_PARABIX = "#1f77b4"   # blue  -- Parabix
C_SIMDUTF = "#ff7f0e"   # orange -- simdutf
C_SCALAR  = "#7f7f7f"   # gray  -- scalar
C_CSIL    = "#d62728"   # red   -- x86-64 CSIL
C_APPLE   = "#2ca02c"   # green -- Apple arm64
C_STAGE   = "#1f77b4"

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 130,
    "font.size": 10,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "axes.axisbelow": True,
})

# ---------------------------------------------------------------------------
# Curated data (adjusted MiB/s unless noted). Source rows cited inline.
# ---------------------------------------------------------------------------

# Group B: Parabix simd_t1 vs simdutf, valid `default`, single-thread, ADJUSTED MiB/s.
# csil_summary Group B `default` rows; apple_summary Group B `default` rows.
GROUP_B = {
    #                 parabix_simd_t1, simdutf
    "CSIL x86-64\n128 MiB": (3036.7, 1256.2),   # csil default/128
    "CSIL x86-64\n256 MiB": (3154.9, 1253.7),   # csil default/256
    "Apple arm64\n128 MiB": (5121.1, 2199.7),   # apple default/128
    "Apple arm64\n256 MiB": (5152.2, 2198.0),   # apple default/256
}

# Group A: scalar vs Parabix simd_t1, valid `default`, single-thread, ADJUSTED MiB/s.
# csil_summary / apple_summary Group A `default` rows.
GROUP_A = {
    #                 scalar,  parabix_simd_t1
    "CSIL x86-64\n128 MiB": (2635.0, 3036.7),   # csil default/128
    "CSIL x86-64\n256 MiB": (2558.4, 3154.9),   # csil default/256
    "Apple arm64\n128 MiB": (2365.2, 5121.1),   # apple default/128
    "Apple arm64\n256 MiB": (2373.1, 5152.2),   # apple default/256
}

# Group C: thread scaling, valid `default`, ADJUSTED MiB/s. Order: t1, t2, t3, default.
# csil_summary / apple_summary Group C `default` rows.
THREAD_MODES = ["t1", "t2", "t3", "default"]
GROUP_C = {
    "CSIL x86-64 128 MiB": [3036.7, 3960.0, 4014.9, 4134.0],
    "CSIL x86-64 256 MiB": [3154.9, 4936.2, 4193.5, 4171.6],
    "Apple arm64 128 MiB": [5121.1, 8281.7, 7700.3, 7808.8],
    "Apple arm64 256 MiB": [5152.2, 8603.7, 8264.2, 8172.1],
}

# Graph 4: Apple pipeline ablation, valid `default` 128 MiB, ALL Parabix stages -thread-num=1,
# ADJUSTED MiB/s. Fresh local Apple measurement (see module docstring + README command).
ABLATION = [
    ("scalar",          2384.6),
    ("SIMD\ncount-only", 5216.4),
    ("errorMarks\nproducer", 5063.2),
    ("errorMarks\n+ scan",   5030.4),
    ("repair\n(U+FFFD)",     1492.6),
    ("simdutf\n(NEON)",      2301.5),
]

# Graph 5: current correctness pass counts (from the repo's test scripts).
CORRECTNESS = [
    ("Repair tests (test_utf16_repair.sh)",        64, 64),
    ("simdutf even-length cross-checks",            7,  7),
    ("Validator suite (test_utf16validate.sh)",    67, 67),
    ("errorMarks suite (test_errormarks.sh)",      49, 49),
    ("Scan consumer (test_scan_consumer.sh)",      54, 54),
    ("UTF-16BE suite (test_utf16be.sh)",           35, 35),
]

# Graph 6: contribution ladder (chronological project evolution).
TIMELINE = [
    "1. Scalar oracle validator",
    "2. Portable byte-oriented SIMD validator",
    "3. Multilingual datasets",
    "4. Controlled malformed inputs",
    "5. simdutf baseline",
    "6. Fair benchmark methodology",
    "7. Thread-scaling analysis",
    "8. SIMD regression diagnosis",
    "9. Optimized signmask-free SIMD",
    "10. Two-level scan study",
    "11. errorMarks producer",
    "12. TwoLevelScanKernel consumer",
    "13. UTF-16BE support",
    "14. Pablo negative-result study",
    "15. U+FFFD repair",
    "16. Cross-architecture evaluation",
    "17. Final graphs / report",
]


def _grouped_bars(ax, categories, series, colors, ylabel, title):
    n = len(categories)
    m = len(series)
    x = range(n)
    width = 0.8 / m
    for j, (label, vals) in enumerate(series):
        offs = [xi + (j - (m - 1) / 2) * width for xi in x]
        bars = ax.bar(offs, vals, width, label=label, color=colors[j], edgecolor="black", linewidth=0.4)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.0f}",
                    ha="center", va="bottom", fontsize=7)
    ax.set_xticks(list(x))
    ax.set_xticklabels(categories)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend()


def graph1_cross_arch():
    cats = list(GROUP_B.keys())
    parabix = [GROUP_B[c][0] for c in cats]
    simdutf = [GROUP_B[c][1] for c in cats]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    _grouped_bars(ax, cats,
                  [("Parabix SIMD (1 thread)", parabix), ("simdutf (native SIMD)", simdutf)],
                  [C_PARABIX, C_SIMDUTF],
                  "Adjusted throughput (MiB/s)",
                  "Cross-architecture validation: Parabix SIMD vs simdutf\n"
                  "(valid `default` input, single-threaded, overhead-adjusted)")
    # ratio labels above each pair
    for i, c in enumerate(cats):
        r = parabix[i] / simdutf[i]
        top = max(parabix[i], simdutf[i])
        ax.text(i, top * 1.08, f"{r:.2f}x", ha="center", va="bottom",
                fontweight="bold", color=C_PARABIX, fontsize=9)
    ax.set_ylim(0, max(parabix) * 1.25)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "01_cross_arch_simd_vs_simdutf.png"))
    plt.close(fig)


def graph2_scalar_vs_simd():
    cats = list(GROUP_A.keys())
    scalar = [GROUP_A[c][0] for c in cats]
    simd = [GROUP_A[c][1] for c in cats]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    _grouped_bars(ax, cats,
                  [("scalar (1 thread)", scalar), ("Parabix SIMD (1 thread)", simd)],
                  [C_SCALAR, C_PARABIX],
                  "Adjusted throughput (MiB/s)",
                  "Scalar vs byte-oriented SIMD validator\n"
                  "(valid `default` input, single-threaded, overhead-adjusted)")
    for i in range(len(cats)):
        r = simd[i] / scalar[i]
        ax.text(i, max(scalar[i], simd[i]) * 1.08, f"{r:.2f}x",
                ha="center", va="bottom", fontweight="bold", color=C_PARABIX, fontsize=9)
    ax.set_ylim(0, max(simd) * 1.25)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "02_scalar_vs_simd_speedup.png"))
    plt.close(fig)


def graph3_thread_scaling():
    fig, ax = plt.subplots(figsize=(8, 4.8))
    styles = {
        "CSIL x86-64 128 MiB": (C_CSIL, "o", "-"),
        "CSIL x86-64 256 MiB": (C_CSIL, "s", "--"),
        "Apple arm64 128 MiB": (C_APPLE, "o", "-"),
        "Apple arm64 256 MiB": (C_APPLE, "s", "--"),
    }
    for label, vals in GROUP_C.items():
        col, mk, ls = styles[label]
        ax.plot(THREAD_MODES, vals, marker=mk, linestyle=ls, color=col, label=label)
    ax.set_xlabel("Parabix thread mode")
    ax.set_ylabel("Adjusted throughput (MiB/s)")
    ax.set_title("Parabix thread scaling (valid `default`, overhead-adjusted)\n"
                 "throughput rises then plateaus; ~2 threads is near-best")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "03_thread_scaling.png"))
    plt.close(fig)


def graph4_ablation():
    labels = [s[0] for s in ABLATION]
    vals = [s[1] for s in ABLATION]
    colors = [C_SCALAR, C_PARABIX, C_PARABIX, C_PARABIX, C_PARABIX, C_SIMDUTF]
    fig, ax = plt.subplots(figsize=(8.6, 4.8))
    bars = ax.bar(labels, vals, color=colors, edgecolor="black", linewidth=0.4)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.0f}",
                ha="center", va="bottom", fontsize=8)
    ax.set_ylabel("Adjusted throughput (MiB/s)")
    ax.set_title("Apple M1 pipeline ablation (valid `default` 128 MiB, single-threaded)\n"
                 "cost of each engineering stage, overhead-adjusted")
    ax.set_ylim(0, max(vals) * 1.18)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "04_pipeline_ablation_apple.png"))
    plt.close(fig)


def graph5_correctness():
    labels = [c[0] for c in CORRECTNESS]
    passed = [c[1] for c in CORRECTNESS]
    total = [c[2] for c in CORRECTNESS]
    y = range(len(labels))
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    ax.barh(list(y), total, color="#e0e0e0", edgecolor="black", linewidth=0.4)
    ax.barh(list(y), passed, color=C_APPLE, edgecolor="black", linewidth=0.4)
    for i, (p, t) in enumerate(zip(passed, total)):
        ax.text(t, i, f"  {p}/{t} pass", va="center", ha="left", fontsize=9, fontweight="bold")
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("checks passed")
    ax.set_xlim(0, max(total) * 1.35)
    ax.set_title("Correctness evidence: every suite passes\n"
                 "(repair validated, not merely implemented)")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "05_correctness_evidence.png"))
    plt.close(fig)


def graph6_timeline():
    fig, ax = plt.subplots(figsize=(8.2, 8.6))
    n = len(TIMELINE)
    for i, step in enumerate(TIMELINE):
        y = n - i
        ax.plot([0.06], [y], "o", color=C_PARABIX, markersize=9, zorder=3)
        if i < n - 1:
            ax.plot([0.06, 0.06], [y - 1, y], "-", color=C_PARABIX, linewidth=2, zorder=1)
        ax.text(0.12, y, step, va="center", ha="left", fontsize=10)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, n + 1)
    ax.axis("off")
    ax.set_title("Project contribution ladder\nfrom scalar oracle to portable repair + cross-arch evaluation",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "06_research_timeline.png"))
    plt.close(fig)


def graph7_pablo():
    """Conceptual figure for the Pablo/transposition negative result."""
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.axis("off")
    boxes = [
        (0.10, "byte stream\n(UTF-16LE)", C_SCALAR),
        (0.37, "S2P transpose\n-> basis bits", C_SIMDUTF),
        (0.64, "Pablo surrogate\nclassifier", C_SIMDUTF),
        (0.90, "errorMarks?", C_SCALAR),
    ]
    for x, txt, col in boxes:
        ax.text(x, 0.72, txt, ha="center", va="center", fontsize=10,
                bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor=col, linewidth=1.6))
    for x0, x1 in [(0.19, 0.28), (0.46, 0.55), (0.73, 0.82)]:
        ax.annotate("", xy=(x1, 0.72), xytext=(x0, 0.72),
                    arrowprops=dict(arrowstyle="->", lw=1.5, color=C_SCALAR))
    ax.text(0.5, 0.40,
            "Blocked: Parabix has no bytes -> 16-bit code-unit-indexed S2P.\n"
            "8-bit basis is byte-indexed; UTF-16 needs code-unit indexing\n"
            "(high-byte parity + 2-position pairing) -- reintroducing exactly the\n"
            "byte-lane bookkeeping the byte-oriented SIMD kernel already handles.",
            ha="center", va="center", fontsize=9.5,
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#fff3e0", edgecolor=C_SIMDUTF))
    ax.text(0.5, 0.08,
            "Conclusion: transposition overhead dominates for a single high-byte compare; "
            "not worth pursuing.\n(Documented in docs/pablo_utf16_prototype.md — an honest negative result.)",
            ha="center", va="center", fontsize=9, fontstyle="italic")
    ax.set_title("Negative result: Pablo / transposition path", fontsize=12)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "07_pablo_negative_result.png"))
    plt.close(fig)


def write_curated_csv():
    path = os.path.join(OUT, "final_graph_data.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["graph", "series", "category", "adjusted_mibps"])
        for c, (p, s) in GROUP_B.items():
            w.writerow(["1_cross_arch", "parabix_simd_t1", c.replace("\n", " "), p])
            w.writerow(["1_cross_arch", "simdutf", c.replace("\n", " "), s])
        for c, (sc, si) in GROUP_A.items():
            w.writerow(["2_scalar_vs_simd", "scalar", c.replace("\n", " "), sc])
            w.writerow(["2_scalar_vs_simd", "parabix_simd_t1", c.replace("\n", " "), si])
        for label, vals in GROUP_C.items():
            for mode, v in zip(THREAD_MODES, vals):
                w.writerow(["3_thread_scaling", label, mode, v])
        for label, v in ABLATION:
            w.writerow(["4_ablation_apple", label.replace("\n", " "), "128MiB_1thread", v])
        for label, p, t in CORRECTNESS:
            w.writerow(["5_correctness", label, "passed/total", f"{p}/{t}"])
    return path


def main():
    os.makedirs(OUT, exist_ok=True)
    graph1_cross_arch()
    graph2_scalar_vs_simd()
    graph3_thread_scaling()
    graph4_ablation()
    graph5_correctness()
    graph6_timeline()
    graph7_pablo()
    csv_path = write_curated_csv()
    print("Wrote graphs + data to", OUT)
    for fn in sorted(os.listdir(OUT)):
        print("  ", fn)
    print("Curated data:", os.path.relpath(csv_path))


if __name__ == "__main__":
    main()
