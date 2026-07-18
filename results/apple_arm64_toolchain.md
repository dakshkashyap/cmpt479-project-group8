# Apple arm64 toolchain

```
host=Harveers-MacBook-Air (Apple M1)
uname=Darwin 25.5.0 Darwin Kernel Version 25.5.0 RELEASE_ARM64_T8103 arm64
arch=arm64
os=macOS 26.5.2 (build 25F84)
cpu_model=Apple M1
cpu_flags_simd=neon / asimd (ARMv8-A NEON, 128-bit)
cpus=8
ram_gib=8
compiler=Apple clang version 21.0.0 (clang-2100.1.1.101)
compiler_path=/usr/bin/c++
llvm=16.0.6 (Homebrew llvm@16 — the Parabix JIT/codegen backend)
llvm_dir=/opt/homebrew/opt/llvm@16
parabix_commit=f0369dd138e2e7a710566d5035f68b9cdc0bf305
simdutf_tag=v9.0.0
simdutf_commit=ca7acbcea967b5dcbab490066e99e3a6e6925539
simdutf_impl=arm64
build_type=Release
bench_label=utf16_benchmark_apple_arm64
bench_datasets=all (default + multilingual modes)
bench_sizes_mb=128,256
bench_warmups=2
bench_repetitions=7
bench_include_simdutf=1
timed_rows=84
result_ok_all=true
summary=results/utf16_benchmark_apple_arm64_summary.md
```

## Notes specific to this host

- **RAM differs from CSIL** (8 GiB here vs 31 GiB on `csil-cpu10`). The 512 MiB size that
  CSIL ran was **omitted** on this 8 GiB machine to avoid memory pressure; 128 and 256 MiB
  are run and both overlap CSIL, so the cross-arch comparison is drawn on those two sizes.
- `simdutf_impl=arm64` is the **NEON high-byte** path — the fair single-threaded competitor
  for our byte-oriented Parabix kernel, exactly mirroring CSIL where simdutf selected the
  SSE4.2 `westmere` path. Both are the native SIMD kernel for their architecture.
- The Parabix fixed per-process overhead is far lower here (~0.019 s) than on the CSIL QEMU
  guest (~0.045 s), so the adjusted (overhead-subtracted) throughput is the fairer per-byte
  number on both hosts; quote both, as the summaries do.

## Mac-only extended modes (NOT part of the cross-arch comparison)

CSIL only benchmarked scalar / Parabix SIMD / simdutf **validation** (Groups A/B/C), so those
are the only directly comparable modes. The extra pipeline stages below were measured on this
Mac for completeness; there is **no CSIL counterpart**, so they are Mac-only and must not be
presented as cross-architecture results. Repair (`--repair`, issue #40) is **not available on
this branch** (it was cut before #40 merged), so it is not measured here.

Whole-process wall time, valid `default` 128 MiB input, 2 warmups / median of 7,
`overhead` measured on a 2-byte input, `adj` = overhead-subtracted per-byte throughput:

| Mode | overhead (s) | 128 MiB (s) | raw MiB/s | adjusted MiB/s |
|---|---|---|---|---|
| scalar | 0.0187 | 0.0726 | 1763 | 2376 |
| parabix_simd_t1 | 0.0181 | 0.0428 | 2993 | 5196 |
| parabix_simd_default | 0.0183 | 0.0338 | 3790 | 8256 |
| simdutf (arm64) | 0.0020 | 0.0582 | 2198 | 2277 |
| errorMarks producer | 0.0180 | 0.0344 | 3717 | 7797 |
| errorMarks + two-level scan | 0.0181 | 0.0436 | 2934 | 5010 |

Observations (Mac-only): emitting the `errorMarks` bitstream on valid input is nearly as fast
as count-only SIMD (the all-zero marker stream is cheap); adding the `TwoLevelScanKernel`
consumer costs a modest amount on clean data (the high-level index is still built over every
block), consistent with the issue #39 scan-cost analysis.
