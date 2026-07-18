#!/usr/bin/env bash
#
# test_utf16_repair.sh -- correctness suite for the PROTOTYPE U+FFFD repair (issue #40).
#
#     ./scripts/test_utf16_repair.sh
#
# --repair rewrites every ill-formed UTF-16 code unit (located by the issue #32 errorMarks
# stream) to U+FFFD, writing repaired bytes to stdout and the diagnostic errorCount to stderr.
# It is a SEPARATE path: the scalar/SIMD count paths, the errorMarks producer, and the
# TwoLevelScanKernel consumer are unchanged (their suites remain the gates for them).
#
# Repair semantics checked here (see docs/utf16_repair.md):
#   - valid BMP / valid surrogate pairs / valid multilingual text are unchanged;
#   - a lone high, a lone low, and each half of a reversed pair each become U+FFFD;
#   - adjacent malformed surrogates are repaired independently;
#   - an odd trailing byte is DROPPED and one U+FFFD code unit is appended (policy);
#   - LE input repairs to LE bytes (FD FF), BE input to BE bytes (FF FD).
#
# For every case the suite verifies:
#   1. repaired output matches the exact expected bytes (U+FFFD at the right positions),
#   2. validate(repair(input)) == 0   (the repaired output is well-formed),
#   3. repair(repair(input)) == repair(input)   (idempotence),
#   4. valid input is returned byte-for-byte unchanged,
#   5. BE output stays BE.
# Coverage includes 64-code-unit and 4096-code-unit (scan-stride) boundaries and randomized
# inputs, in both LE and BE, plus a 32 MiB malformed dataset when present.
#
# All fixtures live in a temporary directory that is removed on exit.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
BIN="$PARABIX_DIR/build/bin/utf16validate"

[ -x "$BIN" ] || {
    echo "ERROR: utf16validate not found at $BIN" >&2
    echo "       Run ./scripts/setup_parabix.sh first." >&2
    exit 1
}
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

DATADIR="$REPO_ROOT/benchmarks/data"

BIN="$BIN" WORK="$WORK" DATADIR="$DATADIR" python3 - <<'PY'
import os, struct, subprocess, sys, random

BIN = os.environ["BIN"]
WORK = os.environ["WORK"]
DATADIR = os.environ["DATADIR"]

FFFD = 0xFFFD
A, B_ = 0x0041, 0x0042
HI, LO = 0xD83D, 0xDE00          # a valid surrogate pair (emoji)
LONE_HI, LONE_LO = 0xD800, 0xDC00

passed = failed = 0


def pack(units, big_endian, extra=b""):
    fmt = ">H" if big_endian else "<H"
    return b"".join(struct.pack(fmt, u) for u in units) + extra


def run_repair(path, big_endian):
    args = [BIN] + (["-be"] if big_endian else []) + ["--repair", path]
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout


def validate(path, big_endian):
    args = [BIN] + (["-be"] if big_endian else []) + [path]
    out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    return int(out.decode().strip().split("=")[-1])


def write(name, data):
    p = os.path.join(WORK, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def check(name, input_bytes, big_endian, expected_units=None, extra=b""):
    """input given as code units + optional raw extra bytes (for odd trailing byte)."""
    global passed, failed
    tag = "BE" if big_endian else "LE"
    src = input_bytes + extra
    path = write("%s.bin" % name.replace(" ", "_"), src)
    problems = []

    repaired = run_repair(path, big_endian)
    rpath = write("%s.rep" % name.replace(" ", "_"), repaired)

    # 1. exact expected bytes
    if expected_units is not None:
        want = pack(expected_units, big_endian)
        if repaired != want:
            problems.append("bytes: got %s want %s" % (repaired.hex(), want.hex()))

    # 2. repaired output validates clean
    v = validate(rpath, big_endian)
    if v != 0:
        problems.append("validate=%d (not 0)" % v)

    # 3. idempotence
    repaired2 = run_repair(rpath, big_endian)
    if repaired2 != repaired:
        problems.append("not idempotent")

    if problems:
        failed += 1
        print("  FAIL [%s] %-38s %s" % (tag, name, "; ".join(problems)))
    else:
        passed += 1
        print("  PASS [%s] %-38s" % (tag, name))


def both(name, units, expected_le, expected_be, extra=b""):
    check(name, pack(units, False), False, expected_le, extra)
    check(name, pack(units, True), True, expected_be, extra)


print("UTF-16 repair (--repair) correctness suite\n")

# --- fixed cases, LE + BE (expected units are code-unit sequences) ------------------
both("valid BMP", [A, B_, 0x03A9, 0x4E2D], [A, B_, 0x03A9, 0x4E2D], [A, B_, 0x03A9, 0x4E2D])
both("valid surrogate pair", [A, HI, LO, B_], [A, HI, LO, B_], [A, HI, LO, B_])
both("valid multilingual",
     [0x0048, 0x00E9, 0x03A9, 0x0915, 0x0A15, 0x4E2D, 0x6587, HI, LO, 0x0062, FFFD],
     [0x0048, 0x00E9, 0x03A9, 0x0915, 0x0A15, 0x4E2D, 0x6587, HI, LO, 0x0062, FFFD],
     [0x0048, 0x00E9, 0x03A9, 0x0915, 0x0A15, 0x4E2D, 0x6587, HI, LO, 0x0062, FFFD])
both("lone high", [A, LONE_HI, B_], [A, FFFD, B_], [A, FFFD, B_])
both("lone low", [A, LONE_LO, B_], [A, FFFD, B_], [A, FFFD, B_])
both("reversed pair", [LONE_LO, LONE_HI], [FFFD, FFFD], [FFFD, FFFD])
both("adjacent highs", [LONE_HI, LONE_HI, A], [FFFD, FFFD, A], [FFFD, FFFD, A])
both("high at position 0", [LONE_HI, A], [FFFD, A], [FFFD, A])
both("dangling high at EOF", [A, A, LONE_HI], [A, A, FFFD], [A, A, FFFD])
both("empty", [], [], [])

# --- odd trailing byte: drop incomplete byte, append one U+FFFD -----------------------
both("odd trailing byte", [A, B_], [A, B_, FFFD], [A, B_, FFFD], extra=b"\x41")
both("odd after malformed", [LONE_HI], [FFFD, FFFD], [FFFD, FFFD], extra=b"\x41")
both("pure odd byte", [], [FFFD], [FFFD], extra=b"\x41")

# --- 64-code-unit and 4096-code-unit (scan-stride) boundary crossings ----------------
for n in (63, 64, 65, 4095, 4096, 4097):
    # valid pair straddling n -> unchanged
    u = [A] * (n - 1) + [HI, LO] + [A] * 3
    both("valid pair straddling %d" % n, u, u, u)
    # lone high at n-1 and lone low at n+1 -> both become U+FFFD, independently
    u = [A] * (n - 1) + [LONE_HI, A, LONE_LO] + [A] * 3
    exp = [A] * (n - 1) + [FFFD, A, FFFD] + [A] * 3
    both("malformed straddling %d" % n, u, exp, exp)

# --- randomized, LE + BE: only check validity + idempotence (positions via validator) --
def random_units(rng, count):
    u = []
    while len(u) < count:
        r = rng.random()
        if r < 0.12 and count - len(u) >= 2:
            u += [rng.randint(0xD800, 0xDBFF), rng.randint(0xDC00, 0xDFFF)]  # valid pair
        elif r < 0.22:
            u.append(rng.randint(0xD800, 0xDBFF))                            # lone high
        elif r < 0.32:
            u.append(rng.randint(0xDC00, 0xDFFF))                            # lone low
        else:
            u.append(rng.choice([rng.randint(0x20, 0xD7FF), rng.randint(0xE000, 0xFFFD)]))
    return u[:count]


rng = random.Random(40)
for i in range(12):
    n = rng.randint(1, 6000)
    u = random_units(rng, n)
    extra = b"\x37" if i % 3 == 0 else b""   # some odd-length inputs
    be = (i % 2 == 1)
    check("random %d (%d units%s)" % (i, n, ", odd" if extra else ""),
          pack(u, be), be, None, extra)

# --- valid input is returned byte-for-byte unchanged (explicit) ----------------------
valid_le = pack([A, HI, LO, 0x4E2D, B_] * 500, False)
p = write("bigvalid_le.bin", valid_le)
if run_repair(p, False) == valid_le:
    passed += 1; print("  PASS [LE] big valid unchanged (byte-for-byte)")
else:
    failed += 1; print("  FAIL [LE] big valid changed")

# --- 32 MiB malformed dataset, if present --------------------------------------------
big = os.path.join(DATADIR, "malformed_utf16le_mixed_multilingual_random_mix_err0.01_32MiB.bin")
if os.path.isfile(big):
    rep = run_repair(big, False)
    rpath = write("big.rep", rep)
    v = validate(rpath, False)
    idem = run_repair(rpath, False) == rep
    if v == 0 and idem and len(rep) == os.path.getsize(big):
        passed += 1; print("  PASS [LE] 32 MiB malformed dataset (valid, idempotent, size kept)")
    else:
        failed += 1; print("  FAIL [LE] 32 MiB dataset: validate=%d idem=%s" % (v, idem))
else:
    print("  SKIP 32 MiB dataset (not generated; run benchmarks/generate_utf16_benchmark.py)")

print("\n%d passed, %d failed" % (passed, failed))
if failed:
    print("UTF-16 REPAIR TESTS FAILED")
    sys.exit(1)
print("ALL UTF-16 REPAIR TESTS PASSED")
PY

# --- Optional: cross-check against simdutf to_well_formed_utf16 (even-length only) --------
# simdutf's API is char16_t-based, so this comparison is valid only for even-length inputs
# (complete code units); the odd-trailing-byte policy is this project's, not simdutf's.
SIMDUTF_SH="$PARABIX_DIR/../simdutf/singleheader"
if [ -f "$SIMDUTF_SH/simdutf.cpp" ] && command -v c++ >/dev/null 2>&1; then
    echo
    echo "Optional simdutf cross-check (even-length inputs only)..."
    CMP="$WORK/simdutf_cmp"
    cat > "$WORK/cmp.cpp" <<'EOF'
#include "simdutf.h"
#include <cstdio>
#include <cstring>
#include <vector>
int main(int argc, char ** argv) {
    if (argc < 2) return 2;
    FILE * f = fopen(argv[1], "rb"); if (!f) return 2;
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    std::vector<char> buf(n > 0 ? n : 0);
    if (n > 0 && fread(buf.data(), 1, n, f) != (size_t)n) { fclose(f); return 2; }
    fclose(f);
    size_t units = (size_t)n / 2;                        // complete code units only
    const char16_t * in = reinterpret_cast<const char16_t *>(buf.data());
    std::vector<char16_t> out(units ? units : 1);
    bool be = argc > 2 && strcmp(argv[2], "be") == 0;
    if (be) simdutf::to_well_formed_utf16be(in, units, out.data());
    else    simdutf::to_well_formed_utf16le(in, units, out.data());
    if (units) fwrite(out.data(), 2, units, stdout);
    return 0;
}
EOF
    if c++ -O2 -std=c++17 -I "$SIMDUTF_SH" "$WORK/cmp.cpp" "$SIMDUTF_SH/simdutf.cpp" \
           -o "$CMP" 2>/dev/null; then
        sc_pass=0; sc_fail=0
        cross() {   # $1=label $2=flag $3=endian(le|be) $4=input.bin
            "$BIN" $2 --repair "$4" 2>/dev/null > "$WORK/mine.bin"
            "$CMP" "$4" "$3" > "$WORK/simd.bin"
            if cmp -s "$WORK/mine.bin" "$WORK/simd.bin"; then
                echo "  MATCH  $1"; sc_pass=$((sc_pass+1))
            else
                echo "  DIFFER $1"; sc_fail=$((sc_fail+1))
            fi
        }
        mk() { python3 -c "import sys;sys.stdout.buffer.write(bytes.fromhex('$1'))" > "$WORK/x.bin"; }
        mk "410000d84200";     cross "LE lone high"     ""    le "$WORK/x.bin"
        mk "00dc00d8";         cross "LE reversed"      ""    le "$WORK/x.bin"
        mk "00d801d84100";     cross "LE adjacent highs" ""   le "$WORK/x.bin"
        mk "41003dd800de4200"; cross "LE valid pair"    ""    le "$WORK/x.bin"
        mk "0041d8000042";     cross "BE lone high"     "-be" be "$WORK/x.bin"
        mk "dc00d800";         cross "BE reversed"      "-be" be "$WORK/x.bin"
        BIG="$DATADIR/malformed_utf16le_mixed_multilingual_random_mix_err0.01_32MiB.bin"
        [ -f "$BIG" ] && cross "LE 32 MiB malformed" "" le "$BIG"
        echo "  simdutf cross-check: $sc_pass matched, $sc_fail differed"
        [ "$sc_fail" -eq 0 ] || { echo "SIMDUTF CROSS-CHECK FAILED" >&2; exit 1; }
    else
        echo "  (simdutf singleheader present but did not compile; skipping cross-check)"
    fi
else
    echo
    echo "simdutf singleheader not found; skipping optional cross-check"
    echo "  (run ./scripts/setup_clausecker_lemire.sh to enable it)"
fi
