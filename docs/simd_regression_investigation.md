# Investigation: why the byte-oriented SIMD validator is slower than scalar

Issue #37. Issue #27 measured the current byte-oriented (`fw=8`) Parabix SIMD kernel as
*slower* than the scalar oracle. This note reproduces that, finds the cause, and backs it
with generated assembly rather than speculation.

**Result: the cause is identified and confirmed in the emitted machine code.** The kernel
calls `hsimd_signmask(8, …)` twice per pack, and on AArch64 that operation has **no NEON
override in Parabix's IDISA layer**, so it lowers to a lane-by-lane extraction: ~16 `umov`
+ scalar bit-assembly per call. The hot loop spends ~112 instructions per 16-byte pack, of
which **2** do actual SIMD work.

Nothing was hidden and nothing was fixed here — this issue is diagnosis only.

---

## 1. Reproduction

`default` dataset, 2 warmups, 5 repetitions, median wall clock, arm64 macOS.

```bash
python3 benchmarks/run_utf16_benchmark.py \
    --datasets default --sizes-mb 32,64,128 \
    --warmups 2 --repetitions 5 \
    --output /tmp/issue37_regression.csv
```

Throughput (MiB/s):

| Mode | 32 MiB | 64 MiB | 128 MiB |
|---|---|---|---|
| `scalar` | **999.2** | **1412.6** | **1747.5** |
| `parabix_simd_t1` | 765.1 | 988.0 | 1150.0 |
| `parabix_simd_t2` | 809.7 | 1053.4 | 1228.4 |
| `parabix_simd_t3` | 779.0 | 1022.2 | 1144.6 |
| `parabix_simd_default` | 794.1 | 1007.3 | 1180.8 |

SIMD (1 thread) is **0.77× / 0.70× / 0.66×** of scalar. Issue #27's finding reproduces.

### It is not startup overhead

The harness measures each tool's fixed per-process cost (~0.018 s) separately. Removing it:

| Size | scalar (adj.) | `simd_t1` (adj.) | SIMD / scalar |
|---|---|---|---|
| 32 MiB | 2327.8 | 1350.6 | **0.58×** |
| 64 MiB | 2367.8 | 1372.0 | **0.58×** |
| 128 MiB | 2328.6 | 1373.7 | **0.59×** |

The ratio is **flat at ~0.58×** once the fixed cost is removed. A constant per-byte
handicap, independent of input size, is exactly what a slower inner loop looks like — and
it rules out process startup, pipeline loading, and small-file effects as the cause.

### Historical note on the committed summary

`results/utf16_benchmark_summary.md` reports scalar 1354.8 MiB/s and SIMD-default
2176.4 MiB/s at 64 MiB (1.61×). Our scalar reproduces (1412.6); our SIMD does not
(1007.3). The **scalar oracle has never been modified**, which is consistent with that
summary having been produced by an **earlier SIMD kernel** — the lane-based (`fw=16`)
implementation that preceded the byte-oriented rewrite. We are not claiming the old
summary was measured incorrectly; we are saying it does not describe the code that is in
the tree today. It was not overwritten.

---

## 2. Root cause (confirmed in generated assembly)

Parabix can dump the code it JITs:

```bash
.deps/parabix/build/bin/utf16validate --simd \
    --ShowASM=/tmp/asm_simd.txt benchmarks/data/valid_utf16le_1MiB.bin
```

### `hsimd_signmask` has no AArch64 implementation

`IDISA_Builder::hsimd_signmask` (generic fallback) is:

```cpp
Value * mask = CreateICmpSLT(a1, ConstantAggregateZero::get(a1->getType()));  // <16 x i1>
mask = CreateBitCast(mask, getIntNTy(maskWidth));                             // -> i16
```

`hsimd_signmask` is overridden **only** in `idisa_sse_builder.cpp` and
`idisa_avx_builder.cpp`. On x86 that becomes a single `pmovmskb`. `idisa_arm_builder.cpp`
overrides `hsimd_packl/packh/packus`, `esimd_mergeh/mergel`, `mvmd_shuffle`,
`simd_bitreverse` — **but not `hsimd_signmask`**. AArch64 has no movemask instruction, so
the `<16 x i1> → i16` bitcast must be synthesized.

### What LLVM actually emits

From `/tmp/asm_simd.txt`, one `hsimd_signmask(8, …)`:

```
cmeq.16b  v1, v0, v1          ; the actual comparison  <-- 1 useful SIMD instruction
str       q1, [sp, #64]       ; 16-byte Folded Spill   <-- register spill
umov.b    w13, v1[1]          ; extract lane 1
umov.b    w12, v1[0]          ; extract lane 0
and       w12, w12, #0x1
bfi       w12, w13, #1, #1
umov.b    w13, v1[2]
bfi       w12, w13, #2, #1
...                            ; repeated for all 16 lanes
```

The mask is assembled **one lane at a time** into a scalar register, as a serial
dependency chain.

### Per-pack instruction census (measured from the dump)

One pack = one 128-bit NEON block = **16 bytes = 8 UTF-16 code units**
(`ldr q0, [x12, x13, lsl #4]` confirms the 16-byte stride).

| Instruction class | Count per pack |
|---|---|
| `umov.b` (vector → scalar lane extract) | **32** |
| `and` / `orr` / `bfi` / `lsl` / `lsr` (scalar bit assembly) | **56** |
| `cmeq.16b` (**actual SIMD comparison**) | **2** |
| `movi.16b` (constant materialisation) | 3 |
| `str` (spills) | 2 |
| other (`mov`, `add`, `ldrh`, `strh`, `lsr`) | ~17 |
| **Total** | **~112** |

**Two useful SIMD instructions out of ~112.** The kernel invokes `hsimd_signmask` twice
per pack (once for `isHi8`, once for `isLo8`) → 2 × 16 = 32 lane extracts.

### The scalar kernel, by contrast

The scalar dump (`--ShowASM` without `--simd`) contains **zero NEON vector instructions**
in its validation loop — a grep for vector registers returns nothing. It is *not*
auto-vectorized; it is a plain `ldr / and / cmp / csel / add` loop. It is fast simply
because it is short and the scheduler handles it well.

So the comparison is not "scalar vs SIMD". It is **a tight scalar loop (~5–6 instructions
per code unit) versus a "SIMD" loop that executes ~14 instructions per code unit, almost
all of them scalar.**

### Static operation count

| | per 16-byte pack (8 code units) | per code unit | of which real SIMD |
|---|---|---|---|
| scalar kernel | — (scalar loop) | ~5–6 instructions | 0 |
| byte-oriented `fw=8` SIMD | ~112 instructions | ~14 | 0.25 (`2 cmeq` / 8 units) |

That ~2.5× instruction disadvantage is entirely consistent with the measured 0.58×
throughput.

---

## 3. Causes: evidence vs hypothesis

**Confirmed by evidence (generated assembly + measurements):**

- **`hsimd_signmask(8, …)` lowering is the dominant cost.** 32 `umov` + 56 scalar
  bit-assembly instructions per pack, plus register spills. Seen directly in the emitted
  code.
- **The kernel calls it twice per pack**, doubling that cost.
- **Not fixed process/pipeline overhead.** The SIMD/scalar ratio is flat (~0.58×) across
  32/64/128 MiB *after* subtracting the measured startup cost.
- **Not memory bandwidth.** The scalar path reaches ~2328 MiB/s on the *same* data through
  the *same* Parabix pipeline. Bandwidth cannot be the limiter for the slower path.
- **Not scalar auto-vectorization.** The scalar dump contains no vector instructions; this
  hypothesis was tested and disproved.

**Hypotheses (plausible, not proven here):**

- **The design is x86-shaped.** On SSE/AVX, `hsimd_signmask` is one `pmovmskb`, so
  "2 signmasks + scalar mask bookkeeping per pack" is cheap. The same kernel may well be
  *fast* on x86. **We have not measured on x86.** Until we do, we should say the kernel is
  slow **on AArch64**, not that it is slow in general.
- **Per-pack scalar carry chain.** `hiBits`/`loBits`/`shifted`/`mism`/`popcount`/`carry`
  form a serial scalar dependency across packs, limiting ILP. Likely a secondary cost, but
  it is dwarfed by the signmask lowering and we did not isolate it.
- **Small pack size.** A 128-bit block gives only 8 code units per pack, so the fixed
  per-pack bookkeeping is amortized over very little data.

**No profiler was run.** All claims above come from the benchmark harness and from the
assembly Parabix emitted; nothing is inferred from a profile.

---

## 4. Recommendations for the next implementation issue

1. **Get `hsimd_signmask` out of the hot loop.** This is the single highest-value change.
   The mismatch count can stay entirely in vector registers:
   - keep `isHi8` / `isLo8` as byte masks (all-ones / zero, as now);
   - compute `prevHigh` with a **vector byte shift** of two lanes (`mvmd_dslli(8, …, 2)`,
     which lowers to `ext` on NEON) instead of shifting a scalar `i64` mask;
   - `mism = (isLo8 XOR prevHigh) AND odd-byte-mask` — all vector ops;
   - **accumulate without extracting**: a byte mask is `0xFF` = −1, so
     `acc = simd_sub(8, acc, mism)` counts matches into a byte accumulator; reduce it
     horizontally (e.g. `simd_popcount`/`addv`) only **once per block**, not once per pack.

   This removes ~88 of the ~112 instructions per pack and eliminates the scalar dependency
   chain.

2. **Or contribute an AArch64 `hsimd_signmask` override to IDISA.** The standard NEON idiom
   (`shrn` narrowing to a 64-bit lane, then one `umov`) is ~4 instructions instead of ~48.
   This would benefit every Parabix kernel on ARM, not just ours — but it means changing
   the Parabix patch, which is a larger scope decision.

3. **Measure on x86 before concluding.** If `pmovmskb` makes the current kernel competitive
   on SSE/AVX, the story becomes "portable source, but one IDISA primitive is unimplemented
   on ARM" — a much more interesting and more accurate finding than "our SIMD is slow".

4. **Be honest about the tradeoff in the report.** The byte-oriented rewrite was the right
   call for *portability and correctness* (it removed the host-endian 16-bit-lane
   assumption). It appears to have cost throughput on ARM, and that trade was never
   measured at the time. Report it as a measured trade-off, not as a success.

5. **Do not repeat the 1.61× claim.** It is not reproducible against the current kernel and
   should not appear in the report or slides without a fresh measurement.

6. **Consider where the project's contribution really lies.** Beating a specialized library
   (simdutf) on raw validation throughput with this pipeline is a hard target. Two-level
   scan / repair (issue #28+) may be the stronger contribution, with validation throughput
   reported honestly as a portability-vs-performance study.

---

## 5. Reproduce this investigation

```bash
# benchmark
python3 benchmarks/run_utf16_benchmark.py --datasets default --sizes-mb 32,64,128 \
    --warmups 2 --repetitions 5 --output /tmp/issue37_regression.csv

# generated assembly for both paths
BIN=.deps/parabix/build/bin/utf16validate
F=benchmarks/data/valid_utf16le_1MiB.bin
$BIN        --ShowASM=/tmp/asm_scalar.txt "$F"
$BIN --simd --ShowASM=/tmp/asm_simd.txt   "$F"

# the signmask lowering
grep -n -A30 "cmeq" /tmp/asm_simd.txt | head -40
grep -c "umov" /tmp/asm_simd.txt            # lane extracts

# confirm no ARM signmask override exists
grep -l "hsimd_signmask" .deps/parabix/lib/idisa/*.cpp   # sse + avx + generic only
```

## 6. Issue #38 — the fix, and what it bought

Recommendation 1 above was implemented: **`hsimd_signmask(8, …)` was removed from the hot
loop** and the pairing check now stays entirely in vector registers. No architecture-specific
intrinsics were added; the kernel is still byte-oriented (`fw=8`) and still classifies on the
high byte, and the surrogate rules are unchanged.

### What changed

| | Before (issue #37) | After (issue #38) |
|---|---|---|
| previous-unit high mask | scalar `i64` bitmask via `hsimd_signmask(8, isHi8)` | the previous pack's `isHi8` **vector**, shifted with `mvmd_dslli(8, …, 2)` |
| low-surrogate mask | scalar `i64` bitmask via `hsimd_signmask(8, isLo8)` | `isLo8` vector, used directly |
| mismatch | scalar `(loBits XOR shifted) & ODD_MASK` | `simd_and(simd_xor(isLo8, prevHi), ODD_ONE_MASK)` |
| counting | scalar `popcount` of the `i64` mask | one `bitblock_popcount` of the mismatch vector |
| cross-pack carry | scalar carry bit | carried in the vector; `mvmd_extract` **once per invocation** |

`ODD_ONE_MASK` leaves a single set bit (`0x01`) at each high-byte lane rather than a full
`0xFF`, so a single block popcount *is* the error count.

### Generated assembly (same `--ShowASM` method as §2)

| | issue #37 | issue #38 |
|---|---|---|
| `umov` (vector→scalar lane extracts) | **32 per pack** | **1 in the entire dump** |
| `bfi` (scalar bit assembly) | 10+ per pack | **0** |
| `ext` (vector lane shift) | 0 | 1 (the `mvmd_dslli`) |
| `cmeq.16b` (real SIMD compares) | 2 | 2 |
| **instructions per pack** | **~112** | **~40** |

### Measured result

`--datasets default --sizes-mb 32,64,128 --warmups 2 --repetitions 5`

| Mode | 32 MiB | 64 MiB | 128 MiB |
|---|---|---|---|
| `scalar` (#37 → #38) | 999.2 → 993.9 | 1412.6 → 1408.5 | 1747.5 → 1778.5 |
| `parabix_simd_t1` | 765.1 → **1285.4** (+68%) | 988.0 → **2084.9** (+111%) | 1150.0 → **2991.0** (+160%) |
| `parabix_simd_default` | 794.1 → **1404.5** (+77%) | 1007.3 → **2431.7** (+141%) | 1180.8 → **3780.7** (+220%) |

**The scalar control is unchanged** (within ±2%), which is what makes this a real kernel
improvement rather than measurement drift.

**SIMD now beats scalar**, having been 0.66× before:

| | 32 MiB | 64 MiB | 128 MiB |
|---|---|---|---|
| `simd_t1` / `scalar`, raw | 1.29× | 1.48× | **1.68×** |
| `simd_t1` / `scalar`, overhead-adjusted | 2.25× | 2.27× | **2.23×** |

The overhead-adjusted ratio is flat at ~2.25×, which is the honest per-byte speedup; the raw
figure is lower only because the fixed ~0.019 s startup is still amortized over the input.

Thread scaling also improved as a side effect: `t2` now gives ~1.29× over `t1` at 128 MiB
(3846 vs 2991 MiB/s), where issue #27 measured a flat ~1.08× ceiling. That is consistent with
the loop no longer being dominated by a serial scalar dependency chain — but it was not the
target of this issue and has not been re-analysed properly.

**Correctness: 67/67 tests pass**, including the cross-pack/block/segment carry cases and the
forced segment sizes that specifically stress it.

### Status

**Improved.** `hsimd_signmask(8, …)` is **fully removed** from the kernel (only comments
mention it). The remaining bottleneck is no longer mask extraction.

Remaining known costs, in rough order:

- the **fixed ~0.019 s per-process startup**, which still caps raw throughput at small inputs;
- one `bitblock_popcount` per pack (a few instructions: horizontal reduce + one `fmov`) — it
  could be deferred by accumulating mismatch bytes in a vector and reducing once per block,
  though that needs an overflow-safe flush every ≤255 packs;
- the still-scalar tail and EOF handling (negligible at these sizes).

The original conclusion of §3 stands and is now confirmed constructively: **IDISA has no
AArch64 lowering for `hsimd_signmask`**, and kernels written as if it were a one-instruction
`pmovmskb` will be pathologically slow on ARM. The portable fix is to avoid the primitive, not
to add intrinsics.

---

## 7. Threats to validity

- **One machine, one architecture** (arm64 macOS laptop). The central claim — that
  `hsimd_signmask` is unimplemented for NEON and lowers to lane extraction — is a property
  of the Parabix source and the emitted code, not of this laptop; but the *magnitude* of the
  slowdown is machine-specific, and the x86 behaviour is untested.
- **Instruction counts are static**, taken from the emitted assembly, not from a profiler.
  They explain the measured ratio consistently, but they are not a cycle-level attribution.
- The per-pack census was taken from the region spanning both `signmask` calls; a few
  instructions of loop overhead may be included or excluded at the boundaries.
