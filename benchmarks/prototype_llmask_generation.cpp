// prototype_llmask_generation.cpp -- issue #29: prototype LLmask generation for UTF-16LE.
//
// This is a STANDALONE PROTOTYPE. It is not a Parabix kernel and it is not wired into
// the validator pipeline. Its only purpose is to answer, with measurements, the question
// that docs/two_level_scan_design.md left open:
//
//     "how do we turn the SIMD validator's per-code-unit verdict into a bitstream
//      (one bit per code unit) cheaply on AArch64, without reintroducing the
//      hsimd_signmask(8) regression that issue #38 removed?"
//
// Definitions used here
// ---------------------
//   * The input is raw UTF-16LE bytes. Code unit k occupies bytes [2k, 2k+1]; only the
//     HIGH byte (2k+1) decides well-formedness:
//         (hi & 0xFC) == 0xD8  ->  high surrogate  (U+D800..U+DBFF)
//         (hi & 0xFC) == 0xDC  ->  low  surrogate  (U+DC00..U+DFFF)
//         otherwise            ->  BMP scalar
//   * An LLmask is a uint64_t covering 64 consecutive code units.
//     Bit i of LLmask g is set iff CODE UNIT (64*g + i) IS ITSELF ILL-FORMED, i.e.
//         bad[k] = (isLow[k]  && !isHigh[k-1])      // low surrogate with no high before it
//               || (isHigh[k] && !isLow[k+1])       // high surrogate with no low after it
//     with isHigh[-1] = pendingHigh (false at start of file) and unit `units` treated as
//     "not a low surrogate" (so a high surrogate at EOF marks itself).
//   * An odd trailing byte is NOT representable in a code-unit-indexed mask -- it has no
//     code unit. It is reported separately, exactly as the validator handles it at EOF.
//
// Relationship to the validator (this is the cross-check that makes the prototype
// trustworthy): the validator counts an error whenever a code unit's verdict changes,
// so for any input
//
//     popcount(all LLmasks) + oddTrailingByte == utf16validate errorCount
//
// The two differ only in WHERE a lone-high error is attributed: the validator's
// XOR formulation (mismatch[k] = isLow[k] ^ isHigh[k-1]) detects it at the SUCCESSOR
// position k, which is fine for counting but is the wrong position for repair. The
// LLmask must mark the offending unit itself, which is why the rule above needs to look
// at unit k+1. See docs/llmask_generation_prototype.md.
//
// Lookahead: every strategy below reads the high byte of unit k+1. The buffer is
// allocated with 32 zero bytes of padding past the end, so the read past the last unit
// is in-bounds and yields 0x00 (not a low surrogate). A Parabix kernel would obtain the
// same guarantee by declaring LookAhead(2) on the byte stream, which the framework
// zero-fills on the final segment.
//
// Strategies compared
// -------------------
// All four produce byte-for-byte identical LLmasks; only the vector -> bitstream
// reduction differs. The classification work is the same in all of them.
//
//   scalar              one byte load + two compares per code unit, LLmask built with
//                       shift/or. Portable C++, no vectors. The reference.
//   signmask_scalarized vector classification, then a movemask realised as one real lane
//                       extraction per lane. MODELS IDISA's hsimd_signmask(8, .) on
//                       AArch64 (no ARM override => the generic <16 x i1> -> i16 bitcast
//                       scalarises into ~16 umov + bfi; issue #38). Cautionary baseline.
//   signmask_optimized  the same movemask, but written so clang can optimise it; on
//                       AArch64 it becomes a vector shift/OR bit-assembly. A FAVOURABLE
//                       stand-in for signmask -- better than IDISA emits today -- so that
//                       the comparison does not rest on IDISA's poor lowering.
//   vector_pack_reduce  vector classification, then the markers are densified with an
//                       odd-lane pack (exactly hsimd_packh(16, a, b), which IS overridden
//                       for ARM -> uzp2) and reduced to bits with a bit-weight AND plus a
//                       64-bit OR-fold. Two lane extractions per 16 code units instead of
//                       signmask's 32. Every step maps to an IDISA primitive that already
//                       exists (see docs/llmask_generation_prototype.md).
//
// Build: see scripts/run_llmask_prototype.sh (compiles to a temp dir; no binary is
// ever written into the repository).
//
// Scope: UTF-16LE on a little-endian host, as everywhere else in this project.

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

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

int failures = 0;

// Checks every strategy against the expected set of ill-formed code-unit positions.
void expect(const char * name, const std::vector<uint8_t> & bytes,
            const std::vector<uint64_t> & badPositions, unsigned expectOddByte) {
    const std::vector<uint8_t> padded = pad(bytes);
    const size_t units = bytes.size() / 2;

    std::vector<uint64_t> want((units + UNITS_PER_MASK - 1) / UNITS_PER_MASK, 0);
    for (uint64_t p : badPositions) want[p / UNITS_PER_MASK] |= uint64_t(1) << (p % UNITS_PER_MASK);

    bool ok = true;
    std::string detail;
    for (const Strategy & s : STRATEGIES) {
        const Result r = run(s.fn, padded, bytes.size());
        if (r.masks != want || r.oddTrailingByte != expectOddByte) {
            ok = false;
            detail += std::string(" [") + s.name + " mismatch]";
        }
    }
    std::printf("  %-4s %-42s units=%-4zu errors=%-2zu oddByte=%u%s\n",
                ok ? "PASS" : "FAIL", name, units, badPositions.size(), expectOddByte,
                detail.c_str());
    if (!ok) ++failures;
}

int self_test() {
    std::printf("LLmask prototype self-test (%zu strategies, all must agree)\n\n",
                NUM_STRATEGIES);

    const uint16_t A = 0x0041;      // 'A'          BMP
    const uint16_t OMEGA = 0x03A9;  // greek omega  BMP
    const uint16_t HAN = 0x4E2D;    // CJK          BMP
    const uint16_t HI = 0xD83D;     // high surrogate (emoji lead)
    const uint16_t LO = 0xDE00;     // low  surrogate (emoji trail)

    // 1. valid BMP
    expect("valid BMP", units_le({A, OMEGA, HAN, A}), {}, 0);

    // 2. valid surrogate pair
    expect("valid surrogate pair", units_le({A, HI, LO, A}), {}, 0);

    // 3. lone high  -- the HIGH surrogate itself (unit 1) is ill-formed
    expect("lone high", units_le({A, HI, A, A}), {1}, 0);

    // 4. lone low   -- the LOW surrogate itself (unit 2) is ill-formed
    expect("lone low", units_le({A, A, 0xDC00, A}), {2}, 0);

    // 5. reversed pair (low then high): both units are ill-formed
    expect("reversed pair", units_le({A, 0xDC00, 0xD800, A}), {1, 2}, 0);

    // 6. valid pair crossing the 64-code-unit boundary (high at 63, low at 64)
    {
        std::vector<uint16_t> u(63, A);
        u.push_back(HI);            // unit 63 -- last unit of LLmask 0
        u.push_back(LO);            // unit 64 -- first unit of LLmask 1
        u.push_back(A);
        expect("valid pair crossing 64-unit boundary", units_le(u), {}, 0);
    }

    // 7. malformed crossing the 64-code-unit boundary: high at 63, BMP at 64.
    //    The error belongs to unit 63 -- in LLmask 0 -- even though it can only be
    //    decided after reading unit 64, which lives in LLmask 1.
    {
        std::vector<uint16_t> u(63, A);
        u.push_back(HI);            // unit 63
        u.push_back(A);             // unit 64
        u.push_back(A);
        expect("malformed crossing 64-unit boundary", units_le(u), {63}, 0);
    }

    // 7b. lone low as the first unit of the second LLmask
    {
        std::vector<uint16_t> u(64, A);
        u.push_back(0xDC00);        // unit 64
        expect("lone low at start of LLmask 1", units_le(u), {64}, 0);
    }

    // 7c. dangling high as the very last unit of a full LLmask group
    {
        std::vector<uint16_t> u(63, A);
        u.push_back(HI);            // unit 63, EOF right after
        expect("dangling high at EOF (group boundary)", units_le(u), {63}, 0);
    }

    // 8. odd trailing byte: it has no code unit, so it is reported separately.
    {
        std::vector<uint8_t> b = units_le({A, OMEGA, HAN});
        b.push_back(0x41);          // a 5th byte -- half a code unit
        expect("odd trailing byte", b, {}, 1);
    }
    {
        // odd trailing byte AND a lone high surrogate (unit 1)
        std::vector<uint8_t> b = units_le({A, HI, A});
        b.push_back(0x41);
        expect("odd trailing byte + lone high", b, {1}, 1);
    }

    // 9. multilingual valid text (Latin, accented, Devanagari, Gurmukhi, CJK, emoji pair)
    {
        std::vector<uint16_t> u = {0x0048, 0x00E9, 0x0915, 0x0A15, 0x4E2D, 0x6587,
                                   0x03A9, 0x0041, HI, LO, 0x0062, 0xFFFD};
        expect("multilingual valid text", units_le(u), {}, 0);
    }

    // Extra: empty input, and a lone high at the very start (no preceding unit)
    expect("empty input", {}, {}, 0);
    expect("lone high at position 0", units_le({HI, A}), {0}, 0);

    // Extra: long run so the vector paths cover several full LLmask groups
    {
        std::vector<uint16_t> u;
        for (int i = 0; i < 300; ++i) u.push_back(A);
        u[100] = 0xDC00;            // lone low
        u[200] = HI;                // lone high
        expect("300 units, errors at 100 and 200", units_le(u), {100, 200}, 0);
    }

    std::printf("\n%s\n", failures == 0 ? "self-test: ALL PASSED" : "self-test: FAILURES");
    return failures == 0 ? 0 : 1;
}

// --- dump (for the Python differential) --------------------------------------

int dump(const char * path, const char * strategyName) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;

    Generator fn = nullptr;
    for (const Strategy & s : STRATEGIES) {
        if (std::strcmp(s.name, strategyName) == 0) fn = s.fn;
    }
    if (fn == nullptr) { std::fprintf(stderr, "ERROR: unknown strategy\n"); return 2; }

    const Result r = run(fn, buf, nbytes);
    std::printf("units=%llu\n", static_cast<unsigned long long>(r.units));
    std::printf("masks=%zu\n", r.masks.size());
    std::printf("errorbits=%llu\n", static_cast<unsigned long long>(r.errorBits));
    std::printf("oddtrailingbyte=%u\n", r.oddTrailingByte);
    for (size_t g = 0; g < r.masks.size(); ++g) {
        if (r.masks[g] != 0) {
            std::printf("%zu %016llx\n", g, static_cast<unsigned long long>(r.masks[g]));
        }
    }
    return 0;
}

// --- cross-check: every strategy must produce identical masks -----------------

int check(const char * path) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;

    const Result ref = run(STRATEGIES[0].fn, buf, nbytes);
    int rc = 0;
    for (size_t i = 1; i < NUM_STRATEGIES; ++i) {
        const Result r = run(STRATEGIES[i].fn, buf, nbytes);
        if (r.masks != ref.masks || r.oddTrailingByte != ref.oddTrailingByte) {
            std::printf("  FAIL %s disagrees with %s\n", STRATEGIES[i].name, STRATEGIES[0].name);
            rc = 1;
        }
    }
    std::printf("  %s %s: units=%llu masks=%zu errorbits=%llu oddtrailingbyte=%u\n",
                rc == 0 ? "PASS" : "FAIL", path,
                static_cast<unsigned long long>(ref.units), ref.masks.size(),
                static_cast<unsigned long long>(ref.errorBits), ref.oddTrailingByte);
    return rc;
}

// --- benchmark ----------------------------------------------------------------

int bench(const char * path, unsigned warmups, unsigned reps) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;

    const size_t units = nbytes / 2;
    const size_t nmasks = (units + UNITS_PER_MASK - 1) / UNITS_PER_MASK;
    std::vector<uint64_t> masks(nmasks, 0);
    const double mib = static_cast<double>(nbytes) / (1024.0 * 1024.0);

    // Sanity: the strategies must agree on this file before any timing is reported.
    const Result ref = run(STRATEGIES[0].fn, buf, nbytes);
    for (size_t i = 1; i < NUM_STRATEGIES; ++i) {
        if (run(STRATEGIES[i].fn, buf, nbytes).masks != ref.masks) {
            std::fprintf(stderr, "ERROR: %s disagrees with %s on %s -- refusing to report timings\n",
                         STRATEGIES[i].name, STRATEGIES[0].name, path);
            return 1;
        }
    }

    std::printf("file            : %s\n", path);
    std::printf("bytes           : %zu (%.2f MiB)\n", nbytes, mib);
    std::printf("code units      : %zu\n", units);
    std::printf("LLmasks         : %zu\n", nmasks);
    std::printf("error bits set  : %llu\n", static_cast<unsigned long long>(ref.errorBits));
    std::printf("odd trailing byte: %u\n", ref.oddTrailingByte);
    std::printf("warmups=%u repetitions=%u (median reported)\n\n", warmups, reps);
    std::printf("  %-20s %12s %12s\n", "strategy", "median MiB/s", "median ms");

    for (const Strategy & s : STRATEGIES) {
        for (unsigned w = 0; w < warmups; ++w) s.fn(buf.data(), units, masks.data());

        std::vector<double> times;
        times.reserve(reps);
        for (unsigned r = 0; r < reps; ++r) {
            std::fill(masks.begin(), masks.end(), 0);
            const auto t0 = std::chrono::steady_clock::now();
            s.fn(buf.data(), units, masks.data());
            const auto t1 = std::chrono::steady_clock::now();
            times.push_back(std::chrono::duration<double>(t1 - t0).count());
            // Keep the optimiser from eliminating the work.
            asm volatile("" : : "r"(masks.data()) : "memory");
        }
        std::sort(times.begin(), times.end());
        const double med = times[times.size() / 2];
        std::printf("  %-20s %12.1f %12.3f\n", s.name, mib / med, med * 1000.0);
    }
    return 0;
}

void usage() {
    std::printf(
        "usage:\n"
        "  prototype_llmask_generation --self-test\n"
        "  prototype_llmask_generation --check FILE\n"
        "  prototype_llmask_generation --dump FILE [--strategy NAME]\n"
        "  prototype_llmask_generation --bench FILE [--warmups N] [--repetitions N]\n"
        "\n"
        "strategies: scalar | signmask_scalarized | signmask_optimized | vector_pack_reduce\n");
}

}  // namespace

int main(int argc, char ** argv) {
    if (argc < 2) { usage(); return 2; }

    const std::string mode = argv[1];
    if (mode == "--self-test") return self_test();

    if (mode == "--check") {
        if (argc < 3) { usage(); return 2; }
        return check(argv[2]);
    }

    if (mode == "--dump") {
        if (argc < 3) { usage(); return 2; }
        const char * strategy = "scalar";
        for (int i = 3; i + 1 < argc; ++i) {
            if (std::strcmp(argv[i], "--strategy") == 0) strategy = argv[i + 1];
        }
        return dump(argv[2], strategy);
    }

    if (mode == "--bench") {
        if (argc < 3) { usage(); return 2; }
        unsigned warmups = 2, reps = 7;
        for (int i = 3; i + 1 < argc; ++i) {
            if (std::strcmp(argv[i], "--warmups") == 0) warmups = std::atoi(argv[i + 1]);
            if (std::strcmp(argv[i], "--repetitions") == 0) reps = std::atoi(argv[i + 1]);
        }
        return bench(argv[2], warmups, reps);
    }

    usage();
    return 2;
}
