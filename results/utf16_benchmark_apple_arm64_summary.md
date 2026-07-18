# UTF-16 validation benchmark summary

- Generated: 2026-07-18T21:40:57.242156+00:00
- Platform: macOS-26.5.2-arm64-arm-64bit-Mach-O
- Machine: arm64
- Processor: arm
- Python: 3.14.5
- Parabix commit: f0369dd138e2e7a710566d5035f68b9cdc0bf305
- simdutf commit: ca7acbcea967b5dcbab490066e99e3a6e6925539 (implementation: arm64)
- Timing scope: whole_process
- Warmups: 2, measured repetitions: 7

Throughput is computed from the **median** time. Speedups are stated against an explicit baseline. Rows that reported an unexpected result are excluded from every speedup.

## Correctness gate

All 84 timed runs reported the expected result.

## Fixed per-process overhead

Measured on a tiny input. This constant cost (process startup, and for Parabix loading the compiled pipeline) is **not the same for each tool** and dominates small inputs, so small-input throughput must not be read as a performance conclusion.

| Tool | Fixed overhead (s) |
| --- | --- |
| parabix_simd | 0.0189 |
| scalar | 0.0191 |
| simdutf | 0.0022 |

## Group A - scalar vs Parabix SIMD (single thread)

Same tool, same process model, both single-threaded: this isolates the SIMD kernel. Baseline: scalar.

| Dataset | MiB | Mode | Median (s) | Stdev (s) | Throughput (MiB/s) | Adjusted (MiB/s) | Speedup vs scalar | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cjk | 128 | parabix_simd_t1 | 0.0436 | 0.0003 | 2934.7 | 5180.3 | 1.68x | yes |
| cjk | 128 | scalar(1) | 0.0734 | 0.0004 | 1744.7 | 2358.7 | 1.00x | yes |
| cjk | 256 | parabix_simd_t1 | 0.0683 | 0.0032 | 3749.7 | 5185.8 | 1.85x | yes |
| cjk | 256 | scalar(1) | 0.1263 | 0.0003 | 2026.4 | 2387.3 | 1.00x | yes |
| default | 128 | parabix_simd_t1 | 0.0439 | 0.0005 | 2915.6 | 5121.1 | 1.67x | yes |
| default | 128 | scalar(1) | 0.0732 | 0.0068 | 1748.2 | 2365.2 | 1.00x | yes |
| default | 256 | parabix_simd_t1 | 0.0686 | 0.0003 | 3732.0 | 5152.2 | 1.85x | yes |
| default | 256 | scalar(1) | 0.1270 | 0.0021 | 2016.1 | 2373.1 | 1.00x | yes |
| emoji_heavy | 128 | parabix_simd_t1 | 0.0439 | 0.0003 | 2918.4 | 5129.9 | 1.67x | yes |
| emoji_heavy | 128 | scalar(1) | 0.0731 | 0.0005 | 1751.8 | 2371.8 | 1.00x | yes |
| emoji_heavy | 256 | parabix_simd_t1 | 0.0683 | 0.0003 | 3748.5 | 5183.6 | 1.87x | yes |
| emoji_heavy | 256 | scalar(1) | 0.1277 | 0.0008 | 2004.8 | 2357.5 | 1.00x | yes |
| english_ascii_heavy | 128 | parabix_simd_t1 | 0.0441 | 0.0013 | 2900.5 | 5074.9 | 1.65x | yes |
| english_ascii_heavy | 128 | scalar(1) | 0.0727 | 0.0002 | 1759.6 | 2386.1 | 1.00x | yes |
| english_ascii_heavy | 256 | parabix_simd_t1 | 0.0681 | 0.0002 | 3759.8 | 5205.2 | 1.86x | yes |
| english_ascii_heavy | 256 | scalar(1) | 0.1269 | 0.0012 | 2017.8 | 2375.4 | 1.00x | yes |
| european_accented | 128 | parabix_simd_t1 | 0.0437 | 0.0002 | 2927.7 | 5158.7 | 1.66x | yes |
| european_accented | 128 | scalar(1) | 0.0727 | 0.0003 | 1760.7 | 2388.1 | 1.00x | yes |
| european_accented | 256 | parabix_simd_t1 | 0.0682 | 0.0002 | 3755.0 | 5196.0 | 1.87x | yes |
| european_accented | 256 | scalar(1) | 0.1276 | 0.0009 | 2006.3 | 2359.5 | 1.00x | yes |
| mixed_multilingual | 128 | parabix_simd_t1 | 0.0436 | 0.0001 | 2938.1 | 5190.8 | 1.67x | yes |
| mixed_multilingual | 128 | scalar(1) | 0.0726 | 0.0002 | 1763.9 | 2394.0 | 1.00x | yes |
| mixed_multilingual | 256 | parabix_simd_t1 | 0.0682 | 0.0002 | 3756.4 | 5198.6 | 1.85x | yes |
| mixed_multilingual | 256 | scalar(1) | 0.1263 | 0.0002 | 2027.0 | 2388.2 | 1.00x | yes |
| south_asian | 128 | parabix_simd_t1 | 0.0437 | 0.0003 | 2932.4 | 5173.2 | 1.66x | yes |
| south_asian | 128 | scalar(1) | 0.0726 | 0.0002 | 1762.8 | 2391.9 | 1.00x | yes |
| south_asian | 256 | parabix_simd_t1 | 0.0682 | 0.0005 | 3753.5 | 5193.1 | 1.85x | yes |
| south_asian | 256 | scalar(1) | 0.1264 | 0.0001 | 2025.1 | 2385.5 | 1.00x | yes |

## Group B - Parabix SIMD (1 thread) vs Clausecker-Lemire/simdutf

Valid input only, both single-threaded. simdutf reports validity, not an error count; on valid input both tools must scan every code unit, so the work is equivalent. Baseline: parabix_simd_t1.

| Dataset | MiB | Mode | Median (s) | Stdev (s) | Throughput (MiB/s) | Adjusted (MiB/s) | Speedup vs simd_t1 | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cjk | 128 | parabix_simd_t1 | 0.0436 | 0.0003 | 2934.7 | 5180.3 | 1.00x | yes |
| cjk | 128 | simdutf | 0.0514 | 0.0011 | 2488.2 | 2599.8 | 0.85x | yes |
| cjk | 256 | parabix_simd_t1 | 0.0683 | 0.0032 | 3749.7 | 5185.8 | 1.00x | yes |
| cjk | 256 | simdutf | 0.1067 | 0.0038 | 2399.9 | 2450.7 | 0.64x | yes |
| default | 128 | parabix_simd_t1 | 0.0439 | 0.0005 | 2915.6 | 5121.1 | 1.00x | yes |
| default | 128 | simdutf | 0.0604 | 0.0005 | 2119.3 | 2199.7 | 0.73x | yes |
| default | 256 | parabix_simd_t1 | 0.0686 | 0.0003 | 3732.0 | 5152.2 | 1.00x | yes |
| default | 256 | simdutf | 0.1187 | 0.0136 | 2157.1 | 2198.0 | 0.58x | yes |
| emoji_heavy | 128 | parabix_simd_t1 | 0.0439 | 0.0003 | 2918.4 | 5129.9 | 1.00x | yes |
| emoji_heavy | 128 | simdutf | 0.0702 | 0.0012 | 1823.8 | 1883.1 | 0.62x | yes |
| emoji_heavy | 256 | parabix_simd_t1 | 0.0683 | 0.0003 | 3748.5 | 5183.6 | 1.00x | yes |
| emoji_heavy | 256 | simdutf | 0.1365 | 0.0006 | 1875.5 | 1906.3 | 0.50x | yes |
| english_ascii_heavy | 128 | parabix_simd_t1 | 0.0441 | 0.0013 | 2900.5 | 5074.9 | 1.00x | yes |
| english_ascii_heavy | 128 | simdutf | 0.0523 | 0.0030 | 2446.4 | 2554.2 | 0.84x | yes |
| english_ascii_heavy | 256 | parabix_simd_t1 | 0.0681 | 0.0002 | 3759.8 | 5205.2 | 1.00x | yes |
| english_ascii_heavy | 256 | simdutf | 0.1011 | 0.0045 | 2531.7 | 2588.2 | 0.67x | yes |
| european_accented | 128 | parabix_simd_t1 | 0.0437 | 0.0002 | 2927.7 | 5158.7 | 1.00x | yes |
| european_accented | 128 | simdutf | 0.0527 | 0.0013 | 2431.0 | 2537.4 | 0.83x | yes |
| european_accented | 256 | parabix_simd_t1 | 0.0682 | 0.0002 | 3755.0 | 5196.0 | 1.00x | yes |
| european_accented | 256 | simdutf | 0.1012 | 0.0027 | 2530.7 | 2587.1 | 0.67x | yes |
| mixed_multilingual | 128 | parabix_simd_t1 | 0.0436 | 0.0001 | 2938.1 | 5190.8 | 1.00x | yes |
| mixed_multilingual | 128 | simdutf | 0.0519 | 0.0006 | 2464.7 | 2574.2 | 0.84x | yes |
| mixed_multilingual | 256 | parabix_simd_t1 | 0.0682 | 0.0002 | 3756.4 | 5198.6 | 1.00x | yes |
| mixed_multilingual | 256 | simdutf | 0.1060 | 0.0059 | 2414.5 | 2465.8 | 0.64x | yes |
| south_asian | 128 | parabix_simd_t1 | 0.0437 | 0.0003 | 2932.4 | 5173.2 | 1.00x | yes |
| south_asian | 128 | simdutf | 0.0521 | 0.0005 | 2454.6 | 2563.1 | 0.84x | yes |
| south_asian | 256 | parabix_simd_t1 | 0.0682 | 0.0005 | 3753.5 | 5193.1 | 1.00x | yes |
| south_asian | 256 | simdutf | 0.1011 | 0.0053 | 2530.9 | 2587.4 | 0.67x | yes |

## Group C - Parabix thread scaling

Only the thread count varies. Baseline: parabix_simd_t1. Thread efficiency is speedup / threads; efficiency well below 1 is a normal result for a memory-bandwidth-bound streaming scan.

| Dataset | MiB | Mode | Median (s) | Stdev (s) | Throughput (MiB/s) | Adjusted (MiB/s) | Speedup vs simd_t1 | OK |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cjk | 128 | parabix_simd_default | 0.0343 | 0.0004 | 3735.4 | 8333.4 | 1.27x | yes |
| cjk | 128 | parabix_simd_t1 | 0.0436 | 0.0003 | 2934.7 | 5180.3 | 1.00x | yes |
| cjk | 128 | parabix_simd_t2 | 0.0349 | 0.0015 | 3663.9 | 7985.9 | 1.25x | yes |
| cjk | 128 | parabix_simd_t3 | 0.0352 | 0.0004 | 3633.8 | 7844.6 | 1.24x | yes |
| cjk | 256 | parabix_simd_default | 0.0501 | 0.0005 | 5110.5 | 8209.0 | 1.36x | yes |
| cjk | 256 | parabix_simd_t1 | 0.0683 | 0.0032 | 3749.7 | 5185.8 | 1.00x | yes |
| cjk | 256 | parabix_simd_t2 | 0.0485 | 0.0002 | 5273.9 | 8638.9 | 1.41x | yes |
| cjk | 256 | parabix_simd_t3 | 0.0508 | 0.0004 | 5040.8 | 8030.5 | 1.34x | yes |
| default | 128 | parabix_simd_default | 0.0353 | 0.0004 | 3626.2 | 7808.8 | 1.24x | yes |
| default | 128 | parabix_simd_t1 | 0.0439 | 0.0005 | 2915.6 | 5121.1 | 1.00x | yes |
| default | 128 | parabix_simd_t2 | 0.0344 | 0.0006 | 3724.9 | 8281.7 | 1.28x | yes |
| default | 128 | parabix_simd_t3 | 0.0355 | 0.0005 | 3602.6 | 7700.3 | 1.24x | yes |
| default | 256 | parabix_simd_default | 0.0502 | 0.0004 | 5096.2 | 8172.1 | 1.37x | yes |
| default | 256 | parabix_simd_t1 | 0.0686 | 0.0003 | 3732.0 | 5152.2 | 1.00x | yes |
| default | 256 | parabix_simd_t2 | 0.0487 | 0.0004 | 5260.8 | 8603.7 | 1.41x | yes |
| default | 256 | parabix_simd_t3 | 0.0499 | 0.0002 | 5131.9 | 8264.2 | 1.38x | yes |
| emoji_heavy | 128 | parabix_simd_default | 0.0346 | 0.0014 | 3696.6 | 8143.1 | 1.27x | yes |
| emoji_heavy | 128 | parabix_simd_t1 | 0.0439 | 0.0003 | 2918.4 | 5129.9 | 1.00x | yes |
| emoji_heavy | 128 | parabix_simd_t2 | 0.0342 | 0.0004 | 3745.9 | 8386.3 | 1.28x | yes |
| emoji_heavy | 128 | parabix_simd_t3 | 0.0345 | 0.0003 | 3705.6 | 8186.8 | 1.27x | yes |
| emoji_heavy | 256 | parabix_simd_default | 0.0501 | 0.0024 | 5106.4 | 8198.2 | 1.36x | yes |
| emoji_heavy | 256 | parabix_simd_t1 | 0.0683 | 0.0003 | 3748.5 | 5183.6 | 1.00x | yes |
| emoji_heavy | 256 | parabix_simd_t2 | 0.0485 | 0.0003 | 5275.1 | 8642.1 | 1.41x | yes |
| emoji_heavy | 256 | parabix_simd_t3 | 0.0497 | 0.0002 | 5152.8 | 8318.6 | 1.37x | yes |
| english_ascii_heavy | 128 | parabix_simd_default | 0.0343 | 0.0004 | 3731.4 | 8313.6 | 1.29x | yes |
| english_ascii_heavy | 128 | parabix_simd_t1 | 0.0441 | 0.0013 | 2900.5 | 5074.9 | 1.00x | yes |
| english_ascii_heavy | 128 | parabix_simd_t2 | 0.0347 | 0.0007 | 3689.4 | 8108.2 | 1.27x | yes |
| english_ascii_heavy | 128 | parabix_simd_t3 | 0.0342 | 0.0004 | 3740.3 | 8358.2 | 1.29x | yes |
| english_ascii_heavy | 256 | parabix_simd_default | 0.0499 | 0.0005 | 5133.4 | 8268.3 | 1.37x | yes |
| english_ascii_heavy | 256 | parabix_simd_t1 | 0.0681 | 0.0002 | 3759.8 | 5205.2 | 1.00x | yes |
| english_ascii_heavy | 256 | parabix_simd_t2 | 0.0493 | 0.0005 | 5197.5 | 8435.8 | 1.38x | yes |
| english_ascii_heavy | 256 | parabix_simd_t3 | 0.0511 | 0.0015 | 5011.3 | 7955.9 | 1.33x | yes |
| european_accented | 128 | parabix_simd_default | 0.0343 | 0.0003 | 3734.0 | 8326.7 | 1.28x | yes |
| european_accented | 128 | parabix_simd_t1 | 0.0437 | 0.0002 | 2927.7 | 5158.7 | 1.00x | yes |
| european_accented | 128 | parabix_simd_t2 | 0.0338 | 0.0003 | 3787.0 | 8594.9 | 1.29x | yes |
| european_accented | 128 | parabix_simd_t3 | 0.0343 | 0.0003 | 3729.1 | 8302.4 | 1.27x | yes |
| european_accented | 256 | parabix_simd_default | 0.0501 | 0.0003 | 5106.8 | 8199.5 | 1.36x | yes |
| european_accented | 256 | parabix_simd_t1 | 0.0682 | 0.0002 | 3755.0 | 5196.0 | 1.00x | yes |
| european_accented | 256 | parabix_simd_t2 | 0.0485 | 0.0002 | 5282.4 | 8661.8 | 1.41x | yes |
| european_accented | 256 | parabix_simd_t3 | 0.0497 | 0.0006 | 5146.0 | 8301.0 | 1.37x | yes |
| mixed_multilingual | 128 | parabix_simd_default | 0.0348 | 0.0002 | 3676.0 | 8043.9 | 1.25x | yes |
| mixed_multilingual | 128 | parabix_simd_t1 | 0.0436 | 0.0001 | 2938.1 | 5190.8 | 1.00x | yes |
| mixed_multilingual | 128 | parabix_simd_t2 | 0.0342 | 0.0005 | 3740.4 | 8358.4 | 1.27x | yes |
| mixed_multilingual | 128 | parabix_simd_t3 | 0.0352 | 0.0003 | 3639.6 | 7871.2 | 1.24x | yes |
| mixed_multilingual | 256 | parabix_simd_default | 0.0495 | 0.0005 | 5171.4 | 8367.3 | 1.38x | yes |
| mixed_multilingual | 256 | parabix_simd_t1 | 0.0682 | 0.0002 | 3756.4 | 5198.6 | 1.00x | yes |
| mixed_multilingual | 256 | parabix_simd_t2 | 0.0485 | 0.0003 | 5273.9 | 8638.9 | 1.40x | yes |
| mixed_multilingual | 256 | parabix_simd_t3 | 0.0496 | 0.0004 | 5166.4 | 8354.0 | 1.38x | yes |
| south_asian | 128 | parabix_simd_default | 0.0349 | 0.0003 | 3662.5 | 7979.2 | 1.25x | yes |
| south_asian | 128 | parabix_simd_t1 | 0.0437 | 0.0003 | 2932.4 | 5173.2 | 1.00x | yes |
| south_asian | 128 | parabix_simd_t2 | 0.0344 | 0.0002 | 3722.4 | 8269.2 | 1.27x | yes |
| south_asian | 128 | parabix_simd_t3 | 0.0350 | 0.0003 | 3654.3 | 7940.7 | 1.25x | yes |
| south_asian | 256 | parabix_simd_default | 0.0496 | 0.0004 | 5158.0 | 8332.1 | 1.37x | yes |
| south_asian | 256 | parabix_simd_t1 | 0.0682 | 0.0005 | 3753.5 | 5193.1 | 1.00x | yes |
| south_asian | 256 | parabix_simd_t2 | 0.0487 | 0.0012 | 5255.6 | 8589.8 | 1.40x | yes |
| south_asian | 256 | parabix_simd_t3 | 0.0498 | 0.0005 | 5136.8 | 8277.1 | 1.37x | yes |

## Notes

- Timings are whole-process wall clock and include process startup; for Parabix they also include loading the compiled pipeline. See the fixed overhead table above and `docs/benchmark_methodology.md` section 5.
- Cross-tool conclusions should be drawn from large inputs on valid data, with both sides single-threaded.
- Results are machine-specific and must be reproduced elsewhere before being treated as general.
