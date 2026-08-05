# Portable Byte-Oriented UTF-16 Validation, Location, and Repair on Parabix

CMPT 479 — Group 8.

This project builds and evaluates a UTF-16 well-formedness validator on the
[Parabix](https://cs-git-research.cs.sfu.ca/cameron/parabix-devel.git) framework. The
implementation is written entirely in portable, byte-oriented (`fw=8`) Parabix/IDISA
operations rather than hand-written per-architecture intrinsics: every code unit is classified
on its **high byte**, following the Clausecker–Lemire strategy, so the same kernel source
compiles and runs unchanged on x86-64 (SSE4.2) and arm64 (NEON) and makes no assumption about
host endianness.

On top of validation the tool **locates** every ill-formed code unit — through a real Parabix
`StreamSet(1)` error-mark bitstream consumed by a subclass of Parabix's `TwoLevelScanKernel` —
and **repairs** input by replacing each ill-formed code unit with U+FFFD. Both UTF-16LE and
UTF-16BE are supported on every path, and all diagnostics are available as machine-readable
JSON.

Correctness is established by differential testing: a scalar oracle kernel, the SIMD kernel,
and an independent Python reference must all agree on every fixture, and a separate
hand-written Python oracle supplies expected positions and repaired bytes for generated cases.
Performance is evaluated on two architectures against `simdutf`, the specialized SIMD library,
using a documented methodology that separates per-byte kernel cost from fixed process overhead.

**This README is the primary technical guide to the artifact.** It is written so that the
project can be understood, built, tested, benchmarked, and reproduced without reading the
source first. Section [Documentation Roadmap](#documentation-roadmap) suggests a reading order
for the deeper design notes.

---

## Research Contributions

**1. A portable byte-oriented SIMD UTF-16 validator.** The validator classifies each UTF-16
code unit by examining only its high byte, using `fw=8` Parabix/IDISA operations. Because a
surrogate is identified purely by the value range of its high byte (`0xD8`–`0xDB` for a high
surrogate, `0xDC`–`0xDF` for a low), the entire well-formedness predicate reduces to byte
comparisons plus a one-position lookahead for the pairing rule. No architecture-specific
intrinsics appear anywhere in the kernel, and the earlier 16-bit-lane implementation — which
carried a compile-time little-endian `static_assert` — was removed entirely. *Why it matters:*
UTF-16 validators in production libraries are typically written once per instruction set. This
work shows the same portable source can be competitive with hand-tuned per-ISA code, which is
the central claim the framework makes and the one the project set out to test.

**2. A cross-architecture portability result.** Under a single documented methodology the
portable kernel was measured on CSIL x86-64 (simdutf selecting its SSE4.2/`westmere` kernel)
and on an Apple M1 (simdutf selecting its arm64/NEON kernel). On both hosts the Parabix kernel
exceeds the architecture's *native* simdutf SIMD path by a similar **~2.3–2.5× adjusted**
throughput (CSIL 2.42×/2.52×, Apple 2.33×/2.34×). *Why it matters:* the important quantity is
the **consistency of the ratio across two unrelated ISAs**, not the absolute MiB/s. Absolute
speeds are deliberately not compared across hosts — CSIL is a QEMU guest with 31 GiB, the M1 is
physical hardware with 8 GiB — so only within-host ratios support the portability claim.

**3. Exact error location on a real Parabix pipeline.** Rather than only counting errors,
`UTF16ErrorMarksKernel` emits a genuine Parabix `StreamSet(1)` — one bit per code unit, set iff
that code unit is ill-formed. `UTF16ErrorMarkScanKernel`, a real subclass of Parabix's
`TwoLevelScanKernel`, consumes that stream: it builds a high-level index in which one bit
summarises 64 low-level mask words, lets a fully clean 4096-code-unit region be skipped with a
single compare and branch, and uses `ctz` / reset-lowest-bit to walk only the dirty scanwords.
*Why it matters:* it demonstrates the framework's sparse-scanning machinery applies to UTF-16
diagnostics, and it turns the validator from a pass/fail gate into a tool that can report
exactly where a file is broken. The count-only path remains the default and is byte-identical,
so location cannot regress validation throughput.

**4. U+FFFD repair with a verified output contract.** `--repair` rewrites every ill-formed code
unit to U+FFFD, using the error-mark stream to locate them, and writes the repaired bytes to
stdout. The repair is specified precisely and then tested against that specification: output
length is always even, an odd trailing byte is discarded and replaced by one appended U+FFFD,
well-formed neighbours are copied through untouched, and repair is idempotent. *Why it
matters:* a repair that is merely "usually right" is not usable in a pipeline. Every case in
the campaign is checked byte-for-byte against an independent oracle, and where `simdutf` is
present the output is additionally diffed against `simdutf::to_well_formed_utf16le`/`be`.

**5. UTF-16BE support on every path, and a real defect it exposed.** `--be` selects big-endian
on validation, marking, both location paths, and repair; positions are code-unit indices, so
they are identical in LE and BE. Building the BE path surfaced a genuine bug: the marker rule
needs `isLow[k+1]`, read as the high byte of the next code unit at raw offset `2(k+1)+HB`. In
BE the high byte comes first (`HB = 0`), so for the final complete code unit that offset landed
**exactly on an odd trailing byte** — and a trailing `0xDC`–`0xDF` then masqueraded as a low
surrogate and paired with a real final high surrogate. The kernel now neutralises any lookahead
byte falling on an incomplete trailing byte. *Why it matters:* it is a concrete instance of the
class of bug that endianness-agnostic byte-oriented code is supposed to avoid, found by the
test campaign rather than by inspection, and it is now pinned by a suite that checks **all 256
trailing-byte values** after a final high surrogate.

**6. A documented negative result: the Pablo/transposition path.** A bitwise Pablo validation
path was genuinely built and run — a real S2P transposition feeding a `PabloKernel` — and then
deliberately **not** adopted. Parabix provides no bytes → 16-code-unit-indexed basis transpose;
the 8-bit basis is byte-indexed, so UTF-16 would need explicit high-byte-parity and
2-position pairing on top of it, reintroducing exactly the byte-lane bookkeeping the
byte-oriented kernel already handles. Since the surrogate predicate is a single high-byte
compare, transposition is a full extra pass that buys nothing. *Why it matters:* the result is
reported rather than buried, and it delimits where bitstream methods pay off — they need a
predicate complex enough to amortise transposition, which UTF-16 surrogate checking is not.

---

## Repository Layout

| Directory | Purpose |
| --- | --- |
| `patches/` | Milestone patches against the pinned Parabix revision — **the committed implementation** |
| `scripts/` | Setup, the regression suites, dataset generation, and the reproduction driver |
| `benchmarks/` | Benchmark drivers, standalone prototypes, shared prototype cores, plotting |
| `docs/` | Design notes, benchmark methodology, prototype write-ups, evaluation records |
| `results/` | Committed benchmark summaries, generated visualizations, reproduction evidence |
| `tests/corpus/` | The committed multilingual / emoji corpus, LE and BE, with a manifest |
| `external/` | Wrapper source and attribution for the `simdutf` baseline |
| `datasets/` | Controlled error-density corpus (generated, git-ignored) |
| `.deps/` | Parabix and `simdutf` checkouts (created by setup, git-ignored, never committed) |

### `patches/` — read this first

This is where the implementation lives, because the Parabix tree it modifies is git-ignored.

| File | Contents |
| --- | --- |
| `utf16-simd-milestone.patch` | The current implementation: the root `CMakeLists.txt` registration plus everything under `tools/utf16validate/`. **This is the file to read to review the code.** |
| `utf16-scalar-milestone.patch` | The earlier scalar-only milestone, retained so the progression from scalar oracle to SIMD kernel stays visible in the repository itself. |

### `scripts/` — setup, suites, generators, reproduction

| File | Role |
| --- | --- |
| `setup_parabix.sh` | Clone, pin, patch, configure and build the validator |
| `setup_clausecker_lemire.sh` | Fetch and build the pinned `simdutf` baseline |
| `reproduce_research.sh` | The five-stage one-command reproduction driver |
| `utf16_oracle.py` | The standalone Python oracle: counts, positions, repaired bytes |
| `test_*.sh` / `test_*.py` | The ten regression suites (see [Testing](#testing)) |
| `generate_multilingual_corpus.py` | Regenerates and verifies `tests/corpus/` |
| `generate_error_density_datasets.{sh,py}` | Builds the exact-count size × density corpus |
| `benchmark_utf16validate.sh` | Wrapper for the validation-throughput harness |
| `run_*_prototype.sh` | Drivers for the three standalone scan prototypes |

*Read first:* `utf16_oracle.py`, because every other suite is ultimately anchored to it.

### `benchmarks/` — drivers, prototypes, plotting

| File | Role |
| --- | --- |
| `benchmark_utf16_pipeline.py` / `.sh` | Per-path campaign over the error-density corpus (validate, mark, locate, scan, repair) |
| `run_utf16_benchmark.py` | Validation-throughput harness: scalar vs Parabix SIMD vs simdutf, across thread counts |
| `generate_utf16_benchmark.py` | Deterministic valid and malformed dataset generator |
| `summarize_utf16_benchmark.py` | Turns raw CSV into the committed Markdown summaries |
| `plot_final_benchmark_graphs.py` | Generates the seven summary visualizations in `results/final_graphs/` |
| `plot_utf16_pipeline_benchmark.py` | Charts from the raw pipeline CSV |
| `analyze_thread_scaling.py` | Thread-scaling analysis helper |
| `llmask_generation.h`, `maskhl_aggregation.h` | Shared prototype cores, included verbatim by the prototypes so all three measure the same code |
| `prototype_*.cpp` | Three standalone prototypes: mask generation, high-level aggregation, position scan |
| `llmask_reference.py` | Per-code-unit Python reference the prototypes are diffed against |

*Read first:* `benchmark_utf16_pipeline.py`, since its correctness gate is what makes the
timing numbers trustworthy.

### `results/` — measured evidence

Raw `.csv`/`.json` are git-ignored; the Markdown summaries and the generated visualizations are
committed so verified evidence survives a fresh clone. Detailed in [Results](#results).

*Read first:* `results/reproduction/reproduction_report.md`, which is the single artifact that
states overall PASS/FAIL for the whole project.

### `docs/` — design and methodology notes

Fourteen notes covering the kernel design, each prototype, the benchmark methodology, the
cross-architecture runbook, and the Pablo negative result. Each is summarised in
[References](#references), and a suggested order is in
[Documentation Roadmap](#documentation-roadmap).

*Read first:* `docs/benchmark_methodology.md` before looking at any number, and
`docs/SIMD_BYTE_ORIENTED_VALIDATOR.md` before reading the patch.

---

## Building

### Dependencies

Nothing is installed automatically by any script in this repository.

| Dependency | Requirement | Why |
| --- | --- | --- |
| **LLVM / Clang** | **16** | Parabix JIT-compiles its pipelines through LLVM and links against LLVM libraries. The pinned Parabix revision is known-good with the LLVM 16 API; adjacent major versions change the APIs Parabix uses and will fail to configure or compile. This is a hard pin, not a preference. |
| **Boost** | `filesystem`, `iostreams`, `regex` | Used by the Parabix driver and tooling |
| **CMake** | 3.x | Parabix build system |
| **C++ compiler** | Clang 16 toolchain | Must match the LLVM the build links against |
| **Python 3** | 3.6+ | The reference oracle, dataset generation, suite drivers |
| **Git** | any | Cloning and patching Parabix |
| **matplotlib** | optional | Only for generating benchmark visualizations; its absence is reported as skipped, never installed |

```bash
brew install llvm@16 boost cmake        # macOS / Homebrew
```

### Build

```bash
git clone https://github.com/dakshkashyap/cmpt479-project-group8.git
cd cmpt479-project-group8
./scripts/setup_parabix.sh
```

### What `setup_parabix.sh` does internally

1. **Clones Parabix** into `.deps/parabix` (override the location with `PARABIX_DIR`). The
   directory is git-ignored and is never committed — it is a ~370 MB nested git repository that
   would bloat this repo and obscure authorship.
2. **Checks out the pinned revision** `f0369dd138e2e7a710566d5035f68b9cdc0bf305` on `master`,
   so the patch always applies against a known tree.
3. **Applies `patches/utf16-simd-milestone.patch`**, which registers the tool in the root
   `CMakeLists.txt` (including a Boost-component compatibility fix) and adds
   `tools/utf16validate/`.
4. **Configures a Release build**, locating LLVM 16 automatically: via `brew --prefix llvm@16`
   on macOS, via `llvm-config-16` on Linux.
5. **Builds the `utf16validate` target.**

The script is **idempotent**: re-running reuses the existing checkout, detects and skips an
already-applied patch, and **stops rather than resetting** if it finds unexpected local
modifications, so in-progress work is never silently discarded.

### Overriding LLVM detection

```bash
LLVM_DIR=/path/to/llvm/lib/cmake/llvm  ./scripts/setup_parabix.sh
LLVM_CONFIG=/path/to/llvm-config       ./scripts/setup_parabix.sh
```

### Where the binary lands

```
.deps/parabix/build/bin/utf16validate
```

Referred to below simply as `utf16validate`. Verify the build:

```bash
./scripts/test_utf16validate.sh          # expect: 67 passed, 0 failed
```

### Rebuilding after a kernel change

```bash
cmake --build .deps/parabix/build --target utf16validate -j"$(nproc)"
```

### Common failure cases

| Symptom | Cause and fix |
| --- | --- |
| CMake cannot find LLVM, or configure fails on LLVM API errors | Wrong LLVM major version. Confirm 16 is installed and pass `LLVM_DIR` / `LLVM_CONFIG` explicitly. |
| Setup reports "unexpected local modifications" and stops | `.deps/parabix` still has an older patch applied, or was hand-edited. This is protective, not an error. Re-apply cleanly: `rm -rf .deps/parabix && ./scripts/setup_parabix.sh` |
| Boost components not found | Install the `filesystem`, `iostreams` and `regex` components; the patch includes a Boost-component compatibility fix for the pinned revision, but the libraries must still be present. |
| `utf16validate not found at ...` from a script | The build has not run or did not complete. Run `./scripts/setup_parabix.sh` first. |
| Benchmark or repair suite reports simdutf as skipped | `.deps/simdutf` is absent or no C++ compiler is on `PATH`. Run `./scripts/setup_clausecker_lemire.sh`. Nothing is downloaded implicitly; the rest of the suite still runs. |
| A suite fails only at a forced `-segment-size` | A genuine cross-segment carry bug. These segment sizes exist precisely to catch it; do not treat it as flakiness. |

---

## Running

All commands below use `utf16validate` as shorthand for
`.deps/parabix/build/bin/utf16validate`. UTF-16LE is the default encoding throughout; add
`--be` to any of them for UTF-16BE.

### Validation (scalar)

**Purpose.** The ground-truth reference implementation: a simple, serial, two-bytes-at-a-time
validator. **When to use it:** as the oracle any other path is compared against, and when you
want the least machinery between the bytes and the answer.

```bash
utf16validate file.bin
```

```
file.bin: errorCount = <number of ill-formed UTF-16 code units>
```

### Validation (SIMD)

**Purpose.** The portable byte-oriented Parabix kernel — the subject of the research.
**Differs from scalar:** same answer, computed through the Parabix pipeline with `fw=8` IDISA
operations. **When to use it:** for every performance measurement, and as the primary
implementation under test.

```bash
utf16validate --simd file.bin
```

Output format is identical to the scalar path; the two must always agree, and every suite
enforces that.

### Error marks

**Purpose.** Builds the real Parabix `StreamSet(1)` — one bit per code unit, set iff that unit
is ill-formed. **Differs from `--simd`:** it materialises a stream rather than only a count,
which is the prerequisite for both location and repair. It still reports the same
`errorCount`. **When to use it:** on its own to confirm the producer agrees with the counting
paths, and as the required input to `--print-positions` and `--scan-error-marks`.

```bash
utf16validate --emit-error-marks file.bin
```

Note that `mark_count` (marker bits) excludes an odd trailing byte, which has no code-unit
position but still contributes 1 to `errorCount`.

### Linear positions

**Purpose.** Prints the code-unit index of every ill-formed unit by walking the mark stream
directly. **Differs from the scan:** it visits every position — no skipping — which makes it
the *simple, trusted* position printer. **When to use it:** whenever you need positions you can
rely on, and as the reference the two-level scan is checked against.

```bash
utf16validate --emit-error-marks --print-positions file.bin
```

Positions are **code-unit indices**, so they are identical in UTF-16LE and UTF-16BE. An odd
trailing byte never appears in the list.

### Scan positions (two-level scan)

**Purpose.** The same positions, recovered through `UTF16ErrorMarkScanKernel` — the
`TwoLevelScanKernel` subclass that skips clean 4096-code-unit regions and scans only dirty
scanwords with `ctz` / reset-lowest-bit. **Differs from the linear printer:** it is the sparse,
research-relevant path; on mostly-clean input it does asymptotically less work. **When to use
it:** to exercise the scan machinery. See [Known Limitation](#known-limitation) before relying
on its output.

```bash
utf16validate --emit-error-marks --scan-error-marks -thread-num=1 file.bin
```

`-thread-num=1` is used here so the position stream is emitted in a single, deterministic
order.

### Repair

**Purpose.** Rewrites every ill-formed code unit to U+FFFD. Repaired bytes go to **stdout**;
the diagnostic count goes to **stderr**, so redirecting stdout gives a clean binary.

```bash
utf16validate --repair file.bin > repaired.bin
```

The output contract, enforced by the campaign in [Testing](#testing):

- Each ill-formed code unit is replaced **in place** with U+FFFD.
- An odd trailing byte is **discarded** and replaced by **one appended** U+FFFD, so an
  odd-length input becomes length + 1 and **output length is always even**.
- Well-formed neighbours are copied through byte-for-byte.
- `validate(repair(x)) == 0`, `repair(repair(x)) == repair(x)`, and `repair(x) == x` when `x`
  is already well-formed.
- U+FFFD is written as `FD FF` in LE and `FF FD` in BE.

### JSON diagnostics

**Purpose.** Machine-readable output for automation and benchmark tooling. **Differs from every
mode above:** human-readable output is byte-for-byte unchanged unless one of these flags is
given, and no validation, repair or scan behaviour changes.

```bash
utf16validate --json file.bin                                       # validation
utf16validate --json --simd file.bin                                # SIMD validation
utf16validate --json --emit-error-marks --print-positions -thread-num=1 file.bin
utf16validate --json --emit-error-marks --scan-error-marks -thread-num=1 file.bin
utf16validate --json --repair file.bin                              # repair report
utf16validate --json-pretty --be file.bin                           # indented, UTF-16BE
```

**Exactly one document, therefore exactly one input file.** A JSON run emits one complete
document and never concatenates several, because the result would not parse. Zero or several
files produce a single `status: "error"` document with code `json_requires_single_input` and a
non-zero exit. Multi-file human-readable behaviour is unchanged.

**Schema.** Keys are stable, counts are JSON numbers, flags are JSON booleans, lists are
arrays, and inapplicable keys are **omitted rather than null**.

| Group | Keys |
| --- | --- |
| Envelope (always) | `version`, `tool`, `command`, `timestamp`, `operation`, `status`, `encoding`, `warnings`, `metadata{implementation, big_endian, odd_trailing_byte}` |
| Success only | `file`, `size_bytes`, `code_units`, `error_count`, `validation{valid, error_count}`, `timing{elapsed_seconds}` |
| Operation-specific | `error_marks{mark_count}`, `positions[]`, `scan_positions[]`, `repair{performed, replacement_count, output_valid}` |
| Failure only | `error{code, message}` |

```json
{
  "version": 1, "tool": "utf16validate", "operation": "validate_simd", "status": "ok",
  "encoding": "UTF-16LE", "file": "bad.bin", "size_bytes": 8, "code_units": 4,
  "error_count": 2, "validation": { "valid": false, "error_count": 2 },
  "timing": { "elapsed_seconds": 0.000158375 }, "warnings": [],
  "metadata": { "implementation": "parabix_simd", "big_endian": false,
                "odd_trailing_byte": false }
}
```

**Error codes** — branch on `code`, never on `message`: `json_requires_single_input`,
`input_open_failed`, `capture_setup_failed`, `capture_read_failed`. A failure document **never
carries `validation` or `error_count`**, so a run that never happened cannot be mistaken for a
clean validation. `size_bytes` and `code_units` may be `null` in a failure document — the one
documented exception, meaning "genuinely unknown" rather than a misleading `0`.

**Option precedence.** When several operation flags are combined the pipeline runs one, and
`operation` always names the one that actually ran:
**repair > scan > linear positions > error marks > `--simd` > scalar.** This precedence is
tested, not incidental.

**Repair reports, it does not embed bytes.** `--json --repair` emits only the document, with
`performed`, `replacement_count` and `output_valid`, plus a warning saying the binary is not
included. Run `--repair` without `--json` to get the repaired bytes.

### UTF-16BE

**Purpose.** Big-endian input on every path. **Differs from LE:** only in which byte of each
pair is the high byte — `2k` for BE, `2k+1` for LE. Positions are code-unit indices and are
therefore identical between encodings, and the scan consumer is endian-agnostic.

```bash
utf16validate --be --simd file.bin
utf16validate --be --emit-error-marks --print-positions file.bin
utf16validate --be --emit-error-marks --scan-error-marks -thread-num=1 file.bin
utf16validate --be --repair file.bin > repaired.be.bin
```

### Thread control

Parabix's threading is set per run. Single-thread and default-threaded runs must always agree;
suites check this.

```bash
utf16validate --simd --thread-num=1 file.bin
utf16validate --simd --thread-num=3 file.bin
utf16validate --simd              file.bin   # Parabix default (3 threads)
```

---

## Testing

Every suite is a **differential** test. The correctness bar for any change is that the scalar
kernel, the SIMD kernel, and an independent Python reference all agree — a bug would have to
appear identically in all of them to pass. Expectations are never taken from the implementation
under test.

### The oracle, and why there are two references

[`scripts/utf16_oracle.py`](scripts/utf16_oracle.py) is a standalone Python oracle: given raw
bytes and an endianness it computes the code units, the ill-formed positions, the
odd-trailing-byte flag, `errorCount`, and the U+FFFD-repaired bytes. It shares no code with
`utf16validate.cpp`, with the scalar validator, or with `benchmarks/llmask_reference.py`. It
decodes **left to right**, letting a high surrogate consume the low surrogate after it, and
calls ill-formed any surrogate no pair could consume. `llmask_reference.py` reaches the same
answer through a **per-code-unit predicate** instead — a structurally different method — so the
two agreeing is evidence rather than tautology.

```bash
python3 scripts/utf16_oracle.py --count FILE [--be]
python3 scripts/utf16_oracle.py --positions FILE [--be]
python3 scripts/utf16_oracle.py --repair FILE [--be] > repaired.bin
python3 scripts/utf16_oracle.py --self-test        # 17 hand-worked vectors x LE/BE
```

**Diagnostic conventions**, identical everywhere in the project: positions are code-unit
indices, so they are the same in LE and BE; an odd trailing byte adds 1 to `errorCount` and has
**no** position; repair replaces each ill-formed unit in place, and discards an odd trailing
byte in favour of one appended U+FFFD.

### Suite summary

| Suite | Issue | Runtime | Expected |
| --- | --- | --- | --- |
| `test_utf16validate.sh` | base | seconds | `67 passed, 0 failed` |
| `test_errormarks.sh` | #32 | seconds | `49/49` |
| `test_scan_consumer.sh` | #39 | seconds | `54/54` |
| `test_utf16be.sh` | #33 | seconds | `35/35` |
| `test_multilingual_corpus.sh` | #40 | seconds | `48/48`, zero errors |
| `test_utf16_malformed_boundaries.sh` | #41 | ~2 min | all pass |
| `test_utf16_oracle_fuzz.sh` | #42 | ~1.5 min | `2360 passed, 16 known-xfail, 0 failed` |
| `test_utf16_repair.sh` | #40 | seconds | all pass |
| `test_utf16_repair_comprehensive.sh` | #43 | ~1.5 min | every section passes |
| `test_utf16_json_output.sh` | #46 | seconds | all pass |

### `test_utf16validate.sh` — the scalar/SIMD count gate

**Validates:** that scalar, `--simd`, and the Python reference report the same `errorCount` on
every fixture, in UTF-16LE. **Relation to the oracle:** the Python reference walks raw UTF-16LE
bytes directly, so it also covers blobs no string encoder would produce (lone surrogates, odd
trailing bytes). **Coverage:** fixed valid/malformed cases; valid multilingual text (ASCII,
accented European, Hindi, Punjabi, CJK, emoji as real non-BMP pairs, and a mixed sample);
malformed sequences (unpaired high/low, reversed pair, odd trailing byte, consecutive malformed
units, malformed data embedded in multilingual text); boundaries at 64/128/256/512 units; and
forced `-segment-size=1,13,64` to stress the cross-segment carry, plus deterministic randomized
inputs. **Supports:** the primary correctness claim, and the pass counts shown in the
correctness-evidence visualization. **Fixtures** are generated into a `mktemp` directory and
removed on exit.

### `test_errormarks.sh` — the mark producer (issue #32)

**Validates:** that `--emit-error-marks` produces a `StreamSet(1)` whose set bits are exactly
the ill-formed code units, and that the count it reports still matches the counting paths.
Checked at four segment sizes. **Differs from the count gate:** it inspects the *stream*, not
just the number. **Supports:** the error-location contribution.

### `test_scan_consumer.sh` — the two-level scan (issue #39)

**Validates:** `--scan-error-marks` — that the `TwoLevelScanKernel` subclass reports the same
positions as the linear printer, including that its **stride skipping** actually engages on
clean regions. **Edge cases:** clean regions adjacent to dirty ones, errors landing on region
boundaries. **Supports:** the sparse-scanning contribution and the scan row of the pipeline
ablation visualization.

### `test_utf16be.sh` — big-endian support (issue #33)

**Validates:** every `--be` path. Each fixture is generated as big-endian bytes and checked
five ways at four segment sizes. **Key edge case:** cross-endian identity — BE bytes must be
exactly the byte swap of LE, and both must report the same count *and the same positions*.
This suite is where the phantom-lookahead trailing-byte class of bug is caught.

### `test_multilingual_corpus.sh` — the committed corpus (issue #40)

**Validates:** every fixture in `tests/corpus/` in both encodings, all reporting zero errors,
plus the manifest. `corpus_manifest.json` records per dataset the byte size, code-unit count,
code-point count, surrogate-pair count, source category, seed, SHA-256 and
`expected_error_count: 0` — all checked against the bytes on disk. Reproducibility is also
checked: two generator runs must be byte-identical to each other **and** to the committed
fixtures. **Scope note:** this tests **surrogate structure, not grapheme or emoji semantics**.
A ZWJ family sequence matters here because it alternates surrogate pairs with BMP joiners, not
because it should render as one glyph.

```bash
python3 scripts/generate_multilingual_corpus.py           # regenerate
python3 scripts/generate_multilingual_corpus.py --check   # verify, writing nothing
```

### `test_utf16_malformed_boundaries.sh` — the cross-product (issue #41)

**Validates:** where the suites above each gate one path, this one is the **cross-product**.
Every fixture is built once as a code-unit sequence, then checked in **both encodings** across
**all four validation paths**, at four segment sizes. A disagreement between any two of them
fails here even when each individual suite still passes.

**Malformed categories:** lone surrogates (as the whole file, embedded in BMP text, at the top
of each range U+DBFF/U+DFFF, first unit, last unit; two, three and four consecutive highs and
lows; high-then-BMP; BMP-then-low; reversed low–high, also at offset 0); mixed valid and
invalid (a valid pair beside a lone high or low on either side; the `high, high, low` trap
where only the *first* high is ill-formed; its `high, low, low` mirror; `low high low high`,
where the middle two form a *valid pair* so it is 2 errors not 4; eight alternating runs; four
ill-formed units in a row; six consecutive lone highs; malformed data at the beginning, middle,
end, and all three at once); byte-length failures (a one-byte file; a one-byte file whose byte
looks like a surrogate lead `0xD8`; an odd trailing byte after BMP data, after a valid pair,
after a lone high, after a lone low, after a reversed pair; and 8192 valid code units plus one
stray byte).

**Boundary offsets.** A code unit is 2 bytes, so these code-unit offsets bracket the SIMD block
boundaries in bytes:

| Code-unit offsets | Brackets byte offset |
| --- | --- |
| 7, 8, 9 | 16 |
| 15, 16, 17 | 32 |
| 31, 32, 33 | 64 |
| 63, 64, 65 | 128 (also the 64-code-unit mask group) |
| 127, 128, 129 | 256 |
| 255, 256, 257 | 512 |
| 511, 512, 513 | 1024 |

At each offset the suite places a valid pair straddling the boundary, a valid pair ending
exactly at it, a valid pair starting exactly at it, a lone high on the low side, a lone low on
the boundary, and two errors on opposite sides. It also covers beginning and end of input
(one-pair file, one-unit file, empty file, a pair at the very end, a dangling high as the last
unit, a lone low as the first unit) and, at forced `-segment-size=1`, `13` and `64`, a
**surrogate pair split across a segment boundary**, a **malformed high at the final unit of a
segment**, and a **low at the first unit of a segment with no matching high**.

**Odd trailing bytes.** The suite asserts on every path and at every segment size that
`errorCount` = ill-formed code units **+ 1** while the position list contains code-unit indices
**only**. A 1-byte file therefore reports `errorCount = 1` with an **empty** position list.

**Expectations come from three independent layers** that must agree *before* any kernel is
consulted: hand-declared positions per fixture, a test-side oracle written from the definition
of well-formedness, and `llmask_reference.py` run over the raw bytes of both encodings. A
representative subset is finally run three times per encoding to confirm determinism.

### `test_utf16_oracle_fuzz.sh` — generated cases (issue #42)

**Validates:** hundreds of cases nobody wrote down, with expectations taken from the oracle.
**Complementary to the boundary suite**, which is hand-curated; these use different reference
implementations on purpose.

```bash
./scripts/test_utf16_oracle_fuzz.sh                  # 200 cases (~1.5 min)
./scripts/test_utf16_oracle_fuzz.sh --quick          # 40 smaller cases (~20 s)
./scripts/test_utf16_oracle_fuzz.sh --seed 1234 --cases 400 --max-units 2000
```

**Eighteen categories**, cycled so every run covers all of them: valid BMP; valid supplementary;
mixed valid; lone highs; lone lows; reversed pairs; consecutive highs; consecutive lows; strict
alternation; odd byte lengths; empty; one-byte; tiny (1–4 units); medium random; large
(4096–12288 units); malformed at beginning/middle/end; malformed on boundary offsets
15/16/17, 31/32/33, 63/64/65, 127/128/129; and cases on forced segment boundaries at
`-segment-size=1`, `13`, `64`.

**Properties checked** on every case, in LE and BE:

| | Property |
| --- | --- |
| P1 | `oracle == scalar == --simd == --emit-error-marks == scan` counts |
| P2 | `oracle == --print-positions == --scan-error-marks` positions |
| P3 | oracle repaired bytes `== --repair` bytes, byte for byte |
| P4 | `validate(repair(x)) == 0` |
| P5 | `repair(repair(x)) == repair(x)` |
| P6 | `x` well-formed ⇒ `repair(x) == x` |
| P7 | LE and BE give the same count and positions; BE bytes are the byte swap of LE |
| P8 | the same seed regenerates byte-identical cases |
| P9 | repeated runs of the same path on the same file agree |

**Determinism.** Each case comes from `random.Random("utf16-fuzz|<seed>|<index>|<category>")`,
so it depends only on the seed, its own index and its category — never on how many cases ran
before it. `--only-case N` reproduces exactly one case anywhere.

**Failure output** prints the seed, case number, category, encoding, raw bytes in hex (elided
in the middle for large cases), decoded code units, the oracle's count and positions, what each
path returned, the first diverging position, and a ready-to-paste rerun command.

**KNOWN-XFAIL.** The 16 known-xfail entries are the scan-consumer defect described in
[Known Limitation](#known-limitation). They are printed and counted separately, never
suppressed, and reproducible as hard failures:

```bash
./scripts/test_utf16_oracle_fuzz.sh --strict-known-defects   # exits non-zero
```

The classification predicate is **deliberately narrow** — narrower than the defect itself. A
position mismatch is accepted as KNOWN-XFAIL only when *all* hold: the property is P2;
`--scan-error-marks` is the only disagreeing path; oracle and `--print-positions` agree exactly;
every count agrees; the input has even byte length; the input is a positive exact multiple of
4096 code units; no real position is missing; and every unexpected position lies **outside** the
valid range. Anything else is an ordinary failure. When a mismatch is rejected the suite's
output names the first condition that ruled it out, so a new defect cannot be absorbed into this
bucket.

### `test_utf16_repair.sh` and `test_utf16_repair_comprehensive.sh` (issues #40, #43)

The first is the focused smoke/regression gate: a few dozen hand-written fixtures with exact
expected bytes plus a small simdutf cross-check. The second is the **campaign**.

```bash
./scripts/test_utf16_repair_comprehensive.sh              # default (~1.5 min)
./scripts/test_utf16_repair_comprehensive.sh --quick      # fast subset (~45 s)
./scripts/test_utf16_repair_comprehensive.sh --seed 1234 --cases 200 --max-units 2000
./scripts/test_utf16_repair_comprehensive.sh --section generated --only-case 57
./scripts/test_utf16_repair_comprehensive.sh --no-simdutf # skip the differential
```

**Hand-curated cases** have their expected bytes declared by hand and cross-checked against the
oracle **before the implementation is consulted** — valid: empty, one BMP unit, ASCII/BMP text,
a supplementary pair, multiple pairs, multilingual text, an emoji ZWJ sequence, a pair at
beginning/middle/end, U+10FFFF, and `D800 DFFF` (a *valid* pair, not two errors); malformed:
lone high, lone low, high-then-BMP, BMP-then-low, reversed low-high, `high high low` (only the
first high replaced), `high low low` (only the trailing low), two and four consecutive highs and
lows, alternating valid/malformed, errors at beginning/middle/end and all three at once,
multiple separated malformed regions, every surrogate ill-formed, U+DBFF and U+DFFF, and
malformed data inside multilingual text; odd-length: one-byte input, an odd byte after BMP / a
valid pair / a lone high / a lone low / a reversed pair / a 4096-unit valid stream, and odd byte
values **00, D8, DC, FD, FF**, each also after a lone high.

**Boundary and segment coverage.** Offset 0, EOF, and code-unit offsets 7/8/9, 15/16/17,
31/32/33, 63/64/65, 127/128/129, 255/256/257, 511/512/513, **4095/4096/4097, 8191/8192/8193**.
Every input up to 2048 code units is additionally repaired at `-segment-size=1`, `13` and `64`
and must produce **byte-identical** output.

**Properties.** P1 impl bytes == oracle bytes · P2 scalar `errorCount(repair(x))==0` · P3
`--simd` the same · P4 idempotence · P5 valid input unchanged · P6 even length preserved · P7
odd length becomes length+1 · P8 output length always even · P9 each U+FFFD is `FD FF` in LE and
`FF FD` in BE · P10 well-formed neighbours copied through · P11 identical across segment sizes ·
P12 repeated runs identical · P13 LE and BE repairs decode to the same code units · P14
replacement count equals the original `errorCount` under the odd-byte convention · P15 no
unpaired surrogate survives.

**Large-file stress** (correctness and stability, *not* a benchmark — no throughput is claimed):
deterministic 1 MiB valid-BMP, mixed-valid, sparse-malformed and dense-malformed streams, a
stream with malformed units at the first, middle and final positions, and a 1 MiB odd-length
stream.

**simdutf differential.** Where `.deps/simdutf/singleheader` is present, `--repair` is compared
**byte for byte** against `simdutf::to_well_formed_utf16le`/`...be` in both encodings over
valid, sparse, dense, boundary and large cases. **Odd-length inputs are not compared:**
simdutf's API is `char16_t`-based and has no notion of an incomplete trailing byte, so this
project's "drop the byte and append one U+FFFD" policy has no simdutf equivalent. Those cases
are reported as skipped and still checked against the Python oracle.

**This suite has no xfail mechanism — any failure is a real failure.** It is also the suite that
found the UTF-16BE phantom-lookahead defect described in
[Research Contributions](#research-contributions), and it now checks **all 256 trailing-byte
values** after a final high surrogate, for U+D800, U+DA00 and U+DBFF, in both encodings, at
segment sizes default/1/13/64.

### `test_utf16_json_output.sh` — machine-readable diagnostics (issue #46)

**Validates:** that `--json` / `--json-pretty` emit one parseable document per input; that the
schema is consistent and keys are stable; that counts stay numeric and flags boolean; that
validation, position, repair and error-mark fields agree with `scripts/utf16_oracle.py`; that
arrays are **empty rather than missing** when there is nothing to report; and that the operation
precedence is respected. Covers every operation in LE and BE.

### Additional confidence checks

```bash
# single-thread and default threading must agree
utf16validate --simd --thread-num=1 file.bin
utf16validate --simd              file.bin

# no host-endianness guard should remain in the SIMD path (expect no output)
grep -n "static_assert\|__BYTE_ORDER__" .deps/parabix/tools/utf16validate/utf16validate.cpp

# the deliverable patch still applies cleanly onto the pinned revision
cd .deps/parabix && git stash -q --include-untracked \
  && git apply --check ../../patches/utf16-simd-milestone.patch && echo OK \
  ; git stash pop -q
```

---

## Benchmarking

**Read [`docs/benchmark_methodology.md`](docs/benchmark_methodology.md) before collecting or
quoting any number.** It defines the benchmark modes, the fair comparison groups — in
particular that the simdutf baseline is single-threaded and may therefore only be compared
against Parabix `--thread-num=1`, on valid input — the startup/JIT distortion that makes
small-input throughput misleading, and the CSV/summary schema the harness must produce.

### The two drivers

| Driver | Question it answers |
| --- | --- |
| `benchmarks/run_utf16_benchmark.py` | Validation throughput: scalar vs Parabix SIMD vs simdutf, across thread counts and sizes |
| `benchmarks/benchmark_utf16_pipeline.py` | Cost of **each processing path independently** — validate, mark, locate linearly, locate by scan, repair — at a *known, exact* malformed density |

They measure different axes and are not substitutes.

### Dataset generation

Two generators exist, for two different needs.

**1. `benchmarks/generate_utf16_benchmark.py` — throughput datasets.** Produces deterministic,
valid UTF-16LE inputs from built-in Unicode repertoires. Nothing is downloaded and no corpus is
stored in the repository. Non-BMP characters are encoded as explicit surrogate pairs and a file
never ends in a half-written code unit, so every dataset validates with `errorCount = 0`.

| Dataset | Composition |
| --- | --- |
| `default` | Synthetic mix (ordinary BMP, BMP above U+E000, surrogate pairs). **Used by the benchmark runner**; bytes are unchanged so existing results stay comparable. |
| `english_ascii_heavy` | ASCII words, capitals and digits; single-unit BMP |
| `european_accented` | Latin words with Latin-1 Supplement / Latin Extended-A accents |
| `south_asian` | Devanagari (Hindi) and Gurmukhi (Punjabi) |
| `cjk` | CJK Han ideographs with Japanese kana and Korean Hangul |
| `emoji_heavy` | Non-BMP emoji (every character is a surrogate pair) + ASCII |
| `mixed_multilingual` | A blend of all of the above |

```bash
python3 benchmarks/generate_utf16_benchmark.py --sizes-mb 1,8,32,64
python3 benchmarks/generate_utf16_benchmark.py --datasets cjk --sizes-mb 1
python3 benchmarks/generate_utf16_benchmark.py --datasets all --sizes-mb 1 --seed 479
```

Each `.bin` gets a `<file>.bin.json` sidecar recording dataset type, requested and actual size,
seed, encoding and composition. Data lands in `benchmarks/data/` and is git-ignored.

Adding `--error-patterns` corrupts any dataset at a controlled *rate*. Invalid sequences are
patched directly into the raw bytes (Python's codecs will not encode a lone surrogate), and the
**expected error count is recomputed from the final bytes** by an independent reference
validator inside the generator, so the metadata cannot disagree with what a validator reports.

| Error pattern | Injected construct |
| --- | --- |
| `unpaired_high` | lone high surrogates (1 error each) |
| `unpaired_low` | lone low surrogates (1 error each) |
| `reversed_pair` | a low surrogate followed by a high surrogate (2 errors each) |
| `odd_trailing_byte` | lone highs, plus the final byte removed so the file ends incomplete (+1 error) |
| `random_mix` | the three constructs above, randomly distributed |
| `clustered_mix` | the same mix, concentrated in a few contiguous runs |

The standard sweep is `0, 0.0001, 0.001, 0.01, 0.1, 1` percent of code units
(`--error-rates all`). **0% is the control:** the file is valid with expected count 0. Existing
surrogate pairs are never overwritten, so an injected error adds a fault rather than destroying
valid text. A *site* is one construct, so the site count is not the error count — a reversed
pair is one site but two errors.

```bash
python3 benchmarks/generate_utf16_benchmark.py --datasets mixed_multilingual \
    --error-patterns random_mix --error-rates 0.01 --sizes-mb 1
python3 benchmarks/generate_utf16_benchmark.py --datasets mixed_multilingual \
    --error-patterns all --error-rates all --sizes-mb 1
```

**2. `scripts/generate_error_density_datasets.py` — the controlled error-density corpus.** The
complementary tool: instead of injecting at an approximate rate and measuring what came out, it
produces a size × density matrix in which the malformed count is **exact and verified**, in both
encodings. This is the corpus the pipeline benchmark runs on.

```bash
./scripts/generate_error_density_datasets.sh              # the full matrix
./scripts/generate_error_density_datasets.sh --quick      # small sizes, reduced sweep
./scripts/generate_error_density_datasets.sh --sizes 64KiB,1MiB --densities 0,1,5
./scripts/generate_error_density_datasets.sh --encodings utf16le --overwrite
```

```
datasets/error_density/
    utf16le/errdens_<size>_d<density>pct.utf16le.bin
    utf16be/errdens_<size>_d<density>pct.utf16be.bin
    manifest.csv
```

- **Sizes:** 4 KiB, 16 KiB, 64 KiB, 256 KiB, 1 MiB, 4 MiB (`--quick` uses the three smallest).
- **Densities** (percent of code units, not bytes): 0, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 20, 50.
- **Rounding rule:** `target_errors = floor(code_units × density / 100 + 0.5)`, and the file
  contains *exactly* that many ill-formed code units. A small file at a small density can
  legitimately round to zero; the generator prints a note and the manifest records the actual
  count.
- **Content:** realistic UTF-16 drawn word-by-word from ASCII, accented Latin, Greek, Cyrillic,
  Hebrew, Arabic, Devanagari, Gurmukhi, Thai, CJK, kana and Hangul, plus supplementary-plane
  characters and emoji — not a repeating filler pattern.
- **Malformed insertion:** six constructs (lone high, lone low, reversed pair, high-high,
  low-low, broken mixed run) chosen so their error counts sum *exactly* to the target. Each is
  written into its own slot at a deterministic offset, spread approximately uniformly, separated
  from neighbours by at least one guard code unit. Where a construct would land on a surrogate
  pair, **both halves are replaced together**, so no unintended lone surrogate is created.
- **Determinism:** each dataset is built from
  `random.Random("utf16-density|<seed>|<size>|<density>")` (default seed 479), so it depends only
  on the seed, its own size and its own density — never on what else ran or in what order. The
  LE and BE files of a pair hold the same code units, so each is the byte swap of the other.
- **Verification:** before a dataset is accepted its error count is checked three ways — scalar,
  `--simd`, and `utf16_oracle.py` — and all three must equal the target. Datasets are written to
  a temporary file and only moved into place once verified, so a failing dataset is never left
  behind.

### `manifest.csv`

One row per file: `filename`, `encoding`, `size_bytes`, `code_units`, `target_density`,
`actual_error_count`, `seed`. **The benchmark reads sizes, densities, code-unit counts and
`actual_error_count` from the manifest and never infers them from filenames**, and fails with an
explicit message if a row or its file is missing. A narrow run keeps the rows of datasets still
on disk rather than dropping them, and warns about dataset files the manifest does not describe.
Without `--overwrite`, existing datasets are kept — but still **re-verified**, so a stale or
hand-edited file is caught rather than silently reused.

### Running the pipeline benchmark

```bash
./scripts/generate_error_density_datasets.sh --quick             # prerequisite datasets
./benchmarks/benchmark_utf16_pipeline.sh --quick --overwrite     # fast subset
./benchmarks/benchmark_utf16_pipeline.sh --quick --estimate-only # gate + estimate only
./benchmarks/benchmark_utf16_pipeline.sh --overwrite             # full matrix (slow)
./benchmarks/benchmark_utf16_pipeline.sh --self-test-gate        # gate predicate only, no timing
```

**Operations measured**, each timed on its own and never combined into one measurement:
`validate_scalar`, `validate_simd`, `emit_error_marks`, `locate_linear` (`--print-positions`),
`locate_scan` (`--scan-error-marks`), `repair`, plus `simdutf_validate` and `simdutf_repair`
where available. Position-printing and repair paths still write all of their output; stdout is
redirected to the null sink so terminal I/O is never timed while the implementation still does
all of its work.

**Options:** `--dataset-dir`, `--manifest`, `--bin`, `--output`, `--summary`, `--sizes`,
`--densities`, `--encodings`, `--operations`, `--iterations`, `--warmups`, `--seed`, `--quick`,
`--no-simdutf`, `--cpu-affinity`, `--timeout`, `--overwrite`, `--estimate-only`. Quick mode uses
a documented subset (3 sizes × 2 densities × both encodings, fewer iterations); the full matrix
is every row of the manifest.

### Timing methodology

- **Whole-process wall clock** (`time.perf_counter_ns`).
- **Warmups** run before measurement and are discarded. They populate Parabix's on-disk object
  cache so measured runs *load* the compiled pipeline rather than recompiling it — without this,
  the first run measures compilation.
- **Iterations** are the measured runs; **every raw iteration is kept in the CSV**, never only a
  summary statistic.
- **Median** is the headline statistic, with min, max, mean and standard deviation recorded
  alongside. The median is used because process-level measurements have occasional large
  outliers from scheduling, which would distort a mean.
- **Rotation by seed:** operation order is rotated per dataset, deterministically from `--seed`,
  so that systematic order effects and thermal drift are spread across operations instead of
  always penalising whichever runs last.
- **Throughput** is computed from **input bytes on disk**, never code units.
- **Overhead adjustment:** each implementation's fixed per-process cost (process start plus, for
  Parabix, loading the compiled pipeline) is measured separately on a tiny input.
  `adjusted_median_mib_s` subtracts it. The adjusted value is reported **only** where the
  measurement is at least 3× that fixed cost, because below that the correction is dominated by
  its own noise.
- Dataset generation and helper compilation happen before any measurement and are **never
  timed**. A timeout applies to every run; a non-zero exit marks the row failed rather than
  being silently dropped.

### The correctness gate

Before timing, all validation, position, and repair paths must agree with the manifest and the
independent oracle. Any mismatch aborts the run. The only tolerated case is the documented
two-level scan symptom where extra positions appear beyond end-of-input while counts and all
real positions remain correct; only `locate_scan` timing is excluded for that dataset. This
defect is classified by symptom rather than by input size because controlled-density testing
showed the trigger depends on both error distribution and the host/runtime environment. See
[Known Limitation](#known-limitation).

### simdutf in the benchmark

simdutf is used from the existing `.deps/simdutf` checkout and compiled **once, before any
measured run**. Nothing is downloaded. If it is missing, or no C++ compiler is present, those
rows are marked skipped with the reason and the Parabix paths are still benchmarked. simdutf
validation returns a boolean where Parabix returns a count, and simdutf repair is compared only
on even-length inputs.

### Validation-throughput harness

```bash
BENCH_SMOKE=1 ./scripts/benchmark_utf16validate.sh              # fast harness check, temp dir
BENCH_INCLUDE_SIMDUTF=1 ./scripts/benchmark_utf16validate.sh    # include the baseline

BENCH_DATASETS=all BENCH_SIZES_MB=128,256,512 BENCH_INCLUDE_SIMDUTF=1 \
    ./scripts/benchmark_utf16validate.sh                        # the final matrix

python3 benchmarks/run_utf16_benchmark.py \
    --datasets mixed_multilingual --sizes-mb 1 \
    --warmups 1 --repetitions 2 --include-simdutf --output /tmp/smoke.csv
```

Defaults are 2 warmups and 7 repetitions. Every timed run is checked against the dataset's
expected result; a run reporting the wrong answer is marked `result_ok=false` and is **never used
as a speedup baseline**.

> A non-smoke run overwrites `results/<label>_summary.md`, a **tracked** file. Use
> `BENCH_RESULTS_DIR=/tmp/...` or `BENCH_SMOKE=1` if you do not intend to update committed
> evidence.

### Output files

```
results/utf16_pipeline_benchmark.csv            raw, one row per measured iteration   (ignored)
results/utf16_pipeline_benchmark_aggregate.csv  per dataset/operation statistics      (ignored)
results/utf16_pipeline_benchmark.json           environment + raw + aggregate         (ignored)
results/utf16_pipeline_benchmark_summary.md     human-readable summary                (committed)
results/utf16_benchmark.csv                     validation-harness raw                (ignored)
results/utf16_benchmark_summary.md              validation-harness summary            (committed)
```

Existing result files are never overwritten without `--overwrite`, or a different
`--output`/`--summary` path.

### Interpretation cautions

Whole-process timing includes process start-up and, for Parabix, loading the compiled pipeline.
On the machines used so far that is tens of milliseconds, which **exceeds the work** for inputs
up to at least 1 MiB — so at those sizes the raw numbers are *process* throughput, not *kernel*
throughput. The generated summary states this from the measured data and declines to rank
kernels on it. Any committed summary is **machine-specific evidence from one run on one machine,
not a universal claim**. No benchmark numbers are hardcoded in this README.

### Standalone scan prototypes

Three standalone prototypes study the two-level scan independently of Parabix. They are **not**
Parabix kernels, and their throughput numbers are microbenchmarks on an in-memory buffer, **not
comparable** to the end-to-end validator numbers.

```bash
./scripts/run_llmask_prototype.sh                # 4 mask-generation strategies + benchmark
./scripts/run_maskhl_prototype.sh                # aggregation, invariants, skip-rate sweep
./scripts/run_error_position_scan_prototype.sh   # position recovery, scanner agreement
```

Their shared cores live in `benchmarks/llmask_generation.h` and
`benchmarks/maskhl_aggregation.h`, included verbatim so all three measure identical code, and
each is diffed against `benchmarks/llmask_reference.py`.

---

## Reproducing the Research Artifact

One command runs the environment checks, dataset verification, every regression suite and the
benchmark campaign, then collects everything into one evidence directory. It adds no UTF-16
functionality — it runs the workflow that already exists.

```bash
./scripts/reproduce_research.sh --quick     # representative evidence, ~5 minutes
./scripts/reproduce_research.sh --full      # the complete evaluation
./scripts/reproduce_research.sh --help
```

**Prerequisites:** a built validator (`./scripts/setup_parabix.sh`) and the controlled datasets
(`./scripts/generate_error_density_datasets.sh`). The script **never downloads or installs
anything**, and **never regenerates datasets silently** — if they are missing it says exactly
what is missing and stops, unless `--force` is given. Existing evidence is likewise never
deleted without `--force`.

**Options:** `--quick`, `--full`, `--skip-tests`, `--skip-benchmarks`, `--skip-datasets`,
`--force`, `--output-dir DIR`, `--seed N`, `--help`.

### Stage 1 — environment

Checks `python3` (3.6+) and reports its version; checks for a C++ compiler, warning (not
failing) that the simdutf comparison will be skipped if absent; requires the validator binary at
`.deps/parabix/build/bin/utf16validate` and **stops with the build command** if it is missing;
verifies the repository layout, that each required script is present, that the patch is in
place, and that there is enough free disk space.

### Stage 2 — controlled error-density datasets

Verifies `datasets/error_density/` and its `manifest.csv`. Datasets are **only** regenerated
under `--force`. This stage is what guarantees the benchmark measures the corpus the manifest
describes.

### Stage 3 — regression suites

Runs, in order, stopping immediately on any unexpected failure:

1. `utf16_oracle.py --self-test`
2. `test_utf16validate.sh`
3. `test_utf16be.sh`
4. `test_errormarks.sh`
5. `test_scan_consumer.sh`
6. `test_utf16_repair.sh`
7. `test_utf16_json_output.sh`
8. `test_utf16_oracle_fuzz.sh --quick`
9. `benchmark_utf16_pipeline.sh --self-test-gate`

Each line prints PASS, PASS with a KNOWN-XFAIL count, or FAIL with the last 20 lines of output.
A suite reporting KNOWN-XFAIL entries **still passes**. An unexpected failure stops the run
before the benchmark stage, so a broken build never produces timing numbers.

### Stage 4 — benchmark campaign

`--quick` runs `--sizes 4MiB --densities 0,1,10,50 --warmups 1 --iterations 3`. 4 MiB is the
smallest size in this corpus at which the measurement is not entirely process start-up. `--full`
runs the complete matrix. Methodology is unchanged from
[Benchmarking](#benchmarking) in either mode.

The benchmark writes its CSV, aggregate CSV, JSON and Markdown summary **directly into this
run's staging directory** via the driver's own `--output`/`--summary` options. The canonical
evidence under `results/` is never read, rewritten or copied from, so a reproduction run leaves
it byte-for-byte untouched.

### Stage 5 — evidence package

Collects everything into `results/reproduction/`:

```
environment.json          run metadata: commit, branch, dirty, seed, binary, simdutf, timings
system_information.json   OS, architecture, CPU model, logical CPUs, Python, free disk
test_summary.json         per-suite PASS / KNOWN-XFAIL / FAIL
benchmark.csv             raw per-iteration rows
benchmark.json            environment + raw + aggregate
aggregate.csv             per dataset/operation statistics
benchmark_summary.md      the measured tables, correctness gate and limitations
reproduction_report.md    commands, environment, results, elapsed time, overall PASS/FAIL
```

Raw CSV/JSON here is git-ignored; the two Markdown files are **not**, so verified evidence can
be committed.

### Expected runtime

`--quick` takes roughly five minutes on the development machine — the regression suites
dominate; the benchmark stage is under a minute. `--full` runs the complete 132-dataset matrix:
its correctness gate alone measures about **7 minutes**, and it plans about **8800 measured
runs**, so budget roughly **30–60 minutes** depending on the machine. That range is an estimate
extrapolated from measured parts, not a timed full run.

### How to verify success

The script exits `0` **only** if every stage that ran succeeded, and prints
`reproduction PASSED`. `reproduction_report.md` ends with an overall PASS/FAIL, and
`test_summary.json` carries `passed`, `known_xfail` and `failed` counts.

### Failure behaviour

A missing prerequisite stops the run with a message naming exactly what is missing and how to
produce it. A failing regression suite stops the run **before** the benchmark stage. A benchmark
path failing its correctness gate is excluded from timing rather than reported as fast, and a
gate violation outside the one tolerated symptom aborts the run entirely. Nothing existing is
deleted or regenerated without `--force`.

---

## Results

| Path | Produced by | Regenerated | Committed | Role in the artifact |
| --- | --- | --- | --- | --- |
| `results/utf16_benchmark_csil_x86_64_summary.md` | `benchmark_utf16validate.sh` on CSIL | Manually, on that host | Yes | The x86-64 half of the cross-architecture claim; source rows for the cross-architecture, scalar-vs-SIMD and thread-scaling charts |
| `results/utf16_benchmark_apple_arm64_summary.md` | `benchmark_utf16validate.sh` on the M1 | Manually, on that host | Yes | The arm64 half of the same claim; additionally the source for the pipeline-ablation chart |
| `results/utf16_benchmark_summary.md` | `benchmark_utf16validate.sh` | On any non-smoke run | Yes | **Stale** — predates the byte-oriented kernel; see the note below |
| `results/utf16_pipeline_benchmark_summary.md` | `benchmark_utf16_pipeline.py` | On an `--overwrite` run | Yes | Per-path costs at known error density; the ablation evidence |
| `results/final_graphs/*.png` | `plot_final_benchmark_graphs.py` | On demand | Yes | The seven summary visualizations |
| `results/final_graphs/final_graph_data.csv` | Same script | With the charts | Yes | The audited numbers behind the charts |
| `results/final_graphs/README.md` | Written by hand | — | Yes | The claim and limitations attached to each chart |
| `results/reproduction/reproduction_report.md` | `reproduce_research.sh` stage 5 | Every reproduction run | Yes | **The overall PASS/FAIL for the artifact** |
| `results/reproduction/benchmark_summary.md` | Same, stage 4→5 | Every reproduction run | Yes | Measured tables, correctness gate, limitations |
| `results/reproduction/*.csv`, `*.json` | Same | Every reproduction run | No (git-ignored) | Raw evidence and machine metadata |
| `results/apple_arm64_toolchain.md`, `results/csil_x86_64_toolchain.md` | Written by hand per host | When a toolchain changes | Yes | Exact compiler/LLVM/CPU on each host, so a number can be attributed |

**The stale summary.** `results/utf16_benchmark_summary.md` predates the current byte-oriented
kernel. `docs/threading_analysis.md` found the current kernel is slower than that file reports,
so **its speedup numbers should not be quoted** — re-measure first. It is kept because deleting
measured evidence to make a story tidier is the wrong instinct; the per-host summaries above are
the current numbers.

**Scope of all measurements.** Machine-specific evidence from measured runs, not universal
claims. No laboratory isolation was applied and other processes were running. Cross-architecture
*absolute* speeds are never compared — CSIL is a QEMU guest with 31 GiB, the M1 is physical with
8 GiB — so only within-host ratios carry the portability argument. The pipeline-ablation chart,
and the repair row of the correctness-evidence chart, are Apple-only; there is no CSIL repair
number.

---

## Patch

### Why Parabix is git-ignored

`.deps/parabix` is a ~370 MB nested git repository carrying machine-specific build artifacts.
Committing it would bloat this repository, and — more importantly — it would obscure authorship:
a reviewer could not tell which lines are ours and which are upstream Parabix.

### Why the patch is the source of truth

Because that tree is git-ignored and regenerated by applying the patch, the `.cpp` inside it is
a *build product*, not the artifact. **`patches/utf16-simd-milestone.patch` is the
implementation.** Whenever the kernel changes, only the patch is committed.

### What the patch modifies

Three paths:

| Path | Change |
| --- | --- |
| `CMakeLists.txt` (root) | Registers the `utf16validate` tool; includes a Boost-component compatibility fix for the pinned revision |
| `tools/utf16validate/CMakeLists.txt` | Build definition for the tool |
| `tools/utf16validate/utf16validate.cpp` | The kernels and driver: scalar validator, byte-oriented SIMD kernel, `UTF16ErrorMarksKernel`, `UTF16ErrorMarkScanKernel`, repair, JSON diagnostics, `--be` |

`patches/utf16-scalar-milestone.patch` is the earlier scalar-only milestone, retained for
history.

### Applying it

`./scripts/setup_parabix.sh` does this automatically as step 3. By hand, against a fresh
checkout:

```bash
git clone https://cs-git-research.cs.sfu.ca/cameron/parabix-devel.git .deps/parabix
cd .deps/parabix
git checkout f0369dd138e2e7a710566d5035f68b9cdc0bf305
git apply ../../patches/utf16-simd-milestone.patch
```

Check that it still applies cleanly without modifying anything:

```bash
cd .deps/parabix && git apply --check ../../patches/utf16-simd-milestone.patch && echo OK
```

### Regenerating it safely

After changing the kernel, regenerate from the Parabix tree and commit **only the patch**:

```bash
cd .deps/parabix
git add CMakeLists.txt tools/utf16validate/CMakeLists.txt tools/utf16validate/utf16validate.cpp
git diff --cached -- CMakeLists.txt tools/utf16validate/ > ../../patches/utf16-simd-milestone.patch
git reset -q -- CMakeLists.txt tools/utf16validate/     # unstage; leaves the tree untouched
cd ../..
```

The `git reset` matters: it unstages without touching the working tree, so the build you just
tested is still the build you have.

### Picking up someone else's patch change

`setup_parabix.sh` deliberately **refuses to reset** a checkout that still has the old patch
applied — it reports "unexpected local modifications" to protect uncommitted work. The reliable
way back in sync is a clean re-apply:

```bash
git pull                                              # the updated patch + docs
rm -rf .deps/parabix && ./scripts/setup_parabix.sh    # clean clone, re-apply, rebuild
./scripts/test_utf16validate.sh                       # confirm 67 passed, 0 failed
```

To avoid re-cloning, reset in place instead:

```bash
cd .deps/parabix && git checkout -- . && git clean -fd && cd ../.. && ./scripts/setup_parabix.sh
```

---

## Known Limitation

The `--scan-error-marks` two-level scan consumer can emit a small number of **extra positions
past end-of-input**. The defect is confined to that one path: `errorCount` remains correct on
every implementation — including the scan's own count, which is what makes the output
self-inconsistent — `--print-positions` and the oracle continue to agree exactly, repair is
unaffected, and **no real error position is ever missing**. Every spurious position lies outside
the valid code-unit range.

**The trigger is broader than the regression cases that pin it, and this matters.** The fuzz
campaign (issue #42) first exposed the symptom on inputs whose code-unit count is a positive
exact multiple of the 4096-unit scan stride, and its KNOWN-XFAIL predicate is deliberately
pinned to exactly those cases. Later controlled-density testing showed the trigger depends on
**both the error distribution and the host/runtime environment**. A 2048-code-unit dataset has
reproduced the symptom on some hosts, including Apple arm64, while remaining clean on others
such as CSIL x86-64; the self-test therefore accepts either outcome for it, and requires only
that a divergence, when it does appear, still classifies as the known exclusion. A
32768-code-unit dataset has stayed clean everywhere it has been run, and the self-test holds it
to that. Input length alone therefore does not characterize the defect, and the exact-multiple
condition describes *the fuzz driver's pinned cases*, **not the boundary of the defect** —
KNOWN-XFAIL passing is not evidence that other inputs are unaffected. For this reason the
benchmark's correctness gate classifies the symptom **by symptom, never by input size**, and
excludes only `locate_scan` timing for an affected dataset while still measuring every other
operation on it. No production code has been changed to hide it, it is reproducible on demand
with `--strict-known-defects`, and the predicate and both real datasets are covered by
`./benchmarks/benchmark_utf16_pipeline.sh --self-test-gate`.

---

## Documentation Roadmap

A suggested reading order for someone evaluating the project.

1. **This README** — scope, build, how to run and reproduce everything.
2. **[`docs/benchmark_methodology.md`](docs/benchmark_methodology.md)** — read *before* any
   number, since it defines the fair comparison groups and the overhead adjustment that every
   later measurement depends on. Reading a result first invites misreading it.
3. **[`docs/SIMD_BYTE_ORIENTED_VALIDATOR.md`](docs/SIMD_BYTE_ORIENTED_VALIDATOR.md)** — the core
   design: high-byte classification and why it is endian-agnostic. This is the context needed to
   read `patches/utf16-simd-milestone.patch`.
4. **[`docs/parabix_errormarks_producer.md`](docs/parabix_errormarks_producer.md)** — how the
   per-code-unit verdict becomes a real `StreamSet(1)`; the bridge from validation to location.
5. **[`docs/two_level_scan_design.md`](docs/two_level_scan_design.md)** then
   **[`docs/two_level_scan_consumer.md`](docs/two_level_scan_consumer.md)** — the design study
   first, then the shipped consumer, so the gap between plan and implementation is visible.
6. **[`docs/utf16_repair.md`](docs/utf16_repair.md)** — the repair contract, especially the
   odd-trailing-byte rule that has no simdutf equivalent.
7. **[`docs/cross_arch_evaluation.md`](docs/cross_arch_evaluation.md)** — the runbook and caveats
   behind the portability claim; last, because it depends on all of the above.

Optional depth, in any order: the three prototype write-ups
([mask generation](docs/llmask_generation_prototype.md),
[aggregation](docs/maskhl_aggregation_prototype.md),
[position scan](docs/error_position_scan_prototype.md)),
[`docs/pablo_utf16_prototype.md`](docs/pablo_utf16_prototype.md) for the negative result,
[`docs/simd_regression_investigation.md`](docs/simd_regression_investigation.md) and
[`docs/threading_analysis.md`](docs/threading_analysis.md) for the performance investigations.

---

## Project Development Timeline

The seventeen steps of the contribution ladder, in the order they were built.

| # | Step |
| --- | --- |
| 1 | Scalar oracle validator |
| 2 | Portable byte-oriented SIMD validator |
| 3 | Multilingual datasets |
| 4 | Controlled malformed inputs |
| 5 | simdutf baseline |
| 6 | Fair benchmark methodology |
| 7 | Thread-scaling analysis |
| 8 | SIMD regression diagnosis |
| 9 | Optimized signmask-free SIMD |
| 10 | Two-level scan study |
| 11 | errorMarks producer |
| 12 | `TwoLevelScanKernel` consumer |
| 13 | UTF-16BE support |
| 14 | Pablo negative-result study |
| 15 | U+FFFD repair |
| 16 | Cross-architecture evaluation |
| 17 | Final benchmark visualizations and evaluation |

The shape of the arc is deliberate: correctness infrastructure (1, 3, 4) and measurement
infrastructure (5, 6) were built before optimisation (8, 9), and the error-location work
(10–12) was prototyped standalone before being committed to a Parabix kernel.

---

## References

### Repository documentation

| Document | What you will learn |
| --- | --- |
| [`docs/SIMD_BYTE_ORIENTED_VALIDATOR.md`](docs/SIMD_BYTE_ORIENTED_VALIDATOR.md) | How the `fw=8` high-byte classifier works, and why it removes both the architecture-specific intrinsics and the little-endian assertion |
| [`docs/parabix_errormarks_producer.md`](docs/parabix_errormarks_producer.md) | How `UTF16ErrorMarksKernel` turns the per-code-unit verdict into a real Parabix `StreamSet(1)`, and how the lookahead rule works |
| [`docs/two_level_scan_design.md`](docs/two_level_scan_design.md) | The design study mapping Parabix's `TwoLevelScanKernel` onto UTF-16 before any kernel existed |
| [`docs/two_level_scan_consumer.md`](docs/two_level_scan_consumer.md) | The shipped `UTF16ErrorMarkScanKernel`: index construction, clean-region skipping, `ctz` position recovery |
| [`docs/utf16be_support.md`](docs/utf16be_support.md) | What `--be` changes on each path, and why positions stay endian-agnostic |
| [`docs/utf16_repair.md`](docs/utf16_repair.md) | The U+FFFD repair contract, including the odd-trailing-byte discard-and-append rule |
| [`docs/benchmark_methodology.md`](docs/benchmark_methodology.md) | Benchmark modes, fair comparison groups, overhead adjustment, and the CSV/summary schema |
| [`docs/cross_arch_evaluation.md`](docs/cross_arch_evaluation.md) | The two-host runbook, and the caveats that limit the comparison to within-host ratios |
| [`docs/pablo_utf16_prototype.md`](docs/pablo_utf16_prototype.md) | What the Pablo/S2P path actually did, why transposition dominates, and why it was not adopted |
| [`docs/simd_regression_investigation.md`](docs/simd_regression_investigation.md) | How the `hsimd_signmask(8)` regression was diagnosed and removed from the hot loop |
| [`docs/threading_analysis.md`](docs/threading_analysis.md) | Why thread scaling plateaus around two threads, and why one committed summary is stale |
| [`docs/llmask_generation_prototype.md`](docs/llmask_generation_prototype.md) | Four strategies for reducing a per-code-unit verdict to a bitstream, measured |
| [`docs/maskhl_aggregation_prototype.md`](docs/maskhl_aggregation_prototype.md) | How 64 low-level masks aggregate into one high-level word, and the resulting skip rates |
| [`docs/error_position_scan_prototype.md`](docs/error_position_scan_prototype.md) | Recovering exact positions by `ctz`/reset-lowest-bit, and the two-level vs one-level vs linear comparison |
| [`docs/multilingual_emoji_corpus.md`](docs/multilingual_emoji_corpus.md) | What the committed corpus covers, and why it tests surrogate structure rather than emoji semantics |
| [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) | The original project plan |
| [`results/final_graphs/README.md`](results/final_graphs/README.md) | The claim each chart supports, its data source, and its limitations |
| [`external/baselines/clausecker_lemire/README.md`](external/baselines/clausecker_lemire/README.md) | Baseline attribution, licensing, and the full output-semantics discussion |

### External dependencies

| Dependency | Version / pin |
| --- | --- |
| [Parabix](https://cs-git-research.cs.sfu.ca/cameron/parabix-devel.git) | commit `f0369dd138e2e7a710566d5035f68b9cdc0bf305`, branch `master` |
| [simdutf](https://github.com/simdutf/simdutf) | `v9.0.0`, commit `ca7acbce` (Apache-2.0 OR MIT) |
| LLVM / Clang | 16 |
| Boost | `filesystem`, `iostreams`, `regex` |
| CMake | 3.x |
| Python 3 | 3.6+ — oracle, dataset generation, suite drivers |
| matplotlib | optional, benchmark visualizations only |

### A note on comparing against simdutf

Our validators report `errorCount = N` — how many code units are ill-formed. simdutf reports
`valid = true/false` plus the index of the **first** ill-formed unit; it does not count every
error, and the wrapper never fabricates a count. The like-for-like comparison is therefore
**accept/reject throughput on valid input**, where both tools do the same work. simdutf is also
single-threaded, so it may only be compared against Parabix at `--thread-num=1`. No upstream
source is vendored: `./scripts/setup_clausecker_lemire.sh` clones the pinned commit into
`.deps/simdutf/` (git-ignored) and builds a wrapper we own.
