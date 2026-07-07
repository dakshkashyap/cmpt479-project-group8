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
2. **One bit per byte position** via `hsimd_signmask(8, …)`: bit *i* ← byte *i*.
   For UTF-16LE the high byte of code unit *k* is memory byte `2k+1`, so the
   high-byte results occupy the **odd** bit positions.
3. **Pairing check on the high-byte stream.** A stream is well-formed iff every
   low surrogate is immediately preceded by a high surrogate, so per code unit
   `mismatch[k] = isLow[k] XOR isHigh[k-1]`. Advancing the high-surrogate bits by
   one code unit = two byte positions and injecting the incoming carry at the
   first predecessor slot gives, for the whole pack:
   ```
   shifted = (hiBits << 2) | (carryIn << 1)
   mism    = (loBits XOR shifted) & ODD_MASK      // ODD_MASK = bits 1,3,…,M-1
   errors += popcount(mism)
   carryOut = (hiBits >> (M-1)) & 1               // high-surrogate status of last unit
   ```
4. A single **carry bit** (`carryIn`/`carryOut`) threads the "previous unit was an
   unmatched high surrogate" state across packs, blocks and segments.

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

## References

- `patches/utf16-simd-milestone.patch` — the applied kernel.
- `tools/base64/` + `lib/kernel/util/radix64.cpp` — byte-oriented multiblock idiom.
- `include/idisa/idisa_builder.h` — `simd_eq`, `simd_fill`, `hsimd_signmask`, etc.
- Clausecker & Lemire, *Fixing ill-formed UTF-16 strings with SIMD instructions*.
