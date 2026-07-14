// prototype_maskhl_aggregation.cpp -- issue #30: maskHL aggregation prototype for UTF-16LE.
//
// This is a STANDALONE PROTOTYPE. It is not a Parabix kernel, it is not wired into the
// validator pipeline, there is no TwoLevelScanKernel subclass, and there is no repair.
// The production validator (patches/utf16-simd-milestone.patch) is untouched.
//
// What it does
// ------------
// Issue #29 established how to produce an LLmask: one uint64_t per 64 UTF-16 code units,
// bit i set iff code unit i is ill-formed. That generation code is shared verbatim via
// llmask_generation.h, so this prototype aggregates exactly the LLmasks that #29 validated
// against an independent Python reference and against the production validator's count.
//
// This issue adds the SECOND level of the two-level scan:
//
//     maskHL[j] bit w  =  1  iff  LLmask[64*j + w] != 0
//
// So one maskHL word summarises 64 LLmasks = 64 * 64 = 4096 code units = 8192 bytes.
// If maskHL[j] == 0 the whole 4096-code-unit region is clean and a future scan can skip it
// with a single compare and branch. If maskHL[j] != 0 the scan visits only the LLmasks whose
// bit is set (cttz / reset-lowest-bit), never the clean ones.
//
// This mirrors what Parabix's TwoLevelScanKernel already does in
// lib/kernel/scan/base.cpp:348 (generateIndexComputation):
//
//     Value * anyBitInField = b.simd_any(mScanWordWidth, s);                 // per 64-bit word
//     Value * indexMask     = b.hsimd_signmask(mScanWordWidth, anyBitInField); // 1 bit per word
//     masks[i] = b.CreateOr(maskPhi[i], b.CreateShl(indexMask, wordCounter));
//
// and its stride geometry (base.cpp:205) works out to exactly 64 scanwords x 64 bits.
// Note that its hsimd_signmask is at fw = 64, which on a 128-bit block is only TWO lanes --
// a completely different cost class from the fw=8 (16-lane) signmask that issue #38 found
// pathological on AArch64. One of the aggregation strategies below models that fw=64 shape
// directly so the claim can be checked rather than asserted.
//
// Aggregation strategies compared
// -------------------------------
// All three produce identical maskHL arrays; only how the "is this LLmask nonzero" bit is
// gathered differs.
//
//   separate_pass   generate every LLmask first, then walk the mask array a second time and
//                   OR the nonzero bits together branchlessly.
//   fused           compute maskHL inside the LLmask generation loop, while the mask is still
//                   in a register. No second pass over memory. This is what a producer kernel
//                   would naturally do.
//   vector_any64    a second pass, but modelling Parabix's own index computation: treat pairs
//                   of LLmasks as a 128-bit block, test each 64-bit lane for "any bit set",
//                   and collect one bit per lane (simd_any(64) + hsimd_signmask(64)).
//
// The baseline `llmask_only` (no maskHL at all) is timed alongside them so the aggregation
// OVERHEAD is a measured delta, not an assumption.
//
// Build: see scripts/run_maskhl_prototype.sh (compiles to a temp dir; no binary is ever
// written into the repository).
//
// Scope: UTF-16LE on a little-endian host, as everywhere else in this project.
#include "maskhl_aggregation.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

using namespace llmask;
using namespace maskhl;


// ---------------------------------------------------------------------------
// Self-test
// ---------------------------------------------------------------------------
int failures = 0;

std::vector<uint8_t> units_le(const std::vector<uint16_t> & units) {
    std::vector<uint8_t> b;
    b.reserve(units.size() * 2);
    for (uint16_t u : units) {
        b.push_back(static_cast<uint8_t>(u & 0xFF));
        b.push_back(static_cast<uint8_t>(u >> 8));
    }
    return b;
}

// expectedRegions: for each maskHL word, the exact expected value.
void expect(const char * name, const std::vector<uint8_t> & bytes,
            const std::vector<uint64_t> & expectedHL, unsigned expectOddByte) {
    const std::vector<uint8_t> padded = pad(bytes);
    std::vector<uint64_t> masks, hl;
    std::string err;
    const bool ok = compute_all(padded, bytes.size(), masks, hl, err);
    const Stats s = gather(masks, hl, bytes.size() / 2, bytes.size() & 1u);

    std::string detail;
    bool pass = ok;
    if (!ok) detail = std::string(" [") + err + "]";
    if (pass && hl != expectedHL) {
        pass = false;
        detail = " [maskHL != expected]";
    }
    if (pass && s.oddTrailingByte != expectOddByte) {
        pass = false;
        detail = " [odd trailing byte != expected]";
    }
    std::printf("  %-4s %-46s LLmasks=%-4zu nz=%-3zu maskHL=%-2zu nz=%-2zu skip=%5.1f%%%s\n",
                pass ? "PASS" : "FAIL", name, s.nmasks, s.nonzeroMasks, s.nregions,
                s.nonzeroRegions, s.regionSkipRate(), detail.c_str());
    if (!pass) ++failures;
}

int self_test() {
    std::printf("maskHL aggregation self-test\n");
    std::printf("  one LLmask  = %zu code units;  one maskHL = %zu LLmasks = %zu code units\n",
                UNITS_PER_MASK, MASKS_PER_MASKHL, UNITS_PER_REGION);
    std::printf("  every case is checked with separate_pass, vector_any64 and fused, and all\n"
                "  three must agree with each other, with the scalar LLmask reference, and\n"
                "  with the three maskHL invariants.\n\n");

    const uint16_t A = 0x0041;      // 'A'
    const uint16_t HI = 0xD83D;     // high surrogate
    const uint16_t LO = 0xDE00;     // low surrogate
    const uint16_t LONE_LOW = 0xDC00;

    // 1. All valid data: maskHL must be zero everywhere.
    {
        std::vector<uint16_t> u(UNITS_PER_REGION * 2 + 100, A);
        for (size_t i = 0; i + 1 < u.size(); i += 64) { u[i] = HI; u[i + 1] = LO; }  // valid pairs
        std::vector<uint64_t> want(maskhl_words((u.size() + 63) / 64), 0);
        expect("all valid (2+ regions, incl. surrogate pairs)", units_le(u), want, 0);
    }

    // 2. One error in one LLmask -> exactly one bit in one maskHL word.
    {
        std::vector<uint16_t> u(UNITS_PER_REGION, A);
        u[130] = LONE_LOW;                                  // LLmask 2 (130/64 = 2)
        std::vector<uint64_t> want(1, uint64_t(1) << 2);
        expect("one error in LLmask 2", units_le(u), want, 0);
    }

    // 3. Errors spread across multiple LLmasks, within one region.
    {
        std::vector<uint16_t> u(UNITS_PER_REGION, A);
        u[0] = LONE_LOW;        // LLmask 0
        u[64] = LONE_LOW;       // LLmask 1
        u[700] = LONE_LOW;      // LLmask 10
        u[4000] = LONE_LOW;     // LLmask 62
        std::vector<uint64_t> want(1, (uint64_t(1) << 0) | (uint64_t(1) << 1) |
                                      (uint64_t(1) << 10) | (uint64_t(1) << 62));
        expect("errors spread across 4 LLmasks (one region)", units_le(u), want, 0);
    }

    // 4. Errors at LLmask boundaries: last unit of LLmask 0 and first unit of LLmask 1.
    //    A lone high at unit 63 belongs to LLmask 0; a lone low at unit 64 to LLmask 1.
    {
        std::vector<uint16_t> u(UNITS_PER_REGION, A);
        u[63] = HI;             // lone high, no low follows -> error at unit 63 (LLmask 0)
        u[64] = A;
        u[128] = LONE_LOW;      // lone low at unit 128 -> LLmask 2
        std::vector<uint64_t> want(1, (uint64_t(1) << 0) | (uint64_t(1) << 2));
        expect("errors at LLmask boundary (units 63, 128)", units_le(u), want, 0);
    }

    // 4b. A VALID surrogate pair straddling an LLmask boundary must NOT set any bit.
    {
        std::vector<uint16_t> u(UNITS_PER_REGION, A);
        u[63] = HI; u[64] = LO;                             // valid pair across LLmask 0 | 1
        std::vector<uint64_t> want(1, 0);
        expect("valid pair straddling LLmask boundary", units_le(u), want, 0);
    }

    // 5. Errors at maskHL boundaries: the last LLmask of region 0 (LLmask 63, units
    //    4032..4095) and the first LLmask of region 1 (LLmask 64, units 4096..4159).
    {
        std::vector<uint16_t> u(UNITS_PER_REGION * 2, A);
        u[4095] = LONE_LOW;     // last unit of region 0 -> LLmask 63, maskHL 0 bit 63
        u[4096] = LONE_LOW;     // first unit of region 1 -> LLmask 64, maskHL 1 bit 0
        std::vector<uint64_t> want = {uint64_t(1) << 63, uint64_t(1) << 0};
        expect("errors at maskHL boundary (units 4095, 4096)", units_le(u), want, 0);
    }

    // 5b. A VALID surrogate pair straddling the maskHL boundary must NOT set any bit.
    {
        std::vector<uint16_t> u(UNITS_PER_REGION * 2, A);
        u[4095] = HI; u[4096] = LO;
        std::vector<uint64_t> want = {0, 0};
        expect("valid pair straddling maskHL boundary", units_le(u), want, 0);
    }

    // 5c. A partial final region AND a partial final LLmask -- the case where the fused
    //     strategy's end-of-loop flush is easy to get wrong.
    {
        std::vector<uint16_t> u(UNITS_PER_REGION + 4064, A);   // 4064 = 63 full LLmasks + 32
        u[UNITS_PER_REGION + 4063] = LONE_LOW;                 // last unit, LLmask 63 of region 1
        std::vector<uint64_t> want = {0, uint64_t(1) << 63};
        expect("partial region + partial LLmask (4064 tail units)", units_le(u), want, 0);
    }

    // 6. Odd trailing byte: it has no code unit, so it can set no maskHL bit.
    {
        std::vector<uint16_t> u(200, A);
        std::vector<uint8_t> b = units_le(u);
        b.push_back(0x41);                                  // half a code unit
        std::vector<uint64_t> want(1, 0);
        expect("odd trailing byte (no maskHL bit possible)", b, want, 1);
    }
    {
        std::vector<uint16_t> u(200, A);
        u[65] = LONE_LOW;                                   // LLmask 1
        std::vector<uint8_t> b = units_le(u);
        b.push_back(0x41);
        std::vector<uint64_t> want(1, uint64_t(1) << 1);
        expect("odd trailing byte + error in LLmask 1", b, want, 1);
    }

    // Extras: empty input, and a fully dirty region (skip rate must be 0%).
    expect("empty input", {}, {}, 0);
    {
        std::vector<uint16_t> u(UNITS_PER_REGION, LONE_LOW);   // every unit ill-formed
        std::vector<uint64_t> want(1, ~uint64_t(0));
        expect("every LLmask dirty (skip rate must be 0%)", units_le(u), want, 0);
    }

    std::printf("\n%s\n", failures == 0 ? "self-test: ALL PASSED" : "self-test: FAILURES");
    return failures == 0 ? 0 : 1;
}

// ---------------------------------------------------------------------------
// File modes
// ---------------------------------------------------------------------------
void print_stats(const Stats & s) {
    std::printf("  code units             : %zu\n", s.units);
    std::printf("  total error bits        : %llu\n", (unsigned long long)s.errorBits);
    std::printf("  odd trailing byte       : %u\n", s.oddTrailingByte);
    std::printf("  LLmasks                 : %zu\n", s.nmasks);
    std::printf("  nonzero (dirty) LLmasks : %zu  (%.4f%%)\n", s.nonzeroMasks,
                s.nmasks ? 100.0 * double(s.nonzeroMasks) / double(s.nmasks) : 0.0);
    std::printf("  maskHL words            : %zu   (one per %zu code units)\n",
                s.nregions, UNITS_PER_REGION);
    std::printf("  nonzero maskHL words    : %zu  (%.4f%%)\n", s.nonzeroRegions,
                s.nregions ? 100.0 * double(s.nonzeroRegions) / double(s.nregions) : 0.0);
    std::printf("  CLEAN-REGION SKIP RATE  : %.4f%%  (regions a scan can skip outright)\n",
                s.regionSkipRate());
    std::printf("  clean-LLmask skip rate  : %.4f%%  (LLmasks a scan never has to look at)\n",
                s.maskSkipRate());
}

int check(const char * path) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;

    std::vector<uint64_t> masks, hl;
    std::string err;
    if (!compute_all(buf, nbytes, masks, hl, err)) {
        std::printf("  FAIL %s: %s\n", path, err.c_str());
        return 1;
    }
    const Stats s = gather(masks, hl, nbytes / 2, static_cast<unsigned>(nbytes & 1));
    std::printf("  PASS %s\n", path);
    std::printf("       all 3 aggregation strategies agree; LLmasks match the scalar reference;\n"
                "       popcount(maskHL)=%zu == nonzero LLmasks=%zu; no stray/missing bits.\n",
                s.nonzeroMasks, s.nonzeroMasks);
    // Printed so the runner script can cross-check against the production validator:
    //     errorbits + oddtrailingbyte == utf16validate errorCount
    std::printf("errorbits=%llu\n", (unsigned long long)s.errorBits);
    std::printf("oddtrailingbyte=%u\n", s.oddTrailingByte);
    return 0;
}

int stats_mode(const char * path) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;
    std::vector<uint64_t> masks, hl;
    std::string err;
    if (!compute_all(buf, nbytes, masks, hl, err)) {
        std::fprintf(stderr, "ERROR: %s\n", err.c_str());
        return 1;
    }
    std::printf("file: %s\n", path);
    print_stats(gather(masks, hl, nbytes / 2, static_cast<unsigned>(nbytes & 1)));
    return 0;
}

// ---------------------------------------------------------------------------
// Benchmark
// ---------------------------------------------------------------------------
double median_time(const std::vector<double> & t) {
    std::vector<double> v = t;
    std::sort(v.begin(), v.end());
    return v[v.size() / 2];
}

int bench(const char * path, unsigned warmups, unsigned reps) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;

    std::vector<uint64_t> masks, hl;
    std::string err;
    if (!compute_all(buf, nbytes, masks, hl, err)) {
        std::fprintf(stderr, "ERROR: %s on %s -- refusing to report timings\n",
                     err.c_str(), path);
        return 1;
    }

    const size_t units = nbytes / 2;
    const size_t nmasks = masks.size();
    const size_t nregions = hl.size();
    const double mib = double(nbytes) / (1024.0 * 1024.0);

    std::printf("file: %s\n", path);
    print_stats(gather(masks, hl, units, static_cast<unsigned>(nbytes & 1)));
    std::printf("\n  warmups=%u repetitions=%u (median reported)\n\n", warmups, reps);
    std::printf("  %-28s %12s %12s %14s\n", "stage", "median MiB/s", "median ms",
                "vs LLmask only");

    std::vector<uint64_t> m(nmasks, 0), h(nregions, 0);
    double baseline = 0.0;

    struct Run {
        const char * name;
        int kind;   // 0 = LLmask only, 1 = +separate_pass, 2 = +vector_any64, 3 = fused
    };
    const Run runs[] = {
        {"llmask_only (baseline)", 0},
        {"llmask + separate_pass", 1},
        {"llmask + vector_any64", 2},
        {"llmask + fused", 3},
    };

    for (const Run & r : runs) {
        for (unsigned w = 0; w < warmups; ++w) {
            if (r.kind == 3) {
                llmask_and_maskhl_fused(buf.data(), units, m.data(), h.data());
            } else {
                llmask_vector_pack_reduce(buf.data(), units, m.data());
                if (r.kind == 1) maskhl_separate_pass(m.data(), nmasks, h.data());
                if (r.kind == 2) maskhl_vector_any64(m.data(), nmasks, h.data());
            }
        }
        std::vector<double> times;
        times.reserve(reps);
        for (unsigned i = 0; i < reps; ++i) {
            std::fill(m.begin(), m.end(), 0);
            std::fill(h.begin(), h.end(), 0);
            const auto t0 = std::chrono::steady_clock::now();
            if (r.kind == 3) {
                llmask_and_maskhl_fused(buf.data(), units, m.data(), h.data());
            } else {
                llmask_vector_pack_reduce(buf.data(), units, m.data());
                if (r.kind == 1) maskhl_separate_pass(m.data(), nmasks, h.data());
                if (r.kind == 2) maskhl_vector_any64(m.data(), nmasks, h.data());
            }
            const auto t1 = std::chrono::steady_clock::now();
            times.push_back(std::chrono::duration<double>(t1 - t0).count());
            asm volatile("" : : "r"(m.data()), "r"(h.data()) : "memory");
        }
        const double med = median_time(times);
        if (r.kind == 0) baseline = med;
        std::printf("  %-28s %12.1f %12.3f %13.2fx\n", r.name, mib / med, med * 1000.0,
                    baseline / med);
    }
    std::printf("\n  ('vs LLmask only' below 1.00x is the cost of aggregation, not a speedup.)\n");
    return 0;
}

void usage() {
    std::printf(
        "usage:\n"
        "  prototype_maskhl_aggregation --self-test\n"
        "  prototype_maskhl_aggregation --check FILE     # invariants + all strategies agree\n"
        "  prototype_maskhl_aggregation --stats FILE     # skip-rate statistics\n"
        "  prototype_maskhl_aggregation --bench FILE [--warmups N] [--repetitions N]\n");
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
    if (mode == "--stats") {
        if (argc < 3) { usage(); return 2; }
        return stats_mode(argv[2]);
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
