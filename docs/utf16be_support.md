# UTF-16BE support (issue #33)

UTF-16BE validation is now supported across every validation and error-location path.
**UTF-16LE remains the default and is byte-for-byte unchanged.**

Scope: still little-endian *host* only (the scalar oracle recovers the code unit with a host
byte-swap; the SIMD paths are host-endian agnostic already). No repair; no changes to the
count-only LE behaviour, the issue #32 producer, the issue #39 consumer, or the
Clausecker–Lemire integration.

---

## 1. Flag

```
--be     Validate UTF-16BE input (default: UTF-16LE).
```

It composes with every existing mode: `--simd`, `--emit-error-marks`, `--print-positions`,
`--scan-error-marks`. UTF-16LE is what you get without it, generating exactly the same pipeline
as before.

The Python reference gained a matching option:

```
python3 benchmarks/llmask_reference.py --positions FILE --be     # or  --endian be
```

---

## 2. How LE and BE byte selection differ

Surrogate classification depends only on the **high byte** of each code unit:

```
high surrogate: (highByte & 0xFC) == 0xD8
low  surrogate: (highByte & 0xFC) == 0xDC
```

and the two encodings place the high byte at opposite positions:

| code unit *k* | UTF-16LE | UTF-16BE |
|---|---|---|
| high byte | byte `2k+1` | byte `2k` |
| low byte | byte `2k` | byte `2k+1` |

Every byte-oriented path already selects the high byte **by memory position**, not by host lane
significance, so BE support is a single parameter: the high-byte offset within a code unit,

```
HB = bigEndian ? 0 : 1        // BE: 2k+0,  LE: 2k+1
```

Everything endian-specific reduces to `HB`. There are **no AVX/SSE/NEON intrinsics** and the
hot-loop still contains **no `hsimd_signmask(8)`** — the issue #38 fix is untouched.

---

## 3. Which paths support BE, and how each selects the byte lane

| path | BE mechanism |
|---|---|
| **scalar oracle** (`UTF16ValidatorKernel`) | loads the code unit as a host-order `i16` (correct for LE), then **byte-swaps** it for BE (`Intrinsic::bswap`) so the identical range checks follow. LE emits no swap and is unchanged. |
| **SIMD count-only** (`UTF16ValidatorSIMDKernel`) | the high-byte-selecting mask `ODD_ONE_MASK` sets its bit at lanes `2j+HB`; `HIGH_BYTE_POS = BYTES_PER_PACK-2+HB` (carry lane); the scalar tail reads byte `2*idx+HB`. The `mvmd_dslli` pairing shift (by one code unit) is endian-independent. |
| **errorMarks producer** (`UTF16ErrorMarksKernel`) | same three, plus: the `WEIGHTS` reduction mask weights lanes `2j+HB`; the `LookAhead(2)` byte for the `k+1` term is read at `(P+1)*BYTES_PER_PACK+HB` and inserted at vector lane `HB`; the scalar tail reads byte `2*idx+HB`. |
| **TwoLevelScanKernel consumer** (`UTF16ErrorMarkScanKernel`) | **no change needed.** It scans the code-unit-indexed `errorMarks` bitstream, which the producer has already made endian-correct. A set bit means "code unit *i* is ill-formed" regardless of encoding. |

Each BE kernel is registered under a distinct name (`utf16validate_be`, `utf16validateSIMD_be`,
`utf16errormarks_be`) so the object cache never serves an LE kernel for a BE request. The LE
kernels keep their original names, so LE remains identical down to the cache key.

---

## 4. Correctness

### `./scripts/test_utf16be.sh` — 35/35 pass

BE fixtures generated as big-endian bytes, each checked **five ways** at **four segment sizes**
(default, 1, 13, 64):

1. scalar `--be` count == a BE-aware reference count;
2. SIMD `--be` count == the same;
3. errorMarks `--be` count == the same;
4. scan `--be` positions == `benchmarks/llmask_reference.py --positions --be`;
5. **cross-endian identity** — BE bytes are exactly the byte-swap of the LE encoding of the same
   code units, so validating the BE file with `--be` must give the **same** count as validating
   the LE-encoded file **without** `--be`. This ties BE back to the already-trusted LE path,
   independent of the reference.

Coverage: valid BMP, valid pair, lone high, lone low, reversed pair, dangling high at EOF, high
at position 0, empty, odd trailing byte (alone and with a lone high), multilingual; valid and
malformed pairs straddling the **64-code-unit group** boundary and the **4096-code-unit scan
stride** boundary (63/64/65, 4095/4096/4097, 8191/8192/8193); a clean scan stride between two
dirty ones; and 5 randomized 5000-unit inputs.

### 32 MiB differential

The 32 MiB LE malformed dataset byte-swapped to BE:

```
scalar --be : 2208    simd --be : 2208    marks --be : 2208
scan --be positions: 2208 == reference (--be): 2208    (byte-identical)
cross-endian: LE original 2208 == BE swapped 2208
```

### LE regression (unchanged)

From the applied patch: `test_utf16validate.sh` **67/67**, `test_errormarks.sh` **49/49**,
`test_scan_consumer.sh` **54/54**. The LE code paths are unchanged (the `HB=1` branch reproduces
the old constants; LE kernel names and the skipped byte-swap keep the generated IR identical),
which these three suites confirm.

---

## 5. Performance

BE does exactly the same work as LE with different constants, so it costs the same. Whole-process
median, `-thread-num=1`, overhead-adjusted, on 32 MiB:

| mode | adj MiB/s |
|---|---|
| LE `--simd` (count) | ~4767 |
| BE `--simd` (count) | ~5259 |
| LE `--emit-error-marks` | ~4738 |
| BE `--emit-error-marks` | ~4663 |

The LE/BE differences are within this benchmark's run-to-run noise; there is no systematic gap.

**LE no-regression** (`benchmarks/run_utf16_benchmark.py --datasets default --sizes-mb 32,64`):
scalar 1000.9, `parabix_simd_t1` 1287.1 MiB/s at 32 MiB; 1404.6 / 2054.3 at 64 MiB — matching the
issue #32/#38/#39 figures, as expected given LE is unchanged.

---

## 6. Limitations

1. **Little-endian *host* only.** The scalar oracle uses a host-order `i16` load plus a byte-swap
   for BE; on a big-endian host the swap direction would be inverted. The SIMD/producer paths are
   host-endian agnostic (they select bytes by position), but the oracle is not, so the tool as a
   whole is scoped to LE hosts — as it already was.
2. **No BOM handling.** Endianness is chosen by the flag, not detected from a byte-order mark.
3. **No repair** (unchanged project-wide).
4. The BE benchmark is a smoke measurement on one machine; BE has not been run through the full
   thread-scaling matrix (nor does it need to be — it is the same kernel shape as LE).

---

## 7. Reproduction

```bash
./scripts/setup_parabix.sh
./scripts/test_utf16be.sh            # 35/35 BE
./scripts/test_utf16validate.sh      # 67/67 LE (unchanged)
./scripts/test_errormarks.sh         # 49/49 LE producer
./scripts/test_scan_consumer.sh      # 54/54 LE consumer

.deps/parabix/build/bin/utf16validate --be FILE
.deps/parabix/build/bin/utf16validate --be --simd FILE
.deps/parabix/build/bin/utf16validate --be --emit-error-marks --scan-error-marks -thread-num=1 FILE
```
