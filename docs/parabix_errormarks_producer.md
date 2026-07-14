# Parabix errorMarks producer kernel (issue #32)

**A real `StreamSet<i1>[1]` bitstream is implemented, in a real Parabix kernel.** This is the
first issue in the error-location series that changes Parabix code rather than a standalone
prototype.

What this is **not**:

- **No repair.** Nothing rewrites a byte of UTF-16.
- **No `TwoLevelScanKernel` subclass.** No maskHL is built in a kernel. The consumer added here
  is a deliberately minimal position printer, described in §4.
- **No UTF-16BE.**
- **No production speedup is claimed.** The producer does strictly more work than the count-only
  validator and is measurably (slightly) slower — see §7.
- **The count-only validator is untouched.** Both existing kernels are byte-identical to the
  previous milestone patch (verified, §2), and the default and `--simd` paths still generate
  exactly the same pipeline.

Scope: UTF-16LE, little-endian host, as everywhere else in this project.

---

## 1. What was implemented

A new kernel, `UTF16ErrorMarksKernel`, added alongside — not in place of — the existing
validators in `tools/utf16validate/utf16validate.cpp`:

```
ReadSourceKernel          -> ByteStream  : StreamSet(1,8)
UTF16ErrorMarksKernel     -> errorMarks  : StreamSet(1)      <-- one bit per code unit
                          -> errorCount  : scalar
[--print-positions]
ErrorMarkPositionKernel   -> (prints the code-unit index of every set bit)
```

`errorMarks` is a genuine Parabix bitstream: **bit *i* == 1 iff UTF-16 code unit *i* is itself
ill-formed.** `errorCount` is derived from the marker bits the kernel actually emitted
(`ctpop` of each word it stores) plus the odd trailing byte, so the count and the stream cannot
silently disagree.

### Flags added

| flag | what it does |
|---|---|
| `--emit-error-marks` | selects the producer pipeline instead of a count-only one. Still prints the same `errorCount`. |
| `--print-positions` | with `--emit-error-marks`, prints the code-unit position of every set bit in the emitted bitstream. |

The default (scalar oracle) and `--simd` (optimized count-only) paths are unchanged and are
still what every existing benchmark and the 67-test suite exercise.

---

## 2. The count-only path is provably untouched

The two existing kernels were extracted from the old and new
`patches/utf16-simd-milestone.patch` and compared:

```
IDENTICAL   scalar UTF16ValidatorKernel (oracle)              (8133 chars)
IDENTICAL   UTF16ValidatorSIMDKernel (optimized count-only)   (12592 chars)
```

The only change to pre-existing code is one line of `utf16validatePipelineGen`
(`if (useSIMD)` became `} else if (useSIMD)`). Everything else is additive.

---

## 3. The marker rule is *not* the count-only rule — and why that matters

This is the substantive design point of the issue.

`UTF16ValidatorSIMDKernel` counts `mismatch[k] = isLow[k] XOR isHigh[k-1]`. That is **exact for
counting** but it registers a lone high surrogate at its **successor's** position. For the input
`D800 0041` it flags unit 1 — which is the letter `A`, a perfectly well-formed code unit. A
consumer told to look at unit 1 would be looking at the wrong code unit, and a repair pass would
corrupt the text while leaving the actual bad unit in place.

A position-accurate marker needs:

```
bad[k] = (isLow[k]  AND NOT isHigh[k-1])      // low surrogate with no high before it
      OR (isHigh[k] AND NOT isLow[k+1])       // high surrogate with no low after it
```

**The `k+1` term is the whole difficulty**: it needs a one-code-unit lookahead that the
count-only kernel does not need. This was predicted by issue #29 and is now confirmed in a real
kernel.

### How `LookAhead(2)` handles it

```cpp
{Binding{"byteStream", byteStream, FixedRate(2), {Linear(), LookAhead(2)}}}
```

The rule reads the high byte of code unit *k+1*, at byte offset `2k+3`. For the last code unit
of a segment that is up to **2 bytes** past the accessible extent — exactly `LookAhead(2)`.
The attribute's contract (`include/kernel/core/attributes.h:25-45`) gives us both halves of
what we need:

- **Valid and linearly contiguous through `N+n`.** On a **non-final** segment the lookahead
  bytes are the *real next segment's* bytes, so a valid surrogate pair straddling a segment
  boundary is correctly **not** marked.
- **Zero-filled on the final call.** A zero byte is not a low surrogate, so a **dangling high
  surrogate at EOF marks itself** — with no EOF special case at all. This is a genuine
  simplification over the count-only kernel, which has to add a `danglingHigh` term explicitly.

Only the **odd trailing byte** still needs EOF handling, because it has **no code unit** and
therefore no bit position. It is added to `errorCount` and never appears in the stream — so
`popcount(errorMarks) + oddTrailingByte == errorCount`, by construction.

Reading only **2 bytes** ahead (rather than a whole next pack) is what keeps the lookahead this
small: for the pairing check the vector only needs one byte from beyond the pack — the high byte
of the next pack's first code unit — which is inserted into a lane and shifted in with
`mvmd_dslli`.

### How `pendingHigh` is handled

Unchanged in spirit from the optimized count-only kernel:

- Within a segment, the previous pack's `isHi8` mask is carried as a **vector** and shifted in
  with `mvmd_dslli(8, isHi8, carry, 2)` (two byte lanes = one code unit). No scalar bit is
  extracted per pack.
- Across segments, a 1-bit `pendingHigh` `InternalScalar` carries the state, injected into the
  carry vector's last high-byte lane at entry and read back once per invocation.

The forced-segment-size tests (§5) exist specifically to stress this together with the
lookahead.

### Rates

`FixedRate(2)` on the byte input against `FixedRate(1)` on the marker output gives
`accessible(bytes) == 2 * writable(marks)`, so the pipeline computes the produced item count
itself (`floor(processed/2)` = the number of complete code units). No manual
`setProducedItemCount` is needed, including when the file ends on an odd byte. The final
partial marker block is built by a scalar tail that leaves the bits past the last code unit
**zero**, so a consumer reading whole blocks never sees a phantom marker.

### No `hsimd_signmask(8)` — the issue #38 lesson holds

The bytes→bits reduction weights the marker bytes (`simd_and` with a constant) and OR-folds each
64-bit lane after **one `mvmd_extract(64, …)`** — one lane extraction per 4 code units, instead
of `hsimd_signmask(8)`'s ~16 per 8. No AVX/SSE/NEON intrinsics are used anywhere; every vector
operation goes through Parabix/IDISA.

This is the same family of operations as issue #29's `vector_pack_reduce`, but deliberately the
**simple, obviously-correct** form rather than the fully optimized one: the prototype's
`hsimd_packh(16, …)` densify step would halve the extractions again. That optimization is left
on the table on purpose — see §8.

---

## 4. The consumer (minimal, prototype-only)

`ErrorMarkPositionKernel` reads the `errorMarks` bitstream and prints the code-unit index of
every set bit, using the **same `ctz` / reset-lowest-bit inner loop** as Parabix's
`TwoLevelScanKernel::strideLogic` (`CreateCountForwardZeroes` / `CreateResetLowestBit`,
`lib/kernel/scan/base.cpp:221`).

It is **not** a `TwoLevelScanKernel` subclass: it builds no maskHL and skips nothing. It exists
solely so the *contents* of the emitted bitstream can be diffed against the standalone
prototypes — which is what actually validates the stream, as opposed to just the count.

One thing worth recording: a kernel with no stream or scalar outputs is dead as far as the
pipeline is concerned and is **silently never scheduled**. It printed nothing until it was
marked `addAttribute(SideEffecting())` (`attributes.h:280-284`). That is a real trap for anyone
adding a sink kernel.

---

## 5. Correctness

### `./scripts/test_errormarks.sh` — 49/49 pass

Every fixture is checked **twice**, and at **four segment sizes** (default, 1, 13, 64 — which
force the cross-segment `pendingHigh` carry and the lookahead region):

1. **count** — `--emit-error-marks` must report the same `errorCount` as the scalar oracle.
2. **stream** — the printed positions must be **identical** to
   `benchmarks/llmask_reference.py --positions`, which derives positions straight from the
   definition with no LLmask, no maskHL and no Parabix.

Coverage: valid BMP, valid surrogate pair, lone high, lone low, reversed pair, two adjacent
highs, high at position 0, dangling high at EOF, empty file, odd trailing byte (alone, with a
lone high, and after a dangling high), multilingual text, **valid and malformed surrogate pairs
straddling code units 7/8/63/64/65/127/128/129/255/256/257/511/512/513** (the 64-code-unit group
and the 128/256/512 block and pack boundaries), a dangling high as the last of 64/128/256 units,
and 5 randomized 900-unit inputs.

### 32 MiB differential against the issue #31 prototype

```
kernel positions : 2208
prototype        : 2208
PASS: identical position lists on 32 MiB
```

The Parabix kernel's emitted bitstream and the standalone prototype's two-level scan agree on
**every one of 2208 error positions**, byte for byte.

### `./scripts/test_utf16validate.sh` — 67/67 pass

The count-only regression gate is unaffected, from a **clean-room rebuild**: `.deps/parabix` was
reset to the pinned base commit `f0369dd`, `patches/utf16-simd-milestone.patch` was reapplied
(it applies cleanly), and the tool was rebuilt from scratch.

---

## 6. Comparison with the standalone prototypes

| | issue #29–#31 prototypes | this kernel |
|---|---|---|
| where | standalone C++ (`benchmarks/*.cpp`) | a real Parabix `MultiBlockKernel` |
| bitstream | a `std::vector<uint64_t>` | a real `StreamSet(1)` in the pipeline |
| lookahead | 32 bytes of buffer padding | the `LookAhead(2)` input attribute |
| segment carry | none (whole file in memory) | `pendingHigh` `InternalScalar` across segments |
| reduction | `hsimd_packh` + OR-fold (1 extract / 8 units) | weighted OR-fold (1 extract / 4 units) |
| positions | identical (verified on 32 MiB) | identical (verified on 32 MiB) |

The prototypes' *semantics* transferred to the kernel exactly. The prototypes' *throughput*
figures did not transfer and were never expected to: they measured an in-memory buffer with no
I/O and no pipeline.

---

## 7. Performance

Whole-process wall time, `-thread-num=1`, median of 9, 2 warmups. The overhead-adjusted column
subtracts the measured fixed startup (JIT + pipeline init), following
`docs/benchmark_methodology.md`.

Fixed overhead: `--simd` 0.0177 s, `--emit-error-marks` 0.0174 s.

| file | mode | total s | raw MiB/s | **overhead-adjusted MiB/s** |
|---|---|---|---|---|
| valid 32 MiB | `--simd` (count only) | 0.0242 | 1323.7 | **4919.8** |
| valid 32 MiB | `--emit-error-marks` | 0.0244 | 1311.8 | **4585.6** |
| valid 64 MiB | `--simd` (count only) | 0.0301 | 2123.3 | **5131.4** |
| valid 64 MiB | `--emit-error-marks` | 0.0306 | 2091.5 | **4854.2** |
| valid 128 MiB | `--simd` (count only) | 0.0416 | 3074.0 | **5340.2** |
| valid 128 MiB | `--emit-error-marks` | 0.0433 | 2954.5 | **4940.8** |

**Emitting the full errorMarks bitstream costs about 5–8% per byte** over counting only
(0.93×, 0.95×, 0.93× of the count-only rate). On raw wall time the gap is only 1–4%, because the
~0.018 s fixed startup still dominates at these sizes — **the raw column cannot resolve the
kernel cost and should not be quoted for it.**

That is a cheaper result than the project's earlier analysis feared. Issue #27 found the
validator close to bandwidth-bound and issue #30 warned that always writing a marker stream might
make the repair-capable path *slower on clean text*. It is slower — but only by a few percent,
not by a factor.

### Count-only no-regression check

`python3 benchmarks/run_utf16_benchmark.py --datasets default --sizes-mb 32,64` against the
figures recorded in `docs/simd_regression_investigation.md` (issue #38):

| mode | 32 MiB (#38 → now) | 64 MiB (#38 → now) |
|---|---|---|
| `scalar` | 993.9 → **1000.1** | 1408.5 → **1416.1** |
| `parabix_simd_t1` | 1285.4 → **1313.5** | 2084.9 → **2108.5** |

Both within ~2% — no regression, as expected given the kernels are byte-identical.

---

## 8. Limitations and next steps

1. **The reduction is not fully optimized.** It extracts one 64-bit lane per 4 code units; issue
   #29's `hsimd_packh(16, …)` densify step would halve that. The simple form was chosen to keep
   the first real kernel obviously correct. Optimizing it is the obvious follow-up and would
   likely shrink the 5–8% further.
2. **`--print-positions` is a debug facility, not a design.** It uses `CallPrintInt` (hex, one
   line per error) and under multiple pipeline threads segments may interleave, so callers must
   sort. It exists to validate the stream, not to be a product.
3. **No maskHL in a kernel, and no `TwoLevelScanKernel` subclass.** The consumer here scans
   every block; it does not skip clean regions. The measured skip rates from issue #30 and the
   near-free scan cost from issue #31 say that is worth doing, but it is not done.
4. **No repair, and the repair policy is still unwritten.** The reversed-pair ambiguity from
   issue #28 is unchanged: the stream says *where*, not *what kind*, so a repair pass must
   re-read the code unit at the flagged position (cheap — it is already there).
5. **UTF-16LE only.** The marker rule selects the high byte by memory position, so UTF-16BE
   remains the same one-line change it always was, and is still not done.
6. **The 5–8% figure is single-threaded whole-process timing on one machine.** It has not been
   measured under the multi-threaded pipeline, and the extra output stream's effect on thread
   scaling (issue #27) has not been re-analysed.

### Recommended next step

Subclass `TwoLevelScanKernel` over `errorMarks` to locate errors while **skipping clean
regions** — the producer is now in place and validated, so the consumer is the only missing
piece, and issue #31 measured it as effectively free. Only after that, write down a repair
policy.

---

## 9. Reproduction

```bash
./scripts/setup_parabix.sh          # applies patches/utf16-simd-milestone.patch, builds
./scripts/test_utf16validate.sh     # 67/67 -- the count-only regression gate
./scripts/test_errormarks.sh        # 49/49 -- the new producer, count + stream, 4 segment sizes

.deps/parabix/build/bin/utf16validate --emit-error-marks FILE
.deps/parabix/build/bin/utf16validate --emit-error-marks --print-positions -thread-num=1 FILE
```
