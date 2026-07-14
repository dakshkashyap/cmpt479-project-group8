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
#include "llmask_generation.h"

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
