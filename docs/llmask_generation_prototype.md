# LLmask generation: prototype and measurements (issue #29)

**Status: prototype and measurement only.** Nothing here is a Parabix kernel. The validator
is unchanged, there is no maskHL aggregation, no `TwoLevelScanKernel` subclass, and no
repair. This note records what was built, what it measured, and what that means for the
next issue.

Issue #28 (`docs/two_level_scan_design.md`) established that Parabix already ships the
two-level scan (`TwoLevelScanKernel`), so the real work is **producing the LLmask
bitstream** — and it flagged one specific danger: the vector→bitstream reduction is a
movemask, and issue #38 proved that IDISA's `hsimd_signmask(8, …)` has no AArch64 lowering
and made our SIMD path *slower than scalar*. This issue prototypes the reduction and
measures it.

Scope: UTF-16LE, little-endian host, as everywhere else in this project.

---

## 1. What an LLmask is here

An LLmask is a `uint64_t` covering **64 consecutive UTF-16 code units**. Bit *i* is set iff
code unit *i* of that group **is itself ill-formed**:

```
bad[k] = (isLow[k]  && !isHigh[k-1])      // low surrogate with no high before it
      || (isHigh[k] && !isLow[k+1])       // high surrogate with no low after it
```

with `isHigh[-1] = pendingHigh` (false at start of file) and unit `units` treated as *not a
low surrogate*, so a high surrogate at EOF marks itself.

An **odd trailing byte is not a code unit**, so it cannot be represented in a code-unit-indexed
mask at all. It is reported separately, exactly as the validator handles it at EOF.

### 1a. This is *not* the validator's formula — and that matters

The validator computes `mismatch[k] = isLow[k] XOR isHigh[k-1]`, which is purely backward-looking.
That is correct for **counting**, but it attributes a lone-high error to the **successor**
position *k*, not to the high surrogate at *k−1*. For `D800 0041` the validator's formulation
would mark unit 1 — which is the letter `A`, a perfectly well-formed code unit. A repair kernel
told to fix unit 1 would corrupt the text and leave the actual bad unit in place.

So the LLmask needs the rule above, which requires looking at unit *k+1*. **Consequence: LLmask
generation needs a one-code-unit lookahead that the current validator does not need.** In a
Parabix kernel this is the `LookAhead(2)` input attribute (`include/kernel/core/attributes.h`),
which guarantees the buffer is readable through `N+k` and is zero-filled on the final segment —
exactly the EOF semantics the rule wants. The prototype gets the same guarantee by allocating
its input buffer with 32 zero bytes of padding.

The counts still agree, which is what makes the cross-check below possible:

```
popcount(all LLmasks) + oddTrailingByte  ==  utf16validate errorCount
```

---

## 2. What was built

| File | What it is |
|---|---|
| `benchmarks/prototype_llmask_generation.cpp` | standalone prototype: 4 LLmask strategies, self-test, differential dump, benchmark |
| `benchmarks/llmask_reference.py` | independent Python reference, written from the definition, not from the C++ algorithm |
| `scripts/run_llmask_prototype.sh` | build (to a temp dir) → self-test → differential → validator cross-check → benchmark |

The prototype is **not** wired into the Parabix pipeline and no binary is ever written into
the working tree.

### The four strategies

All four produce **byte-for-byte identical LLmasks**. Only the vector→bitstream reduction
differs; the classification work (`(byte & 0xFC) == 0xD8 / 0xDC` at fw=8) is the same in all
of them.

| strategy | reduction | why it is here |
|---|---|---|
| `scalar` | one high-byte load + two compares per code unit, shift/or into a `uint64_t` | the reference, and the thing we would actually ship if vectors lose |
| `signmask_scalarized` | movemask realised as one real lane extraction per lane | **models IDISA's `hsimd_signmask(8, …)` on AArch64**: there is no ARM override in `lib/idisa/idisa_arm_builder.cpp`, so the generic `<16 x i1> → i16` bitcast scalarises. Cautionary baseline |
| `signmask_optimized` | the same movemask, left free for clang, which emits a vector shift/OR bit-assembly | a **favourable** stand-in — better than IDISA emits today — so the conclusion does not rest on IDISA's poor lowering |
| `vector_pack_reduce` | densify markers with an odd-lane pack, then a bit-weight AND + 64-bit OR-fold | the proposal (see §4) |

---

## 3. Correctness

`./scripts/run_llmask_prototype.sh`

- **15/15 self-test cases pass**, and all four strategies agree on every one: valid BMP, valid
  surrogate pair, lone high, lone low, reversed pair, valid pair crossing the 64-code-unit
  boundary, malformed crossing the 64-code-unit boundary, lone low at the start of LLmask 1,
  dangling high at EOF on a group boundary, odd trailing byte, odd trailing byte + lone high,
  multilingual valid text, empty input, lone high at position 0, and a 300-unit run spanning
  several groups.
- **Differential vs `benchmarks/llmask_reference.py`**: all four strategies produce identical
  LLmask dumps to the independent Python reference on a 32 MiB valid file, a 32 MiB malformed
  file (0.01% `random_mix`), and an odd-length file. 12/12 PASS.
- **Cross-check against the validator** (which is itself covered by the 67-test suite):

  | file | LLmask bits | odd byte | total | `utf16validate` errorCount |
  |---|---|---|---|---|
  | `valid_utf16le_mixed_multilingual_32MiB.bin` | 0 | 0 | 0 | 0 |
  | `malformed_…_random_mix_err0.01_32MiB.bin` | 2208 | 0 | 2208 | **2208** |
  | odd-length truncation of the above | 0 | 1 | 1 | **1** |

The 64-code-unit boundary cases are the ones worth calling out. `malformed crossing 64-unit
boundary` puts a high surrogate at unit 63 and a BMP character at unit 64: **the error belongs
to unit 63, in LLmask 0, but it cannot be decided until unit 64, which lives in LLmask 1.** That
is the lookahead requirement from §1a, and it is now covered by a test.

---

## 4. Benchmark results

Apple M-series (AArch64), `c++ -O3 -std=c++17`, 2 warmups, 7 repetitions, **median**.
Generated `mixed_multilingual` datasets. Timing covers **LLmask generation on an in-memory
buffer only** — no file I/O, no pipeline, single-threaded.

**32 MiB valid:**

| strategy | median MiB/s | median ms | vs scalar |
|---|---|---|---|
| `scalar` | 1777.9 | 18.00 | 1.00× |
| `signmask_scalarized` | 1491.8 | 21.45 | **0.84×** |
| `signmask_optimized` | 4462.1 | 7.17 | 2.51× |
| `vector_pack_reduce` | **10803.8** | **2.96** | **6.08×** |

**32 MiB malformed (0.01% random_mix, 2208 errors):**

| strategy | median MiB/s | median ms | vs scalar |
|---|---|---|---|
| `scalar` | 1769.8 | 18.08 | 1.00× |
| `signmask_scalarized` | 1491.2 | 21.46 | **0.84×** |
| `signmask_optimized` | 4443.0 | 7.20 | 2.51× |
| `vector_pack_reduce` | **10813.6** | **2.96** | **6.11×** |

**128 MiB valid** (checked separately, to rule out a cache artefact): scalar 1771.6,
`signmask_scalarized` 1511.2, `signmask_optimized` 4546.2, `vector_pack_reduce` 10908.5 MiB/s —
the same ordering and essentially the same ratios, so the result is not an in-cache effect.

Error density makes no measurable difference (within 0.5%), which is expected: **mask
generation is not sparse.** Every code unit is classified whether or not it is bad. Only the
later *scan* is sparse.

### What the compiler actually emitted

Instruction counts from `objdump -d` of each generator (whole function, `-O3`):

| function | instrs | `umov.b` (byte-lane extract) | `bfi` | `fmov` (64-bit lane extract) | `uzp2.16b` (pack) | `cmeq.16b` | any SIMD? |
|---|---|---|---|---|---|---|---|
| `llmask_scalar` | 41 | 0 | 0 | 0 | 0 | 0 | **no** |
| `llmask_signmask_scalarized` | 285 | 2\* | 0 | 1 | 0 | 6 | yes |
| `llmask_signmask_optimized` | 292 | 10 | 4 | 1 | 0 | 6 | yes |
| `llmask_vector_pack_reduce` | 386 | 2 | 0 | 5 | **16** | 20 | yes |

\* `signmask_scalarized`'s lane loop is deliberately left **rolled**, so its *static* count is 2
while its *dynamic* count is 16 extractions per call. Static counts understate it.

Two things are confirmed here rather than assumed:

1. **`scalar` really is scalar** — zero SIMD instructions. It is not secretly being
   auto-vectorised, so it is an honest baseline.
2. **`vector_pack_reduce` really does use the pack** — 16 `uzp2.16b`, and it has essentially no
   byte-lane extraction (2 `umov.b`); its reduction extracts **64-bit** lanes (`fmov`) instead.
   That is the whole point: it trades 32 byte-lane extractions per 16 code units for 2 wide ones.

---

## 5. The `vector_pack_reduce` design, and why every step is IDISA-expressible

This is the part that matters for the next issue. Per 2 packs (32 bytes = 16 code units):

| step | prototype | IDISA primitive | ARM support |
|---|---|---|---|
| classify at fw=8 | `(bytes & 0xFC) == 0xD8 / 0xDC` | `simd_and`, `simd_eq` (fw=8) | generic — already used by the validator |
| previous unit (`k−1`) | 2-lane shuffle from the previous pack | `mvmd_dslli(8, isHigh, prevPackHigh, 2)` | already used by the validator (issue #38) |
| next unit (`k+1`) | 2-lane shuffle from the next pack | same shuffle family, fed by a `LookAhead(2)` input | **new requirement** — see §1a |
| **densify markers** | take the odd (high) byte of each 16-bit field from two vectors | **`hsimd_packh(16, a, b)`** | **overridden for ARM** → `uzp2` (`lib/idisa/idisa_arm_builder.cpp:158`) |
| weight | AND with `{1,2,4,…,128, 1,2,4,…,128}` | `simd_and` with a constant | generic |
| **reduce to bits** | extract two 64-bit lanes, OR-fold each to a byte | **`mvmd_extract(64, v, 0/1)`** + scalar shifts/ORs | one `umov`/`fmov` each — *not* the 16-lane scalarisation |

**Nothing new needs to be added to IDISA.** The reduction avoids `hsimd_signmask` entirely by
(a) using the pack that ARM *does* override to get one dense marker byte per code unit, and
(b) extracting **64-bit** lanes instead of byte lanes. `hsimd_packh`/`packl` at `fw ≥ 16` hit the
ARM override; the 64-bit extract is a single instruction on both x86 and AArch64.

This is also the same family of operations `S2PKernel` uses to turn bytes into bit streams
(`lib/kernel/basis/s2p_kernel.cpp:26` — `s2p_step` is a `hsimd_packh`/`hsimd_packl` pair), which
is Parabix's most-exercised kernel and runs on ARM. The idiom is not exotic.

---

## 6. Answers to the questions issue #29 asked

**Is scalar LLmask generation competitive?**
**As a fallback, yes; as the design, no.** At ~1780 MiB/s it is only 0.4× the vector path's
~10800 MiB/s, and it is beaten 6× on the same data. But it is *not* embarrassing: it beats the
IDISA-signmask model (~1490 MiB/s) and it is trivially correct and endian-agnostic. If the
vector reduction turns out to be hard to express in a real kernel, shipping the scalar LLmask
generator would not be a disaster. It should not be the first choice.

**Does vector reduction remain an open problem?**
**No — the reduction itself is solved, in the prototype.** `vector_pack_reduce` is 6.1× scalar,
2.4× even the compiler-optimised movemask, and every step maps onto an IDISA primitive that
already exists and is ARM-supported. Issue #28 listed the vector→bitstream step as *the* open
problem; this issue closes it **at the algorithm level**.

What is *not* yet closed is the **kernel integration**, and that is now the real risk (§7).

**Does this support moving to maskHL aggregation?**
**Yes.** The prerequisite for a `TwoLevelScanKernel` subclass is a correct, cheap,
code-unit-indexed error bitstream, and we now have a validated algorithm for one that is faster
than scalar rather than slower. The next issue can proceed to emitting `errorMarks` from a real
kernel.

---

## 7. Risks, assumptions and open questions

1. **These are microbenchmark numbers, not kernel numbers.** They measure LLmask generation on
   an in-memory buffer with no I/O, no Parabix pipeline, no segmentation, and no threading. They
   are **not comparable** to the validator throughputs in `docs/simd_regression_investigation.md`
   (which include ~19 ms of fixed startup plus file I/O). They tell us the *relative* cost of the
   four reductions and nothing more. The in-kernel figure must be measured separately.
2. **The prototype's vector code is C++ (GNU vector extensions), not IDISA.** The mapping in §5
   is argued from the IDISA sources, and it is a strong argument, but **it is an argument, not a
   measurement**. LLVM may lower a real `hsimd_packh(16, …)` differently from the
   `__builtin_shufflevector` clang chose here. The next issue must re-measure in the kernel and
   must not assume the 6× carries over.
3. **The signmask strategies are C++ *models* of IDISA's primitive, not IDISA itself.** The
   authoritative statement about what `hsimd_signmask(8, …)` really costs is issue #38's in-kernel
   measurement (SIMD at 0.66× scalar), not `signmask_scalarized`'s 0.84×. The model is included
   because it reproduces the *direction* of that finding independently, not because it replaces it.
4. **The new `LookAhead(2)` requirement is untested in a kernel.** The prototype gets its
   lookahead from buffer padding. A real kernel needs the `LookAhead(2)` attribute, and how that
   interacts with the existing `pendingHigh` cross-segment carry has not been tried. This is the
   most likely place for the integration to go wrong.
5. **Mask generation is not sparse, and it is an extra output stream.** The two-level scan's win
   is on the *consumer* side; the producer pays full cost on every byte, plus a write of 1 bit per
   code unit. Issue #27 found the validator is close to bandwidth-bound, so the repair-capable path
   may be **slower on clean text** than the current count-only validator. Measured, not assumed —
   and not measured yet.
6. **The 64-bit OR-fold assumes lane 0 of the vector sits at the lowest address** (true on
   little-endian hosts, which is all we target). The fold itself is order-independent; only the
   split into two halves depends on lane layout.
7. **The reversed-pair question from issue #28 is still open.** The LLmask marks *both* units of a
   reversed pair, which is right for counting, but a repair kernel still cannot tell a lone low
   from the low half of a reversed pair without re-reading the code units at the flagged positions.

---

## 8. Reproduction

```bash
./scripts/run_llmask_prototype.sh                    # everything: self-test, differential,
                                                     # validator cross-check, benchmark
LLMASK_SIZES_MB=128 ./scripts/run_llmask_prototype.sh
```

The script compiles the prototype into a temporary directory, generates its datasets into the
git-ignored `benchmarks/data/`, writes no CSV and nothing to `results/`, and leaves no binary in
the working tree. The validator cross-check is skipped with a notice if
`.deps/parabix/build/bin/utf16validate` has not been built.

---

## 9. Recommended next step

Emit `errorMarks : StreamSet<i1>[1]` from a real Parabix kernel using the §5 mapping, behind a
flag so the count-only fast path stays intact and existing benchmarks stay comparable — then
**re-measure in the kernel** before trusting the 6×. Only after that, subclass
`TwoLevelScanKernel` for maskHL aggregation and error location.
