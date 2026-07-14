# Error-position scanning over maskHL + LLmasks (issue #31)

**Status: position scanning only, in a prototype.**

- There is **no repair**. Nothing in this repository rewrites a single byte of UTF-16.
- There is **no `TwoLevelScanKernel` subclass** and no Parabix kernel of any kind here.
- The **production validator is unchanged** (`patches/utf16-simd-milestone.patch` untouched).
- **No production speedup is claimed**, because no production code was changed. Every
  throughput figure below is a prototype microbenchmark on an in-memory buffer.

Scope: UTF-16LE, little-endian host, as everywhere else in this project.

---

## 1. What this issue adds

Issue #29 built the low level (**LLmask**: one `uint64_t` per 64 code units, bit *i* set iff
code unit *i* is ill-formed). Issue #30 built the high level (**maskHL**: one `uint64_t` per 64
LLmasks, bit *w* set iff `LLmask[64j+w] != 0`, so one word covers 4096 code units).

This issue adds the **consumer**: walk that structure and emit the **exact code-unit index** of
every ill-formed code unit.

```
for each maskHL word j:
    if maskHL[j] == 0:                              // <-- the entire point
        skip the whole 4096-code-unit region        //     one compare, one branch
    while maskHL[j] != 0:
        llIndex = ctz(maskHL[j])                    // next DIRTY LLmask
        m       = llmasks[64*j + llIndex]
        while m != 0:
            bit = ctz(m)                            // next set bit in that LLmask
            emit((64*j + llIndex) * 64 + bit)       // exact code-unit position
            m  &= m - 1                             // reset lowest bit
        maskHL[j] &= maskHL[j] - 1                  // reset lowest bit
```

This is deliberately the same shape as Parabix's `TwoLevelScanKernel::strideLogic`
(`lib/kernel/scan/base.cpp:221`), which uses `CreateCountForwardZeroes` (ctz) and
`CreateResetLowestBit` (`x & (x-1)`) at both levels and guards each stride with
`CreateLikelyCondBr(metaMask != 0, …)`. See [`two_level_scan_design.md`](two_level_scan_design.md).

**The odd trailing byte is not a code-unit position** — it has no code unit — so it is reported
separately as an EOF condition, exactly as the production validator handles it. The invariant
that ties the whole chain together, checked on every file:

```
number_of_positions + oddTrailingByte  ==  utf16validate errorCount
```

| File | Status | What |
|---|---|---|
| `benchmarks/prototype_error_position_scan.cpp` | **new** | 3 scanners, self-test, position dump, statistics, benchmark |
| `benchmarks/maskhl_aggregation.h` | **new** | issue #30's maskHL core, moved **verbatim** into a shared header so the scanner walks exactly the masks #30 validated |
| `benchmarks/llmask_reference.py` | +`--positions` | the independent reference now also emits a position list |
| `scripts/run_error_position_scan_prototype.sh` | **new** | build (temp dir) → self-test → scanner agreement → differential → validator cross-check → benchmark |
| `benchmarks/prototype_maskhl_aggregation.cpp` | refactor | now includes the shared header; **no algorithm changed** (its 12/12 self-test still passes unmodified) |

---

## 2. The three scanners

All three emit **identical** position lists. They differ only in how much they skip, which is
what makes the value of each level a measurement rather than an assertion.

| scanner | what it does | why it is here |
|---|---|---|
| `two_level` | maskHL `ctz` → LLmask `ctz`. Skips clean regions with one compare; never touches a clean LLmask | the design under test |
| `one_level` | ignores maskHL: visits **every** LLmask, branches past the zero ones, `ctz` within | isolates what the **high** level actually buys |
| `linear` | tests **every bit** of every LLmask | the dumbest possible consumer; makes "sparse scanning is cheap" a measured claim |

Every scan result is additionally required to be **strictly ascending** and **in range** — a
future repair pass will depend on both.

---

## 3. Correctness

`./scripts/run_error_position_scan_prototype.sh`

### Self-test: 15/15 pass

Each case is scanned all three ways and all three must produce the identical list.

| case | positions | odd byte |
|---|---|---|
| valid BMP + valid surrogate pair | `[]` | 0 |
| lone high | `[1]` | 0 |
| lone low | `[2]` | 0 |
| reversed pair | **`[1, 2]`** (two positions) | 0 |
| **valid pair crossing an LLmask boundary** (63\|64) | **`[]`** | 0 |
| malformed crossing an LLmask boundary | `[63, 65]` | 0 |
| **valid pair crossing a maskHL boundary** (4095\|4096) | **`[]`** | 0 |
| malformed crossing a maskHL boundary | `[4095, 4096]` | 0 |
| errors across regions 0, 2, 4 (1 and 3 clean — must be skipped) | `[10, 8692, 20479]` | 0 |
| **odd trailing byte** | **`[]`** — no position exists | **1** |
| odd trailing byte + lone high | `[1]` | 1 |
| empty input | `[]` | 0 |
| lone high at position 0 | `[0]` | 0 |
| dangling high at EOF | `[63]` | 0 |
| every code unit ill-formed (200 units) | `[0..199]` | 0 |

### Differential against the Python reference

`benchmarks/llmask_reference.py --positions` derives the position list **straight from the
definition — with no LLmask and no maskHL at all**. The C++ reaches the same list the long way
round (bytes → LLmask → maskHL → two-level ctz scan), so agreeing validates the **whole chain
end to end**, not just one link.

**4/4 files produce byte-identical position lists:** valid 32 MiB, `random_mix` 0.01% 32 MiB
(2208 positions), `clustered_mix` 0.1% 32 MiB (22351 positions), and an odd-length file.

### Cross-check against the production validator

| file | positions | odd byte | total | `utf16validate` errorCount |
|---|---|---|---|---|
| `valid_…_32MiB.bin` | 0 | 0 | 0 | **0** |
| `…_random_mix_err0.01_32MiB.bin` | 2208 | 0 | 2208 | **2208** |
| `…_clustered_mix_err0.1_32MiB.bin` | 22351 | 0 | 22351 | **22351** |
| odd-length truncation | 0 | 1 | 1 | **1** |

---

## 4. Results

32 MiB `mixed_multilingual`, 2 warmups, 25 repetitions, median. In-memory buffer; no file I/O,
no Parabix pipeline, single-threaded.

### Skip rates and counts

| | valid | `random_mix` 0.01% | `clustered_mix` 0.1% |
|---|---|---|---|
| total code units | 16777216 | 16777216 | 16777216 |
| **total error positions** | **0** | **2208** | **22351** |
| odd trailing byte | 0 | 0 | 0 |
| LLmasks | 262144 | 262144 | 262144 |
| dirty LLmasks | 0 | 1681 (0.64%) | 1188 (0.45%) |
| maskHL words | 4096 | 4096 | 4096 |
| dirty maskHL words | 0 | 1371 (33.5%) | 511 (12.5%) |
| **region skip rate** | **100.00%** | 66.53% | 87.52% |
| **LLmask skip rate** | **100.00%** | 99.36% | 99.55% |

### Scan cost, measured in isolation (masks already built)

| scanner | valid | `random_mix` 0.01% | `clustered_mix` 0.1% |
|---|---|---|---|
| **`two_level`** | **0.0013 ms** | **0.0047 ms** | **0.0269 ms** |
| `one_level` | 0.0870 ms (**65×**) | 0.1086 ms (23×) | 0.1169 ms (4.3×) |
| `linear` | 5.6276 ms (**4222×**) | 5.6921 ms (1220×) | 5.6875 ms (211×) |

### Scan cost as a delta on top of mask generation

Mask generation (LLmask + maskHL, fused) costs **~2.95 ms** for the same 32 MiB.

| stage (valid 32 MiB) | median ms | delta |
|---|---|---|
| mask generation only | 2.952 | — |
| + `two_level` scan | 2.971 | 0.019 |
| + `one_level` scan | 3.073 | 0.121 |
| + `linear` scan | 10.136 | **7.184** |

---

## 5. What the numbers say

**For valid input, the time is almost entirely mask *generation*, not scanning.** This is the
most important honest statement in this document. On valid 32 MiB, generation costs ~2.95 ms and
the two-level scan costs **0.0013 ms** — about **0.04%** of the work. Locating errors is not what
costs anything; *building the bitstream you locate them in* is. Any future performance work on
this path should target mask generation and should not bother optimising the scan.

**The delta table cannot resolve the two-level scan, and the isolated table can.** The
two-level delta (0.019 ms) is well under 1% of the 2.95 ms baseline, i.e. inside this
benchmark's run-to-run noise; it is not a real measurement of anything. The isolated
scan-only figure (0.0013 ms) is 15× smaller and is the number to quote. Both tables are printed
so this is visible rather than hidden.

**The high level earns its keep — on exactly the case that matters.** On valid input `two_level`
is **65× faster** than `one_level`. The reason is mechanical: `one_level` must read the whole
262144-entry LLmask array (2 MiB) to discover that it is all zeros, while `two_level` reads only
the 4096-entry maskHL array (32 KiB) — a 64× reduction in bytes touched, which is almost exactly
the 65× observed. That is the region skip, measured.

**The advantage narrows as errors spread out, exactly as expected.** `two_level` costs 0.0013 →
0.0047 → 0.0269 ms as the data gets dirtier, while `one_level` stays roughly flat (~0.09–0.12 ms)
because it always reads the whole LLmask array regardless. So the two-level advantage falls from
65× to 4.3× on the dirtiest input tested. It never *loses*, but the gap closes.

**Sparse scanning is not a micro-optimisation.** The `linear` consumer costs ~5.7 ms — nearly
**twice the cost of generating the masks in the first place**, and it would more than triple the
total. The `ctz`/reset-lowest-bit structure is what keeps the consumer negligible; without it,
the consumer would dominate.

---

## 6. What this proves for the future `TwoLevelScanKernel` step

1. **The algorithm is correct**, on adversarial boundaries (LLmask boundaries, maskHL
   boundaries, valid pairs straddling both, dangling highs, odd trailing bytes, fully dirty
   input) and on 32 MiB of real multilingual text — validated three independent ways: against a
   Python reference that never builds a mask, against the maskHL invariants, and against the
   production validator's error count.
2. **Exact positions are recoverable**, strictly ascending and in range, which is the
   precondition a repair pass needs. **Repair itself is still not designed or implemented.**
3. **The consumer side is essentially free.** Whatever a real `TwoLevelScanKernel` subclass
   costs, this prototype says the *algorithmic* work it has to do is ~0.04% of mask generation
   on clean text. If a real kernel turns out to be slow, the cause will be Parabix framework
   overhead (stride machinery, `LoopVar` plumbing, extra stream buffers), **not** the scan.
4. **Therefore the remaining risk is entirely on the producer side**: emitting the `errorMarks`
   bitstream from a real kernel, with the `LookAhead(2)` that issue #29 showed is required, and
   its interaction with the cross-segment `pendingHigh` carry.

What this does **not** prove: that a real `TwoLevelScanKernel` subclass is fast, or that it can
be wired into the pipeline at all. No kernel exists. These are prototype figures.

---

## 7. Risks and assumptions

1. **Prototype microbenchmarks, not kernel or production numbers.** In-memory buffer, no I/O,
   no Parabix pipeline, no segmentation, no threading. **Not comparable** to the validator
   throughputs in `docs/simd_regression_investigation.md`.
2. **The scan-only timings are best-case for the scan.** The masks are already built and hot in
   cache. A real kernel would interleave generation and consumption, and the maskHL/LLmask
   arrays might not be resident. The isolated numbers are a lower bound on the scan's cost.
3. **The "MiB/s of input" column for the scanners is an effective rate, not a memory bandwidth.**
   The two-level scan on valid data reads 32 KiB of maskHL, not 32 MiB of input; dividing input
   size by scan time is a convenient normalisation, not a claim about throughput.
4. **Positions are emitted into a preallocated buffer.** A real consumer that allocates, or that
   writes to a stream, would pay more. The prototype deliberately does not measure that.
5. **Skip rates are properties of synthetic corpora.** `random_mix` is a deliberately pessimistic
   distribution; `clustered_mix` is a guess at realistic corruption. Neither is real-world data.
6. **Mask generation is still not sparse.** Every code unit is classified and every LLmask is
   written regardless of the error rate — visible in the benchmark, where valid and malformed
   files generate masks at the same speed. Since issue #27 found the validator close to
   bandwidth-bound, a repair-capable path that always writes a mask stream may still be slower
   on clean text than today's count-only validator. **Still unmeasured; still open.**
7. **The reversed-pair ambiguity from issue #28 is unchanged.** The scan reports *where*, not
   *what kind*. A repair pass cannot tell a lone low from the low half of a reversed pair from
   the position alone; it must re-read the code unit (cheap — it is already at that position).

---

## 8. Reproduction

```bash
./scripts/run_error_position_scan_prototype.sh
LLMASK_SIZES_MB=64 ./scripts/run_error_position_scan_prototype.sh
```

Compiles to a temporary directory, generates datasets into the git-ignored `benchmarks/data/`,
writes no CSV and nothing to `results/`, and leaves no binary in the working tree. The validator
cross-check is skipped with a notice if `.deps/parabix/build/bin/utf16validate` is not built.

---

## 9. Recommended next step

The producer, not the consumer, is now the whole problem. Emit
`errorMarks : StreamSet<i1>[1]` from a **real Parabix kernel** (behind a flag, so the count-only
fast path and the existing benchmarks stay intact), using the `hsimd_packh(16, …)` +
`mvmd_extract(64, …)` mapping from issue #29 and the `LookAhead(2)` it requires — and re-measure
**in the kernel**, because none of the ~6× and ~free figures in these three prototypes are
guaranteed to survive the IDISA lowering and the pipeline. Only then subclass
`TwoLevelScanKernel`, and only after that write down a repair policy.
