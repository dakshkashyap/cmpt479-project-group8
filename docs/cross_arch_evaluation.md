# Cross-architecture evaluation (x86-64 CSIL + arm64 Apple)

The proposal's portability story needs the **same** benchmark methodology on an
x86-64 host and an arm64 host (matching the paper's dual-architecture setup in
spirit: Intel-class x86 + Apple Silicon arm64).

This document is the shared contract so both machines produce comparable
summaries. Methodology details live in
[`benchmark_methodology.md`](benchmark_methodology.md); this page is only the
cross-arch runbook.

---

## Identical command (both hosts)

```bash
# 1. Build Parabix validator (pinned commit + milestone patch)
./scripts/setup_parabix.sh

# 2. Build Clausecker–Lemire / simdutf baseline
./scripts/setup_clausecker_lemire.sh

# 3. Correctness gate
./scripts/test_utf16validate.sh          # expect: 67 passed, 0 failed

# 4. Final matrix (valid input; all datasets; large sizes; includes simdutf)
BENCH_DATASETS=all \
BENCH_SIZES_MB=128,256,512 \
BENCH_WARMUPS=2 \
BENCH_REPETITIONS=7 \
BENCH_INCLUDE_SIMDUTF=1 \
BENCH_LABEL=utf16_benchmark_<HOST_TAG> \
./scripts/benchmark_utf16validate.sh
```

`BENCH_DATASETS=all` must mean the **same** set on both the generator and the
runner: `default` plus every multilingual mode. (A generator bug that omitted
`default` from `all` was fixed so the two stay aligned.)

Replace `<HOST_TAG>` with:

| Host | Suggested `BENCH_LABEL` | Suggested summary path |
|------|-------------------------|------------------------|
| CSIL Linux x86-64 | `utf16_benchmark_csil_x86_64` | `results/utf16_benchmark_csil_x86_64_summary.md` |
| Apple arm64 | `utf16_benchmark_apple_arm64` | `results/utf16_benchmark_apple_arm64_summary.md` |

Also commit a one-page toolchain file next to the summary:

- `results/csil_x86_64_toolchain.md`
- `results/apple_arm64_toolchain.md`

---

## What each host must record

Copy this checklist into the toolchain file (fill in the blanks):

```
host=
uname=
arch=                 # x86_64 or arm64
os=
cpu_model=
cpu_flags_simd=       # e.g. sse4_2 / avx2 / avx512*  OR  neon / asimd
cpus=
ram_gib=
compiler=             # full `c++ --version` first line
llvm=                 # llvm-config --version (or brew llvm@16)
llvm_dir=
parabix_commit=       # must be f0369dd138e2e7a710566d5035f68b9cdc0bf305
simdutf_commit=       # must be ca7acbcea967b5dcbab490066e99e3a6e6925539 (v9.0.0)
simdutf_impl=         # output of utf16validate_cl --impl
build_type=Release
```

On arm64, `simdutf_impl` should be `arm64` (NEON high-byte path). On CSIL's current
QEMU guests it is typically `westmere` (SSE4.2 only — **not** Ice Lake / AVX-512).
State that plainly in the report; do not claim Ice Lake numbers from a virtual CPU
that only exposes SSE4.2.

---

## Fair comparison reminders (do not skip)

From `benchmark_methodology.md`:

- **Headline cross-tool numbers** = Group B = `parabix_simd_t1` vs `simdutf`,
  **valid input**, **large sizes** (128–512 MiB), single-threaded on both sides.
- Do **not** compare multi-threaded Parabix to single-threaded simdutf as a SIMD result.
- Small sizes are dominated by Parabix's fixed pipeline-load cost; the harness
  records `fixed_overhead_seconds` and an adjusted throughput — quote both, and say
  which is which.
- Malformed input is for accept/reject correctness (Group D), **not** cross-tool
  throughput.

---

## Ownership

| Architecture | Owner | Status |
|--------------|-------|--------|
| x86-64 (CSIL Linux) | Daksh | **Done** — `results/utf16_benchmark_csil_x86_64_summary.md` + `results/csil_x86_64_toolchain.md` (126/126 rows OK; simdutf=`westmere`; sizes 128/256/512) |
| arm64 (Apple M1) | — | **Done** — `results/utf16_benchmark_apple_arm64_summary.md` + `results/apple_arm64_toolchain.md` (84/84 rows OK; simdutf=`arm64`; sizes 128/256 — 512 omitted on the 8 GiB M1) |

## Results (comparable Group B: valid input, single-threaded)

Headline cross-tool number = Parabix `simd_t1` vs `simdutf`, both single-threaded, valid
input. **Adjusted** throughput (overhead-subtracted, MiB/s) on the `default` dataset — the
fair per-byte metric because Parabix's fixed pipeline-load cost differs between hosts:

| Size | Metric | CSIL x86-64 (SSE4.2 `westmere`) | Apple arm64 (NEON) |
|---|---|---|---|
| 128 MiB | Parabix `simd_t1` (adj) | 3037 | 5121 |
| 128 MiB | simdutf (adj) | 1256 | 2200 |
| 128 MiB | **Parabix / simdutf (adj)** | **2.42×** | **2.33×** |
| 256 MiB | Parabix `simd_t1` (adj) | 3155 | 5152 |
| 256 MiB | simdutf (adj) | 1254 | 2198 |
| 256 MiB | **Parabix / simdutf (adj)** | **2.52×** | **2.34×** |

**Portability takeaway.** The byte-oriented Parabix kernel is faster than the architecture's
own native simdutf SIMD path by a **similar ~2.3–2.5× factor on both x86-64 and arm64**. That
consistent ratio — not the absolute MiB/s — is the portability result: the *same* pinned patch
and kernel win on both ISAs. (On raw throughput the gap is smaller, ~1.4–2×, because Parabix's
larger fixed overhead is included; both numbers are in the per-host summaries.)

Scalar→SIMD (Group A, `default`): the byte-oriented SIMD path gives **~1.7–1.9× over scalar on
Apple arm64** vs **~1.1–1.2× on the CSIL guest** — the ARM NEON path benefits more, consistent
with the issue #38 optimization that removed the hot-loop `hsimd_signmask(8)` on AArch64.
Thread scaling (Group C) is modest on both (~1.2–1.4× at best), as expected for a
bandwidth-bound streaming scan.

### Comparability caveats (read before quoting)

1. **Different machines, not just different ISAs.** CSIL is a **QEMU/KVM guest** exposing only
   SSE4.2 (simdutf `westmere`), 31 GiB RAM; Apple is a **physical M1** (NEON), 8 GiB RAM.
   Absolute MiB/s across the two hosts mixes virtual-vs-physical and RAM differences, so the
   **ratio within each host** (Parabix/simdutf) is the portable claim, not absolute speed.
2. **Sizes overlap at 128 and 256 MiB.** CSIL additionally ran 512 MiB (omitted on the 8 GiB
   M1). Comparisons are drawn on the two shared sizes.
3. **Mode coverage is validation-only for the cross-arch comparison.** Both hosts ran
   scalar / Parabix SIMD / simdutf **validation** (Groups A/B/C). The `errorMarks` producer,
   the `TwoLevelScanKernel` consumer, and U+FFFD **repair** are **not** part of this cross-arch
   matrix: `benchmark_utf16validate.sh` does not exercise them and CSIL did not run them. The
   Mac toolchain file records errorMarks / errorMarks+scan as **Mac-only** numbers (no CSIL
   counterpart). **Repair (issue #40) is not on this branch** (it was cut before #40 merged),
   so repair was not benchmarked on either host here; repair correctness is covered on `main`
   by `scripts/test_utf16_repair.sh`.

### Compiler-lowering check (source-level)

The optimized ARM64 SIMD validation path avoids the earlier hot-loop `hsimd_signmask(8)`
regression. Every occurrence of `hsimd_signmask` in `patches/utf16-simd-milestone.patch` is in
a **comment or design note** (the historical "an earlier version used…" explanations); the hot
loop reduces the per-pack mismatch with `bitblock_popcount` over an `ODD_ONE_MASK`-selected
vector plus an `mvmd_dslli` carry — no per-byte signmask extraction. The one `hsimd_signmask(64)`
reference is the `TwoLevelScanKernel`'s high-level index build at fw=64 (two lanes on a 128-bit
block — a different, cheap cost class, not the fw=8 regression). This matches the
`docs/simd_regression_investigation.md` finding and is confirmed by grep on the final patch;
full disassembly was not re-run on x86-64 (qualitative source-level evidence only).

### Apple teammate checklist

```bash
git pull
rm -rf .deps/parabix && ./scripts/setup_parabix.sh   # if patch changed
./scripts/setup_clausecker_lemire.sh
./scripts/test_utf16validate.sh                      # expect 67/67

BENCH_DATASETS=all \
BENCH_SIZES_MB=128,256,512 \
BENCH_WARMUPS=2 \
BENCH_REPETITIONS=7 \
BENCH_INCLUDE_SIMDUTF=1 \
BENCH_LABEL=utf16_benchmark_apple_arm64 \
./scripts/benchmark_utf16validate.sh

# Then fill results/apple_arm64_toolchain.md (see template above) and commit:
#   results/utf16_benchmark_apple_arm64_summary.md
#   results/apple_arm64_toolchain.md
```

On Apple Silicon, `utf16validate_cl --impl` should report `arm64` (NEON). That is the
fair high-byte competitor for our byte-oriented Parabix kernel.

After both summaries exist, the report can put them side-by-side without re-running
or inventing numbers.
