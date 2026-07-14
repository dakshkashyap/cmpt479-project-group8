# maskHL aggregation: prototype and measurements (issue #30)

**Status: prototype and design validation only.** Nothing here is a Parabix kernel. The
production validator is unchanged, there is **no `TwoLevelScanKernel` subclass**, **no scan**,
and **no repair**. No production speedup is claimed, because no production code was changed.

Scope: UTF-16LE, little-endian host, as everywhere else in this project.

---

## 1. What was added

Issue #29 produced the **low level** of the two-level scan: an LLmask, one `uint64_t` per 64
UTF-16 code units, bit *i* set iff code unit *i* is ill-formed. This issue adds the **high
level**:

```
maskHL[j] bit w  =  1   iff   LLmask[64*j + w] != 0
```

One maskHL word therefore summarises **64 LLmasks = 4096 code units = 8192 bytes**.

- `maskHL[j] == 0` → the whole 4096-code-unit region is clean and a future scan can skip it
  with a single compare and branch.
- `maskHL[j] != 0` → the scan visits **only** the LLmasks whose bit is set (`cttz` /
  reset-lowest-bit), never the clean ones.

This is exactly the shape Parabix's `TwoLevelScanKernel` already expects — `generateIndexComputation`
(`lib/kernel/scan/base.cpp:348`) builds its index mask with `simd_any(64, block)` +
`hsimd_signmask(64, …)`, and its stride geometry (`base.cpp:205`) works out to 64 scanwords ×
64 bits. See [`two_level_scan_design.md`](two_level_scan_design.md).

| File | What it is |
|---|---|
| `benchmarks/llmask_generation.h` | **new** — the issue #29 LLmask core, moved verbatim into a shared header so both prototypes use the *same* generator and cannot drift |
| `benchmarks/prototype_maskhl_aggregation.cpp` | **new** — 3 aggregation strategies, self-test, invariant checks, skip-rate statistics, benchmark |
| `benchmarks/prototype_llmask_generation.cpp` | now includes the shared header; **no algorithm changed** (its own self-test and `run_llmask_prototype.sh` still pass unmodified) |
| `scripts/run_maskhl_prototype.sh` | **new** — build (temp dir) → self-test → invariants → validator cross-check → skip-rate sweep → benchmark |

---

## 2. The three aggregation strategies

All three produce **identical maskHL arrays**; only how the "is this LLmask nonzero" bit is
gathered differs. The self-test requires all three to agree with each other *and* with the
scalar LLmask reference on every case.

| strategy | how | why it is here |
|---|---|---|
| `separate_pass` | generate every LLmask, then walk the mask array again and OR the nonzero bits together branchlessly | the obvious implementation, and the baseline |
| `vector_any64` | a second pass, but treating LLmask pairs as a 128-bit block: `simd_any(64, …)` then one bit per 64-bit lane | **models Parabix's own `generateIndexComputation`**, so the claim that its `hsimd_signmask` is cheap can be checked rather than asserted |
| `fused` | compute the maskHL bit inside the LLmask generation loop, while the mask is still in a register — no second pass over memory | what a real producer kernel would naturally do |

### Why `vector_any64` matters for the issue #38 lesson

Issue #38 found `hsimd_signmask(8, …)` pathological on AArch64: at fw=8 a 128-bit block has
**16 lanes**, and with no ARM override IDISA's generic lowering scalarises into ~16 lane
extractions per call. `TwoLevelScanKernel`'s index computation uses the same primitive but at
**fw = 64**, where a 128-bit block has only **two lanes** — two extractions per 128 bits, not
sixteen. That is a different cost class, and the measurements below confirm it: `vector_any64`
is indistinguishable from the other strategies. **The issue #38 trap does not apply to the
maskHL level.**

---

## 3. Correctness

`./scripts/run_maskhl_prototype.sh`

### Self-test: 12/12 pass

Every case is checked with all three strategies, and each must agree with the others, with the
scalar LLmask reference, and with the three maskHL invariants.

| case | result |
|---|---|
| all valid (2+ regions, including valid surrogate pairs) | **maskHL = 0 everywhere**, skip 100% |
| one error in LLmask 2 | exactly one bit (bit 2) in one maskHL word |
| errors spread across 4 LLmasks in one region | bits 0, 1, 10, 62 set; one dirty region |
| errors at an LLmask boundary (units 63, 128) | bits 0 and 2 |
| **valid pair straddling an LLmask boundary** | **no bit set** — a pair split across LLmask 0\|1 is not an error |
| errors at a maskHL boundary (units 4095, 4096) | maskHL 0 bit 63, and maskHL 1 bit 0 |
| **valid pair straddling a maskHL boundary** | **no bit set** |
| partial region + partial LLmask (4064 tail units) | correct — this is the case where the fused strategy's end-of-loop flush is easy to get wrong |
| odd trailing byte | **no maskHL bit possible** (it has no code unit); reported separately |
| odd trailing byte + error in LLmask 1 | bit 1 set, odd byte reported separately |
| empty input | 0 LLmasks, 0 maskHL words |
| every LLmask dirty | maskHL = all ones, skip rate 0% |

### Invariants (checked on every file, not just the self-test)

1. `popcount(all maskHL) == number of nonzero LLmasks` ✔
2. every set bit in maskHL corresponds to a nonzero LLmask ✔
3. every nonzero LLmask sets the matching bit in maskHL ✔
   (2 and 3 are checked position by position, in both directions, plus "no maskHL bit is set
   past the last LLmask")

### Cross-check against the production validator

The validator is itself covered by the 67-test suite, so this ties the whole chain together:

| file | LLmask bits | odd byte | total | `utf16validate` errorCount |
|---|---|---|---|---|
| `valid_utf16le_mixed_multilingual_32MiB.bin` | 0 | 0 | 0 | **0** |
| `malformed_…_random_mix_err0.01_32MiB.bin` | 2208 | 0 | 2208 | **2208** |
| odd-length truncation of the above | 0 | 1 | 1 | **1** |

---

## 4. Skip rates

8 MiB of `mixed_multilingual` text per row, so every row is directly comparable. One region =
4096 code units.

| pattern | rate % | errors | dirty maskHL | dirty LLmasks | **REGION skip** | **LLMASK skip** |
|---|---|---|---|---|---|---|
| valid | 0 | 0 | 0 | 0 | **100.0000%** | **100.0000%** |
| `random_mix` | 0.0001 | 5 | 4 | 4 | 99.6094% | 99.9939% |
| `random_mix` | 0.001 | 52 | 41 | 42 | 95.9961% | 99.9359% |
| `random_mix` | 0.01 | 554 | 345 | 419 | 66.3086% | 99.3607% |
| `random_mix` | 0.1 | 5546 | 1008 | 4065 | **1.5625%** | 93.7973% |
| `random_mix` | 1 | 55155 | 1024 | 31257 | **0.0000%** | 52.3056% |
| `clustered_mix` | 0.0001 | 4 | 1 | 1 | 99.9023% | 99.9985% |
| `clustered_mix` | 0.001 | 49 | 5 | 7 | 99.5117% | 99.9893% |
| `clustered_mix` | 0.01 | 547 | 16 | 34 | 98.4375% | 99.9481% |
| `clustered_mix` | 0.1 | 5574 | 126 | 284 | 87.6953% | 99.5667% |
| `clustered_mix` | 1 | 55031 | 750 | 2893 | **26.7578%** | 95.5856% |

**REGION skip** = fraction of 4096-code-unit regions with `maskHL == 0` (level 1: skipped with
one compare). **LLMASK skip** = fraction of 64-code-unit LLmasks that are zero (level 2:
never looked at, even inside a dirty region).

These are properties of the **data**, not measured scan speedups. No scan kernel exists.

### What the table says

**Valid input gives maskHL = 0 everywhere, so the skip rate is 100%.** This is not a
coincidence and not a tuning result — it is structural. A maskHL bit is set only if some LLmask
is nonzero, and an LLmask bit is set only if some code unit is ill-formed. Well-formed text has
no ill-formed code units, so every LLmask is zero, so every maskHL word is zero. **The
overwhelmingly common case costs one compare and one branch per 8192 bytes.** That is the entire
argument for the two-level structure, and it now has a measurement behind it.

**The two levels degrade at different rates, and that is the interesting finding.** The high
level is a *coarse* summary: one dirty code unit dirties a whole 4096-unit region. So as errors
scatter, the region skip rate collapses long before the LLmask skip rate does. At `random_mix`
0.1% the region skip rate has fallen to **1.6%** — level 1 is doing essentially nothing — yet
**93.8%** of LLmasks are still clean, so level 2 is still avoiding most of the work. The two
levels are not redundant; the second one keeps paying off well after the first has stopped.

**Clustering matters more than the error rate.** At the same error count, `clustered_mix`
holds a 26.8% region skip rate at a 1% error rate where `random_mix` is at 0.0%, and 98.4% vs
66.3% at 0.01%. Errors in real corrupted text are usually clustered (a truncated buffer, a
mis-sliced string, one bad decoder), so the pessimistic `random_mix` rows should be read as a
worst case, not as the expected case.

---

## 5. Cost of aggregation

32 MiB, `mixed_multilingual`, 2 warmups, 15 repetitions, median. Timing covers LLmask
generation (± aggregation) on an in-memory buffer — no file I/O, no Parabix pipeline,
single-threaded.

**Valid 32 MiB:**

| stage | median MiB/s | median ms | vs LLmask only |
|---|---|---|---|
| `llmask_only` (baseline) | 10392.3 | 3.079 | 1.00× |
| `llmask + separate_pass` | 10680.2 | 2.996 | 1.03× |
| `llmask + vector_any64` | 10562.5 | 3.030 | 1.02× |
| `llmask + fused` | 10786.1 | 2.967 | 1.04× |

**Malformed 32 MiB (`random_mix` 0.01%):**

| stage | median MiB/s | median ms | vs LLmask only |
|---|---|---|---|
| `llmask_only` (baseline) | 10943.1 | 2.924 | 1.00× |
| `llmask + separate_pass` | 10634.5 | 3.009 | 0.97× |
| `llmask + vector_any64` | 10670.4 | 2.999 | 0.98× |
| `llmask + fused` | 10940.3 | 2.925 | 1.00× |

### Read this carefully

The ratios land on **both sides of 1.00×** (0.97×–1.04×) and they flip between runs. **Adding
maskHL aggregation cannot make LLmask generation faster.** The correct reading is therefore not
"fused is a 4% speedup" — it is that **the cost of aggregation is below this benchmark's
run-to-run noise (roughly ±4%)**, so no strategy can be distinguished from any other, or from
doing no aggregation at all.

That is consistent with a simple cost argument. Per 128 input bytes there is one LLmask, so the
LLmask array is 1/16 the size of the input and the maskHL array is 1/1024 of it. Aggregation
adds about one compare and one shift-or per LLmask — a handful of operations against the ~80
the vector LLmask generator already spends on those same 128 bytes. **Roughly 1–4% expected,
which is exactly where it disappears into the noise.**

**Honest conclusion: maskHL aggregation is essentially free at this scale, but this prototype
cannot resolve its cost precisely, and it says nothing about the cost inside a real Parabix
kernel.**

---

## 6. What this proves for the future `TwoLevelScanKernel` step

1. **The aggregation is correct**, on adversarial boundary cases (LLmask boundaries, maskHL
   boundaries, valid pairs straddling both, partial regions, odd trailing bytes) and on 32 MiB
   of real multilingual text, cross-checked against both an independent LLmask reference and
   the production validator's error count.
2. **The data structure the scan kernel needs is producible cheaply.** A producer kernel can
   emit both levels in one pass without a measurable throughput penalty over emitting the
   LLmasks alone.
3. **The skip is real and is largest exactly where it matters.** Valid text — the common case —
   skips 100% of regions.
4. **The issue #38 signmask trap does not extend to the maskHL level**, because it operates at
   fw=64 (2 lanes/block) rather than fw=8 (16 lanes/block). `vector_any64` models that and is
   not slower.

What is **not** proven: that a real `TwoLevelScanKernel` subclass is fast. The skip rates above
say how much work such a scan *could avoid*; they do not say what the scan *costs*. That
requires the kernel, which is the next issue.

---

## 7. Risks and assumptions

1. **These are prototype microbenchmarks, not kernel numbers.** In-memory buffer, no I/O, no
   Parabix pipeline, no segmentation, no threading. They are **not comparable** to the validator
   throughputs in `docs/simd_regression_investigation.md`, and they are not a production result.
2. **The aggregation overhead is below noise, not measured to be zero.** A precise figure would
   need a lower-variance harness. Nothing here should be quoted as "aggregation is free" without
   that caveat.
3. **Skip rates are properties of the test corpora**, which are synthetic. `random_mix` is a
   deliberately pessimistic distribution; `clustered_mix` is a guess at realistic corruption.
   Neither is measured real-world data.
4. **Sparse errors do not make mask *generation* sparse.** Every code unit is still classified
   and every LLmask is still written, regardless of the error rate — visible in the benchmark,
   where the valid and malformed files run at the same speed. The skip only benefits a future
   *consumer*. Issue #27 found the validator close to bandwidth-bound, so a repair-capable path
   that always writes an LLmask stream may still be slower on clean text than today's
   count-only validator. **Not measured; still open.**
5. **The region size is fixed at 4096 code units** by `64 × 64`. That is what Parabix's stride
   geometry yields on a 128-bit target, but a different bit-block width changes it, and the
   region-skip column is sensitive to that choice in a way the LLmask-skip column is not.
6. **The one-code-unit lookahead requirement from issue #29 is unchanged and still untested in a
   kernel** (`LookAhead(2)`, and its interaction with the cross-segment `pendingHigh` carry).
   It remains the most likely place for integration to go wrong.

---

## 8. Reproduction

```bash
./scripts/run_maskhl_prototype.sh                        # everything above
MASKHL_SWEEP_MB=32 ./scripts/run_maskhl_prototype.sh     # a larger skip-rate sweep
```

The script compiles the prototype into a temporary directory, generates datasets into the
git-ignored `benchmarks/data/`, writes no CSV and nothing to `results/`, and leaves no binary
in the working tree. The validator cross-check is skipped with a notice if
`.deps/parabix/build/bin/utf16validate` has not been built.

---

## 9. Recommended next step

Emit `errorMarks : StreamSet<i1>[1]` from a real Parabix kernel (behind a flag, so the
count-only fast path and existing benchmarks stay intact), then subclass `TwoLevelScanKernel`
over it to **locate** errors — positions only, still no repair — and validate the reported
positions against the Python reference, which already knows where every injected error is.
Only after that has been measured in the kernel should the repair policy be written down.
