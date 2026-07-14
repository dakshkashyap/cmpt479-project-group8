// prototype_error_position_scan.cpp -- issue #31: two-level error-position scan for UTF-16LE.
//
// This is a STANDALONE PROTOTYPE. It is not a Parabix kernel. There is NO TwoLevelScanKernel
// subclass, NO repair, and the production validator (patches/utf16-simd-milestone.patch) is
// untouched. The throughput figures it prints are prototype microbenchmarks on an in-memory
// buffer, not production validator throughput.
//
// What it does
// ------------
// Issue #29 built the low level (LLmask: one uint64_t per 64 code units, bit i set iff code
// unit i is ill-formed) and issue #30 built the high level (maskHL: one uint64_t per 64
// LLmasks, bit w set iff LLmask[64j+w] is nonzero). Both cores are shared verbatim via
// llmask_generation.h and maskhl_aggregation.h, so this prototype scans exactly the masks
// those issues validated against an independent Python reference and against the production
// validator's error count.
//
// This issue adds the CONSUMER: walk maskHL and the LLmasks and emit the EXACT code-unit
// index of every ill-formed code unit.
//
//     for each maskHL word j:
//         if maskHL[j] == 0:  skip the entire 4096-code-unit region     <-- the whole point
//         while maskHL[j] != 0:
//             llIndex  = ctz(maskHL[j])                     // next DIRTY LLmask
//             m        = llmasks[64*j + llIndex]
//             while m != 0:
//                 bit  = ctz(m)                             // next set bit in that LLmask
//                 emit((64*j + llIndex) * 64 + bit)         // exact code-unit position
//                 m   &= m - 1                              // reset lowest bit
//             maskHL[j] &= maskHL[j] - 1                    // reset lowest bit
//
// This is the same shape as Parabix's TwoLevelScanKernel::strideLogic
// (lib/kernel/scan/base.cpp:221), which uses CreateCountForwardZeroes (ctz) and
// CreateResetLowestBit (x & (x-1)) at both levels, and guards the whole stride with
// CreateLikelyCondBr(metaMask != 0, ...). See docs/two_level_scan_design.md.
//
// The odd trailing byte is NOT a code-unit position -- it has no code unit -- so it is
// reported separately, exactly as the production validator handles it at EOF. The invariant
// that ties everything together and is checked on every file:
//
//     number_of_positions + oddTrailingByte  ==  utf16validate errorCount
//
// Scanners compared
// -----------------
// All three emit identical position lists; they differ only in how much they skip.
//
//   two_level   maskHL ctz -> LLmask ctz. Skips clean regions with one compare, and clean
//               LLmasks inside a dirty region for free. The design under test.
//   one_level   ignores maskHL: visits every LLmask, skips the zero ones with a branch, then
//               ctz within. Isolates what the HIGH level actually buys.
//   linear      tests every bit of every LLmask. The dumbest possible consumer; included so
//               "sparse scanning is cheap" is a measured claim rather than an assertion.
//
// Each is timed on top of a mask-generation baseline, so the scan cost is a measured DELTA.
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
// The scanners. Each writes code-unit positions into `out` and returns how many.
// `out` must have room for every set bit (worst case: one per code unit).
// ---------------------------------------------------------------------------

// ctz on a nonzero uint64_t. Parabix spells this CreateCountForwardZeroes
// (include/codegen/CBuilder.h:335).
inline unsigned ctz64(uint64_t x) { return static_cast<unsigned>(__builtin_ctzll(x)); }

// x & (x - 1). Parabix spells this CreateResetLowestBit (CBuilder.h:339).
inline uint64_t reset_lowest_bit(uint64_t x) { return x & (x - 1); }

// The design under test: skip clean regions wholesale, then sparse-scan the dirty ones.
size_t scan_two_level(const uint64_t * masks, size_t nmasks,
                      const uint64_t * hl, size_t nregions, uint64_t * out) {
    size_t n = 0;
    for (size_t j = 0; j < nregions; ++j) {
        uint64_t meta = hl[j];
        if (meta == 0) continue;                     // skip 4096 code units, one compare
        const size_t regionBase = j * MASKS_PER_MASKHL;
        while (meta != 0) {
            const unsigned llIndex = ctz64(meta);    // next DIRTY LLmask
            const size_t g = regionBase + llIndex;
            uint64_t m = masks[g];
            const uint64_t unitBase = static_cast<uint64_t>(g) * UNITS_PER_MASK;
            while (m != 0) {
                out[n++] = unitBase + ctz64(m);      // exact code-unit position
                m = reset_lowest_bit(m);
            }
            meta = reset_lowest_bit(meta);
        }
    }
    (void)nmasks;
    return n;
}

// No maskHL: every LLmask is visited. Isolates what the high level buys.
size_t scan_one_level(const uint64_t * masks, size_t nmasks, uint64_t * out) {
    size_t n = 0;
    for (size_t g = 0; g < nmasks; ++g) {
        uint64_t m = masks[g];
        if (m == 0) continue;
        const uint64_t unitBase = static_cast<uint64_t>(g) * UNITS_PER_MASK;
        while (m != 0) {
            out[n++] = unitBase + ctz64(m);
            m = reset_lowest_bit(m);
        }
    }
    return n;
}

// Every bit of every LLmask is tested. The baseline a sparse scan has to beat.
size_t scan_linear(const uint64_t * masks, size_t nmasks, uint64_t * out) {
    size_t n = 0;
    for (size_t g = 0; g < nmasks; ++g) {
        const uint64_t m = masks[g];
        const uint64_t unitBase = static_cast<uint64_t>(g) * UNITS_PER_MASK;
        for (unsigned i = 0; i < UNITS_PER_MASK; ++i) {
            if ((m >> i) & uint64_t(1)) out[n++] = unitBase + i;
        }
    }
    return n;
}

// ---------------------------------------------------------------------------
// Driving: generate masks, scan with all three, require identical position lists.
// ---------------------------------------------------------------------------
struct ScanResult {
    std::vector<uint64_t> masks;
    std::vector<uint64_t> hl;
    std::vector<uint64_t> positions;
    size_t units = 0;
    unsigned oddTrailingByte = 0;
};

// Returns "" on success, else the reason the scan is not trustworthy.
std::string scan_all(const std::vector<uint8_t> & padded, size_t nbytes, ScanResult & r) {
    r.units = nbytes / 2;
    r.oddTrailingByte = static_cast<unsigned>(nbytes & 1);
    const size_t nmasks = (r.units + UNITS_PER_MASK - 1) / UNITS_PER_MASK;
    const size_t nregions = maskhl_words(nmasks);

    r.masks.assign(nmasks, 0);
    r.hl.assign(nregions, 0);
    llmask_and_maskhl_fused(padded.data(), r.units, r.masks.data(), r.hl.data());

    // The masks themselves must still satisfy issue #30's invariants before we trust a scan
    // over them, and they must still match issue #29's scalar reference generator.
    std::vector<uint64_t> refMasks(nmasks, 0);
    llmask_scalar(padded.data(), r.units, refMasks.data());
    if (refMasks != r.masks) return "LLmasks disagree with the scalar reference generator";
    const std::string inv = verify_invariants(r.masks, r.hl);
    if (!inv.empty()) return "maskHL invariant broken: " + inv;

    size_t errorBits = 0;
    for (uint64_t m : r.masks) errorBits += static_cast<size_t>(__builtin_popcountll(m));

    std::vector<uint64_t> a(errorBits), b(errorBits), c(errorBits);
    const size_t na = scan_two_level(r.masks.data(), nmasks, r.hl.data(), nregions, a.data());
    const size_t nb = scan_one_level(r.masks.data(), nmasks, b.data());
    const size_t nc = scan_linear(r.masks.data(), nmasks, c.data());

    if (na != errorBits) return "two_level emitted the wrong number of positions";
    if (nb != errorBits) return "one_level emitted the wrong number of positions";
    if (nc != errorBits) return "linear emitted the wrong number of positions";
    if (a != b) return "two_level and one_level disagree on the positions";
    if (a != c) return "two_level and linear disagree on the positions";

    // Positions must come out strictly ascending -- a repair pass will depend on that.
    for (size_t i = 1; i < na; ++i) {
        if (a[i] <= a[i - 1]) return "positions are not strictly ascending";
    }
    // ...and every position must be a real code unit.
    for (size_t i = 0; i < na; ++i) {
        if (a[i] >= r.units) return "position is past the end of the input";
    }

    r.positions = std::move(a);
    return "";
}

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

std::string join(const std::vector<uint64_t> & v, size_t limit = 6) {
    std::string s = "[";
    for (size_t i = 0; i < v.size() && i < limit; ++i) {
        if (i) s += ", ";
        s += std::to_string(v[i]);
    }
    if (v.size() > limit) s += ", ...";
    s += "]";
    return s;
}

void expect(const char * name, const std::vector<uint8_t> & bytes,
            const std::vector<uint64_t> & wantPositions, unsigned wantOddByte) {
    ScanResult r;
    const std::string err = scan_all(pad(bytes), bytes.size(), r);

    bool pass = err.empty();
    std::string detail;
    if (!pass) {
        detail = " [" + err + "]";
    } else if (r.positions != wantPositions) {
        pass = false;
        detail = " [got " + join(r.positions) + ", want " + join(wantPositions) + "]";
    } else if (r.oddTrailingByte != wantOddByte) {
        pass = false;
        detail = " [odd trailing byte != expected]";
    }
    std::printf("  %-4s %-48s positions=%-14s oddByte=%u%s\n",
                pass ? "PASS" : "FAIL", name, join(r.positions, 4).c_str(),
                r.oddTrailingByte, detail.c_str());
    if (!pass) ++failures;
}

int self_test() {
    std::printf("error-position scan self-test\n");
    std::printf("  1 LLmask = %zu code units;  1 maskHL word = %zu LLmasks = %zu code units\n",
                UNITS_PER_MASK, MASKS_PER_MASKHL, UNITS_PER_REGION);
    std::printf("  every case is scanned three ways (two_level, one_level, linear) and all\n"
                "  three must emit the identical, strictly ascending position list.\n\n");

    const uint16_t A = 0x0041;          // 'A'
    const uint16_t HI = 0xD83D;         // high surrogate
    const uint16_t LO = 0xDE00;         // low surrogate
    const uint16_t LONE_LOW = 0xDC00;

    // 1. valid input -> empty position list
    expect("valid BMP + valid surrogate pair", units_le({A, A, HI, LO, A}), {}, 0);

    // 2. lone high: the HIGH surrogate itself (unit 1) is ill-formed
    expect("lone high", units_le({A, HI, A, A}), {1}, 0);

    // 3. lone low: the LOW surrogate itself (unit 2) is ill-formed
    expect("lone low", units_le({A, A, LONE_LOW, A}), {2}, 0);

    // 4. reversed pair (low then high): TWO positions
    expect("reversed pair (two positions)", units_le({A, LONE_LOW, 0xD800, A}), {1, 2}, 0);

    // 5. valid pair crossing an LLmask boundary (high at 63, low at 64) -> no positions
    {
        std::vector<uint16_t> u(63, A);
        u.push_back(HI);                // unit 63, last of LLmask 0
        u.push_back(LO);                // unit 64, first of LLmask 1
        u.push_back(A);
        expect("valid pair crossing LLmask boundary", units_le(u), {}, 0);
    }

    // 6. malformed crossing an LLmask boundary: lone high at 63, lone low at 64.
    //    Note these are two SEPARATE errors, not a pair, and they land in different LLmasks.
    {
        std::vector<uint16_t> u(63, A);
        u.push_back(HI);                // unit 63: high, followed by a BMP -> ill-formed
        u.push_back(A);                 // unit 64
        u.push_back(LONE_LOW);          // unit 65: low, preceded by a BMP -> ill-formed
        expect("malformed crossing LLmask boundary", units_le(u), {63, 65}, 0);
    }

    // 7. valid pair crossing a maskHL boundary (high at 4095, low at 4096) -> no positions
    {
        std::vector<uint16_t> u(UNITS_PER_REGION * 2, A);
        u[UNITS_PER_REGION - 1] = HI;   // unit 4095, last of region 0
        u[UNITS_PER_REGION] = LO;       // unit 4096, first of region 1
        expect("valid pair crossing maskHL boundary", units_le(u), {}, 0);
    }

    // 8. malformed crossing a maskHL boundary
    {
        std::vector<uint16_t> u(UNITS_PER_REGION * 2, A);
        u[UNITS_PER_REGION - 1] = LONE_LOW;   // unit 4095, region 0
        u[UNITS_PER_REGION] = LONE_LOW;       // unit 4096, region 1
        expect("malformed crossing maskHL boundary", units_le(u),
               {UNITS_PER_REGION - 1, UNITS_PER_REGION}, 0);
    }

    // 9. errors spread across several maskHL regions, with clean regions in between that the
    //    two-level scan must skip entirely.
    {
        std::vector<uint16_t> u(UNITS_PER_REGION * 5, A);
        u[10] = LONE_LOW;                          // region 0
        u[UNITS_PER_REGION * 2 + 500] = LONE_LOW;  // region 2 (region 1 is clean)
        u[UNITS_PER_REGION * 4 + 4095] = LONE_LOW; // region 4 (region 3 is clean)
        expect("errors across regions 0, 2, 4 (1 and 3 clean)", units_le(u),
               {10, UNITS_PER_REGION * 2 + 500, UNITS_PER_REGION * 4 + 4095}, 0);
    }

    // 10. odd trailing byte: it has NO code unit, so it produces no position. It is an EOF
    //     condition, reported separately -- exactly as the production validator does.
    {
        std::vector<uint8_t> b = units_le({A, A, A});
        b.push_back(0x41);              // half a code unit
        expect("odd trailing byte (no position; EOF condition)", b, {}, 1);
    }
    {
        std::vector<uint8_t> b = units_le({A, HI, A});
        b.push_back(0x41);
        expect("odd trailing byte + lone high at unit 1", b, {1}, 1);
    }

    // Extras
    expect("empty input", {}, {}, 0);
    expect("lone high at position 0", units_le({HI, A}), {0}, 0);
    {
        // Dangling high as the very last code unit: it marks itself.
        std::vector<uint16_t> u(64, A);
        u[63] = HI;
        expect("dangling high at EOF (unit 63)", units_le(u), {63}, 0);
    }
    {
        // Every code unit ill-formed: the scan must still emit them all, in order.
        std::vector<uint16_t> u(200, LONE_LOW);
        std::vector<uint64_t> want(200);
        for (size_t i = 0; i < 200; ++i) want[i] = i;
        expect("every code unit ill-formed (200 positions)", units_le(u), want, 0);
    }

    std::printf("\n%s\n", failures == 0 ? "self-test: ALL PASSED" : "self-test: FAILURES");
    return failures == 0 ? 0 : 1;
}

// ---------------------------------------------------------------------------
// File modes
// ---------------------------------------------------------------------------

// Canonical dump, diffable against benchmarks/llmask_reference.py --positions.
int dump(const char * path) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;
    ScanResult r;
    const std::string err = scan_all(buf, nbytes, r);
    if (!err.empty()) { std::fprintf(stderr, "ERROR: %s\n", err.c_str()); return 1; }

    std::printf("units=%zu\n", r.units);
    std::printf("positions=%zu\n", r.positions.size());
    std::printf("oddtrailingbyte=%u\n", r.oddTrailingByte);
    for (uint64_t p : r.positions) std::printf("%llu\n", (unsigned long long)p);
    return 0;
}

void report(const ScanResult & r) {
    const Stats s = gather(r.masks, r.hl, r.units, r.oddTrailingByte);
    std::printf("  total code units        : %zu\n", s.units);
    std::printf("  total error positions   : %zu\n", r.positions.size());
    std::printf("  odd trailing byte       : %u  (EOF condition -- has no code-unit position)\n",
                s.oddTrailingByte);
    std::printf("  LLmasks                 : %zu\n", s.nmasks);
    std::printf("  dirty LLmasks           : %zu  (%.4f%%)\n", s.nonzeroMasks,
                s.nmasks ? 100.0 * double(s.nonzeroMasks) / double(s.nmasks) : 0.0);
    std::printf("  maskHL words            : %zu   (one per %zu code units)\n",
                s.nregions, UNITS_PER_REGION);
    std::printf("  dirty maskHL words      : %zu  (%.4f%%)\n", s.nonzeroRegions,
                s.nregions ? 100.0 * double(s.nonzeroRegions) / double(s.nregions) : 0.0);
    std::printf("  region skip rate        : %.4f%%\n", s.regionSkipRate());
    std::printf("  LLmask skip rate        : %.4f%%\n", s.maskSkipRate());
}

int stats_mode(const char * path) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;
    ScanResult r;
    const std::string err = scan_all(buf, nbytes, r);
    if (!err.empty()) { std::fprintf(stderr, "ERROR: %s\n", err.c_str()); return 1; }
    std::printf("file: %s\n", path);
    report(r);
    // For the runner's validator cross-check.
    std::printf("positions=%zu\n", r.positions.size());
    std::printf("oddtrailingbyte=%u\n", r.oddTrailingByte);
    return 0;
}

int check(const char * path) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;
    ScanResult r;
    const std::string err = scan_all(buf, nbytes, r);
    if (!err.empty()) {
        std::printf("  FAIL %s: %s\n", path, err.c_str());
        return 1;
    }
    std::printf("  PASS %s\n", path);
    std::printf("       two_level == one_level == linear; %zu positions, strictly ascending,\n"
                "       all in range; maskHL invariants hold; LLmasks match the scalar reference.\n",
                r.positions.size());
    std::printf("positions=%zu\n", r.positions.size());
    std::printf("oddtrailingbyte=%u\n", r.oddTrailingByte);
    return 0;
}

// ---------------------------------------------------------------------------
// Benchmark
// ---------------------------------------------------------------------------
double median_of(std::vector<double> v) {
    std::sort(v.begin(), v.end());
    return v[v.size() / 2];
}

int bench(const char * path, unsigned warmups, unsigned reps) {
    std::vector<uint8_t> buf;
    size_t nbytes = 0;
    if (!read_padded(path, buf, nbytes)) return 2;

    ScanResult r;
    const std::string err = scan_all(buf, nbytes, r);
    if (!err.empty()) {
        std::fprintf(stderr, "ERROR: %s on %s -- refusing to report timings\n",
                     err.c_str(), path);
        return 1;
    }

    const size_t units = r.units;
    const size_t nmasks = r.masks.size();
    const size_t nregions = r.hl.size();
    const double mib = double(nbytes) / (1024.0 * 1024.0);

    std::printf("file: %s\n", path);
    report(r);
    std::printf("\n  warmups=%u repetitions=%u (median reported)\n\n", warmups, reps);

    std::vector<uint64_t> m(nmasks, 0), h(nregions, 0);
    std::vector<uint64_t> out(std::max<size_t>(r.positions.size(), 1));

    // Stage 0 is mask generation with no scan at all. Stages 1..3 add a scanner on top, so
    // the scan cost is the DELTA -- which is the only honest way to report it.
    enum Stage { GEN_ONLY = 0, TWO_LEVEL, ONE_LEVEL, LINEAR };
    struct Row { const char * name; Stage stage; };
    const Row rows[] = {
        {"mask generation only", GEN_ONLY},
        {"  + two_level scan", TWO_LEVEL},
        {"  + one_level scan", ONE_LEVEL},
        {"  + linear scan", LINEAR},
    };

    auto once = [&](Stage st) {
        llmask_and_maskhl_fused(buf.data(), units, m.data(), h.data());
        switch (st) {
            case GEN_ONLY:   break;
            case TWO_LEVEL:  scan_two_level(m.data(), nmasks, h.data(), nregions, out.data()); break;
            case ONE_LEVEL:  scan_one_level(m.data(), nmasks, out.data()); break;
            case LINEAR:     scan_linear(m.data(), nmasks, out.data()); break;
        }
    };

    std::printf("  %-24s %12s %12s %14s\n", "stage", "median MiB/s", "median ms",
                "scan cost (ms)");
    double baseline = 0.0;
    for (const Row & row : rows) {
        for (unsigned w = 0; w < warmups; ++w) once(row.stage);
        std::vector<double> times;
        times.reserve(reps);
        for (unsigned i = 0; i < reps; ++i) {
            std::fill(m.begin(), m.end(), 0);
            std::fill(h.begin(), h.end(), 0);
            const auto t0 = std::chrono::steady_clock::now();
            once(row.stage);
            const auto t1 = std::chrono::steady_clock::now();
            times.push_back(std::chrono::duration<double>(t1 - t0).count());
            asm volatile("" : : "r"(m.data()), "r"(h.data()), "r"(out.data()) : "memory");
        }
        const double med = median_of(times);
        if (row.stage == GEN_ONLY) {
            baseline = med;
            std::printf("  %-24s %12.1f %12.3f %14s\n", row.name, mib / med, med * 1000.0, "--");
        } else {
            std::printf("  %-24s %12.1f %12.3f %14.3f\n", row.name, mib / med, med * 1000.0,
                        (med - baseline) * 1000.0);
        }
    }
    std::printf("\n  'scan cost' is the delta over mask generation. For the two-level scan that\n"
                "  delta is smaller than this benchmark's run-to-run noise, so it is measured\n"
                "  again below in isolation, where it can actually be resolved.\n");

    // ---- the scan, timed on its own, over masks that are already built ----------------
    // The delta above cannot resolve a cost of well under 1% of the baseline. Timing the
    // scanners alone removes the mask-generation noise, so these are the numbers to quote for
    // the scan itself. They are NOT a pipeline result -- a real consumer also pays for mask
    // generation -- which is exactly why both tables are printed.
    llmask_and_maskhl_fused(buf.data(), units, m.data(), h.data());   // build once, reuse

    std::printf("\n  scan only (masks already built; the generation cost above is excluded)\n\n");
    std::printf("  %-24s %12s %14s %16s\n", "scanner", "median ms", "MiB/s of input",
                "vs two_level");

    struct SRow { const char * name; Stage stage; };
    const SRow srows[] = {
        {"two_level", TWO_LEVEL},
        {"one_level", ONE_LEVEL},
        {"linear", LINEAR},
    };
    double twoLevel = 0.0;
    for (const SRow & row : srows) {
        auto scan_once = [&]() {
            switch (row.stage) {
                case TWO_LEVEL: return scan_two_level(m.data(), nmasks, h.data(), nregions,
                                                      out.data());
                case ONE_LEVEL: return scan_one_level(m.data(), nmasks, out.data());
                default:        return scan_linear(m.data(), nmasks, out.data());
            }
        };
        for (unsigned w = 0; w < warmups; ++w) scan_once();
        std::vector<double> times;
        times.reserve(reps);
        for (unsigned i = 0; i < reps; ++i) {
            const auto t0 = std::chrono::steady_clock::now();
            const size_t n = scan_once();
            const auto t1 = std::chrono::steady_clock::now();
            times.push_back(std::chrono::duration<double>(t1 - t0).count());
            asm volatile("" : : "r"(out.data()), "r"(n) : "memory");
        }
        const double med = median_of(times);
        if (row.stage == TWO_LEVEL) twoLevel = med;
        std::printf("  %-24s %12.4f %14.1f %15.1fx\n", row.name, med * 1000.0, mib / med,
                    med / twoLevel);
    }
    std::printf("\n  ('vs two_level' > 1 means that scanner is SLOWER than the two-level scan.)\n");
    return 0;
}

void usage() {
    std::printf(
        "usage:\n"
        "  prototype_error_position_scan --self-test\n"
        "  prototype_error_position_scan --check FILE   # all 3 scanners agree; invariants hold\n"
        "  prototype_error_position_scan --dump  FILE   # canonical position list (diffable)\n"
        "  prototype_error_position_scan --stats FILE   # skip rates and counts\n"
        "  prototype_error_position_scan --bench FILE [--warmups N] [--repetitions N]\n");
}

}  // namespace

int main(int argc, char ** argv) {
    if (argc < 2) { usage(); return 2; }
    const std::string mode = argv[1];

    if (mode == "--self-test") return self_test();
    if (mode == "--check") { if (argc < 3) { usage(); return 2; } return check(argv[2]); }
    if (mode == "--dump")  { if (argc < 3) { usage(); return 2; } return dump(argv[2]); }
    if (mode == "--stats") { if (argc < 3) { usage(); return 2; } return stats_mode(argv[2]); }
    if (mode == "--bench") {
        if (argc < 3) { usage(); return 2; }
        unsigned warmups = 2, reps = 15;
        for (int i = 3; i + 1 < argc; ++i) {
            if (std::strcmp(argv[i], "--warmups") == 0) warmups = std::atoi(argv[i + 1]);
            if (std::strcmp(argv[i], "--repetitions") == 0) reps = std::atoi(argv[i + 1]);
        }
        return bench(argv[2], warmups, reps);
    }
    usage();
    return 2;
}
