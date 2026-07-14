# Byte-oriented (fw=8) SIMD UTF-16 validator — design note

This note documents the SIMD validation kernel (`UTF16ValidatorSIMDKernel` in
`tools/utf16validate/utf16validate.cpp`, delivered via
`patches/utf16-simd-milestone.patch`). It corresponds to issue #1: *rewrite the
SIMD validator to a byte-oriented (fw=8) high-byte classification*.

## Motivation

The professor's feedback asked for a **byte-oriented SIMD implementation using
byte-oriented techniques in Parabix** — portable SIMD expressed through the
Parabix/IDISA layer rather than hard-coded intrinsics — so that our validator is
the *fairest competitor* for the Clausecker–Lemire `simdutf` work (which
classifies the **high byte** of each UTF-16 code unit on NEON). The Parabix
`base64` tool is the reference byte-oriented multiblock kernel (fw=8
`simd_eq`/`simd_fill`/`loadInputStreamPack`), and we follow its idioms.

The previous SIMD path classified **whole 16-bit code units**: it did
`fwCast(16, pack)` + `simd_eq(16, …)`, i.e. it reinterpreted raw bytes as 16-bit
lanes *in host order* and was fenced behind a little-endian-host `static_assert`.
That is the "generic x64" style — not byte-oriented, tied to host lane order, and
unable to handle UTF-16BE.

## Key fact used

Only the **high byte** of a UTF-16 code unit decides well-formedness:

| high byte pattern      | class          | range          |
|------------------------|----------------|----------------|
| `(hi & 0xFC) == 0xD8`  | high surrogate | `0xD800–0xDBFF`|
| `(hi & 0xFC) == 0xDC`  | low surrogate  | `0xDC00–0xDFFF`|
| otherwise              | BMP scalar     | —              |

The low byte is never inspected.

## Approach (host-endian agnostic)

Let `BITS` be the SIMD register width (the widest available, e.g. 256/512) and
`M = BITS/8` the number of bytes in one pack. Per BITS-wide byte pack:

1. **Byte classification at fw=8** (no 16-bit reinterpretation):
   ```
   masked = bytes & 0xFC          (simd_and, fw=8)
   isHi8  = (masked == 0xD8)      (simd_eq,  fw=8)   // all-ones per matching byte
   isLo8  = (masked == 0xDC)      (simd_eq,  fw=8)
   ```
2. **Pairing check, entirely in vector registers.** A stream is well-formed iff every
   low surrogate is immediately preceded by a high surrogate, so per code unit
   `mismatch[k] = isLow[k] XOR isHigh[k-1]`. "isHigh of the previous code unit" is
   `isHi8` advanced by one code unit = **two byte lanes**:
   ```
   prevHi   = mvmd_dslli(8, isHi8, prevPackHi, 2)   // vector lane shift; pulls the
                                                    // previous pack's top lanes in
   mismatch = (isLo8 XOR prevHi) & ODD_ONE_MASK     // 0x01 at odd (high-byte) lanes
   errors  += bitblock_popcount(mismatch)           // one bit per ill-formed unit
   ```
   `ODD_ONE_MASK` selects the high-byte position of each code unit (memory byte `2k+1`
   for UTF-16LE) and leaves a **single set bit** there, so one block popcount is exactly
   the error count.
3. The **carry is a vector**: the previous pack's `isHi8` mask. `mvmd_dslli` pulls its
   top lanes into the vacated ones, so the cross-pack "previous unit was an unmatched
   high surrogate" state needs no scalar bit. It threads across packs, blocks and
   segments; the incoming `pendingHigh` scalar is injected into the last high-byte lane
   of the initial carry vector, and the outgoing one is read back with a single
   `mvmd_extract` **once per invocation** (not once per pack).

> **Performance note (issue #38).** An earlier version of this kernel extracted two
> scalar bitmasks per pack with `hsimd_signmask(8, …)`. On AArch64 that primitive has no
> NEON lowering in IDISA and expanded into ~16 lane extractions plus scalar bit assembly
> *per call*, which dominated the loop and made SIMD slower than scalar. Keeping the
> whole pairing check in vector registers removed it: ~112 → ~40 instructions per pack,
> and SIMD went from 0.66× to 1.68× of scalar at 128 MiB. See
> [`simd_regression_investigation.md`](simd_regression_investigation.md).

The scalar tail (sub-pack remainder) is byte-oriented too: it reads the high byte
at offset `2*idx+1` directly. EOF finalisation adds one error for a dangling
final high surrogate and one for a single trailing (odd) byte, matching the
scalar oracle.

## Why this is host-endian agnostic

The high byte of each code unit is selected by **memory position** (which byte of
the pair), never by host lane significance. `simd_eq` at fw=8 compares raw bytes,
and `hsimd_signmask(8, …)` maps byte lane *i* → bit *i* (the standard IDISA
convention on the little-endian targets Parabix supports). Nothing reinterprets a
byte pair as a host-order 16-bit integer, so **the little-endian-host
`static_assert` is gone**.

**UTF-16BE (issue #2)** is a one-line change: the high byte then sits at the
*even* positions, so `ODD_MASK` becomes the even mask, the `<< 2` predecessor
shift is unchanged, the tail offset becomes `2*idx`, and the last high-byte
position becomes `M-2`. Endianness is chosen via `cc::ByteNumbering`, not a
compile guard.

## Correctness

The scalar `UTF16ValidatorKernel` is kept **unchanged** as the differential
oracle. `./scripts/test_utf16validate.sh` passes **31/31** (scalar ≡ SIMD),
covering fixed cases, block/pack-boundary crossings at 128/256/512, forced
pipeline segment sizes (1, 13, 64) that stress the cross-segment carry, and
randomized inputs checked against a Python reference. Both paths also agree under
`--thread-num=1` and the default (3) thread count.

## Issue #20 audit — byte-oriented / portability verification

Audited against `patches/utf16-simd-milestone.patch`, which is the tracked source
of truth for the kernel (`.deps/parabix/` is a git-ignored checkout that setup
regenerates from the patch, so it can lag behind and must not be audited on its
own).

| Check | Result | Evidence in the kernel |
|-------|--------|------------------------|
| Surrogate classification is byte-level | Confirmed | `fwCast(8, pack)`, `simd_and(bytes, simd_fill(8, 0xFC))`, `simd_eq(8, …)` — bytes are never reassembled into 16-bit lanes |
| High surrogate detected from `0xD8–0xDB` | Confirmed | `(highByte & 0xFC) == 0xD8`, via `simd_fill(8, 0xD8)` |
| Low surrogate detected from `0xDC–0xDF` | Confirmed | `(highByte & 0xFC) == 0xDC`, via `simd_fill(8, 0xDC)` |
| No AVX/SSE/NEON intrinsics used directly | Confirmed | No `_mm*`, `__m128`/`__m256`, `immintrin.h` or `arm_neon.h`; every vector op goes through Parabix/IDISA. The one "NEON" occurrence is prose in a comment |
| No host-endian 16-bit lane assumption | Confirmed | No `fwCast(16, …)`, `simd_eq(16, …)`, `hsimd_signmask(16, …)` or `0xFC00` remain, and the former little-endian-host `static_assert` is gone |
| Boundary state handled across blocks/segments | Confirmed | 1-bit `pendingHigh` internal scalar → `carryInit` → per-pack `carryPhi` → `carryFinal` → `setScalarField`; EOF finalisation via `isFinal()` |
| Existing tests still pass | Confirmed | `./scripts/test_utf16validate.sh` → **31 passed, 0 failed**, after rebuilding from the current patch |

### Why the SIMD path is portable

The high byte alone decides the class, and it is selected by **memory position**
(byte `2k+1` of code unit `k` for UTF-16LE) rather than by host lane significance.
All comparisons run at `fw=8` on raw bytes through the Parabix/IDISA layer, so the
kernel never depends on how a given host would assemble a byte pair into a 16-bit
integer, and it contains no architecture-specific intrinsics: Parabix lowers the
same source to whatever SIMD width the target provides.

### Known limitations (unchanged by this audit)

- The current target is **UTF-16LE only**.
- The scalar `UTF16ValidatorKernel` is the differential **oracle**, not the portable
  path: it still forms each code unit with a host-order 16-bit load, which is
  correct for UTF-16LE on the little-endian hosts we build on. The SIMD path makes
  no such assumption.
- **UTF-16BE remains future work**, not current support. The high byte would move to
  the even byte positions, so the high-byte position mask and the scalar-tail offset
  change; the pairing logic and the carry mechanism stay as they are.

## References

- `patches/utf16-simd-milestone.patch` — the applied kernel.
- `tools/base64/` + `lib/kernel/util/radix64.cpp` — byte-oriented multiblock idiom.
- `include/idisa/idisa_builder.h` — `simd_eq`, `simd_fill`, `hsimd_signmask`, etc.
- Clausecker & Lemire, *Fixing ill-formed UTF-16 strings with SIMD instructions*.
