// maskhl_aggregation.h -- shared maskHL aggregation core (issues #30, #31).
//
// The high level of the two-level scan. One maskHL word summarises 64 LLmasks:
//
//     maskHL[j] bit w  =  1  iff  LLmask[64*j + w] != 0
//
// so one word covers 64 * 64 = 4096 UTF-16 code units = 8192 bytes. If maskHL[j] == 0 the
// whole region is clean and a consumer can skip it with a single compare and branch.
//
// This header holds the three aggregation strategies, the statistics gatherer and the
// invariant checker. It is included by BOTH prototypes:
//
//   benchmarks/prototype_maskhl_aggregation.cpp   (issue #30 -- validates the aggregation)
//   benchmarks/prototype_error_position_scan.cpp  (issue #31 -- scans it for positions)
//
// It exists so the two cannot drift apart: the scanner walks exactly the maskHL that issue
// #30 validated against its invariants and against the production validator's error count.
// The code below was moved here verbatim from prototype_maskhl_aggregation.cpp; nothing
// about the algorithms changed.
//
// These are PROTOTYPES. None of this is a Parabix kernel, there is no TwoLevelScanKernel
// subclass, there is no repair, and the production validator (delivered via
// patches/utf16-simd-milestone.patch) is untouched by any of them.
//
// The LLmask core these build on lives in llmask_generation.h (issue #29).
//
// Scope: UTF-16LE on a little-endian host, as everywhere else in this project.

#ifndef MASKHL_AGGREGATION_H
#define MASKHL_AGGREGATION_H

#include "llmask_generation.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace maskhl {

using namespace llmask;

constexpr size_t MASKS_PER_MASKHL = 64;                              // LLmasks per maskHL word
constexpr size_t UNITS_PER_REGION = UNITS_PER_MASK * MASKS_PER_MASKHL;  // 4096 code units

inline size_t maskhl_words(size_t nmasks) {
    return (nmasks + MASKS_PER_MASKHL - 1) / MASKS_PER_MASKHL;
}

// ---------------------------------------------------------------------------
// Strategy A: separate_pass -- LLmasks first, then a second pass over the array.
// ---------------------------------------------------------------------------
void maskhl_separate_pass(const uint64_t * masks, size_t nmasks, uint64_t * hl) {
    const size_t nregions = maskhl_words(nmasks);
    for (size_t j = 0; j < nregions; ++j) {
        const size_t base = j * MASKS_PER_MASKHL;
        const size_t len = std::min(MASKS_PER_MASKHL, nmasks - base);
        uint64_t h = 0;
        for (size_t w = 0; w < len; ++w) {
            h |= static_cast<uint64_t>(masks[base + w] != 0) << w;
        }
        hl[j] = h;
    }
}

// ---------------------------------------------------------------------------
// Strategy B: vector_any64 -- models Parabix's generateIndexComputation.
//
// simd_any(64, block) asks "does each 64-bit field contain any set bit?", and
// hsimd_signmask(64, .) collapses that to one bit per field. On a 128-bit block that is two
// lanes, so it costs TWO lane extractions per 128 bits -- not the sixteen that fw=8 needs.
// That difference is the entire reason the scan kernel's own index computation is not
// expected to suffer the issue #38 regression, and modelling it here lets us check that.
// ---------------------------------------------------------------------------
using u64x2 = uint64_t __attribute__((vector_size(16)));

void maskhl_vector_any64(const uint64_t * masks, size_t nmasks, uint64_t * hl) {
    const size_t nregions = maskhl_words(nmasks);
    for (size_t j = 0; j < nregions; ++j) {
        const size_t base = j * MASKS_PER_MASKHL;
        const size_t len = std::min(MASKS_PER_MASKHL, nmasks - base);
        uint64_t h = 0;
        size_t w = 0;
        for (; w + 2 <= len; w += 2) {                  // one 128-bit block = 2 LLmasks
            u64x2 v;
            std::memcpy(&v, masks + base + w, sizeof(v));
            // simd_any(64, v): 0 or ~0 per 64-bit lane.
            const u64x2 any = (u64x2)(v != (u64x2){0, 0});
            // hsimd_signmask(64, any): one bit per lane.
            h |= ((any[0] & 1u) | ((any[1] & 1u) << 1)) << w;
        }
        for (; w < len; ++w) {                          // odd final LLmask, if any
            h |= static_cast<uint64_t>(masks[base + w] != 0) << w;
        }
        hl[j] = h;
    }
}

// ---------------------------------------------------------------------------
// Strategy C: fused -- maskHL is built during LLmask generation, from the register.
//
// The body is llmask_vector_pack_reduce (issue #29's winner, shared from the header) with
// one extra OR per group. Nothing about the LLmask computation changes, which is why the
// self-test can demand that this produces bit-identical LLmasks AND bit-identical maskHL to
// the separate-pass strategies.
// ---------------------------------------------------------------------------
void llmask_and_maskhl_fused(const uint8_t * data, size_t units,
                             uint64_t * masks, uint64_t * hl) {
    const size_t fullGroups = units / UNITS_PER_MASK;
    const size_t nmasks = (units + UNITS_PER_MASK - 1) / UNITS_PER_MASK;
    u8x16 prevHigh = splat(0);
    uint64_t h = 0;

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

            const u8x16 dense = __builtin_shufflevector(
                bad0, bad1, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31);
            m |= static_cast<uint64_t>(reduce_markers_to_bits(dense)) << (q * 16);
        }
        masks[g] = m;
        // The maskHL bit, taken while the LLmask is still in a register.
        h |= static_cast<uint64_t>(m != 0) << (g & (MASKS_PER_MASKHL - 1));
        if ((g & (MASKS_PER_MASKHL - 1)) == MASKS_PER_MASKHL - 1) {
            hl[g / MASKS_PER_MASKHL] = h;
            h = 0;
        }
    }

    llmask_tail(data, units, masks, fullGroups * UNITS_PER_MASK, prevHigh[15] != 0);

    if (nmasks == 0) return;
    if (nmasks > fullGroups) {   // a final partial LLmask exists; fold it into h
        h |= static_cast<uint64_t>(masks[fullGroups] != 0)
             << (fullGroups & (MASKS_PER_MASKHL - 1));
    }
    // Flush the final region if the in-loop flush above never reached it. That happens
    // whenever the last region is not completely filled by whole LLmask groups -- including
    // the case where a partial LLmask is the 64th mask of its region.
    const size_t lastRegion = (nmasks - 1) / MASKS_PER_MASKHL;
    if (fullGroups < MASKS_PER_MASKHL * (lastRegion + 1)) {
        hl[lastRegion] = h;
    }
}

// ---------------------------------------------------------------------------
// Statistics and correctness
// ---------------------------------------------------------------------------
struct Stats {
    size_t units = 0;
    size_t nmasks = 0;
    size_t nonzeroMasks = 0;
    size_t nregions = 0;
    size_t nonzeroRegions = 0;
    uint64_t errorBits = 0;
    unsigned oddTrailingByte = 0;

    double regionSkipRate() const {
        return nregions ? 100.0 * double(nregions - nonzeroRegions) / double(nregions) : 100.0;
    }
    double maskSkipRate() const {
        return nmasks ? 100.0 * double(nmasks - nonzeroMasks) / double(nmasks) : 100.0;
    }
};

Stats gather(const std::vector<uint64_t> & masks, const std::vector<uint64_t> & hl,
             size_t units, unsigned oddByte) {
    Stats s;
    s.units = units;
    s.nmasks = masks.size();
    s.nregions = hl.size();
    s.oddTrailingByte = oddByte;
    for (uint64_t m : masks) {
        if (m != 0) ++s.nonzeroMasks;
        s.errorBits += static_cast<uint64_t>(__builtin_popcountll(m));
    }
    for (uint64_t h : hl) {
        if (h != 0) ++s.nonzeroRegions;
    }
    return s;
}

// The three invariants issue #30 asks for. Returns an empty string on success.
std::string verify_invariants(const std::vector<uint64_t> & masks,
                              const std::vector<uint64_t> & hl) {
    if (hl.size() != maskhl_words(masks.size())) return "maskHL word count is wrong";

    // (1) popcount(maskHL) == number of nonzero LLmasks
    size_t hlBits = 0;
    for (uint64_t h : hl) hlBits += static_cast<size_t>(__builtin_popcountll(h));
    size_t nonzero = 0;
    for (uint64_t m : masks) if (m != 0) ++nonzero;
    if (hlBits != nonzero) return "popcount(maskHL) != number of nonzero LLmasks";

    // (2) every set bit in maskHL corresponds to a nonzero LLmask, and
    // (3) every nonzero LLmask sets the matching bit in maskHL.
    // Checked together, position by position, in both directions.
    for (size_t g = 0; g < masks.size(); ++g) {
        const bool bitSet =
            (hl[g / MASKS_PER_MASKHL] >> (g % MASKS_PER_MASKHL)) & uint64_t(1);
        const bool maskNonzero = masks[g] != 0;
        if (bitSet && !maskNonzero) return "maskHL bit set for a ZERO LLmask";
        if (maskNonzero && !bitSet) return "nonzero LLmask with a CLEAR maskHL bit";
    }
    // No maskHL bit may be set beyond the last LLmask (padding bits must be clear).
    if (!hl.empty()) {
        const size_t used = masks.size() % MASKS_PER_MASKHL;
        if (used != 0) {
            const uint64_t tailBits = hl.back() >> used;
            if (tailBits != 0) return "maskHL has bits set past the last LLmask";
        }
    }
    return "";
}

// Runs every strategy on the same input and requires that they all agree.
// Returns the (agreed) LLmasks and maskHL.
bool compute_all(const std::vector<uint8_t> & padded, size_t nbytes,
                 std::vector<uint64_t> & masks, std::vector<uint64_t> & hl,
                 std::string & err) {
    const size_t units = nbytes / 2;
    const size_t nmasks = (units + UNITS_PER_MASK - 1) / UNITS_PER_MASK;
    const size_t nregions = maskhl_words(nmasks);

    masks.assign(nmasks, 0);
    hl.assign(nregions, 0);
    llmask_vector_pack_reduce(padded.data(), units, masks.data());
    maskhl_separate_pass(masks.data(), nmasks, hl.data());

    std::vector<uint64_t> masksB(nmasks, 0), hlB(nregions, 0);
    llmask_vector_pack_reduce(padded.data(), units, masksB.data());
    maskhl_vector_any64(masksB.data(), nmasks, hlB.data());
    if (hlB != hl) { err = "vector_any64 disagrees with separate_pass"; return false; }

    std::vector<uint64_t> masksC(nmasks, 0), hlC(nregions, 0);
    llmask_and_maskhl_fused(padded.data(), units, masksC.data(), hlC.data());
    if (masksC != masks) { err = "fused produced different LLmasks"; return false; }
    if (hlC != hl) { err = "fused produced a different maskHL"; return false; }

    // The LLmasks must also match the scalar reference generator (issue #29's oracle).
    std::vector<uint64_t> masksRef(nmasks, 0);
    llmask_scalar(padded.data(), units, masksRef.data());
    if (masksRef != masks) { err = "LLmasks disagree with the scalar reference"; return false; }

    err = verify_invariants(masks, hl);
    return err.empty();
}

}  // namespace maskhl

#endif  // MASKHL_AGGREGATION_H
