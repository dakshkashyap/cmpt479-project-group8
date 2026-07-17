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
simdutf_commit=       # must be ca7acbcea967b5dcbab490066e99e3a6e7865539 (v9.0.0)
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
| x86-64 (CSIL Linux) | Daksh | **Done** — `results/utf16_benchmark_csil_x86_64_summary.md` + `results/csil_x86_64_toolchain.md` (126/126 rows OK; simdutf=`westmere`) |
| arm64 (Apple) | teammate | **TODO** — run the identical command above; commit `results/utf16_benchmark_apple_arm64_summary.md` + `results/apple_arm64_toolchain.md` |

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
