// llmask_generation.h -- shared UTF-16LE LLmask generation core (issues #29, #30).
//
// This header holds the LLmask definition, the four generation strategies and the small
// amount of file/buffer plumbing they need. It is included by BOTH prototypes:
//
//   benchmarks/prototype_llmask_generation.cpp   (issue #29 -- compares the strategies)
//   benchmarks/prototype_maskhl_aggregation.cpp  (issue #30 -- aggregates LLmasks -> maskHL)
//
// It exists so the two prototypes cannot drift apart: the maskHL prototype aggregates
// exactly the LLmasks that issue #29 validated against an independent Python reference and
// against the production validator's error count. The code below was moved here verbatim
// from prototype_llmask_generation.cpp; nothing about the algorithms changed.
//
// These are PROTOTYPES. None of this is a Parabix kernel, and the production validator
// (delivered via patches/utf16-simd-milestone.patch) is untouched by either of them.
//
// Definitions
// -----------
//   * Input is raw UTF-16LE bytes. Code unit k occupies bytes [2k, 2k+1]; only the HIGH
//     byte (2k+1) decides well-formedness:
//         (hi & 0xFC) == 0xD8  ->  high surrogate  (U+D800..U+DBFF)
//         (hi & 0xFC) == 0xDC  ->  low  surrogate  (U+DC00..U+DFFF)
//         otherwise            ->  BMP scalar
//   * An LLmask is a uint64_t covering 64 consecutive code units. Bit i is set iff
//     CODE UNIT (64*g + i) IS ITSELF ILL-FORMED:
//         bad[k] = (isLow[k]  && !isHigh[k-1])     // low surrogate with no high before it
//               || (isHigh[k] && !isLow[k+1])      // high surrogate with no low after it
//     with isHigh[-1] = pendingHigh (false at start of file) and unit `units` treated as
//     "not a low surrogate", so a high surrogate at EOF marks itself.
//   * An odd trailing byte has NO code unit, so it cannot be represented in a code-unit
//     indexed mask. It is reported separately, exactly as the validator handles it at EOF.
//
//     popcount(all LLmasks) + oddTrailingByte  ==  utf16validate errorCount
//
// Note the rule needs unit k+1, i.e. a one-code-unit LOOKAHEAD, which the production
// validator does not need (its backward-only XOR marks the SUCCESSOR of a lone high
// surrogate -- fine for counting, wrong position for repair). Callers guarantee the
// lookahead by padding the input buffer with PAD_BYTES zero bytes; a Parabix kernel would
// use the LookAhead(2) input attribute, which the framework zero-fills at EOF.
//
// Strategies (all four produce byte-for-byte identical LLmasks; only the vector ->
// bitstream reduction differs -- see docs/llmask_generation_prototype.md):
//   scalar              portable C++, no vectors. The reference.
//   signmask_scalarized models IDISA hsimd_signmask(8, .) on AArch64. Cautionary baseline.
//   signmask_optimized  the same movemask, left free for the compiler. Favourable stand-in.
//   vector_pack_reduce  odd-lane pack (= hsimd_packh(16, a, b), ARM-overridden -> uzp2)
//                       + bit-weight AND + 64-bit OR-fold. Fastest; ~6x scalar.
//
// Scope: UTF-16LE on a little-endian host, as everywhere else in this project.

#ifndef LLMASK_GENERATION_H
#define LLMASK_GENERATION_H

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace llmask {

constexpr size_t UNITS_PER_MASK = 64;
constexpr size_t BYTES_PER_PACK = 16;   // 16 bytes = 8 UTF-16 code units
constexpr size_t PAD_BYTES = 32;        // zero padding for the k+1 lookahead

// A code unit's class, derived from its high byte only.
constexpr uint8_t SUR_MASK = 0xFC;
constexpr uint8_t HIGH_TAG = 0xD8;
constexpr uint8_t LOW_TAG = 0xDC;

struct Result {
    std::vector<uint64_t> masks;
    uint64_t units = 0;
    uint64_t errorBits = 0;
    unsigned oddTrailingByte = 0;
};

// ---------------------------------------------------------------------------
// Strategy 1: scalar reference.
//
// One high-byte load per code unit, carried forward so each byte is read once.
// `prevHigh` is the pendingHigh carry; it is what a kernel would keep in an
// InternalScalar across packs, blocks and segments.
// ---------------------------------------------------------------------------
void llmask_scalar(const uint8_t * data, size_t units, uint64_t * masks) {
    if (units == 0) return;
    bool prevHigh = false;                       // pendingHigh carry
    uint8_t cur = data[1] & SUR_MASK;            // high byte of unit 0
    uint64_t m = 0;
    for (size_t k = 0; k < units; ++k) {
        // Unit k+1's high byte. Past the end this reads the zero padding, which is
        // not a low surrogate -- exactly the EOF semantics we want.
        const uint8_t nxt = data[2 * k + 3] & SUR_MASK;
        const bool isHigh = (cur == HIGH_TAG);
        const bool isLow = (cur == LOW_TAG);
        const bool nextLow = (nxt == LOW_TAG);
        const bool bad = (isLow & !prevHigh) | (isHigh & !nextLow);
        m |= static_cast<uint64_t>(bad) << (k & 63);
        if ((k & 63) == 63) {
            masks[k >> 6] = m;
            m = 0;
        }
        prevHigh = isHigh;
        cur = nxt;
    }
    if (units & 63) masks[(units - 1) >> 6] = m;   // final partial group
}

// ---------------------------------------------------------------------------
// Shared vector classification (GNU vector extensions -- portable across clang and
// gcc, no architecture intrinsics; this file contains no <arm_neon.h>/<immintrin.h>).
// ---------------------------------------------------------------------------
using u8x16 = uint8_t __attribute__((vector_size(16)));

inline u8x16 load_pack(const uint8_t * p) {
    u8x16 v;
    std::memcpy(&v, p, sizeof(v));               // unaligned load
    return v;
}

inline u8x16 splat(uint8_t x) {
    return u8x16{x, x, x, x, x, x, x, x, x, x, x, x, x, x, x, x};
}

// 0xFF at the odd (high) byte lane of each UTF-16LE code unit, 0x00 elsewhere.
// The even lanes hold low bytes, which can coincidentally look like 0xD8/0xDC
// (e.g. code unit 0x00D8), so they must be masked off.
inline u8x16 odd_lane_mask() {
    return u8x16{0, 0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF,
                 0, 0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF};
}

// Per-lane verdict for one 16-byte pack (8 code units).
//   prevHi : isHigh vector of the PREVIOUS pack (for the k-1 term)
//   nextLo : isLow  vector of the NEXT pack     (for the k+1 term)
// Returns 0xFF at the high-byte lane of every ill-formed code unit, 0x00 elsewhere,
// and writes back this pack's isHigh/isLow so the caller can chain them.
inline u8x16 classify_pack(u8x16 bytes, u8x16 prevPackHigh, u8x16 nextPackLow,
                           u8x16 & isHighOut, u8x16 & isLowOut) {
    const u8x16 masked = bytes & splat(SUR_MASK);
    const u8x16 isHigh = (u8x16)(masked == splat(HIGH_TAG));   // 0xFF / 0x00
    const u8x16 isLow = (u8x16)(masked == splat(LOW_TAG));

    // prevHigh[k] = isHigh[k-2 lanes] = isHigh of the previous CODE UNIT.
    // Lanes 0,1 come from the previous pack's lanes 14,15. This is IDISA's
    // mvmd_dslli(8, isHigh, prevPackHigh, 2) -- the same carry the validator uses.
    const u8x16 prevHigh = __builtin_shufflevector(
        prevPackHigh, isHigh, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29);

    // nextLow[k] = isLow[k+2 lanes] = isLow of the next CODE UNIT.
    // Lanes 14,15 come from the next pack's lanes 0,1 (the lookahead).
    const u8x16 nextLow = __builtin_shufflevector(
        isLow, nextPackLow, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17);

    isHighOut = isHigh;
    isLowOut = isLow;
    // bad = (isLow & ~prevHigh) | (isHigh & ~nextLow), restricted to high-byte lanes.
    return ((isLow & ~prevHigh) | (isHigh & ~nextLow)) & odd_lane_mask();
}

// ---------------------------------------------------------------------------
// Strategies 2 and 3: two signmask-style reductions, both cautionary baselines.
//
// Both compute the SAME thing -- bit i = sign bit (MSB) of byte lane i, i.e. exactly
// IDISA's hsimd_signmask(8, .) -- but they differ in how the compiler is allowed to
// realise it, and that difference is the whole point:
//
//   signmask_scalarized  forces one real lane extraction per lane with an optimisation
//                        barrier. This MODELS how IDISA's hsimd_signmask(8, .) actually
//                        lowers on AArch64: there is no ARM override in
//                        lib/idisa/idisa_arm_builder.cpp, so the generic implementation
//                        (a CreateBitCast of a <16 x i1> compare to i16) is scalarised
//                        by LLVM into ~16 umov + bfi. Issue #38 measured this in the real
//                        kernel and it made SIMD slower than scalar.
//
//   signmask_optimized   writes the same reduction without the barrier and lets clang do
//                        its best. On AArch64 clang turns the lane loop into a vector
//                        shift/OR bit-assembly (ushll/ushl/orr -- verified in the emitted
//                        assembly; no umov chain). This is therefore a FAVOURABLE stand-in
//                        for signmask: an upper bound on what a movemask-style reduction
//                        could achieve, better than what IDISA emits today.
//
// Reporting both keeps the comparison honest: if the pack-based reduction beats even
// signmask_optimized, then the conclusion does not rest on IDISA's poor lowering.
// ---------------------------------------------------------------------------
inline uint32_t signmask_scalarized(u8x16 v) {
    uint32_t m = 0;
    // Both guards are needed. The pragma stops clang from vectorising the lane loop back
    // into a shift/OR bit-assembly, and the per-lane barrier forces each lane into a
    // general-purpose register -- which is the `umov` that IDISA's scalarised signmask
    // performs. With only one of the two, clang recovers a vector form and the model
    // stops modelling anything.
#if defined(__clang__)
#pragma clang loop vectorize(disable) unroll(disable)
#endif
    for (unsigned i = 0; i < 16; ++i) {
        uint8_t lane = v[i];
        asm volatile("" : "+r"(lane));
        m |= static_cast<uint32_t>(lane >> 7) << i;
    }
    return m;
}

inline uint32_t signmask_optimized(u8x16 v) {
    uint32_t m = 0;
    for (unsigned i = 0; i < 16; ++i) {
        m |= static_cast<uint32_t>((v[i] >> 7) & 1u) << i;
    }
    return m;
}

// Gather the odd-numbered bits of a 16-bit signmask into a contiguous 8-bit field.
// A kernel would need this because signmask returns one bit per BYTE lane, but only
// the high-byte lanes carry a code-unit verdict.
inline uint32_t compress_odd_bits(uint32_t x) {
    x = (x >> 1) & 0x5555u;
    x = (x | (x >> 1)) & 0x3333u;
    x = (x | (x >> 2)) & 0x0F0Fu;
    x = (x | (x >> 4)) & 0x00FFu;
    return x;
}

// Scalar continuation of any vector strategy, for the final fewer-than-64 code units.
// The vector part already resolved every unit it emitted (its lookahead saw the first
// tail unit), so the tail only needs the pendingHigh carry, which is lane 15 of the last
// isHigh vector.
inline void llmask_tail(const uint8_t * data, size_t units, uint64_t * masks,
                        size_t k, bool pend) {
    if (k >= units) return;
    uint64_t m = 0;
    for (; k < units; ++k) {
        const uint8_t cur = data[2 * k + 1] & SUR_MASK;
        const uint8_t nxt = data[2 * k + 3] & SUR_MASK;
        const bool isHigh = (cur == HIGH_TAG);
        const bool bad = ((cur == LOW_TAG) & !pend) | (isHigh & !(nxt == LOW_TAG));
        m |= static_cast<uint64_t>(bad) << (k & 63);
        pend = isHigh;
    }
    masks[(units - 1) >> 6] = m;
}

// Body shared by both signmask strategies; SIGNMASK is the reduction under test.
template <uint32_t (*SIGNMASK)(u8x16)>
void llmask_signmask(const uint8_t * data, size_t units, uint64_t * masks) {
    const size_t fullGroups = units / UNITS_PER_MASK;     // 64 code units = 8 packs
    u8x16 prevHigh = splat(0);

    for (size_t g = 0; g < fullGroups; ++g) {
        uint64_t m = 0;
        const uint8_t * base = data + g * (UNITS_PER_MASK * 2);
        for (unsigned p = 0; p < 8; ++p) {                 // 8 packs x 8 code units
            const u8x16 bytes = load_pack(base + p * BYTES_PER_PACK);
            const u8x16 nextBytes = load_pack(base + (p + 1) * BYTES_PER_PACK);  // padded
            const u8x16 nextLow =
                (u8x16)((nextBytes & splat(SUR_MASK)) == splat(LOW_TAG));
            u8x16 isHigh, isLow;
            const u8x16 bad = classify_pack(bytes, prevHigh, nextLow, isHigh, isLow);
            // signmask gives one bit per BYTE lane; only the odd (high-byte) lanes carry
            // a code-unit verdict, so the 16 bits must be compacted down to 8.
            const uint32_t bits8 = compress_odd_bits(SIGNMASK(bad));
            m |= static_cast<uint64_t>(bits8) << (p * 8);
            prevHigh = isHigh;
        }
        masks[g] = m;
    }
    llmask_tail(data, units, masks, fullGroups * UNITS_PER_MASK, prevHigh[15] != 0);
}

void llmask_signmask_scalarized(const uint8_t * data, size_t units, uint64_t * masks) {
    llmask_signmask<signmask_scalarized>(data, units, masks);
}

void llmask_signmask_optimized(const uint8_t * data, size_t units, uint64_t * masks) {
    llmask_signmask<signmask_optimized>(data, units, masks);
}

// ---------------------------------------------------------------------------
// Strategy 3: vector_pack_reduce -- vector classification + a pack-based reduction.
//
// Two packs (16 code units) are classified, then:
//   1. DENSIFY: take the odd (high-byte) lane of each 16-bit field from both packs.
//      That is exactly IDISA hsimd_packh(16, a, b), which IS overridden for ARM in
//      lib/idisa/idisa_arm_builder.cpp:158 (aarch64 uzp2). Result: 16 dense marker
//      bytes, one per code unit, each 0xFF or 0x00.
//   2. WEIGHT: AND with {1,2,4,...,128, 1,2,4,...,128}  (IDISA simd_and with a constant)
//   3. FOLD:   OR-reduce each 8-byte half to a single byte. Two 64-bit lane extractions
//      (IDISA mvmd_extract(64, v, 0/1) -- ONE umov each on AArch64) plus a few scalar
//      shifts/ORs. The OR-fold is order-independent, so it does not depend on lane order.
//
// Cost: 2 lane extractions per 16 code units. signmask_lanewise needs 32 for the same
// 16 code units (two 16-lane movemasks). Same classification work; only the reduction
// differs.
// ---------------------------------------------------------------------------
inline uint16_t reduce_markers_to_bits(u8x16 dense) {
    const u8x16 weights = {1, 2, 4, 8, 16, 32, 64, 128,
                           1, 2, 4, 8, 16, 32, 64, 128};
    const u8x16 w = dense & weights;
    uint64_t half[2];
    std::memcpy(half, &w, sizeof(w));
    uint64_t a = half[0];                 // lanes 0..7
    uint64_t b = half[1];                 // lanes 8..15
    a |= a >> 32; a |= a >> 16; a |= a >> 8;
    b |= b >> 32; b |= b >> 16; b |= b >> 8;
    return static_cast<uint16_t>((a & 0xFF) | ((b & 0xFF) << 8));
}

void llmask_vector_pack_reduce(const uint8_t * data, size_t units, uint64_t * masks) {
    const size_t fullGroups = units / UNITS_PER_MASK;
    u8x16 prevHigh = splat(0);

    for (size_t g = 0; g < fullGroups; ++g) {
        uint64_t m = 0;
        const uint8_t * base = data + g * (UNITS_PER_MASK * 2);
        for (unsigned q = 0; q < 4; ++q) {                // 4 chunks x 16 code units
            const uint8_t * cp = base + q * (2 * BYTES_PER_PACK);
            const u8x16 b0 = load_pack(cp);
            const u8x16 b1 = load_pack(cp + BYTES_PER_PACK);
            const u8x16 b2 = load_pack(cp + 2 * BYTES_PER_PACK);   // lookahead (padded)

            const u8x16 lo1 = (u8x16)((b1 & splat(SUR_MASK)) == splat(LOW_TAG));
            const u8x16 lo2 = (u8x16)((b2 & splat(SUR_MASK)) == splat(LOW_TAG));

            u8x16 hi0, l0, hi1, l1;
            const u8x16 bad0 = classify_pack(b0, prevHigh, lo1, hi0, l0);
            const u8x16 bad1 = classify_pack(b1, hi0, lo2, hi1, l1);
            prevHigh = hi1;

            // hsimd_packh(16, bad0, bad1): the high byte of each 16-bit field.
            const u8x16 dense = __builtin_shufflevector(
                bad0, bad1, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31);
            m |= static_cast<uint64_t>(reduce_markers_to_bits(dense)) << (q * 16);
        }
        masks[g] = m;
    }

    llmask_tail(data, units, masks, fullGroups * UNITS_PER_MASK, prevHigh[15] != 0);
}

// ---------------------------------------------------------------------------
// Driver plumbing
// ---------------------------------------------------------------------------
using Generator = void (*)(const uint8_t *, size_t, uint64_t *);

struct Strategy {
    const char * name;
    Generator fn;
};

const Strategy STRATEGIES[] = {
    {"scalar", llmask_scalar},
    {"signmask_scalarized", llmask_signmask_scalarized},
    {"signmask_optimized", llmask_signmask_optimized},
    {"vector_pack_reduce", llmask_vector_pack_reduce},
};
constexpr size_t NUM_STRATEGIES = sizeof(STRATEGIES) / sizeof(STRATEGIES[0]);

Result run(Generator fn, const std::vector<uint8_t> & padded, size_t nbytes) {
    Result r;
    r.units = nbytes / 2;
    r.oddTrailingByte = static_cast<unsigned>(nbytes & 1);
    r.masks.assign((r.units + UNITS_PER_MASK - 1) / UNITS_PER_MASK, 0);
    fn(padded.data(), r.units, r.masks.data());
    for (uint64_t m : r.masks) r.errorBits += static_cast<uint64_t>(__builtin_popcountll(m));
    return r;
}

// Reads a file and appends PAD_BYTES zero bytes (the k+1 lookahead guarantee).
bool read_padded(const char * path, std::vector<uint8_t> & buf, size_t & nbytes) {
    std::FILE * f = std::fopen(path, "rb");
    if (f == nullptr) {
        std::fprintf(stderr, "ERROR: cannot open %s\n", path);
        return false;
    }
    std::fseek(f, 0, SEEK_END);
    const long sz = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (sz < 0) { std::fclose(f); return false; }
    nbytes = static_cast<size_t>(sz);
    buf.assign(nbytes + PAD_BYTES, 0);
    const size_t got = std::fread(buf.data(), 1, nbytes, f);
    std::fclose(f);
    if (got != nbytes) {
        std::fprintf(stderr, "ERROR: short read on %s\n", path);
        return false;
    }
    return true;
}

std::vector<uint8_t> pad(const std::vector<uint8_t> & bytes) {
    std::vector<uint8_t> b(bytes.size() + PAD_BYTES, 0);
    std::memcpy(b.data(), bytes.data(), bytes.size());
    return b;
}

// --- self-test ---------------------------------------------------------------

std::vector<uint8_t> units_le(const std::vector<uint16_t> & units) {
    std::vector<uint8_t> b;
    b.reserve(units.size() * 2);
    for (uint16_t u : units) {
        b.push_back(static_cast<uint8_t>(u & 0xFF));
        b.push_back(static_cast<uint8_t>(u >> 8));
    }
    return b;
}

}  // namespace llmask

#endif  // LLMASK_GENERATION_H
