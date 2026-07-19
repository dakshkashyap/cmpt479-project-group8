# Final benchmark graphs

Research-quality figures for the report and slides, summarising the UTF-16 SIMD project from
start to finish. Regenerate all of them from the repo root with:

```bash
python3 benchmarks/plot_final_benchmark_graphs.py     # requires matplotlib (no seaborn)
```

The script writes every PNG here plus `final_graph_data.csv` (the compact, audited numbers
behind the charts). It does **not** re-run any large benchmark: the heavy matrices already live
in the committed summaries, and the numbers are transcribed with the source row cited inline in
the script.

## Throughput metric used

Every performance chart uses **adjusted throughput** = MiB / (median wall time − per-tool fixed
overhead). The fixed overhead (process start + Parabix's compiled-pipeline load) is measured on
a tiny input and differs per tool, so it is subtracted to give a fair **per-byte** rate. This is
the metric the committed summaries call "Adjusted (MiB/s)". Where a chart shows a speedup ratio,
it is the ratio of these adjusted values; the raw whole-process speedups (also in the summaries)
are smaller because Parabix's fixed startup is included on both sides.

Data sources:
`results/utf16_benchmark_csil_x86_64_summary.md` (CSIL x86-64, simdutf = `westmere`/SSE4.2,
QEMU guest, 31 GiB), `results/utf16_benchmark_apple_arm64_summary.md` (Apple M1 arm64,
simdutf = `arm64`/NEON, 8 GiB), and `results/apple_arm64_toolchain.md`. All at the `default`
valid dataset.

---

## 01 — Cross-architecture validation: Parabix SIMD vs simdutf
`01_cross_arch_simd_vs_simdutf.png` — Group B, adjusted MiB/s, valid `default`, single-threaded,
128 and 256 MiB, both hosts.

**Claim:** the portable byte-oriented Parabix kernel beats each architecture's *native* simdutf
SIMD path (SSE4.2 on x86-64, NEON on arm64) by a **similar ~2.3–2.5× on both** machines
(CSIL 2.42×/2.52×, Apple 2.33×/2.34×). The consistent ratio — not the absolute MiB/s — is the
portability result. (Absolute speeds are not compared across hosts: CSIL is a QEMU guest, Apple
is a physical M1.)

## 02 — Scalar vs byte-oriented SIMD validator
`02_scalar_vs_simd_speedup.png` — Group A, adjusted MiB/s, scalar vs `parabix_simd_t1`, valid
`default`, 128 and 256 MiB, both hosts.

**Claim:** the SIMD kernel improves over the scalar oracle on both hosts, and the gain is
**stronger on Apple arm64** (≈2.17× per-byte) than on the CSIL guest (≈1.15–1.23×) — consistent
with the AArch64-specific optimisation in issue #38 that removed the hot-loop `hsimd_signmask(8)`.
(These are overhead-adjusted per-byte ratios; the raw whole-process speedups in the summary are
smaller, ~1.7–1.9× on Apple and ~1.1–1.2× on CSIL.)

## 03 — Parabix thread scaling
`03_thread_scaling.png` — Group C, adjusted MiB/s, valid `default`, thread modes t1/t2/t3/default,
128 and 256 MiB, both hosts.

**Claim:** threading raises throughput but **plateaus quickly — ~2 threads is at or near the best**
on every host/size; t3 and the default do not improve on t2. This is the expected shape for a
memory-bandwidth-bound streaming scan.

## 04 — Apple M1 pipeline ablation (feature cost)
`04_pipeline_ablation_apple.png` — adjusted MiB/s, valid `default` 128 MiB, **all Parabix stages
single-threaded** so per-stage kernel costs are directly comparable.

**Claim:** the engineering stages have the costs you would predict.
- **errorMarks producer** (5063) is nearly as fast as **SIMD count-only** (5216): emitting the
  one-bit-per-code-unit stream is cheap on valid input (the marker stream is all zeros).
- **errorMarks + two-level scan** (5030) adds only a small fixed cost — the high-level index is
  built over every block even though clean regions are skipped (issue #39).
- **repair** (1493) is markedly lower because it is a **real end-to-end output pipeline**: it
  copies every code unit and writes the full 128 MiB result stream — the output cost dominates,
  exactly as expected for a byte-for-byte transform rather than a scan-only kernel.

This is a Mac-only figure (no CSIL counterpart); it is not a cross-architecture comparison.

## 05 — Correctness evidence
`05_correctness_evidence.png` — current pass counts from the repo's test suites.

**Claim:** the features are **verified, not merely implemented**. Repair passes 64/64 semantic
tests and 7/7 byte-for-byte simdutf `to_well_formed_utf16` cross-checks on even-length inputs
(with `validate(repair(input)) == 0` and idempotence checked inside the suite); the validator,
errorMarks, scan-consumer, and UTF-16BE suites pass 67/49/54/35.

## 06 — Project contribution ladder
`06_research_timeline.png` — the chronological arc of the project.

**Purpose:** a slide-ready visual of the seventeen steps from the scalar oracle to portable
U+FFFD repair and the cross-architecture evaluation, including the honest negative result.

## 07 — Negative result: Pablo / transposition
`07_pablo_negative_result.png` — a conceptual figure of the attempted Pablo/S2P path.

**Claim (from `docs/pablo_utf16_prototype.md`):** a bitwise/Pablo validator was genuinely built,
but Parabix has no bytes → 16-bit code-unit-indexed transpose; the 8-bit basis is byte-indexed,
so UTF-16 needs explicit high-byte-parity + 2-position pairing — exactly the byte-lane
bookkeeping the byte-oriented SIMD kernel already handles. Since the surrogate predicate is a
single high-byte compare, the transposition overhead dominates and the path is **not worth
pursuing**. Showing this demonstrates research maturity (not every path paid off).

---

## Limitations

- All performance figures are **whole-process, single-machine, adjusted** numbers on the valid
  `default` dataset; other datasets are in the per-host summaries and behave similarly.
- The two hosts differ in more than ISA (QEMU vs physical, 31 vs 8 GiB RAM), so only the
  **within-host Parabix/simdutf and scalar/SIMD ratios** are compared, never absolute cross-host
  speed. CSIL additionally ran 512 MiB (omitted on the 8 GiB M1); the graphs use the shared
  128/256 MiB sizes.
- Graph 4 and the repair row of Graph 5 are Apple-only (repair was benchmarked/tested on the
  branch that carries issue #40; there is no CSIL repair number).
- The compiler-lowering claim behind Graph 2 is source-level (grep of the final patch confirms no
  hot-loop `hsimd_signmask(8)`; see `docs/simd_regression_investigation.md`); full disassembly was
  not re-run for these figures.
