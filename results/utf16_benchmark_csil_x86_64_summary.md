# UTF-16 validation benchmark summary — CSIL x86-64

**Architecture label:** `csil_x86_64`  
**Companion toolchain file:** [`csil_x86_64_toolchain.md`](csil_x86_64_toolchain.md)  
**Raw CSV (git-ignored):** `utf16_benchmark_csil_x86_64.csv`  
**Methodology:** [`docs/benchmark_methodology.md`](../docs/benchmark_methodology.md) ·
[`docs/cross_arch_evaluation.md`](../docs/cross_arch_evaluation.md)

### Host / toolchain (for the report)

| Field | Value |
| --- | --- |
| Host | `csil-cpu10` (SFU CSIL) |
| OS | Ubuntu 24.04.4 LTS (`Linux 6.17.0-35-generic`) |
| Arch | **x86_64** |
| CPU | QEMU Virtual CPU version 2.5+ (KVM); 8 vCPUs; 31 GiB RAM |
| SIMD ISA exposed | SSE / SSE2 / SSSE3 / SSE4.1 / SSE4.2 (**no AVX / AVX2 / AVX-512**) |
| Compiler | `g++` (Ubuntu) **13.3.0** (`/usr/bin/c++`) |
| LLVM (Parabix JIT) | **18.1.3** (`/usr/lib/llvm-18`) — LLVM 16 preferred by docs; CSIL falls back to 18 |
| Build type | Release |
| Parabix commit | `f0369dd138e2e7a710566d5035f68b9cdc0bf305` |
| simdutf | `v9.0.0` / `ca7acbcea967b5dcbab490066e99e3a6e7865539` |
| simdutf selected kernel | **`westmere`** (SSE4.2 path — *not* Ice Lake / AVX-512) |

**Caveat for the paper comparison:** this CSIL guest is **not** an Ice Lake physical
CPU. It only exposes SSE4.2, so the simdutf baseline here is the Westmere kernel.
Do not label these numbers as "Ice Lake". The matching arm64 Apple run still provides
the dual-architecture portability story (x86-64 vs arm64) under identical methodology.

---

# UTF-16 validation benchmark summary

- Generated: 2026-07-17T23:21:23.815402+00:00
- Platform: Linux-6.17.0-35-generic-x86_64-with-glibc2.39
- Machine: x86_64
- Processor: x86_64
- Python: 3.12.3
- Parabix commit: f0369dd138e2e7a710566d5035f68b9cdc0bf305
- simdutf commit: ca7acbcea967b5dcbab490066e99e3a6e6925539 (implementation: westmere)
- Timing scope: whole_process
- Warmups: 2, measured repetitions: 7

Throughput is computed from the **median** time. Speedups are stated against an explicit baseline. Rows that reported an unexpected result are excluded from every speedup.

## Correctness gate

All 126 timed runs reported the expected result.

## Fixed per-process overhead

Measured on a tiny input. This constant cost (process startup, and for Parabix loading the compiled pipeline) is **not the same for each tool** and dominates small inputs, so small-input throughput must not be read as a performance conclusion.

| Tool | Fixed overhead (s) |
| --- | --- |
| parabix_simd | 0.0446 |
| scalar | 0.0449 |
| simdutf | 0.0026 |

## Group A - scalar vs Parabix SIMD (single thread)

Same tool, same process model, both single-threaded: this isolates the SIMD kernel. Baseline: scalar.

| Dataset | MiB | Mode | Median (s) | Stdev (s) | Throughput (MiB/s) | Adjusted (MiB/s) | Speedup vs scalar | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cjk | 128 | parabix_simd_t1 | 0.0859 | 0.0032 | 1490.4 | 3100.5 | 1.12x | yes |
| cjk | 128 | scalar(1) | 0.0958 | 0.0025 | 1336.5 | 2515.1 | 1.00x | yes |
| cjk | 256 | parabix_simd_t1 | 0.1286 | 0.0043 | 1990.8 | 3047.8 | 1.14x | yes |
| cjk | 256 | scalar(1) | 0.1468 | 0.0035 | 1743.5 | 2510.9 | 1.00x | yes |
| cjk | 512 | parabix_simd_t1 | 0.2089 | 0.0044 | 2451.5 | 3117.1 | 1.15x | yes |
| cjk | 512 | scalar(1) | 0.2396 | 0.0109 | 2136.8 | 2629.2 | 1.00x | yes |
| default | 128 | parabix_simd_t1 | 0.0867 | 0.0023 | 1475.5 | 3036.7 | 1.08x | yes |
| default | 128 | scalar(1) | 0.0935 | 0.0035 | 1369.6 | 2635.0 | 1.00x | yes |
| default | 256 | parabix_simd_t1 | 0.1257 | 0.0030 | 2035.9 | 3154.9 | 1.15x | yes |
| default | 256 | scalar(1) | 0.1449 | 0.0063 | 1766.2 | 2558.4 | 1.00x | yes |
| default | 512 | parabix_simd_t1 | 0.1965 | 0.0023 | 2606.2 | 3371.6 | 1.23x | yes |
| default | 512 | scalar(1) | 0.2417 | 0.0099 | 2118.3 | 2601.2 | 1.00x | yes |
| emoji_heavy | 128 | parabix_simd_t1 | 0.0865 | 0.0017 | 1480.3 | 3057.0 | 1.14x | yes |
| emoji_heavy | 128 | scalar(1) | 0.0983 | 0.0021 | 1301.5 | 2394.0 | 1.00x | yes |
| emoji_heavy | 256 | parabix_simd_t1 | 0.1210 | 0.0030 | 2114.9 | 3348.7 | 1.20x | yes |
| emoji_heavy | 256 | scalar(1) | 0.1455 | 0.0046 | 1759.0 | 2543.2 | 1.00x | yes |
| emoji_heavy | 512 | parabix_simd_t1 | 0.1954 | 0.0024 | 2620.4 | 3395.4 | 1.21x | yes |
| emoji_heavy | 512 | scalar(1) | 0.2366 | 0.0060 | 2164.2 | 2670.8 | 1.00x | yes |
| english_ascii_heavy | 128 | parabix_simd_t1 | 0.0852 | 0.0032 | 1502.8 | 3154.8 | 1.12x | yes |
| english_ascii_heavy | 128 | scalar(1) | 0.0958 | 0.0022 | 1336.5 | 2514.9 | 1.00x | yes |
| english_ascii_heavy | 256 | parabix_simd_t1 | 0.1194 | 0.0020 | 2143.3 | 3420.4 | 1.14x | yes |
| english_ascii_heavy | 256 | scalar(1) | 0.1364 | 0.0022 | 1876.7 | 2796.9 | 1.00x | yes |
| english_ascii_heavy | 512 | parabix_simd_t1 | 0.1941 | 0.0010 | 2638.4 | 3425.8 | 1.16x | yes |
| english_ascii_heavy | 512 | scalar(1) | 0.2243 | 0.0029 | 2283.0 | 2854.1 | 1.00x | yes |
| european_accented | 128 | parabix_simd_t1 | 0.0831 | 0.0017 | 1539.8 | 3322.0 | 1.12x | yes |
| european_accented | 128 | scalar(1) | 0.0930 | 0.0033 | 1375.8 | 2657.9 | 1.00x | yes |
| european_accented | 256 | parabix_simd_t1 | 0.1203 | 0.0021 | 2128.8 | 3383.6 | 1.13x | yes |
| european_accented | 256 | scalar(1) | 0.1360 | 0.0049 | 1881.9 | 2808.3 | 1.00x | yes |
| european_accented | 512 | parabix_simd_t1 | 0.2076 | 0.0069 | 2465.8 | 3140.3 | 1.14x | yes |
| european_accented | 512 | scalar(1) | 0.2364 | 0.0107 | 2166.1 | 2673.8 | 1.00x | yes |
| mixed_multilingual | 128 | parabix_simd_t1 | 0.0875 | 0.0022 | 1463.4 | 2985.6 | 1.09x | yes |
| mixed_multilingual | 128 | scalar(1) | 0.0951 | 0.0034 | 1346.6 | 2550.9 | 1.00x | yes |
| mixed_multilingual | 256 | parabix_simd_t1 | 0.1253 | 0.0035 | 2043.8 | 3173.8 | 1.18x | yes |
| mixed_multilingual | 256 | scalar(1) | 0.1475 | 0.0075 | 1735.8 | 2495.0 | 1.00x | yes |
| mixed_multilingual | 512 | parabix_simd_t1 | 0.2022 | 0.0082 | 2532.6 | 3249.5 | 1.16x | yes |
| mixed_multilingual | 512 | scalar(1) | 0.2354 | 0.0096 | 2174.6 | 2686.8 | 1.00x | yes |
| south_asian | 128 | parabix_simd_t1 | 0.0904 | 0.0031 | 1415.2 | 2791.9 | 1.07x | yes |
| south_asian | 128 | scalar(1) | 0.0969 | 0.0021 | 1321.6 | 2462.8 | 1.00x | yes |
| south_asian | 256 | parabix_simd_t1 | 0.1285 | 0.0039 | 1992.2 | 3051.1 | 1.15x | yes |
| south_asian | 256 | scalar(1) | 0.1476 | 0.0117 | 1734.5 | 2492.4 | 1.00x | yes |
| south_asian | 512 | parabix_simd_t1 | 0.2042 | 0.0079 | 2506.9 | 3207.2 | 1.19x | yes |
| south_asian | 512 | scalar(1) | 0.2433 | 0.0151 | 2104.3 | 2580.3 | 1.00x | yes |

## Group B - Parabix SIMD (1 thread) vs Clausecker-Lemire/simdutf

Valid input only, both single-threaded. simdutf reports validity, not an error count; on valid input both tools must scan every code unit, so the work is equivalent. Baseline: parabix_simd_t1.

| Dataset | MiB | Mode | Median (s) | Stdev (s) | Throughput (MiB/s) | Adjusted (MiB/s) | Speedup vs simd_t1 | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cjk | 128 | parabix_simd_t1 | 0.0859 | 0.0032 | 1490.4 | 3100.5 | 1.00x | yes |
| cjk | 128 | simdutf | 0.0962 | 0.0112 | 1330.9 | 1367.6 | 0.89x | yes |
| cjk | 256 | parabix_simd_t1 | 0.1286 | 0.0043 | 1990.8 | 3047.8 | 1.00x | yes |
| cjk | 256 | simdutf | 0.2133 | 0.0138 | 1200.1 | 1214.8 | 0.60x | yes |
| cjk | 512 | parabix_simd_t1 | 0.2089 | 0.0044 | 2451.5 | 3117.1 | 1.00x | yes |
| cjk | 512 | simdutf | 0.4139 | 0.0265 | 1237.1 | 1244.9 | 0.50x | yes |
| default | 128 | parabix_simd_t1 | 0.0867 | 0.0023 | 1475.5 | 3036.7 | 1.00x | yes |
| default | 128 | simdutf | 0.1045 | 0.0040 | 1225.2 | 1256.2 | 0.83x | yes |
| default | 256 | parabix_simd_t1 | 0.1257 | 0.0030 | 2035.9 | 3154.9 | 1.00x | yes |
| default | 256 | simdutf | 0.2068 | 0.0022 | 1238.1 | 1253.7 | 0.61x | yes |
| default | 512 | parabix_simd_t1 | 0.1965 | 0.0023 | 2606.2 | 3371.6 | 1.00x | yes |
| default | 512 | simdutf | 0.3983 | 0.0171 | 1285.5 | 1293.9 | 0.49x | yes |
| emoji_heavy | 128 | parabix_simd_t1 | 0.0865 | 0.0017 | 1480.3 | 3057.0 | 1.00x | yes |
| emoji_heavy | 128 | simdutf | 0.1128 | 0.0091 | 1135.2 | 1161.7 | 0.77x | yes |
| emoji_heavy | 256 | parabix_simd_t1 | 0.1210 | 0.0030 | 2114.9 | 3348.7 | 1.00x | yes |
| emoji_heavy | 256 | simdutf | 0.2199 | 0.0119 | 1164.1 | 1177.9 | 0.55x | yes |
| emoji_heavy | 512 | parabix_simd_t1 | 0.1954 | 0.0024 | 2620.4 | 3395.4 | 1.00x | yes |
| emoji_heavy | 512 | simdutf | 0.4244 | 0.0093 | 1206.5 | 1213.9 | 0.46x | yes |
| english_ascii_heavy | 128 | parabix_simd_t1 | 0.0852 | 0.0032 | 1502.8 | 3154.8 | 1.00x | yes |
| english_ascii_heavy | 128 | simdutf | 0.0927 | 0.0072 | 1381.3 | 1420.8 | 0.92x | yes |
| english_ascii_heavy | 256 | parabix_simd_t1 | 0.1194 | 0.0020 | 2143.3 | 3420.4 | 1.00x | yes |
| english_ascii_heavy | 256 | simdutf | 0.1860 | 0.0084 | 1376.7 | 1396.1 | 0.64x | yes |
| english_ascii_heavy | 512 | parabix_simd_t1 | 0.1941 | 0.0010 | 2638.4 | 3425.8 | 1.00x | yes |
| english_ascii_heavy | 512 | simdutf | 0.3472 | 0.0149 | 1474.6 | 1485.6 | 0.56x | yes |
| european_accented | 128 | parabix_simd_t1 | 0.0831 | 0.0017 | 1539.8 | 3322.0 | 1.00x | yes |
| european_accented | 128 | simdutf | 0.0946 | 0.0016 | 1352.9 | 1390.8 | 0.88x | yes |
| european_accented | 256 | parabix_simd_t1 | 0.1203 | 0.0021 | 2128.8 | 3383.6 | 1.00x | yes |
| european_accented | 256 | simdutf | 0.1852 | 0.0076 | 1382.1 | 1401.6 | 0.65x | yes |
| european_accented | 512 | parabix_simd_t1 | 0.2076 | 0.0069 | 2465.8 | 3140.3 | 1.00x | yes |
| european_accented | 512 | simdutf | 0.3856 | 0.0375 | 1327.8 | 1336.7 | 0.54x | yes |
| mixed_multilingual | 128 | parabix_simd_t1 | 0.0875 | 0.0022 | 1463.4 | 2985.6 | 1.00x | yes |
| mixed_multilingual | 128 | simdutf | 0.1176 | 0.0076 | 1088.3 | 1112.7 | 0.74x | yes |
| mixed_multilingual | 256 | parabix_simd_t1 | 0.1253 | 0.0035 | 2043.8 | 3173.8 | 1.00x | yes |
| mixed_multilingual | 256 | simdutf | 0.2039 | 0.0100 | 1255.2 | 1271.3 | 0.61x | yes |
| mixed_multilingual | 512 | parabix_simd_t1 | 0.2022 | 0.0082 | 2532.6 | 3249.5 | 1.00x | yes |
| mixed_multilingual | 512 | simdutf | 0.4048 | 0.0316 | 1264.8 | 1272.9 | 0.50x | yes |
| south_asian | 128 | parabix_simd_t1 | 0.0904 | 0.0031 | 1415.2 | 2791.9 | 1.00x | yes |
| south_asian | 128 | simdutf | 0.1038 | 0.0106 | 1232.7 | 1264.0 | 0.87x | yes |
| south_asian | 256 | parabix_simd_t1 | 0.1285 | 0.0039 | 1992.2 | 3051.1 | 1.00x | yes |
| south_asian | 256 | simdutf | 0.2164 | 0.0226 | 1182.7 | 1197.0 | 0.59x | yes |
| south_asian | 512 | parabix_simd_t1 | 0.2042 | 0.0079 | 2506.9 | 3207.2 | 1.00x | yes |
| south_asian | 512 | simdutf | 0.3740 | 0.0442 | 1369.0 | 1378.5 | 0.55x | yes |

## Group C - Parabix thread scaling

Only the thread count varies. Baseline: parabix_simd_t1. Thread efficiency is speedup / threads; efficiency well below 1 is a normal result for a memory-bandwidth-bound streaming scan.

| Dataset | MiB | Mode | Median (s) | Stdev (s) | Throughput (MiB/s) | Adjusted (MiB/s) | Speedup vs simd_t1 | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cjk | 128 | parabix_simd_default | 0.0803 | 0.0016 | 1593.1 | 3580.8 | 1.07x | yes |
| cjk | 128 | parabix_simd_t1 | 0.0859 | 0.0032 | 1490.4 | 3100.5 | 1.00x | yes |
| cjk | 128 | parabix_simd_t2 | 0.0750 | 0.0047 | 1705.9 | 4205.7 | 1.14x | yes |
| cjk | 128 | parabix_simd_t3 | 0.0789 | 0.0027 | 1622.4 | 3732.0 | 1.09x | yes |
| cjk | 256 | parabix_simd_default | 0.1094 | 0.0070 | 2339.0 | 3947.6 | 1.17x | yes |
| cjk | 256 | parabix_simd_t1 | 0.1286 | 0.0043 | 1990.8 | 3047.8 | 1.00x | yes |
| cjk | 256 | parabix_simd_t2 | 0.1079 | 0.0058 | 2373.2 | 4045.9 | 1.19x | yes |
| cjk | 256 | parabix_simd_t3 | 0.1094 | 0.0044 | 2340.3 | 3951.1 | 1.18x | yes |
| cjk | 512 | parabix_simd_default | 0.1770 | 0.0036 | 2892.7 | 3867.2 | 1.18x | yes |
| cjk | 512 | parabix_simd_t1 | 0.2089 | 0.0044 | 2451.5 | 3117.1 | 1.00x | yes |
| cjk | 512 | parabix_simd_t2 | 0.1760 | 0.0085 | 2909.5 | 3897.3 | 1.19x | yes |
| cjk | 512 | parabix_simd_t3 | 0.1731 | 0.0080 | 2957.9 | 3984.5 | 1.21x | yes |
| default | 128 | parabix_simd_default | 0.0756 | 0.0013 | 1694.0 | 4134.0 | 1.15x | yes |
| default | 128 | parabix_simd_t1 | 0.0867 | 0.0023 | 1475.5 | 3036.7 | 1.00x | yes |
| default | 128 | parabix_simd_t2 | 0.0769 | 0.0096 | 1664.0 | 3960.0 | 1.13x | yes |
| default | 128 | parabix_simd_t3 | 0.0765 | 0.0035 | 1673.6 | 4014.9 | 1.13x | yes |
| default | 256 | parabix_simd_default | 0.1060 | 0.0051 | 2415.9 | 4171.6 | 1.19x | yes |
| default | 256 | parabix_simd_t1 | 0.1257 | 0.0030 | 2035.9 | 3154.9 | 1.00x | yes |
| default | 256 | parabix_simd_t2 | 0.0965 | 0.0046 | 2654.0 | 4936.2 | 1.30x | yes |
| default | 256 | parabix_simd_t3 | 0.1056 | 0.0043 | 2423.2 | 4193.5 | 1.19x | yes |
| default | 512 | parabix_simd_default | 0.1457 | 0.0025 | 3513.9 | 5063.8 | 1.35x | yes |
| default | 512 | parabix_simd_t1 | 0.1965 | 0.0023 | 2606.2 | 3371.6 | 1.00x | yes |
| default | 512 | parabix_simd_t2 | 0.1408 | 0.0031 | 3635.3 | 5319.8 | 1.39x | yes |
| default | 512 | parabix_simd_t3 | 0.1477 | 0.0123 | 3466.3 | 4965.6 | 1.33x | yes |
| emoji_heavy | 128 | parabix_simd_default | 0.0776 | 0.0031 | 1649.6 | 3879.0 | 1.11x | yes |
| emoji_heavy | 128 | parabix_simd_t1 | 0.0865 | 0.0017 | 1480.3 | 3057.0 | 1.00x | yes |
| emoji_heavy | 128 | parabix_simd_t2 | 0.0783 | 0.0045 | 1634.0 | 3794.3 | 1.10x | yes |
| emoji_heavy | 128 | parabix_simd_t3 | 0.0786 | 0.0028 | 1629.5 | 3769.6 | 1.10x | yes |
| emoji_heavy | 256 | parabix_simd_default | 0.1071 | 0.0014 | 2389.3 | 4093.1 | 1.13x | yes |
| emoji_heavy | 256 | parabix_simd_t1 | 0.1210 | 0.0030 | 2114.9 | 3348.7 | 1.00x | yes |
| emoji_heavy | 256 | parabix_simd_t2 | 0.1103 | 0.0045 | 2320.4 | 3894.7 | 1.10x | yes |
| emoji_heavy | 256 | parabix_simd_t3 | 0.1107 | 0.0029 | 2313.1 | 3874.3 | 1.09x | yes |
| emoji_heavy | 512 | parabix_simd_default | 0.1656 | 0.0056 | 3091.5 | 4230.8 | 1.18x | yes |
| emoji_heavy | 512 | parabix_simd_t1 | 0.1954 | 0.0024 | 2620.4 | 3395.4 | 1.00x | yes |
| emoji_heavy | 512 | parabix_simd_t2 | 0.1437 | 0.0097 | 3561.8 | 5163.9 | 1.36x | yes |
| emoji_heavy | 512 | parabix_simd_t3 | 0.1662 | 0.0024 | 3079.8 | 4209.0 | 1.18x | yes |
| english_ascii_heavy | 128 | parabix_simd_default | 0.0701 | 0.0031 | 1825.2 | 5013.8 | 1.21x | yes |
| english_ascii_heavy | 128 | parabix_simd_t1 | 0.0852 | 0.0032 | 1502.8 | 3154.8 | 1.00x | yes |
| english_ascii_heavy | 128 | parabix_simd_t2 | 0.0709 | 0.0014 | 1806.5 | 4875.1 | 1.20x | yes |
| english_ascii_heavy | 128 | parabix_simd_t3 | 0.0724 | 0.0021 | 1768.6 | 4608.6 | 1.18x | yes |
| english_ascii_heavy | 256 | parabix_simd_default | 0.0935 | 0.0017 | 2739.3 | 5239.8 | 1.28x | yes |
| english_ascii_heavy | 256 | parabix_simd_t1 | 0.1194 | 0.0020 | 2143.3 | 3420.4 | 1.00x | yes |
| english_ascii_heavy | 256 | parabix_simd_t2 | 0.0927 | 0.0022 | 2763.0 | 5327.4 | 1.29x | yes |
| english_ascii_heavy | 256 | parabix_simd_t3 | 0.0950 | 0.0032 | 2693.6 | 5075.2 | 1.26x | yes |
| english_ascii_heavy | 512 | parabix_simd_default | 0.1635 | 0.0108 | 3131.6 | 4306.3 | 1.19x | yes |
| english_ascii_heavy | 512 | parabix_simd_t1 | 0.1941 | 0.0010 | 2638.4 | 3425.8 | 1.00x | yes |
| english_ascii_heavy | 512 | parabix_simd_t2 | 0.1408 | 0.0021 | 3637.5 | 5324.6 | 1.38x | yes |
| english_ascii_heavy | 512 | parabix_simd_t3 | 0.1434 | 0.0072 | 3571.4 | 5184.1 | 1.35x | yes |
| european_accented | 128 | parabix_simd_default | 0.0693 | 0.0015 | 1846.1 | 5174.6 | 1.20x | yes |
| european_accented | 128 | parabix_simd_t1 | 0.0831 | 0.0017 | 1539.8 | 3322.0 | 1.00x | yes |
| european_accented | 128 | parabix_simd_t2 | 0.0698 | 0.0018 | 1832.6 | 5069.7 | 1.19x | yes |
| european_accented | 128 | parabix_simd_t3 | 0.0695 | 0.0015 | 1841.3 | 5137.1 | 1.20x | yes |
| european_accented | 256 | parabix_simd_default | 0.0988 | 0.0032 | 2590.9 | 4722.5 | 1.22x | yes |
| european_accented | 256 | parabix_simd_t1 | 0.1203 | 0.0021 | 2128.8 | 3383.6 | 1.00x | yes |
| european_accented | 256 | parabix_simd_t2 | 0.0922 | 0.0021 | 2775.9 | 5375.4 | 1.30x | yes |
| european_accented | 256 | parabix_simd_t3 | 0.0962 | 0.0039 | 2660.0 | 4957.0 | 1.25x | yes |
| european_accented | 512 | parabix_simd_default | 0.1645 | 0.0106 | 3111.8 | 4269.0 | 1.26x | yes |
| european_accented | 512 | parabix_simd_t1 | 0.2076 | 0.0069 | 2465.8 | 3140.3 | 1.00x | yes |
| european_accented | 512 | parabix_simd_t2 | 0.1715 | 0.0181 | 2985.9 | 4035.4 | 1.21x | yes |
| european_accented | 512 | parabix_simd_t3 | 0.1527 | 0.0114 | 3353.2 | 4736.7 | 1.36x | yes |
| mixed_multilingual | 128 | parabix_simd_default | 0.0776 | 0.0012 | 1649.2 | 3877.1 | 1.13x | yes |
| mixed_multilingual | 128 | parabix_simd_t1 | 0.0875 | 0.0022 | 1463.4 | 2985.6 | 1.00x | yes |
| mixed_multilingual | 128 | parabix_simd_t2 | 0.0771 | 0.0030 | 1660.7 | 3940.9 | 1.13x | yes |
| mixed_multilingual | 128 | parabix_simd_t3 | 0.0802 | 0.0047 | 1596.4 | 3597.3 | 1.09x | yes |
| mixed_multilingual | 256 | parabix_simd_default | 0.1098 | 0.0084 | 2332.2 | 3928.3 | 1.14x | yes |
| mixed_multilingual | 256 | parabix_simd_t1 | 0.1253 | 0.0035 | 2043.8 | 3173.8 | 1.00x | yes |
| mixed_multilingual | 256 | parabix_simd_t2 | 0.1061 | 0.0051 | 2412.7 | 4162.1 | 1.18x | yes |
| mixed_multilingual | 256 | parabix_simd_t3 | 0.1003 | 0.0044 | 2552.7 | 4597.1 | 1.25x | yes |
| mixed_multilingual | 512 | parabix_simd_default | 0.1667 | 0.0025 | 3072.1 | 4194.5 | 1.21x | yes |
| mixed_multilingual | 512 | parabix_simd_t1 | 0.2022 | 0.0082 | 2532.6 | 3249.5 | 1.00x | yes |
| mixed_multilingual | 512 | parabix_simd_t2 | 0.1735 | 0.0112 | 2951.4 | 3972.7 | 1.17x | yes |
| mixed_multilingual | 512 | parabix_simd_t3 | 0.1692 | 0.0101 | 3026.4 | 4109.8 | 1.19x | yes |
| south_asian | 128 | parabix_simd_default | 0.0769 | 0.0036 | 1664.8 | 3964.1 | 1.18x | yes |
| south_asian | 128 | parabix_simd_t1 | 0.0904 | 0.0031 | 1415.2 | 2791.9 | 1.00x | yes |
| south_asian | 128 | parabix_simd_t2 | 0.0759 | 0.0037 | 1686.9 | 4091.8 | 1.19x | yes |
| south_asian | 128 | parabix_simd_t3 | 0.0765 | 0.0035 | 1672.1 | 4006.1 | 1.18x | yes |
| south_asian | 256 | parabix_simd_default | 0.1062 | 0.0052 | 2410.2 | 4154.7 | 1.21x | yes |
| south_asian | 256 | parabix_simd_t1 | 0.1285 | 0.0039 | 1992.2 | 3051.1 | 1.00x | yes |
| south_asian | 256 | parabix_simd_t2 | 0.1063 | 0.0063 | 2407.8 | 4147.5 | 1.21x | yes |
| south_asian | 256 | parabix_simd_t3 | 0.1074 | 0.0067 | 2384.1 | 4077.7 | 1.20x | yes |
| south_asian | 512 | parabix_simd_default | 0.1660 | 0.0106 | 3084.9 | 4218.5 | 1.23x | yes |
| south_asian | 512 | parabix_simd_t1 | 0.2042 | 0.0079 | 2506.9 | 3207.2 | 1.00x | yes |
| south_asian | 512 | parabix_simd_t2 | 0.1422 | 0.0100 | 3599.5 | 5243.5 | 1.44x | yes |
| south_asian | 512 | parabix_simd_t3 | 0.1456 | 0.0115 | 3517.0 | 5070.2 | 1.40x | yes |

## Notes

- Timings are whole-process wall clock and include process startup; for Parabix they also include loading the compiled pipeline. See the fixed overhead table above and `docs/benchmark_methodology.md` section 5.
- Cross-tool conclusions should be drawn from large inputs on valid data, with both sides single-threaded.
- Results are machine-specific and must be reproduced elsewhere before being treated as general.
