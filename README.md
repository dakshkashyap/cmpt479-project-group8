# CMPT 479 — UTF-16LE Validation with Parabix

## Project objective

Build and evaluate a UTF-16LE well-formedness validator on top of the
[Parabix](https://cs-git-research.cs.sfu.ca/cameron/parabix-devel.git) framework:

- A **scalar UTF-16LE oracle** — a simple, serial, two-bytes-at-a-time validator
  used as the ground-truth reference for correctness.
- A **portable SIMD implementation** using Parabix / IDISA operations instead of
  hard-coded architecture-specific intrinsics.
- **Malformed surrogate-pair counting**: the tool reports the number of
  ill-formed UTF-16 code units (unpaired high/low surrogates, incomplete final
  unit).
- A preliminary **single-thread vs. multi-thread** benchmark using Parabix's
  `--thread-num` control.
- Planned comparison against the **Clausecker–Lemire** UTF-16 validation approach.

## Current status

Only verified results are listed here:

- Scalar implementation works.
- SIMD implementation works.
- All basic correctness tests pass (scalar and `--simd` agree with expected counts).
- Randomized tests (against an independent Python reference) pass.
- Boundary tests pass (full SIMD block + scalar tail, exact-block-sized input,
  surrogate pairs and malformed sequences crossing a SIMD block boundary, odd
  trailing byte after a large input).
- Forced segment-size tests (`-segment-size=1,13,64`) pass.
- A reproducible preliminary scalar/SIMD/thread-count benchmark is available.
- The full performance evaluation and Clausecker–Lemire comparison are still pending.

## Repository layout

```
patches/    Milestone patches applied on top of the pinned Parabix revision
scripts/    setup_parabix.sh (build) and test_utf16validate.sh (tests)
docs/       Project plan and design/reference notes
results/    Benchmark output (generated; large artifacts are git-ignored)
benchmarks/ Benchmark drivers / configurations
src/        Project-local sources (kept out of the Parabix tree)
tests/      Project-local test material
.deps/      Local Parabix checkout, created by setup (git-ignored, never committed)
```

## Prerequisites

Install these yourself (the setup script does **not** install anything):

- **Git**
- **CMake** (3.x)
- A **C++ compiler** — the pinned Parabix revision is known-good with the
  **LLVM 16** Clang toolchain.
- **Python 3** (used by the test suite's reference validator)
- **LLVM 16**
- **Boost** libraries (`filesystem`, `iostreams`, `regex`)

macOS / Homebrew notes (nothing is installed automatically):

```
brew install llvm@16 boost cmake
```

`setup_parabix.sh` auto-detects LLVM 16 via `brew --prefix llvm@16`. On Linux it
looks for `llvm-config-16`. You can always override detection:

```
LLVM_DIR=/path/to/llvm/lib/cmake/llvm   ./scripts/setup_parabix.sh
LLVM_CONFIG=/path/to/llvm-config         ./scripts/setup_parabix.sh
```

## Quick setup

```
git clone https://github.com/dakshkashyap/cmpt479-project-group8.git
cd cmpt479-project-group8
./scripts/setup_parabix.sh
./scripts/test_utf16validate.sh
```

`setup_parabix.sh` automatically:

1. clones Parabix into `.deps/parabix` (override with `PARABIX_DIR`),
2. checks out the required Parabix revision,
3. applies `patches/utf16-simd-milestone.patch`,
4. configures a Release build, and
5. builds the `utf16validate` tool.

Teammates therefore do **not** clone or patch Parabix manually. The step is
idempotent: re-running it reuses the checkout, skips an already-applied patch,
and stops (rather than resetting) if it finds unexpected local modifications.

## Manual usage

```
.deps/parabix/build/bin/utf16validate file.bin           # scalar validator
.deps/parabix/build/bin/utf16validate --simd file.bin     # SIMD validator
```

Each prints, per input file:

```
file.bin: errorCount = <number of ill-formed UTF-16 code units>
```

## Thread testing

Parabix's threading is controlled per run (no measured speedups are claimed yet):

```
.deps/parabix/build/bin/utf16validate --simd --thread-num=1 file.bin
.deps/parabix/build/bin/utf16validate --simd --thread-num=3 file.bin
```

## Preliminary benchmarking

A reproducible preliminary benchmark compares the **scalar** validator, the
**SIMD** validator (plain `--simd`, i.e. Parabix default threading), and the SIMD
validator at several explicit thread counts (`--simd --thread-num=N`). The
Clausecker–Lemire comparison is **not** included yet; it is planned for a later
project update.

The workflow runs the correctness suite first, then generates deterministic valid
UTF-16LE datasets, then benchmarks each configuration:

- Dataset sizes: 1, 8, 32, and 64 MiB (all valid input; every run must report
  `errorCount = 0`).
- Each configuration does a few warmup runs followed by measured repetitions
  (defaults: 2 warmups, 7 repetitions). Warmups populate Parabix's on-disk
  object cache so measured runs load the compiled pipeline rather than
  recompiling it.
- Wall-clock time is measured per run; **throughput (MiB/s)** is computed from the
  **median** time, and **speedup** is the scalar median time divided by the
  configuration's median time.

Run the full benchmark:

```
./scripts/benchmark_utf16validate.sh
```

Run a shorter development benchmark:

```
BENCH_SIZES_MB=1,8 \
BENCH_WARMUPS=1 \
BENCH_REPETITIONS=3 \
./scripts/benchmark_utf16validate.sh
```

Outputs:

- Raw per-configuration data: `results/utf16_benchmark.csv` (git-ignored)
- Human-readable summary: `results/utf16_benchmark_summary.md`

Results are **machine-specific and preliminary**; no speedup is claimed here.
Numbers should be read from a generated summary on the machine that produced them.

## Reproducibility details

- **Parabix remote:** `https://cs-git-research.cs.sfu.ca/cameron/parabix-devel.git`
- **Parabix commit:** `f0369dd138e2e7a710566d5035f68b9cdc0bf305` (branch `master`)
- **LLVM version:** 16 (Clang 16 toolchain)
- **Milestone patch:** `patches/utf16-simd-milestone.patch`
  (root `CMakeLists.txt` registration + Boost-component compatibility, plus
  `tools/utf16validate/`)
- **Host scope:** little-endian hosts only (see below).

## Known limitations

- Currently validates **UTF-16LE** only.
- **Little-endian host** scope: the SIMD path reinterprets bytes as 16-bit lanes
  in host order and is guarded by a compile-time little-endian assertion.
- Reports an **error count**, not the exact source positions of malformed units.
  The SIMD mismatch bits are used only for counting.
- The **final remainder** (fewer than one SIMD block of code units) is handled by
  scalar processing.
- Preliminary benchmarking is available, but the final performance evaluation and
  Clausecker–Lemire comparison are still pending.
