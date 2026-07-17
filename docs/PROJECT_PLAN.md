# Project Plan

## Goal

Implement a byte-oriented UTF-16 validator using a Parabix multiblock kernel.

## Progress

Shared status so the team can track work. Legend: `[x]` done · `[~]` in progress ·
`[ ]` not started. Update the box (and add a one-line note) when your work lands.

### Validator (scalar + byte-oriented SIMD)

- [x] Scalar UTF-16LE oracle kernel — serial, two-bytes-at-a-time; kept unchanged
      as the differential reference.
- [x] Portable SIMD validator built as a Parabix multiblock kernel (base64-style).
- [x] **Byte-oriented (fw=8) high-byte classification** — classifies each code unit
      on its high byte, host-endian agnostic; the little-endian-host `static_assert`
      is removed. Design note: `SIMD_BYTE_ORIENTED_VALIDATOR.md`.
- [x] Correctness: `./scripts/test_utf16validate.sh` passes **31/31** (scalar ≡
      SIMD, cross-checked against an independent Python reference); agrees under
      `--thread-num=1` and the default (3).
- [x] Reproducible `setup_parabix.sh` + `test_utf16validate.sh`.

### Evaluation

- [x] Preliminary scalar / SIMD / thread-count benchmark harness
      (`scripts/benchmark_utf16validate.sh`, summary in `results/`).
- [x] Benchmark methodology + fair comparison groups
      (`docs/benchmark_methodology.md`).
- [x] Cross-architecture evaluation (identical methodology on both hosts) —
      runbook: `docs/cross_arch_evaluation.md`.
      - [x] **CSIL x86-64** — `results/utf16_benchmark_csil_x86_64_summary.md` +
            `results/csil_x86_64_toolchain.md` (126/126 OK; simdutf=`westmere`
            on SSE4.2 QEMU guest — not Ice Lake). Owner: Daksh.
      - [ ] **Apple arm64** — teammate runs the same command with
            `BENCH_LABEL=utf16_benchmark_apple_arm64`.
- [~] Clear single-thread (`--thread-num=1`) vs. default-thread throughput analysis.
- [~] Clausecker–Lemire (`simdutf`) **performance** comparison (included in the
      cross-arch matrix as Group B; needs both machine summaries before reporting).
- [ ] `simdutf` **differential correctness** oracle over a large corpus (stronger
      than our own scalar reference).
- [ ] Graphs + final comparison tables; explain any unexpected results.

### Follow-ups / stretch

- [ ] UTF-16BE support — the byte-oriented classifier only needs to select the
      **even** byte positions instead of the odd ones.
- [ ] Bitwise data-parallel variant + study of whether transposition cost is
      justified for logic this simple.

## Main comparison

We will compare:

1. Clausecker-Lemire implementation
2. Parabix with `--thread-num=1`
3. Parabix with the default thread count

## Core work

- Build and understand the Parabix `base64` multiblock-kernel example
- Create a UTF-16 validator kernel
- Test valid and invalid UTF-16 input
- Verify correctness
- Measure single-threaded and multi-threaded performance
- Compare results with Clausecker-Lemire

## Optional work

- Compare against a bitwise data-parallel Parabix implementation
- Study whether transposition cost makes the bitwise approach worthwhile
