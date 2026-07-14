# Two-level scan: design study for UTF-16 error location and repair

**Status: study/design artifact only.** Nothing described here is implemented. There is no
LLmask generator, no maskHL aggregation, and no repair in this repository. This note exists
so that the later implementation issues start from a design that is grounded in Parabix's
actual scan infrastructure rather than from a sketch.

Scope: UTF-16LE, little-endian hosts, as everywhere else in this project.

---

## 1. Parabix already has this: `TwoLevelScanKernel`

The professor's suggested strategy is not something we need to invent — Parabix ships an
abstract implementation of exactly this pattern.

- Declared: `include/kernel/scan/base.h`
- Implemented: `lib/kernel/scan/base.cpp` (`TwoLevelScanKernel`, lines ~212–395)
- Real in-tree subclass: `InsertionSpreadMaskKernel`
  (`include/kernel/streamutils/pdep_kernel.h:99`, `lib/kernel/streamutils/pdep_kernel.cpp:1283`)

`TwoLevelScanKernel` extends `MultiStrideKernel`. It consumes a **scan bitstream** (one bit
per position) plus a configurable `scanWordWidth`, and it drives two nested sparse loops.

### 1a. Building the high-level mask — `generateIndexComputation` (base.cpp:348)

For every block in the stride:

```cpp
Value * s            = b.loadInputStreamBlock(mScanStreamName, i, inputBlockIndex);
Value * anyBitInField = b.simd_any(mScanWordWidth, s);              // per scanword: any bit set?
Value * indexMask     = b.CreateZExt(b.hsimd_signmask(mScanWordWidth, anyBitInField), sizeTy);
masks[i] = b.CreateOr(maskPhi[i], b.CreateShl(indexMask, wordCounter));
```

`simd_any(64, s)` marks each 64-bit scanword that contains at least one set bit;
`hsimd_signmask(64, …)` collapses that to **one bit per scanword**. Shifted and OR'd across
the stride's blocks, this produces a `size_t` in which **bit *w* = "scanword *w* is dirty"**.
That is precisely the professor's **maskHL**.

### 1b. The sparse double loop — `strideLogic` (base.cpp:221)

```cpp
metaMask = OR of the per-stream masks
b.CreateLikelyCondBr(metaMask != 0, metaMaskLoop, strideLogicDone);   // whole stride skipped if clean

// outer loop: jump to the next DIRTY scanword
wordsToSkip = CreateCountForwardZeroes(remainingMaskPhi);            // cttz on maskHL
... load that scanword (the LLmask) ...
wordPrologueLogic(b, wordBasePosition, indexWord, loopVars);         // subclass hook

  // inner loop: jump to the next SET BIT inside the scanword
  indexInWord = CreateCountForwardZeroes(remainingWordPhi);          // cttz on LLmask
  itemPos     = wordBasePosition + indexInWord;
  generateProcessingLogic(b, itemPos, loopVars);                     // subclass hook: per-error work
  scanWordNext = CreateResetLowestBit(remainingWordPhi);             // clear that bit, continue

nextMask = CreateResetLowestBit(remainingMaskPhi);                   // next dirty scanword
```

The two primitives that make it sparse are `CreateCountForwardZeroes` (count-trailing-zeros)
and `CreateResetLowestBit` (`x & (x-1)`), both in `include/codegen/CBuilder.h:335,339`.

**The key property:** if `metaMask == 0` the entire stride is skipped with a single branch
(`CreateLikelyCondBr` even tells LLVM that the clean case is the likely one). Work is done
*only* at positions that actually have a bit set. On valid UTF-16 — the overwhelmingly common
case — the scan costs one compare and one branch per stride.

### 1c. Stride geometry falls out to exactly 64 × 64

`maxScanBlocks` (base.cpp:205) sizes the stride so the index mask fits in a `size_t`:

```
scanWordsPerBlock = bitBlockWidth / scanWordWidth = 128 / 64 = 2
maxScanBlocks     = 64 / 2 = 32 blocks per stride
=> 32 blocks x 128 bits = 4096 positions = 64 scanwords x 64 bits
```

So with `scanWordWidth = 64` on our 128-bit NEON target, one stride covers **64 LLmasks of 64
positions each, indexed by a 64-bit maskHL** — the professor's design, arrived at by the
framework's own constraint. `InsertionSpreadMaskKernel` also uses `ScanWordWidth = 64`.

### 1d. Subclass hooks and carried state

A subclass supplies `wordPrologueLogic` (once per dirty scanword) and
`generateProcessingLogic` (once per set bit), and declares `LoopVar`s that thread state
through both loops. `InsertionSpreadMaskKernel` uses three:

```cpp
{LoopVar("bn_processed", sizeTy), LoopVar("sm_produced", sizeTy),
 LoopVar("sm_pending", ts.getIntNTy(ScanWordWidth))}
```

and declares a **variable-rate output**:

```cpp
mOutputStreamSets.push_back(Binding{"spread_mask", spread_mask,
                                    BoundedRate(1, mExpansionWidth), EmptyWriteOverflow()});
```

This matters for us: repair produces a *different amount* of output than input, and this is the
in-tree template showing how that is expressed.

---

## 2. What this changes for our project: counts vs. positions

This is the central gap, and it is bigger than it first appears.

| | Current SIMD validator | Two-level scan needs |
|---|---|---|
| Output | `errorCount = N` (a scalar) | the **position** of every ill-formed code unit |
| Per pack | builds a mismatch **vector**, `bitblock_popcount`s it, **throws the positions away** | a **bitstream**: 1 bit per code unit, set where malformed |
| Consumer | a scalar accumulator | `TwoLevelScanKernel`, which requires a `StreamSet<i1>[1]` scan stream |

Repair fundamentally needs positions, not counts. So the first implementation issue is **not**
the scan kernel — Parabix already has that — it is **producing the LLmask bitstream**. The scan
kernel is essentially free to adopt once the marker stream exists.

Historical note: an early version of our SIMD kernel *did* emit a `mismatchMarks : StreamSet(1)`
output. It was removed for an unrelated pipeline reason (a kernel→kernel scalar dependency could
not be expressed). Reinstating a marker stream is therefore known to be feasible.

---

## 3. Where the LLmask could come from — and the Issue #38 trap

> **Update (issue #29).** This section framed the vector→bitstream reduction as the open
> problem. It has since been prototyped and measured — see
> [`llmask_generation_prototype.md`](llmask_generation_prototype.md). Option 2 below (the
> pack-based reduction) won: **6.1× scalar**, with no `hsimd_signmask` anywhere. Option 3
> (scalar) is a viable fallback but is 6× slower. Option 1 (`hsimd_signmask(8)`) is confirmed
> as the trap it was suspected to be. The prototype also found something this section missed:
> the LLmask rule needs a **one-code-unit lookahead**, because the validator's backward-only
> XOR marks the *successor* of a lone high surrogate rather than the surrogate itself — the
> wrong position for repair. The analysis below is kept as written for the record.

Our mismatch is currently computed as a **byte vector**: `0x01` at the high-byte lane of each
ill-formed code unit (see `docs/SIMD_BYTE_ORIENTED_VALIDATOR.md`). To feed a scan kernel we must
compress 16 bytes → 8 bits (one bit per code unit). That compression is exactly a movemask.

**And that is the trap.** Issue #38 (`docs/simd_regression_investigation.md`) found that
`hsimd_signmask(8, …)` has **no AArch64 lowering in IDISA** and expands into ~16 lane extracts
plus scalar bit assembly per call — it made our SIMD path *slower than scalar*. Naively producing
the LLmask with `hsimd_signmask(8, …)` would reintroduce the exact regression we just removed.

Four candidate sources, none yet implemented:

1. **`hsimd_signmask(8, …)` on the mismatch vector.** Simplest to write. **Likely pathological on
   ARM** for the reason above. Do not start here without measuring.
2. **A pack-tree reduction (`hsimd_packh`/`hsimd_packl`).** These *are* overridden in
   `lib/idisa/idisa_arm_builder.cpp` (packl:145, packh:158, packus:171), unlike `hsimd_signmask`.
   Narrowing bytes → bits with a pack tree is the idiom `S2PKernel` already uses, so it is both
   portable and ARM-supported. **This is the most promising option** and should be prototyped first.
3. **A scalar LLmask generator.** Walk 64 code units and build a `uint64_t` with shifts/ORs. Our
   scalar validator already runs at ~2.3 GB/s (overhead-adjusted), so a scalar LLmask builder may
   be *competitive*, and it is trivially correct. Worth measuring as the baseline before optimizing.
4. **Hybrid**: keep the vector classification (which is cheap and already correct), and only
   reduce to a bitstream — i.e. the question is purely "how do we do the vector→bitstream step",
   not "how do we classify".

Note the scan kernel's *own* use of signmask is at `fw = scanWordWidth = 64`, which on a 128-bit
block is only **2 lanes** — a completely different cost class from `fw=8`'s 16 lanes. So
`TwoLevelScanKernel`'s index computation is not expected to suffer the #38 problem. The danger is
confined to producing the marker stream.

---

## 4. `pendingHigh` and LLmask boundaries

Surrogate pairing is a **cross-position dependency**, so LLmask groups are not independent.

- `mismatch[k] = isLow[k] XOR isHigh[k-1]` — code unit *k*'s verdict depends on *k−1*.
- A valid pair may **straddle** a 64-code-unit boundary: high at unit 63 of group *g*, low at unit
  0 of group *g+1*. Neither is an error, but computing unit 0 of group *g+1* requires the
  `isHigh` of the last unit of group *g*.
- A high surrogate at the **very last** unit of a group is not yet an error — it becomes one only
  if the next unit is not a low surrogate (or if the stream ends).

Our current kernel already solves this correctly: it carries the previous pack's `isHi8` **as a
vector** and shifts it in with `mvmd_dslli(8, …, 2)`, and it threads a `pendingHigh` internal
scalar across packs, blocks and segments. The forced-segment tests (`-segment-size=1,13,64`)
exist specifically to stress this.

**Design consequence:** the LLmask **producer** must own the carry — the marker bitstream it emits
must already be correct at group boundaries. If it is, then `TwoLevelScanKernel` sees a plain
bitstream and needs no surrogate knowledge at all: a set bit simply means "this code unit is
ill-formed", and the scan is oblivious to *why*. This is a clean separation and is the design we
should aim for. It also means the repair kernel must not re-derive pairing from the mask alone —
see the open question in §7.

---

## 5. Possible repair strategy (later issue — not designed in detail here)

Sketch only:

- **Clean groups** (`maskHL` bit clear): copy through unchanged. This is the fast path and should
  dominate on real text.
- **Dirty groups**: scan set bits with the inner `cttz`/`ResetLowestBit` loop and rewrite only
  those positions.
- **Replacement policy must be defined before implementation.** U+FFFD is the conventional choice,
  but the policy must state explicitly what happens for:
  - a **lone high** surrogate → replace the high with U+FFFD;
  - a **lone low** surrogate → replace the low with U+FFFD;
  - a **reversed pair** (low then high) → this is *two* errors in our counting; does it become two
    U+FFFDs, or one?
  - an **odd trailing byte** → the final byte is not a whole code unit; is it dropped, or padded?
    This one **cannot be expressed as a bit in a code-unit-indexed LLmask at all** (there is no
    code unit to mark), so it must be handled outside the scan.

Output is variable-rate if any policy drops or inserts units; `InsertionSpreadMaskKernel`'s
`BoundedRate(1, N) + EmptyWriteOverflow()` binding is the template.

---

## 6. Pseudocode

```
# ---- Stage 1: LLmask producer (NOT IMPLEMENTED; the real work) ----
# Emits a code-unit-indexed error bitstream. Owns the surrogate carry.
kernel Utf16ErrorMarks(byteStream) -> errorMarks : StreamSet<i1>[1]
    carry = pendingHigh            # from internal scalar, threaded across segments
    for each pack of BYTES_PER_PACK bytes:
        isHi8    = (bytes & 0xFC) == 0xD8        # vector, fw=8
        isLo8    = (bytes & 0xFC) == 0xDC
        prevHi   = mvmd_dslli(8, isHi8, prevPackHi, 2)     # carry shifted in
        mismatch = (isLo8 XOR prevHi) & ODD_ONE_MASK       # 1 per bad code unit
        emit_bits(mismatch)        # <-- vector -> bitstream. THE open problem (section 3)
        prevPackHi = isHi8
    store pendingHigh = isHi8[last high-byte lane]

# ---- Stage 2: two-level scan  (Parabix ALREADY provides this) ----
class Utf16ErrorScan : TwoLevelScanKernel(scanWordWidth = 64, scanStream = "errorMarks")

    # framework builds maskHL for us:
    #   anyBit  = simd_any(64, block)
    #   maskHL |= hsimd_signmask(64, anyBit) << wordCounter      # bit w = LLmask w is dirty

    strideLogic:                                   # provided by the base class
        if maskHL == 0: skip whole stride          # <-- the win: clean text costs ~nothing
        while maskHL != 0:
            w        = cttz(maskHL)                # next dirty group
            LLmask   = load scanword w             # 64 code units
            wordPrologueLogic(basePos(w), LLmask)  # our hook
            while LLmask != 0:
                i   = cttz(LLmask)                 # next bad code unit in the group
                pos = basePos(w) + i
                generateProcessingLogic(pos)       # our hook: record / repair position `pos`
                LLmask = LLmask & (LLmask - 1)     # reset lowest bit
            maskHL = maskHL & (maskHL - 1)         # reset lowest bit
```

---

## 7. Risks and open questions

1. **Producing the LLmask is the whole problem, and it is the one that can undo Issue #38.**
   A vector→bitstream reduction is a movemask; `hsimd_signmask(8, …)` is unimplemented for NEON.
   *Open:* is a `hsimd_packh`/`packl` tree (ARM-overridden) cheap enough? **Prototype and measure
   before committing to a design.**
2. **A scalar LLmask generator may simply win.** Our scalar validator reaches ~2.3 GB/s
   overhead-adjusted. A scalar loop producing a `uint64_t` per 64 code units is simple and
   obviously correct. It must be measured as the baseline; if it is competitive, the vector path
   may not be worth the complexity.
3. **Cost is only paid on dirty data — but the LLmask must be built for *all* data.** The scan is
   sparse; the mask *generation* is not. So the steady-state cost of repair-capable validation is
   the cost of always emitting the bitstream, even for perfectly valid input. This could make the
   repair-capable path slower than the current count-only validator on clean text. That trade must
   be measured, not assumed.
4. **Memory bandwidth.** The marker stream is an extra output (1 bit per code unit ≈ 1/16 of the
   input). Issue #27 found the validator is likely bandwidth-bound; adding a write may cost more
   than the arithmetic saves.
5. **Multithreading and boundary state.** `pendingHigh` is a serial cross-segment dependency.
   Issue #27 measured thread scaling as essentially flat, and #38 improved it as a side effect.
   How the carry interacts with `MultiStrideKernel`'s stride loop is **not yet understood** and
   needs study before implementation.
6. **Reversed pairs are two errors but one construct.** The LLmask says *where* bits are set, not
   *what kind* of error occurred. A repair kernel that only sees the mask cannot distinguish a lone
   low from the low half of a reversed pair. It may need to re-inspect the code units at the
   flagged positions (cheap — it is already at that position), or the producer may need to emit
   more than one marker stream. **Open.**
7. **The odd trailing byte has no code-unit position** and therefore cannot be represented in a
   code-unit-indexed LLmask. It must be handled as an EOF special case, exactly as the current
   validator does.
8. **`MultiStrideKernel` internals are only partly understood.** We have read `strideLogic` and
   `generateIndexComputation` in detail; the surrounding `MultiStrideKernel::generateMultiBlockLogic`
   and its `LoopVar` initial-value machinery were read but not fully traced. Documented here as an
   open question rather than guessed at.

---

## 8. What was inspected (reproduction notes)

All paths relative to the git-ignored `.deps/parabix/` checkout, which
`scripts/setup_parabix.sh` regenerates from the pinned commit.

| File | What we took from it |
|---|---|
| `include/kernel/scan/base.h` | `TwoLevelScanKernel` / `MultiStrideKernel` / `SingleStreamScanKernelTemplate` class declarations, subclass hooks, `LoopVar` |
| `lib/kernel/scan/base.cpp:205` | `maxScanBlocks` — the stride geometry that yields 64 scanwords × 64 bits |
| `lib/kernel/scan/base.cpp:212–347` | `TwoLevelScanKernel::strideLogic` — the sparse double loop, `metaMask != 0` skip |
| `lib/kernel/scan/base.cpp:348–393` | `generateIndexComputation` — how maskHL is built (`simd_any` + `hsimd_signmask` at scanword width) |
| `include/kernel/streamutils/pdep_kernel.h:99` | `InsertionSpreadMaskKernel` — the only in-tree `TwoLevelScanKernel` subclass |
| `lib/kernel/streamutils/pdep_kernel.cpp:1283` | its constructor: `ScanWordWidth = 64`, `LoopVar`s, `BoundedRate` variable-rate output |
| `include/codegen/CBuilder.h:335,339` | `CreateCountForwardZeroes`, `CreateResetLowestBit` |
| `lib/idisa/idisa_arm_builder.cpp` | ARM overrides exist for `hsimd_packl/packh/packus`, `esimd_mergeh/mergel` — but **not** for `hsimd_signmask` |

Commands used:

```bash
grep -rn "TwoLevelScanKernel" .deps/parabix/lib .deps/parabix/include
sed -n '205,395p' .deps/parabix/lib/kernel/scan/base.cpp
grep -rn "CreateCountForwardZeroes\|CreateResetLowestBit" .deps/parabix/include/
```

---

## 9. Recommended order for the follow-up issues

1. ~~**Prototype the LLmask producer and measure it**~~ — **done in issue #29**; see
   [`llmask_generation_prototype.md`](llmask_generation_prototype.md). Outcome: a pack-based
   reduction (`hsimd_packh(16, …)` to densify + `mvmd_extract(64, …)` + OR-fold) runs at **6.1×
   scalar** and **2.4× even a compiler-optimised movemask**, and every step maps to an IDISA
   primitive that already exists and is ARM-overridden — so §3's "the vector→bitstream step is
   the open problem" is closed **at the algorithm level**. Two things changed as a result:
   the LLmask needs a **one-code-unit lookahead** that the current validator does not (§2 below
   is superseded by that note), and the remaining risk moved from *algorithm* to *kernel
   integration*. Everything else is comparatively mechanical.
1b. ~~**Aggregate LLmasks into maskHL**~~ — **done in issue #30**; see
   [`maskhl_aggregation_prototype.md`](maskhl_aggregation_prototype.md). Outcome: aggregation is
   correct on every boundary case and cross-checks against the validator's error count, and its
   cost is **below the benchmark's run-to-run noise** (~±4%) on top of LLmask generation.
   **Valid text yields `maskHL == 0` everywhere — a 100% region skip rate**, which is the
   structural argument for this whole design, now measured. Two things worth carrying forward:
   the fw=64 `hsimd_signmask` used by `generateIndexComputation` (§1a) is confirmed to be a
   different cost class from the fw=8 one that issue #38 found pathological (2 lanes/block vs
   16), so the trap does **not** extend to this level; and the two levels **degrade at different
   rates** — with scattered errors at 0.1% the region skip collapses to 1.6% while 93.8% of
   LLmasks are still clean, so level 2 keeps paying off well after level 1 has stopped.
1c. ~~**Scan the two-level structure for error positions**~~ — **done in issue #31**; see
   [`error_position_scan_prototype.md`](error_position_scan_prototype.md). The §6 pseudocode
   below is now implemented as a prototype (still **no Parabix kernel, no repair**) and emits
   exact, strictly ascending code-unit positions, verified against a Python reference that
   never builds a mask and against the production validator's error count (2208 and 22351
   positions on 32 MiB malformed files, both exact). Outcome: **the consumer side is
   effectively free.** On valid 32 MiB the two-level scan costs **0.0013 ms** against **~2.95
   ms** of mask generation — about **0.04%** of the work. It is **65× faster than a one-level
   scan** on clean data (it reads 32 KiB of maskHL instead of 2 MiB of LLmasks), and ~4200×
   faster than testing every bit. **The remaining risk is entirely on the producer side**: the
   cost of this design is building the mask stream, not scanning it.
2. Emit the `errorMarks` bitstream from the validator (behind a flag, so the count-only fast path
   is preserved and benchmarks stay comparable).
3. Subclass `TwoLevelScanKernel` to *locate* errors (report positions only — still no repair).
   Validate positions against the Python reference, which already knows exactly where every
   injected error is (issue #23 generators).
4. Define the repair policy in writing (§5) **before** writing repair code.
5. Only then implement repair.
