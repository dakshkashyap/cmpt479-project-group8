# CMPT 479 — UTF-16LE Validation with Parabix

## Project objective

Build and evaluate a UTF-16LE well-formedness validator on top of the
[Parabix](https://cs-git-research.cs.sfu.ca/cameron/parabix-devel.git) framework:

- A **scalar UTF-16LE oracle** — a simple, serial, two-bytes-at-a-time validator
  used as the ground-truth reference for correctness.
- A **portable, byte-oriented (fw=8) SIMD implementation** using Parabix / IDISA
  operations instead of hard-coded architecture-specific intrinsics. It classifies
  each code unit on its **high byte** (the Clausecker–Lemire strategy), so it makes
  no host-endianness assumption. See
  [`docs/SIMD_BYTE_ORIENTED_VALIDATOR.md`](docs/SIMD_BYTE_ORIENTED_VALIDATOR.md).
- **Malformed surrogate-pair counting**: the tool reports the number of
  ill-formed UTF-16 code units (unpaired high/low surrogates, incomplete final
  unit).
- A preliminary **single-thread vs. multi-thread** benchmark using Parabix's
  `--thread-num` control.
- Planned comparison against the **Clausecker–Lemire** UTF-16 validation approach.

## Current status

Only verified results are listed here:

- Scalar implementation works.
- SIMD implementation works, and is now **byte-oriented (fw=8, high-byte
  classification)** and **host-endian agnostic** — the earlier 16-bit-lane,
  host-order code path and its compile-time little-endian assertion have been
  removed (see [`docs/SIMD_BYTE_ORIENTED_VALIDATOR.md`](docs/SIMD_BYTE_ORIENTED_VALIDATOR.md)).
- All basic correctness tests pass (scalar and `--simd` agree with expected counts).
- Randomized tests (against an independent Python reference) pass.
- Boundary tests pass (full SIMD block + scalar tail, exact-block-sized input,
  surrogate pairs and malformed sequences crossing a SIMD block boundary, odd
  trailing byte after a large input).
- Forced segment-size tests (`-segment-size=1,13,64`) pass.
- A deterministic multilingual / emoji corpus (ten languages, emoji, supplementary
  planes, LE **and** BE) validates with zero errors on every path —
  [`docs/multilingual_emoji_corpus.md`](docs/multilingual_emoji_corpus.md).
- Malformed and boundary coverage is checked as a cross-product: every fixture in
  UTF-16LE **and** UTF-16BE, on scalar / `--simd` / errorMarks / two-level scan, at
  segment sizes 1/13/64 — see
  [Malformed and boundary suite](#malformed-and-boundary-suite).
- A reproducible preliminary scalar/SIMD/thread-count benchmark is available.
- Cross-architecture evaluation is **done on both hosts**: CSIL x86-64
  (`results/utf16_benchmark_csil_x86_64_summary.md`, SSE4.2/`westmere`) and Apple arm64
  (`results/utf16_benchmark_apple_arm64_summary.md`, NEON). Under identical methodology the
  byte-oriented Parabix kernel beats the architecture's native simdutf SIMD path by a similar
  ~2.3–2.5× (adjusted) on **both** ISAs. Shared runbook and caveats:
  [`docs/cross_arch_evaluation.md`](docs/cross_arch_evaluation.md).

## Repository layout

```
patches/    Milestone patches applied on top of the pinned Parabix revision
scripts/    setup_parabix.sh (build) and test_utf16validate.sh (tests)
docs/       Project plan and design/reference notes
results/    Benchmark output (generated; large artifacts are git-ignored)
datasets/   Controlled error-density datasets (generated; git-ignored)
benchmarks/ Benchmark drivers / configurations
src/        Project-local sources (kept out of the Parabix tree)
tests/      Project-local test material (incl. tests/corpus/, the multilingual corpus)
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

## Staying in sync after a patch change

The kernel source of truth is **`patches/utf16-simd-milestone.patch`**, not the
`.cpp` inside `.deps/parabix` (that tree is git-ignored and is regenerated by
applying the patch). So whenever the kernel changes, only the patch is committed —
never `.deps/parabix` itself (it is a ~370 MB nested git repo with machine-specific
build artifacts; committing it would bloat the repo and obscure who wrote what).

**When you change the kernel**, regenerate the patch from the Parabix tree and
commit it (the three affected paths are the root `CMakeLists.txt` and the two files
under `tools/utf16validate/`):

```bash
cd .deps/parabix
git add CMakeLists.txt tools/utf16validate/CMakeLists.txt tools/utf16validate/utf16validate.cpp
git diff --cached -- CMakeLists.txt tools/utf16validate/ > ../../patches/utf16-simd-milestone.patch
git reset -q -- CMakeLists.txt tools/utf16validate/     # unstage; leaves the tree untouched
cd ../..
# then commit patches/utf16-simd-milestone.patch (plus any docs/scripts you changed)
```

**When you pull a teammate's patch change**, `setup_parabix.sh` will *refuse to
reset* a checkout that still has the old patch applied (it reports "unexpected
local modifications" to protect your work). The foolproof way to get back in sync
is a clean re-apply:

```bash
git pull                                              # gets the updated patch + docs
rm -rf .deps/parabix && ./scripts/setup_parabix.sh    # clean clone, re-apply, rebuild
./scripts/test_utf16validate.sh                       # confirm "31 passed, 0 failed"
```

(If you prefer not to re-clone, reset the tree in place instead:
`cd .deps/parabix && git checkout -- . && git clean -fd && cd ../.. && ./scripts/setup_parabix.sh`.)

## Verifying correctness

The correctness bar for every change is a **differential test against the scalar
oracle plus an independent Python reference** — all three implementations must
agree, so a bug would have to appear identically in all of them to slip through.

```bash
# rebuild the tool after editing the kernel, then run the suite
cmake --build .deps/parabix/build --target utf16validate -j"$(nproc)"
./scripts/test_utf16validate.sh          # expect: "67 passed, 0 failed"
```

The suite runs every fixture in both `scalar` and `--simd` modes and fails on any
disagreement, crash, or wrong count. The Python reference validates the raw
UTF-16LE byte structure directly, so it also covers blobs no string encoder would
produce (lone surrogates, odd trailing bytes). Grouped coverage:

- **fixed cases** — valid BMP, valid pair, and each malformed class;
- **multilingual (valid)** — ASCII/English, accented European, Hindi, Punjabi, CJK,
  emoji (real non-BMP surrogate pairs), and a mixed sample (also run under forced
  segment sizes);
- **malformed sequences** — unpaired high/low, reversed pair, odd trailing byte,
  consecutive malformed units, and malformed data embedded in multilingual text;
- **boundaries** — valid and malformed pairs on, just before, and just after
  64-code-unit group boundaries, plus block/tail boundaries at 64/128/256/512 units;
- **forced pipeline segment sizes** (`-segment-size=1,13,64`) that stress the
  cross-segment carry, and **deterministic randomized inputs**.

All fixtures of this suite are generated into a `mktemp` directory and removed on exit.
The one committed test corpus is `tests/corpus/` (small, valid, multilingual/emoji — see
[Multilingual and emoji corpus](#multilingual-and-emoji-corpus)); everything else is
generated.

### Malformed and boundary suite

Where the suites above each gate one path, this one is the **cross-product**: every
fixture is built once as a code-unit sequence, then checked in **both encodings** across
**all four validation paths**, at four segment sizes. A disagreement between any two of
them fails here even if each individual suite still passes.

```bash
./scripts/test_utf16_malformed_boundaries.sh   # approximately 2 minutes
```

**Malformed categories covered**

- **lone surrogates** — a lone high or low as the whole file, embedded in BMP text, at
  the top of each range (U+DBFF / U+DFFF), as the first unit, as the last unit; two,
  three and four consecutive highs; two, three and four consecutive lows; high followed
  by BMP; BMP followed by low; a reversed low–high pair (also at offset 0);
- **mixed valid and invalid** — a valid pair beside a lone high and beside a lone low (on
  either side), the `high, high, low` trap (only the *first* high is ill-formed), its
  `high, low, low` mirror, `low high low high` (the middle two are a *valid pair*, so it
  is 2 errors, not 4), eight alternating valid/invalid runs, four ill-formed units in a
  row, six consecutive lone highs, and malformed data at the beginning, the middle, the
  end, and all three at once;
- **byte-length failures** — a one-byte file, a one-byte file whose byte looks like a
  surrogate lead (`0xD8`), an odd trailing byte after BMP data, after a valid surrogate
  pair, after a lone high, after a lone low, after a reversed pair, and a large valid
  input (8192 code units) plus one stray byte.

**Odd trailing bytes in expected diagnostics.** A trailing byte with no partner is not a
code unit, so it has no code-unit index. The suite asserts, on every path and at every
segment size: `errorCount` = ill-formed code units **+ 1**, while the position list
contains ill-formed **code-unit indices only** — an odd trailing byte never appears in it.
A 1-byte file therefore reports `errorCount = 1` with an empty position list.

**Boundary offsets covered.** A code unit is 2 bytes, so these code-unit offsets bracket
the SIMD block boundaries in bytes:

| Code-unit offsets | Brackets byte offset |
| --- | --- |
| 7, 8, 9 | 16 |
| 15, 16, 17 | 32 |
| 31, 32, 33 | 64 |
| 63, 64, 65 | 128 (also the 64-code-unit LLmask group) |
| 127, 128, 129 | 256 |
| 255, 256, 257 | 512 |
| 511, 512, 513 | 1024 |

At each offset the suite places a valid pair straddling the boundary, a valid pair ending
exactly at it, a valid pair starting exactly at it, a lone high on the low side, a lone low
on the boundary, and two errors on opposite sides. It also covers the beginning and end of
input (one-pair file, one-unit file, empty file, a pair at the very end, a dangling high as
the last unit, a lone low as the first unit) and, at forced `-segment-size=1`, `13` and
`64`, a **surrogate pair split across the segment boundary**, a **malformed high at the
final unit of a segment**, and a **low at the first unit of a segment with no matching
high** — at the first three multiples of each segment size.

**LE and BE coverage.** Every fixture runs in UTF-16LE and, via `--be`, in UTF-16BE, from
the same intended code-unit sequence. Both encodings must report the same count and the
same code-unit positions (positions are code-unit indices, so they are endian-agnostic),
and the BE bytes must be exactly the byte swap of the LE bytes.

**Path agreement.** For each fixture, encoding and segment size, the suite requires
scalar count = `--simd` count = `--emit-error-marks` count = the count reported by the
two-level scan, and the `--scan-error-marks` position list = the expected positions; the
linear `--print-positions` printer is compared as well. Expectations come from three
independent layers that must agree *before* any kernel is consulted: hand-declared
positions per fixture, a test-side oracle written from the definition of well-formedness,
and `benchmarks/llmask_reference.py` run over the raw bytes of both encodings. A
representative subset is finally run three times per encoding to confirm repeated runs are
byte-for-byte deterministic.

### Independent oracle and deterministic fuzzing

[`scripts/utf16_oracle.py`](scripts/utf16_oracle.py) is a standalone Python **oracle**: given
raw bytes and an endianness it computes the code units, the ill-formed code-unit positions,
the odd-trailing-byte flag, the total `errorCount`, and the **U+FFFD-repaired bytes**. It is
written from the definition of UTF-16 well-formedness and shares no code with
`utf16validate.cpp`, with `benchmarks/llmask_reference.py`, or with the scalar validator: it
decodes left to right, letting a high surrogate consume the low surrogate after it, and calls
any surrogate that no pair could consume ill-formed. `llmask_reference.py` reaches the same
answer through a per-code-unit predicate instead, so the two agreeing is evidence rather than
tautology. It is usable on its own:

```bash
python3 scripts/utf16_oracle.py --count FILE [--be]
python3 scripts/utf16_oracle.py --positions FILE [--be]
python3 scripts/utf16_oracle.py --repair FILE [--be] > repaired.bin
python3 scripts/utf16_oracle.py --self-test        # 17 hand-worked vectors x LE/BE
```

**Diagnostic conventions** (identical to the rest of the project): positions are **code-unit
indices**, so they are the same in LE and BE; an odd trailing byte adds **1** to `errorCount`
and has **no position**; repair replaces each ill-formed code unit with U+FFFD in place; and an
odd trailing byte is **discarded and replaced by one appended U+FFFD**, per
[`docs/utf16_repair.md`](docs/utf16_repair.md).

**How this differs from the boundary suite.** The
[Malformed and boundary suite](#malformed-and-boundary-suite) is hand-curated: every case was
chosen deliberately and its expected positions declared by hand. This one is *generated* —
hundreds of cases nobody wrote down, with expectations taken from the oracle. They are
complementary, and they use different reference implementations on purpose.

```bash
./scripts/test_utf16_oracle_fuzz.sh              # default: 200 cases (~1.5 min), passes
./scripts/test_utf16_oracle_fuzz.sh --quick      # 40 smaller cases (~20 s), passes
./scripts/test_utf16_oracle_fuzz.sh --seed 1234 --cases 400 --max-units 2000
```

**What is fuzzed.** Eighteen categories, cycled so every run covers all of them: valid BMP;
valid supplementary (all surrogate pairs); mixed valid; lone highs; lone lows; reversed pairs;
consecutive highs; consecutive lows; strict alternation of valid and malformed; odd byte
lengths; empty; one-byte; tiny (1–4 units); medium random; large (4096–12288 units); malformed
at the beginning, middle and end; malformed on the boundary offsets 15/16/17, 31/32/33,
63/64/65, 127/128/129; and cases placed on forced segment boundaries, run at
`-segment-size=1`, `13` and `64`.

**Deterministic seeds.** Each case is generated from
`random.Random("utf16-fuzz|<seed>|<index>|<category>")`, so a case depends only on the seed,
its own index and its category — never on how many cases ran before it. The same `--seed`
reproduces the same bytes anywhere, and `--only-case N` reproduces exactly one case.
Reproducibility controls: `--seed`, `--cases`, `--max-units`, `--quick`, `--only-case`,
`--repair-every`, `--bin`. The seed and full configuration are printed at start-up.

**Properties checked**, every case, in UTF-16LE and UTF-16BE:

| | Property |
| --- | --- |
| P1 | `oracle == scalar == --simd == --emit-error-marks == scan` counts |
| P2 | `oracle == --print-positions == --scan-error-marks` positions |
| P3 | oracle repaired bytes `== --repair` bytes, byte for byte |
| P4 | `validate(repair(x)) == 0` |
| P5 | `repair(repair(x)) == repair(x)` |
| P6 | `x` well-formed ⇒ `repair(x) == x` |
| P7 | LE and BE of the same code units give the same count and positions, and the BE bytes are the byte swap of the LE |
| P8 | the same seed regenerates byte-identical cases |
| P9 | repeated runs of the same path on the same file agree |

Repair (P3–P6) is checked on every case in the odd-byte, degenerate-size, edge, reversed-pair,
boundary and segment categories, plus every `--repair-every`-th case elsewhere; the broad
repair campaign is [below](#comprehensive-repair-campaign).

**Failure reproduction format.** A failing case prints the seed, case number, category,
encoding, the raw bytes in hex (elided in the middle for large cases), the decoded code units,
the oracle's count and positions, what *each* implementation path returned, the first position
at which the paths diverge, and a ready-to-paste rerun command:

```
python3 scripts/test_utf16_oracle_fuzz.py --seed 479 --only-case 32 --max-units 600
```

All generated fixtures live in a temporary directory that is removed on exit.

**Known defect, classified as KNOWN-XFAIL.** The suite found one real defect in
`--scan-error-marks`: the two-level scan prints extra positions **past the end of the
input**. Counts stay correct on every path — including the scan's own `errorCount`,
which is what makes that output self-inconsistent — and `--print-positions` stays correct,
so only the two-level scan's position stream is affected. No production code has been
changed.

**Scope of the trigger.** This fuzz campaign first exposed the defect on inputs whose
code-unit count is a **positive exact multiple of the 4096-unit scan stride**, and the
KNOWN-XFAIL predicate below is pinned to exactly those regression cases. Later
controlled-density testing (the issue #45 benchmark gate) showed the **trigger is broader and
depends on the error distribution, not only the input length**: a 2048-code-unit dataset
reproduces the symptom while a 32768-code-unit one does not. So the exact-multiple condition
below describes *this driver's pinned cases*, not the boundary of the defect —
**KNOWN-XFAIL here is not evidence that other inputs are unaffected.** The benchmark gate
therefore classifies by symptom rather than by size; see
[UTF-16 pipeline benchmark](#utf-16-pipeline-benchmark-validation-location-scan-repair).

So that the suite remains a usable regression gate, exactly this defect is reported as
**KNOWN-XFAIL** and does not fail the run. It is not suppressed — it is printed, counted
separately, and reproducible on demand:

```bash
./scripts/test_utf16_oracle_fuzz.sh --strict-known-defects   # reproduce it; exits non-zero
```

The final summary always separates the three outcomes, for example
`2360 passed, 16 known-xfail, 0 failed` by default and
`2360 passed, 0 known-xfail, 16 failed` under `--strict-known-defects`.

**The classification is deliberately narrow**, and intentionally narrower than the defect
itself. A position mismatch is accepted as KNOWN-XFAIL only when *all* of the following hold:
the property is **P2**;
`--scan-error-marks` is the only disagreeing path; the oracle and `--print-positions` agree
**exactly**; every validator count agrees; the input has an **even** byte length; the input
is a **positive exact multiple of 4096** code units; no real position is missing from the
scan output; and **every** unexpected scan position lies **outside** the valid code-unit
range. Anything else is an ordinary failure: KNOWN-XFAIL never suppresses a count mismatch,
a repair mismatch, a wrong `--print-positions` result, a missing scan position, an in-range
spurious scan position, an odd-length input, or a length that is not a multiple of 4096.
When a mismatch is rejected the report names the first condition that ruled it out, so a
new defect cannot be absorbed into this bucket.

A **targeted deterministic regression** (seed-independent, LE and BE) pins the defect down
at exactly **4096** and **8192** code units: it asserts that the oracle and
`--print-positions` still agree, that every count is still correct, and that any scan
divergence still satisfies the narrow predicate above.

### Comprehensive repair campaign

[`scripts/test_utf16_repair.sh`](scripts/test_utf16_repair.sh) stays the focused
smoke/regression gate for `--repair`: a few dozen hand-written fixtures with exact expected
bytes, plus a small simdutf cross-check. The **campaign** on top of it is
[`scripts/test_utf16_repair_comprehensive.sh`](scripts/test_utf16_repair_comprehensive.sh) —
hundreds of cases, every one checked against `scripts/utf16_oracle.py` as the exact-output
oracle, in UTF-16LE *and* UTF-16BE, across forced segment sizes, up to 1 MiB streams.

```bash
./scripts/test_utf16_repair_comprehensive.sh              # default (~1.5 min)
./scripts/test_utf16_repair_comprehensive.sh --quick      # fast subset (~45 s)
./scripts/test_utf16_repair_comprehensive.sh --seed 1234 --cases 200 --max-units 2000
./scripts/test_utf16_repair_comprehensive.sh --section generated --only-case 57
./scripts/test_utf16_repair_comprehensive.sh --no-simdutf # skip the differential
```

**Categories covered.** *Hand-curated, with expected bytes declared by hand and
cross-checked against the oracle before the implementation is consulted* — valid: empty, one
BMP unit, ASCII/BMP text, a supplementary pair, multiple pairs, multilingual text, an emoji
ZWJ sequence, a pair at the beginning/middle/end, U+10FFFF, and `D800 DFFF` (a *valid* pair,
not two errors); malformed: lone high, lone low, high-then-BMP, BMP-then-low, reversed
low-high, `high high low` (only the first high is replaced), `high low low` (only the
trailing low), two and four consecutive highs and lows, alternating valid/malformed, errors
at the beginning/middle/end and all three at once, multiple separated malformed regions,
every surrogate ill-formed, U+DBFF and U+DFFF, and malformed data inside multilingual text;
odd-length: one-byte input, an odd byte after BMP / a valid pair / a lone high / a lone low /
a reversed pair / a 4096-unit valid stream, and odd byte values **00, D8, DC, FD, FF** (each
also after a lone high). *Generated, from an explicit seed* — valid-only, malformed-only,
sparse and dense malformed, mixed BMP/supplementary, long runs of highs and of lows, reversed
pairs, alternating pair/surrogate, odd-length streams, large streams, malformed clustered
near boundaries, and malformed distributed across the file.

**Boundary and segment-size coverage.** Offset 0, EOF, and code-unit offsets
**7/8/9, 15/16/17, 31/32/33, 63/64/65, 127/128/129, 255/256/257, 511/512/513,
4095/4096/4097, 8191/8192/8193**. At each: a valid pair split across the boundary, a lone
high immediately before it, a lone low exactly on it, malformed units on both sides, a valid
pair beside malformed units, and an odd trailing byte after a boundary-sized input. Every
input up to 2048 code units is additionally repaired at `-segment-size=1`, `13` and `64` and
must produce **byte-identical** output (P11).

**Properties.** P1 impl bytes == oracle bytes · P2 scalar `errorCount(repair(x))==0` ·
P3 `--simd` the same · P4 idempotence · P5 valid input unchanged · P6 even length preserved ·
P7 odd length becomes length+1 · P8 output length always even · P9 each U+FFFD is `FD FF` in
LE and `FF FD` in BE · P10 well-formed neighbours copied through · P11 identical across
segment sizes · P12 repeated runs identical · P13 LE and BE repairs decode to the same code
units · P14 replacement count equals the original `errorCount` under the odd-byte convention ·
P15 no unpaired surrogate survives.

**Large-file stress** (correctness and stability, *not* a benchmark — no throughput is
claimed and no benchmark file is touched): deterministic 1 MiB valid-BMP, mixed-valid,
sparse-malformed and dense-malformed streams, a stream with malformed units at the first,
middle and final code-unit positions, and a 1 MiB odd-length stream. Each reports input
bytes, output bytes, original error count, replacement count and the post-repair validation
result.

**simdutf differential.** Where `.deps/simdutf/singleheader` is present (from
`./scripts/setup_clausecker_lemire.sh`) a small helper is compiled into the temporary
directory and `--repair` is compared **byte for byte** against
`simdutf::to_well_formed_utf16le` / `...be`, in both encodings, over valid, sparse, dense,
boundary and large cases. **Odd-length inputs are not compared**: simdutf's API is
`char16_t`-based and has no notion of an incomplete trailing byte, so this project's "drop
the byte and append one U+FFFD" policy has no simdutf equivalent — those cases are reported
as skipped and are still checked against the Python oracle. If simdutf is absent, or no C++
compiler is on `PATH`, the differential is reported as skipped with the reason and the rest
of the campaign still runs; nothing is downloaded or installed.

**Determinism.** Every generated case comes from
`random.Random("utf16-repair|<seed>|<index>|<category>")`, so it depends only on the seed,
its index and its category. Controls: `--seed`, `--cases`, `--max-units`, `--quick`,
`--only-case`, `--section`, `--no-simdutf`, `--bin`. A failure prints the seed, case number
and category, encoding, segment size, input length, input hex (elided in the middle for large
inputs), the decoded code units, the oracle's count and malformed positions, expected and
actual repaired hex, the first differing byte offset, the post-repair validation counts, and
an exact rerun command.

**Expected behaviour:** every section passes. This suite has no xfail mechanism — any failure
is a real failure.

**Regression fixed: the UTF-16BE phantom-lookahead trailing byte.** This campaign found, and
the kernel now fixes, a real defect in `UTF16ErrorMarksKernel`. The marker rule needs
`isLow[k+1]`, which it read as the high byte of the next code unit at raw offset
`2(k+1)+HB`. In UTF-16BE the high byte comes first (`HB = 0`), so for the last complete code
unit that offset landed **exactly on an odd trailing byte** — and a trailing `0xDC`–`0xDF`
then looked like a low surrogate and paired with a real final high surrogate. The
consequences were a lone high that `--emit-error-marks` did not mark (one error where the
scalar path reported two), no `--print-positions` output, and a `--repair` result that still
contained the lone high, so `validate(repair(x)) != 0` and repair was not idempotent.
UTF-16LE was never affected: there the lookahead reads the byte *after* the trailing one,
which the pipeline zero-fills.

The fix neutralises any lookahead byte that lands on an incomplete trailing byte, so such a
byte can never take part in surrogate pairing. Non-final segments are untouched (their
lookahead is genuine next-segment data), and the documented odd-byte rule is unchanged: the
odd byte contributes one error, has no code-unit position, and repair discards it and appends
one U+FFFD. A dedicated section of this suite now checks **all 256 trailing-byte values**
after a final high surrogate — for U+D800, U+DA00 and U+DBFF, in both encodings, at
`-segment-size` default/1/13/64 — covering the former trigger values 0xDC–0xDF explicitly.

Extra confidence beyond the suite:

- **Single-thread vs. default threading must agree** (this also answers the
  thread-count question in the project brief):

```bash
.deps/parabix/build/bin/utf16validate --simd --thread-num=1 file.bin
.deps/parabix/build/bin/utf16validate --simd              file.bin   # default (3 threads)
.deps/parabix/build/bin/utf16validate                     file.bin   # scalar oracle
```

- **No host-endianness guard** should remain in the SIMD path:

```bash
grep -n "static_assert\|__BYTE_ORDER__" .deps/parabix/tools/utf16validate/utf16validate.cpp   # (no output)
```

- **The deliverable patch applies cleanly** onto the pinned revision (the
  `.deps/parabix` tree is git-ignored, so the patch is the real artifact):

```bash
cd .deps/parabix && git stash -q --include-untracked \
  && git apply --check ../../patches/utf16-simd-milestone.patch && echo OK \
  ; git stash pop -q
```

- **Planned stronger oracle:** diff the accept/reject decision against
  `simdutf::validate_utf16le` over a large corpus, upgrading the reference from
  our own scalar kernel to the library everyone trusts (tracked as a follow-up).

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

The harness runs the full matrix — `scalar`, Parabix SIMD at `--thread-num=1/2/3` and
default threads, and (optionally) the Clausecker–Lemire/simdutf baseline — and records
enough metadata to analyse it fairly (see `docs/benchmark_methodology.md`).

Smoke test (fast; checks the harness without a long run, writes to a temp dir so it
cannot touch committed results):

```
BENCH_SMOKE=1 ./scripts/benchmark_utf16validate.sh
```

Run the benchmark, including the simdutf baseline:

```
BENCH_INCLUDE_SIMDUTF=1 ./scripts/benchmark_utf16validate.sh
```

Run the final matrix over the large sizes and every dataset:

```
BENCH_DATASETS=all \
BENCH_SIZES_MB=128,256,512 \
BENCH_INCLUDE_SIMDUTF=1 \
./scripts/benchmark_utf16validate.sh
```

Or drive the harness directly:

```
python3 benchmarks/run_utf16_benchmark.py \
    --datasets mixed_multilingual --sizes-mb 1 \
    --warmups 1 --repetitions 2 --include-simdutf \
    --output /tmp/smoke.csv
```

Every timed run is checked against the dataset's expected result; a run reporting the
wrong answer is marked `result_ok=false` and is never used as a speedup baseline. The
harness also measures each tool's **fixed per-process overhead** on a tiny input and
reports an overhead-adjusted throughput next to the raw one — Parabix pays pipeline-load
cost that simdutf does not, so raw small-input throughput is not a fair comparison.

A non-smoke run overwrites `results/<label>_summary.md` (a tracked file). Use
`BENCH_RESULTS_DIR=/tmp/...` or `BENCH_SMOKE=1` if you do not intend to update it.

Outputs:

- Raw per-configuration data: `results/utf16_benchmark.csv` (git-ignored)
- Human-readable summary: `results/utf16_benchmark_summary.md`

Results are **machine-specific and preliminary**; no speedup is claimed here.
Numbers should be read from a generated summary on the machine that produced them.

## Error location (LLmask / two-level scan) — design and prototype

A design study for future error-location and repair work — mapping Parabix's existing
`TwoLevelScanKernel` (LLmask / maskHL sparse scanning) onto UTF-16 — is in
[`docs/two_level_scan_design.md`](docs/two_level_scan_design.md). It is a **design
artifact only**: no scan kernel and no repair is implemented.

Its central open question — *how do we reduce the SIMD validator's per-code-unit verdict to a
bitstream without reintroducing the `hsimd_signmask(8)` regression?* — is prototyped and
measured in [`docs/llmask_generation_prototype.md`](docs/llmask_generation_prototype.md):

```bash
./scripts/run_llmask_prototype.sh     # self-test, differential vs a Python reference,
                                      # cross-check vs the validator, and a benchmark
```

This compares four LLmask generation strategies (`benchmarks/prototype_llmask_generation.cpp`,
checked against `benchmarks/llmask_reference.py`).

The second level — aggregating 64 LLmasks into one 64-bit **maskHL**, so a clean 4096-code-unit
region can be skipped with a single compare — is prototyped and measured in
[`docs/maskhl_aggregation_prototype.md`](docs/maskhl_aggregation_prototype.md):

```bash
./scripts/run_maskhl_prototype.sh     # self-test, maskHL invariants, validator cross-check,
                                      # skip-rate sweep, and an aggregation-cost benchmark
```

The consumer — scanning maskHL and the LLmasks with `ctz` / reset-lowest-bit to recover the
**exact code-unit position** of every ill-formed unit — is prototyped in
[`docs/error_position_scan_prototype.md`](docs/error_position_scan_prototype.md):

```bash
./scripts/run_error_position_scan_prototype.sh   # self-test, scanner agreement, differential
                                                 # vs the Python reference, validator
                                                 # cross-check, and a scan benchmark
```

Those three are **standalone prototypes**: not Parabix kernels, and their throughput figures are
microbenchmarks on an in-memory buffer, **not** comparable to the end-to-end validator numbers
below. Their shared cores live in `benchmarks/llmask_generation.h` (LLmask) and
`benchmarks/maskhl_aggregation.h` (maskHL).

**In the real pipeline**, a `UTF16ErrorMarksKernel` now emits a genuine Parabix
`StreamSet(1)` — one bit per UTF-16 code unit, set iff that code unit is ill-formed — see
[`docs/parabix_errormarks_producer.md`](docs/parabix_errormarks_producer.md):

```bash
utf16validate --emit-error-marks FILE                   # emit the bitstream; same errorCount
utf16validate --emit-error-marks --print-positions FILE # print each ill-formed code unit's index
./scripts/test_errormarks.sh                            # 49/49: count + stream, 4 segment sizes
```

The **consumer** is a real subclass of Parabix's `TwoLevelScanKernel`,
`UTF16ErrorMarkScanKernel`, which builds a high-level index, **skips clean 4096-code-unit
regions**, and scans only dirty scanwords to report exact error positions — see
[`docs/two_level_scan_consumer.md`](docs/two_level_scan_consumer.md):

```bash
utf16validate --emit-error-marks --scan-error-marks -thread-num=1 FILE  # two-level scan → positions
./scripts/test_scan_consumer.sh                                         # 54/54, incl. stride skips
```

The optimized **count-only validator and the issue #32 producer are untouched** (byte-identical)
and the count-only path remains the default, so this cannot regress it. There is still **no
repair**.

**UTF-16BE** is supported on every path via `--be` (UTF-16LE stays the default and unchanged) —
see [`docs/utf16be_support.md`](docs/utf16be_support.md):

```bash
utf16validate --be --simd FILE                              # BE count-only
utf16validate --be --emit-error-marks --scan-error-marks -thread-num=1 FILE  # BE locate
./scripts/test_utf16be.sh                                   # 35/35, incl. cross-endian identity
```

Endianness only changes which byte of each pair is the high byte (`2k` for BE, `2k+1` for LE);
the scan consumer is endian-agnostic (it works on code-unit positions).

A bitwise/**Pablo** validation path was investigated and **deliberately not adopted** — see
[`docs/pablo_utf16_prototype.md`](docs/pablo_utf16_prototype.md). A real Pablo pipeline (S2P
transposition + `PabloKernel`) was built and ran, but Parabix has no bytes→16-code-unit-indexed
basis transpose, and transposition is a full extra pass with no benefit for a predicate as simple
as a single high-byte surrogate compare. This confirms the expectation that transposition
overhead dominates; the tool is unchanged (no `--pablo` mode shipped).

Thread scaling is analysed in [`docs/threading_analysis.md`](docs/threading_analysis.md)
(helper: `benchmarks/analyze_thread_scaling.py`). **Note:** that analysis found that the
current byte-oriented SIMD kernel is slower than the committed summary reports, so the
speedup figures in `results/utf16_benchmark_summary.md` are stale — re-measure before
quoting them.

Before collecting or quoting any performance number, read
[`docs/benchmark_methodology.md`](docs/benchmark_methodology.md). It defines the
benchmark modes, the fair comparison groups (in particular: the Clausecker–Lemire
baseline is single-threaded, so it may only be compared against Parabix
`--thread-num=1`, on valid input), the startup/JIT distortion that makes small-input
throughput misleading, and the CSV/summary schema the comparison harness must produce.

### Benchmark datasets

`benchmarks/generate_utf16_benchmark.py` produces deterministic, valid UTF-16LE
inputs. Text is built from built-in Unicode repertoires — nothing is downloaded and
no corpus is stored in the repository. Non-BMP characters are encoded as explicit
surrogate pairs, and a file never ends in a half-written code unit, so every dataset
validates with `errorCount = 0`.

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
# default dataset (what ./scripts/benchmark_utf16validate.sh generates)
python3 benchmarks/generate_utf16_benchmark.py --sizes-mb 1,8,32,64

# a single multilingual dataset
python3 benchmarks/generate_utf16_benchmark.py --datasets cjk --sizes-mb 1

# every multilingual dataset at 1 MiB, with a chosen seed
python3 benchmarks/generate_utf16_benchmark.py --datasets all --sizes-mb 1 --seed 479
```

Each `.bin` gets a `<file>.bin.json` sidecar recording the dataset type, requested
and actual size, seed, encoding, and a short composition description. Generated data
lives in `benchmarks/data/` and is git-ignored.

The benchmark runner currently measures the `default` dataset; wiring the
multilingual datasets into the timed runs is tracked separately.

### Multilingual and emoji corpus

A separate, **committed** corpus of valid UTF-16 fixtures lives in `tests/corpus/`, in
**both UTF-16LE and UTF-16BE** — see
[`docs/multilingual_emoji_corpus.md`](docs/multilingual_emoji_corpus.md). It covers ten
languages (English, accented Latin, Punjabi, Hindi, Arabic, Hebrew, Chinese, Japanese,
Korean, Thai), mixed multilingual paragraphs, emoji (a single pair, emoji-dense text,
skin tone modifiers, flags including tag sequences, variation selectors, ZWJ family and
profession sequences), non-emoji supplementary-plane characters, and the degenerate empty
and one-code-unit inputs. Every file validates with `errorCount = 0`; this corpus adds no
malformed data (that is what the error patterns above are for).

It tests **UTF-16 well-formedness — surrogate structure — not grapheme or emoji
semantics**. A ZWJ family sequence is interesting here because it alternates surrogate
pairs with BMP joiners, not because it should render as one glyph.

```bash
python3 scripts/generate_multilingual_corpus.py          # regenerate tests/corpus
python3 scripts/generate_multilingual_corpus.py --check   # verify, writing nothing
./scripts/test_multilingual_corpus.sh                     # 48/48, LE + BE, zero errors

# benchmark-sized version (git-ignored), and the same suite against it
python3 scripts/generate_multilingual_corpus.py --profile bench --size-mib 8
CORPUS_DIR="$PWD/benchmarks/data/multilingual_corpus" ./scripts/test_multilingual_corpus.sh
```

Files are written with the explicit `utf-16-le` / `utf-16-be` codecs, so there is **no
BOM**; the BE file is exactly the byte swap of the LE file. `tests/corpus/corpus_manifest.json`
records, per dataset, the byte size, code-unit count, code-point count, surrogate-pair
count, source category, seed, SHA-256 and `expected_error_count: 0` — all of which the
suite checks against the bytes on disk, along with reproducibility (two generator runs must
be byte-identical to each other and to the committed fixtures).

### Malformed datasets with controlled error rates

Adding `--error-patterns` corrupts any dataset above at a controlled error rate.
Invalid surrogate sequences are patched directly into the raw UTF-16LE bytes
(Python's codecs will not encode a lone surrogate), and the **expected error count is
recomputed from the final bytes** by an independent reference validator inside the
generator — so the metadata cannot disagree with what a validator will report.

| Error pattern | Injected construct |
| --- | --- |
| `unpaired_high` | lone high surrogates (1 error each) |
| `unpaired_low` | lone low surrogates (1 error each) |
| `reversed_pair` | a low surrogate followed by a high surrogate (2 errors each) |
| `odd_trailing_byte` | lone high surrogates, plus the final byte removed so the file ends in an incomplete code unit (+1 error) |
| `random_mix` | the three constructs above, randomly distributed |
| `clustered_mix` | the same mix, concentrated in a few contiguous runs |

Error rates are a percentage of the total code units. The standard sweep is
`0, 0.0001, 0.001, 0.01, 0.1, 1` (`--error-rates all`). A rate of **0% is the control:
the file is valid and its expected error count is 0.** Existing surrogate pairs in the
source text are never overwritten, so an injected error adds a fault rather than
destroying valid text.

```bash
# 0.01% randomly distributed errors in multilingual text
python3 benchmarks/generate_utf16_benchmark.py --datasets mixed_multilingual \
    --error-patterns random_mix --error-rates 0.01 --sizes-mb 1

# clustered errors at 0.1%
python3 benchmarks/generate_utf16_benchmark.py --datasets mixed_multilingual \
    --error-patterns clustered_mix --error-rates 0.1 --sizes-mb 1

# one specific malformed construct
python3 benchmarks/generate_utf16_benchmark.py --datasets cjk \
    --error-patterns unpaired_high --error-rates 0.01 --sizes-mb 1

# the full sweep: every pattern at every standard rate
python3 benchmarks/generate_utf16_benchmark.py --datasets mixed_multilingual \
    --error-patterns all --error-rates all --sizes-mb 1
```

Files are named
`malformed_utf16le_<dataset>_<pattern>_err<rate>_<size>MiB.bin`, and each sidecar adds
`error_rate_percent`, `error_pattern`, `error_sites_injected`, `expected_error_count`,
and a description of the injected errors. A *site* is one malformed construct, so the
site count is not the error count (a reversed pair is one site but two errors).

### Controlled error-density datasets

`benchmarks/generate_utf16_benchmark.py` above injects errors at an approximate *rate* and
then measures what it produced. [`scripts/generate_error_density_datasets.py`](scripts/generate_error_density_datasets.py)
is the complementary tool: it produces a size × density matrix in which the malformed count
is **exact and verified**, in **both encodings**, for reproducible experiments.

**Purpose.** Inputs for later benchmarking and validation work. The generator measures
nothing and touches no benchmark script or result.

```bash
./scripts/generate_error_density_datasets.sh              # the full matrix
./scripts/generate_error_density_datasets.sh --quick      # small sizes, reduced sweep
./scripts/generate_error_density_datasets.sh --sizes 64KiB,1MiB --densities 0,1,5
./scripts/generate_error_density_datasets.sh --encodings utf16le --overwrite
```

**Layout.** Generated data is git-ignored (like `benchmarks/data/`) and reproducible from
the seed:

```
datasets/error_density/
    utf16le/errdens_<size>_d<density>pct.utf16le.bin
    utf16be/errdens_<size>_d<density>pct.utf16be.bin
    manifest.csv
```

`manifest.csv` has one row per file: `filename`, `encoding`, `size_bytes`, `code_units`,
`target_density`, `actual_error_count`, `seed`. A narrow run (say `--quick`, or one density)
keeps the rows of datasets that are still on disk rather than dropping them, and warns if it
finds dataset files the manifest does not describe.

**Options.** `--output-dir`, `--seed`, `--sizes`, `--densities`, `--encodings`,
`--overwrite`, `--quick`, `--bin`. Without `--overwrite`, datasets that already exist are
kept — but they are still re-verified, so a stale or hand-edited file is caught rather than
silently reused.

**Sizes.** 4 KiB, 16 KiB, 64 KiB, 256 KiB, 1 MiB, 4 MiB. `--quick` uses the three smallest.

**Densities (percentages of code units, not bytes).** 0, 0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10,
20 and 50. `--quick` uses a reduced sweep.

**Rounding rule.** `target_errors = floor(code_units × density / 100 + 0.5)` — round half
up — and the file contains *exactly* that many ill-formed code units. A small file at a small
density can legitimately round to zero (for example 0.01 % of 2048 code units is 0.2 → 0);
the generator prints a note when that happens, and the manifest records the actual count.

**Deterministic seeds.** Everything derives from `--seed` (default 479). Each dataset is
built from `random.Random("utf16-density|<seed>|<size>|<density>")`, so it depends only on
the seed, its own size and its own density — never on what else ran in the same invocation
or in what order. Re-running with the same arguments reproduces byte-identical files. The
UTF-16LE and UTF-16BE files of a pair hold the same code units, so each is the byte swap of
the other and both report identical diagnostics.

**Content.** The valid base stream is realistic UTF-16 drawn word-by-word from ASCII,
accented Latin, Greek, Cyrillic, Hebrew, Arabic, Devanagari, Gurmukhi, Thai, CJK, kana and
Hangul, plus supplementary-plane characters and emoji (surrogate pairs) — not a repeating
filler pattern.

**Malformed insertion.** Errors come from six constructs — lone high, lone low, reversed
pair, high-high, low-low, and a broken mixed run — chosen so their error counts sum exactly
to the target. Each is written into its own slot of the stream with a deterministic offset,
so the malformed units are spread approximately uniformly rather than clustered, and each is
separated from its neighbours by at least one guard code unit so its error count is exactly
what was intended. Where a construct would land on a surrogate pair, both halves of that pair
are replaced together, so no unintended lone surrogate is ever created.

**Verification.** Before any dataset is accepted, its error count is checked three ways — the
scalar validator, `--simd`, and [`scripts/utf16_oracle.py`](scripts/utf16_oracle.py) — and
all three must equal the target. Datasets are written to a temporary file first and only
moved into place once verified, so a failing dataset is never left behind; any disagreement
aborts the run.

### UTF-16 pipeline benchmark (validation, location, scan, repair)

`benchmarks/run_utf16_benchmark.py` answers one question — scalar vs Parabix SIMD vs simdutf
*validation* throughput. [`benchmarks/benchmark_utf16_pipeline.py`](benchmarks/benchmark_utf16_pipeline.py)
is a separate campaign along a different axis: it measures **each processing path
independently** against the controlled error-density corpus above, so the cost of validation,
marker generation, error location, scan-based location and repair can be compared at a
*known, exact* malformed-unit density.

**How issue #44 feeds this.** The datasets and their `manifest.csv` are the input. The
benchmark reads sizes, densities, code-unit counts and `actual_error_count` **from the
manifest**, never inferring them from filenames, and fails with an explicit message if a
manifest row or its file is missing. It never generates datasets itself — run
`./scripts/generate_error_density_datasets.sh` first.

```bash
./scripts/generate_error_density_datasets.sh --quick        # prerequisite datasets
./benchmarks/benchmark_utf16_pipeline.sh --quick --overwrite        # fast subset
./benchmarks/benchmark_utf16_pipeline.sh --quick --estimate-only    # gate + estimate only
./benchmarks/benchmark_utf16_pipeline.sh --overwrite                # full matrix (slow)
python3 benchmarks/plot_utf16_pipeline_benchmark.py                 # charts from the raw CSV
```

**Operations measured** (each timed on its own — never combined into one measurement):
`validate_scalar`, `validate_simd`, `emit_error_marks` (marker generation), `locate_linear`
(`--print-positions`), `locate_scan` (`--scan-error-marks`), `repair`, plus `simdutf_validate`
and `simdutf_repair` where available. Position-printing and repair paths still write all of
their output; stdout is redirected to the null sink so terminal I/O is never timed.

**Options:** `--dataset-dir`, `--manifest`, `--bin`, `--output`, `--summary`, `--sizes`,
`--densities`, `--encodings`, `--operations`, `--iterations`, `--warmups`, `--seed`,
`--quick`, `--no-simdutf`, `--cpu-affinity`, `--timeout`, `--overwrite`, `--estimate-only`.
Quick mode uses a documented subset (3 sizes × 2 densities × both encodings, fewer
iterations); the full matrix is every row of the manifest.

**Timing methodology.** Whole-process wall clock (`time.perf_counter_ns`), warm-up runs
followed by measured iterations, **every raw iteration recorded**, median as the headline
statistic with min/max/mean/stddev alongside. Throughput comes from **input bytes on disk**,
never code units. Operation order is rotated per dataset, deterministically from `--seed`.
Each implementation's fixed per-process cost is measured once on a tiny input; an
overhead-adjusted throughput is reported **only** where the measurement is at least 3× that
cost, because below that the correction is dominated by its own noise. Helper compilation and
dataset generation happen before any measurement and are never timed. Non-zero exits and
timeouts are recorded as failures, never silently dropped.

**Correctness gate.** Before a dataset is timed, scalar, `--simd`, `--emit-error-marks`, the
Python oracle, `--print-positions` and `--scan-error-marks` must agree with the manifest's
`actual_error_count`, positions must match the oracle exactly, and `--repair` output must
equal the oracle's repaired bytes and re-validate to zero errors. **No timing row is written
for a path that fails its gate.**

One known scan-consumer symptom is tolerated, and it is identified **by symptom, never by
input size**: `--scan-error-marks` reports extra positions **beyond end-of-input** while the
oracle and `--print-positions` agree exactly, no real position is missing, and the scan's own
`errorCount` is still correct. A dataset showing exactly that is excluded from **`locate_scan`
timing only** — every other operation on it is still measured — and the reason is recorded in
the CSV, the JSON and the summary. **Anything else aborts the run**: a missing real position,
an extra position inside the valid code-unit range, a count disagreement, a wrong
`--print-positions` result, or a difference with no extra position at all.

This is deliberately *not* stated as issue #42's exact-multiple-of-4096 size condition. The
controlled-density corpus shows the trigger is broader than that and depends on the error
distribution: a **2048**-code-unit dataset reproduces the symptom while a **32768**-code-unit
one does not. The predicate and both of those real datasets (in each encoding) are covered by
a self-test that needs no timing:

```bash
./benchmarks/benchmark_utf16_pipeline.sh --self-test-gate
```

**simdutf** is used from the repository's existing `.deps/simdutf` checkout and compiled once
before measurement. Nothing is downloaded. If it is missing, or no C++ compiler is present,
those rows are marked skipped with the reason and the Parabix paths are still benchmarked.
simdutf validation returns a boolean where Parabix returns a count, and simdutf repair is
compared only on even-length inputs — its `char16_t` API has no odd-trailing-byte concept.

**Outputs** (`results/*.csv` and `*.json` are git-ignored; the Markdown summary is committed):

```
results/utf16_pipeline_benchmark.csv            raw, one row per measured iteration
results/utf16_pipeline_benchmark_aggregate.csv  per dataset/operation statistics
results/utf16_pipeline_benchmark.json           environment + raw + aggregate
results/utf16_pipeline_benchmark_summary.md     human-readable summary
results/utf16_pipeline_graphs/                  charts, when matplotlib is present
```

Existing result files are never overwritten without `--overwrite` (or a different
`--output`/`--summary` path, e.g. a timestamped run directory).

**Charts** come only from the raw CSV. matplotlib is **not** a dependency of this repository:
if it is absent the chart step reports itself as skipped and installs nothing.

**Interpretation cautions.** Whole-process timing includes process start-up and, for Parabix,
loading the compiled pipeline — on the machine used so far that is tens of milliseconds, which
*exceeds the work* for inputs up to at least 1 MiB, so at those sizes the raw numbers are
process throughput rather than kernel throughput. The summary states this from the measured
data and declines to rank kernels on it. Any committed summary is **machine-specific evidence
from one run on one machine, not a universal claim**; reproduce it locally before relying on
it. No benchmark numbers are hardcoded in this README.

## Clausecker–Lemire baseline

The external, specialized SIMD baseline is [simdutf](https://github.com/simdutf/simdutf)
(pinned at `v9.0.0`, commit `ca7acbce`; Apache-2.0 OR MIT). On arm64 it selects the
NEON kernel that classifies surrogates from the **high byte** — the same strategy as our
byte-oriented Parabix kernel, so it is a like-for-like competitor.

No upstream source is vendored: the setup script clones the pinned commit into
`.deps/simdutf/` (git-ignored) and builds a wrapper we own. See
[`external/baselines/clausecker_lemire/README.md`](external/baselines/clausecker_lemire/README.md)
for attribution, licensing, and the full output-semantics discussion.

```bash
./scripts/setup_clausecker_lemire.sh                       # fetch + build the baseline

BIN=.deps/baselines/bin/utf16validate_cl
$BIN --impl                                                # which SIMD kernel was selected
$BIN benchmarks/data/valid_utf16le_1MiB.bin                # -> valid = true
```

**Output semantics differ, by design.** Our validators report `errorCount = N` (how many
code units are ill-formed); simdutf reports `valid = true/false` plus the index of the
**first** ill-formed unit — it does not count every error, and the wrapper never
fabricates a count. The fair comparison is therefore **accept/reject throughput on valid
input**, where both tools do the same work. **No performance comparison has been run
yet**; no speed numbers are claimed.

## Reproducibility details

- **Parabix remote:** `https://cs-git-research.cs.sfu.ca/cameron/parabix-devel.git`
- **Parabix commit:** `f0369dd138e2e7a710566d5035f68b9cdc0bf305` (branch `master`)
- **LLVM version:** 16 (Clang 16 toolchain)
- **Milestone patch:** `patches/utf16-simd-milestone.patch`
  (root `CMakeLists.txt` registration + Boost-component compatibility, plus
  `tools/utf16validate/`)
- **Endianness:** the SIMD path is **byte-oriented and host-endian agnostic**; it
  currently validates **UTF-16LE data** (UTF-16BE planned).

## Known limitations

- Validates **UTF-16LE** data only. UTF-16BE is a planned extension — the
  byte-oriented classifier only needs to select the **even** byte positions
  instead of the odd ones (tracked as a follow-up issue).
- Reports an **error count**, not the exact source positions of malformed units.
  The SIMD mismatch bits are used only for counting.
- The **final remainder** (fewer than one SIMD block of code units) is handled by
  scalar processing.
- Preliminary benchmarking is available, but the final performance evaluation and
  Clausecker–Lemire comparison are still pending.
