# Two-level scan consumer for errorMarks (issue #39)

**Parabix's `TwoLevelScanKernel` was directly subclassed.** The consumer side of the
error-location pipeline is now a real Parabix kernel, `UTF16ErrorMarkScanKernel`, that reuses
the framework's own two-level scan machinery to turn the `errorMarks` bitstream (issue #32) into
exact code-unit positions.

What this is **not**:

- **No repair.** Positions are located and printed; nothing is rewritten.
- **No UTF-16BE.**
- **The count-only validator is untouched.** Both count-only kernels *and* the issue #32
  producer are byte-identical to the previous milestone patch (verified, §3). The default and
  `--simd` paths generate exactly the same pipeline as before.

Scope: UTF-16LE, little-endian host, as everywhere else in this project.

---

## 1. Was `TwoLevelScanKernel` directly subclassed? Yes.

```cpp
class UTF16ErrorMarkScanKernel final : public TwoLevelScanKernel {
    UTF16ErrorMarkScanKernel(LLVMTypeSystemInterface & ts, StreamSet * errorMarks)
    : TwoLevelScanKernel(ts, "utf16errormarkscan", /*scanWordWidth*/ 64,
                         /*scanStreamName*/ "errorMarks", /*loopVars*/ {}) {
        mInputStreamSets.push_back(Binding{"errorMarks", errorMarks});
        addAttribute(SideEffecting());
    }
    void generateProcessingLogic(KernelBuilder & b, Value * absItemPos,
                                 std::vector<Value *> &) override {
        b.CallPrintInt("errpos", absItemPos, CBuilder::STD_FD::STD_OUT);
    }
    // initialize / wordPrologueLogic / finalize: empty -- no loop variables needed.
};
```

The entire two-level algorithm is **Parabix's own code** (`lib/kernel/scan/base.cpp`), not a
reimplementation. The subclass supplies only the leaf action. This is the smallest possible
honest consumer: locating positions is stateless, so the `LoopVar` list is empty and three of
the four hooks are no-ops.

`UTF16ErrorMarkScanKernel` is the *only* in-tree subclass of `TwoLevelScanKernel` besides
Parabix's own `InsertionSpreadMaskKernel`, which confirmed the construction pattern
(`mInputStreamSets.push_back` for the scan stream, `LoopVar`s threaded through both loop
levels — which we simply leave empty).

### What the base class does for us

`TwoLevelScanKernel::strideLogic` (`base.cpp:221-346`) and `generateIndexComputation`
(`base.cpp:348-390`) implement exactly the algorithm issues #30 and #31 prototyped:

1. **Build the high-level index (maskHL).** Per stride, `generateIndexComputation` computes a
   `size_t` whose bit *w* is set iff scanword *w* of `errorMarks` is nonzero, via
   `simd_any(64, block)` + `hsimd_signmask(64, …)`. At `scanWordWidth = 64` on a 128-bit block
   this is **two lanes per block**, so it does *not* hit the fw=8 `hsimd_signmask` problem issue
   #38 found on AArch64 (16 lanes). This is the point issue #30 argued and it holds in the real
   kernel.
2. **Skip clean regions.** `strideLogic` does `CreateLikelyCondBr(metaMask != 0, …)` — a stride
   whose maskHL is all-zero (a clean 4096-code-unit region) is skipped past with a single
   branch, and LLVM is told the clean case is the likely one.
3. **Scan sparsely.** `CreateCountForwardZeroes` (ctz) walks only the dirty scanwords in maskHL,
   and within each dirty scanword, only the set bits; `CreateResetLowestBit` (`x & (x-1)`)
   advances both levels. `generateProcessingLogic` is called once per set bit with the
   **absolute code-unit position** already computed.

### Stride geometry

`scanWordWidth = 64` ⇒ `maxScanBlocks = 64 / (BITS/64)`. On a 128-bit block that is 32
blocks/stride = **64 scanwords × 64 bits = 4096 code units per stride** — exactly the region
size from issues #30/#31. One maskHL word summarises one stride.

---

## 2. Flag

`--scan-error-marks` (requires `--emit-error-marks`) wires
`ByteStream → UTF16ErrorMarksKernel → errorMarks → UTF16ErrorMarkScanKernel`.

It prints the same `errpos = <hex>` lines as issue #32's `--print-positions` — the positions are
identical by construction — so both are diffable against `benchmarks/llmask_reference.py
--positions`. **The distinction is the scan strategy, not the output:** `--scan-error-marks`
skips clean regions (this kernel); `--print-positions` is the issue #32 linear debug printer
that visits every block and skips nothing. Both are kept; the issue #32 printer is retained as
the linear cross-check the tests rely on.

`SideEffecting()` is required, as it was for the #32 printer: a kernel whose only result is what
it prints is otherwise considered dead and never scheduled (`attributes.h:280-284`).

---

## 3. The count-only and producer paths are provably untouched

Extracted from the old and new `patches/utf16-simd-milestone.patch` and compared:

```
IDENTICAL   scalar UTF16ValidatorKernel (oracle)
IDENTICAL   UTF16ValidatorSIMDKernel (optimized count-only)
IDENTICAL   UTF16ErrorMarksKernel (issue #32 producer)
IDENTICAL   ErrorMarkPositionKernel body (issue #32 linear printer)
```

The only edits to pre-existing code are additive: the `--print-positions` help text gained the
words "issue #32 linear debug printer", and `utf16validatePipelineGen`'s
`if (printPositions)` became `if (scanErrorMarks) … else if (printPositions)`. `CMakeLists.txt`
gained the `kernel.scan` dependency.

---

## 4. Correctness

### `./scripts/test_scan_consumer.sh` — 54/54 pass

Every fixture is checked **three ways**, at **four segment sizes** (default, 1, 13, 64):

1. scan positions == `benchmarks/llmask_reference.py --positions` (the definition, with no
   LLmask/maskHL/Parabix);
2. scan positions == the issue #32 **linear** printer's positions — same bitstream, two
   different scan strategies (skip-clean-regions vs visit-every-block), which is what proves the
   region skipping is sound;
3. `errorCount` unchanged from the scalar oracle.

Coverage adds the cases the two-level structure specifically stresses: boundary crossings at
code units **4095/4096/4097 and 8191/8192/8193** (the 4096-code-unit **scan stride**
boundaries), and a `errors in strides 0 and 2, stride 1 clean` case that forces the scan to
**skip an entire clean middle stride** and still find both errors — verified at positions
`[0, 8293]`. Plus valid/lone-high/lone-low/reversed/dangling/odd-byte/multilingual and 5
randomized 5000-unit inputs.

### 32 MiB differential

```
scan consumer  : 2208 positions
linear printer : 2208 positions
python ref     : 2208 positions
PASS: scan == reference on 32 MiB
PASS: scan == #32 linear printer on 32 MiB
```

Byte-identical position lists across all three, on 32 MiB of malformed multilingual text.

### Regression gates (from a clean-room rebuild)

`.deps/parabix` was reset to the pinned base `f0369dd`, the patch reapplied cleanly, and the
tool rebuilt. Then:

- `./scripts/test_utf16validate.sh` → **67/67** (count-only)
- `./scripts/test_errormarks.sh` → **49/49** (issue #32 producer)
- `./scripts/test_scan_consumer.sh` → **54/54** (this issue)

---

## 5. Performance — and the honest prototype-vs-kernel gap

Whole-process wall time, `-thread-num=1`, median of 15, 3 warmups. Overhead-adjusted subtracts
the measured fixed startup, per `docs/benchmark_methodology.md`.

**Valid input — the clean measurement, because zero errors means zero prints:**

| file | `--emit-error-marks` | `--emit + --scan-error-marks` | scan overhead |
|---|---|---|---|
| valid 64 MiB | 5379 MiB/s (adj) | 5041 MiB/s (adj) | **~6.7%** |
| valid 128 MiB | 5266 MiB/s (adj) | 5067 MiB/s (adj) | **~3.9%** |

**On clean data, adding the scan stage costs roughly 4–7% over producing the marks alone.**

This is honestly *more* than the standalone prototype suggested. Issue #31 measured the scan at
**0.04%** of mask generation — but it measured only the `ctz` inner loop over masks **already
built and hot in cache**. The real kernel pays for two things the prototype excluded:

1. **The high-level index is built over *every* block, clean or not.**
   `generateIndexComputation` runs `simd_any(64)` + `hsimd_signmask(64)` on the whole stride
   before `strideLogic` can decide to skip it. The `CreateLikelyCondBr` skip avoids the
   *scanword-processing* loop, **not** the index construction. So on all-clean data the scan
   still reads the entire `errorMarks` stream and builds maskHL over all of it.
2. **It is a separate pipeline stage** — segment scheduling, buffer management, and re-reading
   the marker stream (≈1/16 of the input) from memory.

So the two-level scan's cheapness is real but narrower than the prototype implied: it makes the
**per-error work** negligible (clean regions cost one branch each at the scanword level), but the
**index-building floor** is paid on every byte. On clean text that floor is the whole ~4–7%.

For malformed input the whole-process number is dominated by the position **printing** (one
`write`-backed `CallPrintInt` per error), which is an artifact of this debug consumer, not the
scan — so it is not a meaningful scan-cost measurement and is not quoted here.

### Count-only no-regression

`python3 benchmarks/run_utf16_benchmark.py --datasets default --sizes-mb 32,64`: scalar 1412.2,
`parabix_simd_t1` 2075.8 MiB/s at 64 MiB — unchanged from the issue #32 / #38 figures, as
expected given the count-only kernels are byte-identical.

---

## 6. Limitations and what remains before repair

1. **The consumer's only output is printed positions.** It does not yet write positions to a
   stream or scalar a downstream kernel could consume, so its cost on malformed data is
   entangled with `write` I/O. A repair pass will need the positions *in the pipeline*, not on
   stdout — likely by having the scan kernel drive an edit/replacement kernel directly rather
   than printing.
2. **The index-building floor (§5) is the real steady-state cost**, not the per-error scan. Any
   future optimization of the location path should target index construction / stream bandwidth,
   not the `ctz` loop.
3. **Still no repair, and the repair policy is still unwritten.** The reversed-pair ambiguity
   from issue #28 is unchanged: the stream says *where*, not *what kind*, so a repair pass must
   re-read the code unit at each flagged position (cheap — the scan is already there).
4. **UTF-16LE only.**
5. **Single-threaded, one machine.** The scan stage's effect on multi-threaded scaling (issue
   #27) has not been re-analysed; an extra pipeline stage changes the dataflow graph.

### What is now in place

The full **locate** pipeline exists and is validated end to end in real Parabix:

```
bytes → UTF16ErrorMarksKernel → errorMarks : StreamSet(1) → UTF16ErrorMarkScanKernel → positions
        (issue #32 producer)                                (issue #39 consumer, this doc)
```

The only missing piece before repair is a **replacement policy** and a kernel that acts on the
located positions instead of printing them.

---

## 7. Reproduction

```bash
./scripts/setup_parabix.sh          # applies the patch, builds
./scripts/test_utf16validate.sh     # 67/67 -- count-only regression gate
./scripts/test_errormarks.sh        # 49/49 -- issue #32 producer
./scripts/test_scan_consumer.sh     # 54/54 -- this issue's consumer

.deps/parabix/build/bin/utf16validate --emit-error-marks --scan-error-marks -thread-num=1 FILE
```

Note: on this machine a full CMake *reconfigure* currently fails on an unrelated Boost 1.90 /
`find_package` environment issue (it affects any reconfigure, not this change); the tool itself
compiles and links cleanly from the applied patch.
