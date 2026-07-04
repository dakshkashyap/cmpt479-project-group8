# UTF-16LE validation -- preliminary benchmark summary

- Generated: 2026-07-04T23:14:46.049759+00:00
- Platform: macOS-26.5.1-arm64-arm-64bit-Mach-O
- Machine: arm64
- Processor: arm
- Python: 3.14.5
- Binary: utf16validate

## Benchmark settings

- Warmups per configuration: 2
- Measured repetitions per configuration: 7
- Throughput uses the median time; speedup is scalar median / configuration median.

## Results

| Dataset | Size (MiB) | Configuration | Median (s) | Throughput (MiB/s) | Speedup vs scalar |
| --- | --- | --- | --- | --- | --- |
| valid_utf16le_1MiB.bin | 1 | scalar | 0.0215 | 46.5 | 1.00x |
| valid_utf16le_1MiB.bin | 1 | simd (default threads) | 0.0215 | 46.5 | 1.00x |
| valid_utf16le_1MiB.bin | 1 | simd (--thread-num=1) | 0.0211 | 47.5 | 1.02x |
| valid_utf16le_1MiB.bin | 1 | simd (--thread-num=2) | 0.0220 | 45.6 | 0.98x |
| valid_utf16le_1MiB.bin | 1 | simd (--thread-num=3) | 0.0204 | 49.1 | 1.06x |
| valid_utf16le_8MiB.bin | 8 | scalar | 0.0237 | 337.7 | 1.00x |
| valid_utf16le_8MiB.bin | 8 | simd (default threads) | 0.0218 | 366.5 | 1.09x |
| valid_utf16le_8MiB.bin | 8 | simd (--thread-num=1) | 0.0229 | 349.9 | 1.04x |
| valid_utf16le_8MiB.bin | 8 | simd (--thread-num=2) | 0.0219 | 365.2 | 1.08x |
| valid_utf16le_8MiB.bin | 8 | simd (--thread-num=3) | 0.0215 | 372.5 | 1.10x |
| valid_utf16le_32MiB.bin | 32 | scalar | 0.0338 | 947.9 | 1.00x |
| valid_utf16le_32MiB.bin | 32 | simd (default threads) | 0.0248 | 1291.5 | 1.36x |
| valid_utf16le_32MiB.bin | 32 | simd (--thread-num=1) | 0.0277 | 1156.2 | 1.22x |
| valid_utf16le_32MiB.bin | 32 | simd (--thread-num=2) | 0.0247 | 1293.4 | 1.36x |
| valid_utf16le_32MiB.bin | 32 | simd (--thread-num=3) | 0.0250 | 1281.3 | 1.35x |
| valid_utf16le_64MiB.bin | 64 | scalar | 0.0472 | 1354.8 | 1.00x |
| valid_utf16le_64MiB.bin | 64 | simd (default threads) | 0.0294 | 2176.4 | 1.61x |
| valid_utf16le_64MiB.bin | 64 | simd (--thread-num=1) | 0.0354 | 1806.2 | 1.33x |
| valid_utf16le_64MiB.bin | 64 | simd (--thread-num=2) | 0.0303 | 2111.8 | 1.56x |
| valid_utf16le_64MiB.bin | 64 | simd (--thread-num=3) | 0.0294 | 2179.2 | 1.61x |

## Fastest measured configuration per size

- **valid_utf16le_1MiB.bin** (1 MiB): highest measured median throughput was `simd (--thread-num=3)` at 49.1 MiB/s.
- **valid_utf16le_8MiB.bin** (8 MiB): highest measured median throughput was `simd (--thread-num=3)` at 372.5 MiB/s.
- **valid_utf16le_32MiB.bin** (32 MiB): highest measured median throughput was `simd (--thread-num=2)` at 1293.4 MiB/s.
- **valid_utf16le_64MiB.bin** (64 MiB): highest measured median throughput was `simd (--thread-num=3)` at 2179.2 MiB/s.

## Observations

- For the 1 MiB input, configuration `simd (--thread-num=3)` had the highest measured median throughput (49.1 MiB/s).
- For the 8 MiB input, configuration `simd (--thread-num=3)` had the highest measured median throughput (372.5 MiB/s).
- For the 32 MiB input, configuration `simd (--thread-num=2)` had the highest measured median throughput (1293.4 MiB/s).
- For the 64 MiB input, configuration `simd (--thread-num=3)` had the highest measured median throughput (2179.2 MiB/s).
- These results are preliminary and apply only to this machine and this benchmark configuration.
- Timings include process startup and Parabix pipeline compilation; warmup runs populate Parabix's on-disk object cache before measurement.
- More repetitions and additional machines are required before drawing final conclusions.
- The Clausecker-Lemire comparison is not included here and is planned for a later project update.
