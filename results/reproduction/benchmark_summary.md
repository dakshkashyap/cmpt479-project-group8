# UTF-16 pipeline benchmark (issue #45)

Validation, error location, two-level scan and repair, measured over the controlled error-density corpus from issue #44.

**These numbers are machine-specific evidence from one run on one machine, not universal claims.** Other processes were running, no laboratory isolation was applied, and the figures should be reproduced locally before being relied on.

## Environment

- **timestamp**: 2026-07-29T18:33:13.482553+00:00
- **os**: Darwin 25.5.0
- **architecture**: arm64
- **cpu_model**: Apple M1
- **logical_cpus**: 8
- **python_version**: 3.14.5
- **compiler**: Apple clang version 21.0.0 (clang-2100.1.1.101)
- **git_commit**: 03cf3e3756db678743fed9690654a50d7f5bc7ab
- **branch**: issue-47-one-command-reproduction
- **dirty**: True
- **validator_binary**: /Users/harveervirk/SFU/Summer2026/Cmpt-479/Project/cmpt479-project-group8/.deps/parabix/build/bin/utf16validate
- **simdutf**: available (compiled once, before any measured run)
- **cpu_affinity_applied**: False
- **cpu_affinity_note**: not requested
- **command**: `/Users/harveervirk/SFU/Summer2026/Cmpt-479/Project/cmpt479-project-group8/benchmarks/benchmark_utf16_pipeline.py --bin /Users/harveervirk/SFU/Summer2026/Cmpt-479/Project/cmpt479-project-group8/.deps/parabix/build/bin/utf16validate --sizes 4MiB --densities 0,1,10,50 --warmups 1 --iterations 3 --seed 479 --output /Users/harveervirk/SFU/Summer2026/Cmpt-479/Project/cmpt479-project-group8/results/reproduction.partial/benchmark.csv --summary /Users/harveervirk/SFU/Summer2026/Cmpt-479/Project/cmpt479-project-group8/results/reproduction.partial/benchmark_summary.md`

## Methodology

- Whole-process wall clock (`time.perf_counter_ns`), 1 warm-up run(s) and 3 measured iteration(s) per command; every raw iteration is kept in the CSV.
- The **median** is the headline statistic; min, max, mean and standard deviation are recorded alongside it.
- Throughput is computed from **input bytes on disk**, not code units.
- stdout and stderr of every measured command are redirected to the null sink, so terminal I/O is never timed while the implementation still does all of its work.
- Operation order is rotated per dataset, deterministically from `--seed 479`, to spread systematic order and thermal bias.
- Each implementation's fixed per-process cost was measured separately on a two-code-unit input; `adjusted_median_mib_s` in the CSV subtracts it. That cost dominates the smallest inputs.
- Dataset generation and helper compilation happen before any measurement and are never timed.
- A timeout of 300.0 s applies to every run; a non-zero exit marks the row failed rather than being silently dropped.

## Correctness gate

Before any dataset is timed, scalar, `--simd`, `--emit-error-marks`, the Python oracle, `--print-positions` and `--scan-error-marks` must all agree with the manifest's `actual_error_count`, positions must match the oracle exactly, and `--repair` output must equal the oracle's repaired bytes and re-validate to zero errors. No timing row is written for a path that fails its gate.

Excluded by the gate:

- `locate_scan` excluded for errdens_4MiB_d10pct.utf16be.bin (14 extra scan position(s) beyond EOF, e.g. [2097154, 2097180, 2097181], on a 2097152-code-unit input; oracle and linear agree, no real position missing, errorCount correct)
- `locate_scan` excluded for errdens_4MiB_d10pct.utf16le.bin (14 extra scan position(s) beyond EOF, e.g. [2097154, 2097180, 2097181], on a 2097152-code-unit input; oracle and linear agree, no real position missing, errorCount correct)
- `locate_scan` excluded for errdens_4MiB_d50pct.utf16be.bin (68 extra scan position(s) beyond EOF, e.g. [2097153, 2097154, 2097155], on a 2097152-code-unit input; oracle and linear agree, no real position missing, errorCount correct)
- `locate_scan` excluded for errdens_4MiB_d50pct.utf16le.bin (68 extra scan position(s) beyond EOF, e.g. [2097153, 2097154, 2097155], on a 2097152-code-unit input; oracle and linear agree, no real position missing, errorCount correct)

## Selected matrix

- datasets timed: 8 (from `datasets/error_density/manifest.csv`)
- sizes: 4194304 B
- densities: 0%, 1%, 10%, 50%
- encodings: UTF-16BE, UTF-16LE

## Operations

| operation | category | definition |
| --- | --- | --- |
| `validate_scalar` | validation | scalar kernel; reports errorCount only |
| `validate_simd` | validation | `--simd` byte-oriented Parabix kernel; errorCount only |
| `emit_error_marks` | marker generation | `--emit-error-marks`: builds the one-bit-per-code-unit stream, still reports a count |
| `locate_linear` | location materialization | `--print-positions`: visits every block and prints each ill-formed index |
| `locate_scan` | scan-based location | `--scan-error-marks`: TwoLevelScanKernel, skips clean 4096-unit regions |
| `repair` | repair output | `--repair`: writes repaired UTF-16 to stdout |
| `simdutf_validate` | validation | `simdutf::validate_utf16{le,be}` |
| `simdutf_repair` | repair output | `simdutf::to_well_formed_utf16{le,be}` |

## Median throughput by operation

Median of the per-dataset medians, over every dataset that passed its gate. Small inputs are dominated by process start-up; see the adjusted column in the CSV.

`adjusted` subtracts that implementation's measured fixed per-process cost, which differs per tool. It is reported only where the measurement is at least 3x that cost; below that the corrected value would be dominated by its own noise, so it is left as n/a rather than published.

| operation | datasets | median MiB/s (raw) | median MiB/s (adjusted) |
| --- | --- | --- | --- |
| `validate_scalar` | 8 | 101.5 | not resolved by this timing scope |
| `validate_simd` | 8 | 102.8 | not resolved by this timing scope |
| `emit_error_marks` | 8 | 103.7 | not resolved by this timing scope |
| `locate_linear` | 8 | 35.0 | 12.0 |
| `locate_scan` | 4 | 76.2 | not resolved by this timing scope |
| `repair` | 8 | 100.1 | not resolved by this timing scope |
| `simdutf_validate` | 8 | 761.8 | not resolved by this timing scope |
| `simdutf_repair` | 8 | 563.9 | not resolved by this timing scope |

## Throughput by size and operation -- raw (whole process)

| size (bytes) | `validate_scalar` | `validate_simd` | `emit_error_marks` | `locate_linear` | `locate_scan` | `repair` | `simdutf_validate` | `simdutf_repair` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4194304 | 101.5 | 102.8 | 103.7 | 35.0 | 76.2 | 100.1 | 761.8 | 563.9 |

## Throughput by size and operation -- overhead-adjusted

| size (bytes) | `validate_scalar` | `validate_simd` | `emit_error_marks` | `locate_linear` | `locate_scan` | `repair` | `simdutf_validate` | `simdutf_repair` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4194304 | not resolved by this timing scope | not resolved by this timing scope | not resolved by this timing scope | 12.0 | not resolved by this timing scope | not resolved by this timing scope | not resolved by this timing scope | not resolved by this timing scope |

At the smallest sizes the raw table is dominated by process start-up, so the operations look identical there; that is a property of whole-process timing, not of the kernels.

**Resolution limit measured on this machine.** 7 of the size/operation combinations timed here have a median wall time below 3x their implementation's fixed per-process cost, including most operations at 4194304 bytes, the largest size in this run. Across those combinations the median wall time is about 0.039 s against a median measured start-up of about 0.020 s. At this timing scope the work is not separable from process start-up at these sizes, so the raw throughput columns above should be read as *process* throughput, not kernel throughput, and no per-kernel ranking is claimed from them.

Larger inputs (the 4 MiB tier, and repeating with `--sizes 4MiB`) are where this matrix starts to measure the kernels rather than the process. That is a property of the measurement, not of the code.

## Density sensitivity

Median MiB/s at each malformed-unit density, per operation.

| density | `validate_scalar` | `validate_simd` | `emit_error_marks` | `locate_linear` | `locate_scan` | `repair` | `simdutf_validate` | `simdutf_repair` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0% | 101.4 | 104.2 | 106.4 | 101.9 | 100.2 | 101.0 | 756.5 | 753.3 |
| 1% | 100.3 | 101.7 | 100.4 | 53.0 | 52.7 | 101.0 | 759.1 | 568.8 |
| 10% | 105.5 | 103.5 | 103.7 | 17.0 | no data | 99.2 | 763.3 | 583.0 |
| 50% | 103.4 | 100.9 | 102.3 | 4.0 | no data | 104.5 | 768.6 | 386.3 |

A gap means every dataset at that density/operation was excluded by the correctness gate (see above), not that the run failed.

Unlike the cross-operation tables, a **trend down a single column is meaningful even at this timing scope**: the size is fixed and the same per-process start-up is present at every density, so it cancels out of the comparison. Differences *between* columns remain confounded by start-up and are not ranked here.

## Pairwise comparisons

Adjusted medians (fixed per-process cost subtracted), because at these sizes the raw numbers are mostly process start-up.

| comparison | A adjusted MiB/s | B adjusted MiB/s | B / A |
| --- | --- | --- | --- |
| scalar vs SIMD validation | not resolved by this timing scope | not resolved by this timing scope | not resolved by this timing scope |
| validation vs errorMarks generation | not resolved by this timing scope | not resolved by this timing scope | not resolved by this timing scope |
| linear vs two-level scan location | 12.0 | not resolved by this timing scope | not resolved by this timing scope |
| Parabix vs simdutf validation | not resolved by this timing scope | not resolved by this timing scope | not resolved by this timing scope |
| Parabix vs simdutf repair | not resolved by this timing scope | not resolved by this timing scope | not resolved by this timing scope |

Ratios are medians on this machine over the datasets that passed the gate. The overhead subtraction is an estimate, the two tools do not perform identical work, and `locate_scan` is measured on a different (smaller) set of datasets than the other paths -- see the exclusions above. These are directional observations, not kernel-only speedups.

## UTF-16LE vs UTF-16BE

| operation | LE median MiB/s | BE median MiB/s |
| --- | --- | --- |
| `validate_scalar` | 102.2 | 101.5 |
| `validate_simd` | 102.7 | 102.9 |
| `emit_error_marks` | 102.1 | 103.8 |
| `locate_linear` | 35.3 | 34.7 |
| `locate_scan` | 76.8 | 76.2 |
| `repair` | 100.1 | 101.1 |
| `simdutf_validate` | 758.5 | 763.7 |
| `simdutf_repair` | 577.7 | 563.9 |

## simdutf

- status: available (compiled once, before any measured run)
- simdutf validation returns a boolean; Parabix reports an error count. Both read the whole input, but they are not doing identical work.
- simdutf repair is compared only on even-length inputs. Its `char16_t` API has no notion of an odd trailing byte, so this project's "drop the byte and append one U+FFFD" policy has no simdutf equivalent.

## Skipped rows

| dataset | operation | reason |
| --- | --- | --- |
| errdens_4MiB_d10pct.utf16be.bin | `locate_scan` | known scan-consumer symptom (14 extra scan position(s) beyond EOF, e.g. [2097154, 2097180, 2097181], on a 2097152-code-unit input; oracle and linear agree, no real position missing, errorCount correct); the position output is incorrect, so only locate_scan is excluded for this dataset |
| errdens_4MiB_d10pct.utf16le.bin | `locate_scan` | known scan-consumer symptom (14 extra scan position(s) beyond EOF, e.g. [2097154, 2097180, 2097181], on a 2097152-code-unit input; oracle and linear agree, no real position missing, errorCount correct); the position output is incorrect, so only locate_scan is excluded for this dataset |
| errdens_4MiB_d50pct.utf16be.bin | `locate_scan` | known scan-consumer symptom (68 extra scan position(s) beyond EOF, e.g. [2097153, 2097154, 2097155], on a 2097152-code-unit input; oracle and linear agree, no real position missing, errorCount correct); the position output is incorrect, so only locate_scan is excluded for this dataset |
| errdens_4MiB_d50pct.utf16le.bin | `locate_scan` | known scan-consumer symptom (68 extra scan position(s) beyond EOF, e.g. [2097153, 2097154, 2097155], on a 2097152-code-unit input; oracle and linear agree, no real position missing, errorCount correct); the position output is incorrect, so only locate_scan is excluded for this dataset |

## Charts

- regenerate with `python3 benchmarks/plot_utf16_pipeline_benchmark.py --input results/reproduction.partial/benchmark.csv`

## Limitations and threats to validity

- Whole-process timing includes start-up and, for Parabix, loading the compiled pipeline. At 4 KiB that cost is most of the measurement; the adjusted column exists for exactly this reason and is still an estimate.
- One machine, one run, shared with other processes. No CPU pinning, no cache control, no frequency pinning on this platform.
- Operation order is rotated but the machine's thermal state still drifts over a long run.
- simdutf and Parabix are different tools solving overlapping but non-identical problems; the ratios above are directional only.
- The corpus is synthetic: uniformly spread malformed units, not the clustering of real-world damaged data.
- Densities are exact by construction, but each density is one sample of one generated stream, not an average over many streams.

