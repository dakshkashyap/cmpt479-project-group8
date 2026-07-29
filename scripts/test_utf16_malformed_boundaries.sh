#!/usr/bin/env bash
#
# test_utf16_malformed_boundaries.sh -- malformed and boundary correctness suite (issue #41).
#
#     ./scripts/test_utf16_malformed_boundaries.sh
#
# The existing suites each cover one path: test_utf16validate.sh is the scalar/SIMD count
# gate (UTF-16LE), test_errormarks.sh the producer, test_scan_consumer.sh the two-level
# scan, test_utf16be.sh the --be paths. This suite is the CROSS-PRODUCT: every fixture is
# built once as a code-unit sequence and then checked in BOTH encodings across ALL FOUR
# validation paths, so a disagreement between any two of them fails here even if each
# individual suite still passes.
#
# For every fixture:
#
#   encodings   UTF-16LE and UTF-16BE (--be), from the SAME intended code-unit sequence,
#               so both must report the same count and the same code-unit positions; the
#               BE bytes must also be exactly the byte swap of the LE bytes.
#   paths       scalar, --simd, --emit-error-marks (count), and the TwoLevelScanKernel
#               (--scan-error-marks) position output. The linear --print-positions printer
#               is compared too, at the default segment size.
#   segments    forced -segment-size=1, 13 and 64 in addition to the default, which stress
#               the cross-segment pendingHigh carry.
#
# Expected-position oracle
# ------------------------
# Expectations come from three independent layers, and all three must agree before the
# validator is even consulted:
#
#   1. DECLARED   every fixed and boundary case states the positions it expects by hand
#                 (e.g. "a lone high surrogate placed at unit 64 is an error at 64").
#   2. ORACLE     expected_errors() below recomputes them from the definition:
#                     bad[k] = (isLow[k]  and not isHigh[k-1])
#                           or (isHigh[k] and not isLow[k+1])
#                 written test-side here, with no reference to how the validator works.
#   3. REFERENCE  benchmarks/llmask_reference.py, the project's separately written
#                 reference, is run over the raw bytes of BOTH encodings.
#
# A mismatch between 1 and 2 is a bug in this test; a mismatch with 3 means two independent
# implementations of the definition disagree. Only then are the kernels compared.
#
# Odd trailing bytes in expected diagnostics
# ------------------------------------------
# A trailing byte with no partner is NOT a code unit, so it has no code-unit index. The
# convention this suite asserts, matching the validator and the reference, is:
#
#     error count    = number of ill-formed code units + 1 if the file has odd length
#     position list  = ill-formed code-unit indices ONLY -- an odd trailing byte NEVER
#                      appears in it, at any segment size, in either encoding
#
# So a 1-byte file reports errorCount = 1 with an empty position list, and a valid 8192-unit
# file plus one stray byte reports errorCount = 1 with an empty position list.
#
# Every fixture is written to a temporary directory that is removed on exit; nothing is left
# in the repository and no binary fixture is committed.

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

BIN="$BIN" WORK="$WORK" REPO_ROOT="$REPO_ROOT" python3 - <<'PY'
import os
import struct
import subprocess
import sys

BIN = os.environ["BIN"]
WORK = os.environ["WORK"]
REPO_ROOT = os.environ["REPO_ROOT"]

# The project's independently written reference validator (a third opinion, on the raw
# bytes of both encodings).
sys.path.insert(0, os.path.join(REPO_ROOT, "benchmarks"))
import llmask_reference

A, B_ = 0x0041, 0x0042              # ordinary BMP filler
OMEGA, HAN = 0x03A9, 0x4E2D         # more BMP, to keep fixtures from being all-ASCII
PAIR_HI, PAIR_LO = 0xD83D, 0xDE00   # a real emoji: the valid surrogate pair
HI, LO = 0xD800, 0xDC00             # lone surrogates
HI2, LO2 = 0xDBFF, 0xDFFF           # the far ends of both surrogate ranges

HIGH_LO, HIGH_HI = 0xD800, 0xDBFF
LOW_LO, LOW_HI = 0xDC00, 0xDFFF

passed = failed = 0
fixture_seq = 0


# --- Layer 2: the test-side oracle ---------------------------------------------
# Written from the definition of UTF-16 well-formedness, not from the validator: a code
# unit is ill-formed iff it is a low surrogate with no high surrogate immediately before
# it, or a high surrogate with no low surrogate immediately after it. Positions are
# code-unit indices, which is why they are identical for UTF-16LE and UTF-16BE.

def expected_errors(units, odd_trailing_byte):
    """Return (positions, count) for a code-unit sequence.

    The odd trailing byte is deliberately kept out of `positions`: it is not a code unit
    and has no code-unit index. It only ever contributes to the count.
    """
    n = len(units)

    def is_high(k):
        return 0 <= k < n and HIGH_LO <= units[k] <= HIGH_HI

    def is_low(k):
        return 0 <= k < n and LOW_LO <= units[k] <= LOW_HI

    positions = [k for k in range(n)
                 if (is_low(k) and not is_high(k - 1))
                 or (is_high(k) and not is_low(k + 1))]
    return positions, len(positions) + (1 if odd_trailing_byte else 0)


# --- Fixture helpers -----------------------------------------------------------

def pack(units, big_endian):
    fmt = ">H" if big_endian else "<H"
    return b"".join(struct.pack(fmt, u) for u in units)


def byteswap(data):
    out = bytearray(data)
    out[0::2], out[1::2] = data[1::2], data[0::2]
    return bytes(out)


def write(name, data):
    path = os.path.join(WORK, name)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def run(args):
    proc = subprocess.run([BIN] + args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("crash (rc=%d): %s :: %s"
                           % (proc.returncode, " ".join(args), proc.stderr.strip()))
    if "errorCount = " not in proc.stdout:
        raise RuntimeError("no errorCount in output: %s :: %r"
                           % (" ".join(args), proc.stdout))
    return proc.stdout


def count_of(out):
    return int(out.split("errorCount = ")[1].split()[0])


def positions_of(out):
    return sorted(int(line.split("=")[-1].strip(), 16)
                  for line in out.splitlines() if line.startswith("errpos"))


SEGS = (None, 1, 13, 64)            # default plus the three forced segment sizes
SEGS_LIGHT = (None, 64)             # for multi-thousand-unit fixtures


def seg_args(size):
    return [] if size is None else ["-segment-size=%d" % size]


# --- The per-fixture check -----------------------------------------------------

def check(name, units, extra=b"", want_pos=None, want_count=None, segs=SEGS):
    """Validate one code-unit sequence in both encodings, on every path.

    want_pos / want_count are the hand-declared expectations (layer 1). When given they
    are checked against the oracle (layer 2) BEFORE any kernel runs, so a wrong
    expectation in this file is reported as a test bug rather than silently accepted.
    """
    global passed, failed, fixture_seq
    fixture_seq += 1
    assert len(extra) <= 1, "extra is the odd trailing byte: 0 or 1 byte"
    problems = []

    # Layer 2: the oracle.
    oracle_pos, oracle_count = expected_errors(units, len(extra))
    if want_pos is not None and list(want_pos) != oracle_pos:
        problems.append("TEST BUG: declared positions %s != oracle %s"
                        % (list(want_pos), oracle_pos))
    if want_count is not None and want_count != oracle_count:
        problems.append("TEST BUG: declared count %d != oracle %d"
                        % (want_count, oracle_count))
    exp_pos, exp_count = oracle_pos, oracle_count

    le_data = pack(units, False) + extra
    be_data = pack(units, True) + extra
    stem = "f%03d" % fixture_seq
    le_path = write(stem + "_le.bin", le_data)
    be_path = write(stem + "_be.bin", be_data)

    # The two encodings must be the same bytes with each code unit swapped; the odd
    # trailing byte, having no partner, is carried through untouched.
    body = len(le_data) - len(extra)
    if byteswap(le_data[:body]) + extra != be_data:
        problems.append("BE bytes are not the byte swap of the LE bytes")

    # Layer 3: the project's reference validator, run over both encodings' raw bytes.
    for label, data, big in (("LE", le_data, False), ("BE", be_data, True)):
        ref_pos, _, ref_odd = llmask_reference.error_positions(data, big)
        if sorted(ref_pos) != exp_pos:
            problems.append("%s reference positions %s != oracle %s"
                            % (label, sorted(ref_pos)[:8], exp_pos[:8]))
        if len(ref_pos) + ref_odd != exp_count:
            problems.append("%s reference count %d != oracle %d"
                            % (label, len(ref_pos) + ref_odd, exp_count))

    # The kernels, in both encodings, at every requested segment size.
    for enc, path, be_flag in (("LE", le_path, []), ("BE", be_path, ["-be"])):
        for size in segs:
            seg = seg_args(size)
            tag = "%s" % enc if size is None else "%s ss=%d" % (enc, size)

            for mode, extra_args in (("scalar", []),
                                     ("simd", ["-simd"]),
                                     ("marks", ["-emit-error-marks"])):
                got = count_of(run(be_flag + extra_args + seg + [path]))
                if got != exp_count:
                    problems.append("%s %s count %d != %d" % (tag, mode, got, exp_count))

            scan = run(be_flag + ["-emit-error-marks", "-scan-error-marks",
                                  "-thread-num=1"] + seg + [path])
            if count_of(scan) != exp_count:
                problems.append("%s scan count %d != %d" % (tag, count_of(scan), exp_count))
            if positions_of(scan) != exp_pos:
                problems.append("%s scan positions %s != %s"
                                % (tag, positions_of(scan)[:8], exp_pos[:8]))

        # The linear printer walks every block instead of skipping clean regions; at the
        # default segment size it must still produce the identical position list.
        linear = run(be_flag + ["-emit-error-marks", "-print-positions",
                                "-thread-num=1", path])
        if positions_of(linear) != exp_pos:
            problems.append("%s linear positions %s != %s"
                            % (enc, positions_of(linear)[:8], exp_pos[:8]))

    if problems:
        failed += 1
        print("  FAIL %-54s %s" % (name, "; ".join(problems[:4])))
    else:
        passed += 1
        shown = str(exp_pos) if len(exp_pos) <= 4 else str(exp_pos[:4]) + "..."
        print("  PASS %-54s errors=%-4d positions=%s" % (name, exp_count, shown))


print("UTF-16 malformed and boundary suite (issue #41)")
print("  every fixture: UTF-16LE and UTF-16BE x scalar/--simd/errorMarks/two-level scan,")
print("  at segment sizes default/1/13/64, against a hand-declared expectation that is")
print("  cross-checked by a test-side oracle and by benchmarks/llmask_reference.py.")
print("  An odd trailing byte counts as 1 error and has NO code-unit position.")
print()

# --- 1. Lone surrogates ---------------------------------------------------------
print("== 1. lone surrogates ==")
check("lone high surrogate alone in the file", [HI], want_pos=[0], want_count=1)
check("lone low surrogate alone in the file", [LO], want_pos=[0], want_count=1)
check("lone high surrogate in BMP text", [A, B_, HI, A, B_], want_pos=[2], want_count=1)
check("lone low surrogate in BMP text", [A, B_, LO, A, B_], want_pos=[2], want_count=1)
check("lone high at the top of the range (U+DBFF)", [A, HI2, A], want_pos=[1], want_count=1)
check("lone low at the top of the range (U+DFFF)", [A, LO2, A], want_pos=[1], want_count=1)
check("two consecutive high surrogates", [A, HI, HI, A], want_pos=[1, 2], want_count=2)
check("three consecutive high surrogates", [A, HI, HI, HI, A], want_pos=[1, 2, 3], want_count=3)
check("four consecutive high surrogates",
      [A, HI, HI, HI, HI, A], want_pos=[1, 2, 3, 4], want_count=4)
check("two consecutive low surrogates", [A, LO, LO, A], want_pos=[1, 2], want_count=2)
check("three consecutive low surrogates", [A, LO, LO, LO, A], want_pos=[1, 2, 3], want_count=3)
check("four consecutive low surrogates",
      [A, LO, LO, LO, LO, A], want_pos=[1, 2, 3, 4], want_count=4)
check("high surrogate followed by BMP", [A, HI, HAN, B_], want_pos=[1], want_count=1)
check("BMP followed by low surrogate", [A, HAN, LO, B_], want_pos=[2], want_count=1)
check("reversed pair (low then high)", [A, LO, HI, B_], want_pos=[1, 2], want_count=2)
check("reversed pair at the start of the file", [LO, HI], want_pos=[0, 1], want_count=2)
check("high surrogate as the very first unit", [HI, A, B_], want_pos=[0], want_count=1)
check("low surrogate as the very first unit", [LO, A, B_], want_pos=[0], want_count=1)
check("high surrogate as the very last unit", [A, B_, HI], want_pos=[2], want_count=1)
check("low surrogate as the very last unit", [A, B_, LO], want_pos=[2], want_count=1)

# --- 2. Mixed valid and invalid sequences ---------------------------------------
print()
print("== 2. mixed valid and invalid ==")
check("valid pair, then a lone high",
      [A, PAIR_HI, PAIR_LO, HI, B_], want_pos=[3], want_count=1)
check("lone high, then a valid pair",
      [A, HI, PAIR_HI, PAIR_LO, B_], want_pos=[1], want_count=1)
check("valid pair, then a lone low",
      [A, PAIR_HI, PAIR_LO, LO, B_], want_pos=[3], want_count=1)
check("lone low, then a valid pair",
      [A, LO, PAIR_HI, PAIR_LO, B_], want_pos=[1], want_count=1)
check("valid pair immediately followed by a lone high",
      [PAIR_HI, PAIR_LO, HI], want_pos=[2], want_count=1)
check("lone high immediately followed by a valid pair (high consumes nothing)",
      [HI, PAIR_HI, PAIR_LO], want_pos=[0], want_count=1)
# The classic trap: the FIRST high is ill-formed, the second high pairs with the low.
check("high, high, low: only the first high is ill-formed",
      [A, HI, PAIR_HI, PAIR_LO, A], want_pos=[1], want_count=1)
# ... and the mirror: the low pairs with the high before it, the second low is orphaned.
check("high, low, low: only the trailing low is ill-formed",
      [A, PAIR_HI, PAIR_LO, LO, A], want_pos=[3], want_count=1)

alt_units, alt_pos = [], []
for i in range(8):
    alt_units.extend([A, PAIR_HI, PAIR_LO])     # valid
    alt_pos.append(len(alt_units))
    alt_units.append(HI if i % 2 == 0 else LO)  # invalid
check("alternating valid pair / lone surrogate x8", alt_units,
      want_pos=alt_pos, want_count=len(alt_pos))

check("consecutive malformed sequences (four ill-formed units in a row)",
      [A, LO, LO, HI, HI, A], want_pos=[1, 2, 3, 4], want_count=4)
check("consecutive malformed sequences (six lone highs)",
      [A] + [HI] * 6 + [A], want_pos=list(range(1, 7)), want_count=6)
# low high low high: the MIDDLE high/low is a well-formed pair, so only the outer two
# units are ill-formed. Stated explicitly because it is easy to miscount by eye.
check("low high low high: the middle two form a valid pair",
      [A, LO, HI, LO, HI, A], want_pos=[1, 4], want_count=2)

check("malformed at the beginning", [HI] + [A] * 20, want_pos=[0], want_count=1)
check("malformed in the middle", [A] * 10 + [LO] + [A] * 10, want_pos=[10], want_count=1)
check("malformed at the end", [A] * 20 + [HI], want_pos=[20], want_count=1)
check("malformed at beginning, middle and end",
      [LO] + [A] * 9 + [HI] + [A] * 9 + [LO],
      want_pos=[0, 10, 20], want_count=3)
check("valid pairs at both ends, malformed in the middle",
      [PAIR_HI, PAIR_LO] + [A] * 5 + [HI] + [A] * 5 + [PAIR_HI, PAIR_LO],
      want_pos=[7], want_count=1)

# --- 3. Byte-length failures ----------------------------------------------------
print()
print("== 3. byte-length failures ==")
check("one-byte input", [], b"\x41", want_pos=[], want_count=1)
check("one-byte input that looks like a high surrogate byte", [], b"\xd8",
      want_pos=[], want_count=1)
check("odd trailing byte after BMP data", [A, B_, HAN], b"\x41",
      want_pos=[], want_count=1)
check("odd trailing byte after a valid surrogate pair", [A, PAIR_HI, PAIR_LO], b"\x41",
      want_pos=[], want_count=1)
check("odd trailing byte after a lone high", [A, HI], b"\x41",
      want_pos=[1], want_count=2)
check("odd trailing byte after a lone low", [A, LO], b"\x41",
      want_pos=[1], want_count=2)
check("odd trailing byte after a reversed pair", [A, LO, HI], b"\x41",
      want_pos=[1, 2], want_count=3)
check("odd trailing byte 0xd8 after a lone high", [A, HI], b"\xd8",
      want_pos=[1], want_count=2)
check("large valid input (8192 units) with one odd trailing byte",
      [A] * 8192, b"\x41", want_pos=[], want_count=1, segs=SEGS_LIGHT)
check("large input (4096 units) with a lone high and an odd trailing byte",
      [A] * 2048 + [HI] + [A] * 2047, b"\x41",
      want_pos=[2048], want_count=2, segs=SEGS_LIGHT)

# --- 4. Boundaries --------------------------------------------------------------
# Code-unit offsets bracket the SIMD block boundaries in bytes: a code unit is 2 bytes,
# so units 7/8/9 bracket byte 16, units 15/16/17 bracket byte 32, 31/32/33 bracket byte
# 64, 63/64/65 bracket byte 128 (and the 64-code-unit LLmask group), 127/128/129 bracket
# byte 256, 255/256/257 bracket byte 512, and 511/512/513 bracket byte 1024.
print()
print("== 4. boundaries: code-unit offsets (byte offsets = 2x) ==")
BOUNDARY_OFFSETS = (7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 129)
WIDE_OFFSETS = (255, 256, 257, 511, 512, 513)

for n in BOUNDARY_OFFSETS + WIDE_OFFSETS:
    segs = SEGS if n <= 129 else SEGS_LIGHT
    tail = [A] * 4

    # A valid pair straddling the boundary: high at n-1, low at n. No error at all.
    units = [A] * (n - 1) + [PAIR_HI, PAIR_LO] + tail
    check("valid pair straddles unit %d" % n, units, want_pos=[], want_count=0, segs=segs)

    # The same pair placed entirely before / entirely after the boundary.
    units = [A] * (n - 2) + [PAIR_HI, PAIR_LO] + tail
    check("valid pair ends exactly at unit %d" % (n - 1), units,
          want_pos=[], want_count=0, segs=segs)
    units = [A] * n + [PAIR_HI, PAIR_LO] + tail
    check("valid pair starts exactly at unit %d" % n, units,
          want_pos=[], want_count=0, segs=segs)

    # A broken pair straddling the boundary: high at n-1 followed by BMP at n.
    units = [A] * (n - 1) + [HI, A] + tail
    check("lone high at unit %d, BMP after" % (n - 1), units,
          want_pos=[n - 1], want_count=1, segs=segs)

    # A lone low exactly ON the boundary, with ordinary BMP before it.
    units = [A] * n + [LO] + tail
    check("lone low at unit %d, BMP before" % n, units,
          want_pos=[n], want_count=1, segs=segs)

    # Two errors on opposite sides of the boundary, in different mask groups.
    units = [A] * (n - 1) + [HI, A, LO] + tail
    check("lone high at %d and lone low at %d" % (n - 1, n + 1), units,
          want_pos=[n - 1, n + 1], want_count=2, segs=segs)

print()
print("== 4b. boundaries: beginning and end of input ==")
check("file is exactly one valid pair", [PAIR_HI, PAIR_LO], want_pos=[], want_count=0)
check("file is exactly one lone high", [HI], want_pos=[0], want_count=1)
check("file is exactly one BMP unit", [A], want_pos=[], want_count=0)
check("empty file", [], want_pos=[], want_count=0)
for n in (63, 64, 65, 127, 128, 129):
    check("valid pair at the very end of %d units" % (n + 1),
          [A] * (n - 1) + [PAIR_HI, PAIR_LO], want_pos=[], want_count=0)
    check("dangling high as the last of %d units" % n,
          [A] * (n - 1) + [HI], want_pos=[n - 1], want_count=1)
    check("lone low as the first of %d units" % n,
          [LO] + [A] * (n - 1), want_pos=[0], want_count=1)

# --- 4c. Segment-boundary specifics ---------------------------------------------
# With -segment-size=S the pipeline hands the kernel S units at a time, so unit S-1 is the
# last of a segment and unit S the first of the next. These fixtures put valid pairs and
# lone surrogates exactly there and are run AT that forced segment size, which is what
# exercises the cross-segment pendingHigh carry and the zero-filled LookAhead at the end
# of the final segment.
print()
print("== 4c. surrogates on forced segment boundaries ==")
for S in (1, 13, 64):
    segs_here = (None, S)
    for mult in (1, 2, 3):
        n = S * mult
        tail = [A] * 4
        units = [A] * (n - 1) + [PAIR_HI, PAIR_LO] + tail
        check("pair split across segment boundary %d (ss=%d)" % (n, S), units,
              want_pos=[], want_count=0, segs=segs_here)

        units = [A] * (n - 1) + [HI, A] + tail
        check("malformed high at final unit %d of a segment (ss=%d)" % (n - 1, S), units,
              want_pos=[n - 1], want_count=1, segs=segs_here)

        units = [A] * n + [LO] + tail
        check("low at first unit %d of a segment, no matching high (ss=%d)" % (n, S),
              units, want_pos=[n], want_count=1, segs=segs_here)

    # A high surrogate as the very last unit of the whole file, on a segment boundary.
    units = [A] * (S - 1) + [HI]
    check("dangling high as the last unit of segment (ss=%d)" % S, units,
          want_pos=[S - 1], want_count=1, segs=(None, S))

# --- 5. Determinism -------------------------------------------------------------
print()
print("== 5. determinism ==")
DETERMINISM_CASES = [
    ("bmp", [A, B_, OMEGA, HAN], b""),
    ("valid_pair", [A, PAIR_HI, PAIR_LO, B_], b""),
    ("lone_high", [A, HI, B_], b""),
    ("reversed", [A, LO, HI, B_], b""),
    ("odd_byte", [A, HI, B_], b"\x41"),
    ("boundary_64", [A] * 63 + [HI, A] + [A] * 4, b""),
    ("mixed", [PAIR_HI, PAIR_LO, HI, A, LO, A, PAIR_HI, PAIR_LO], b""),
]
for label, units, extra in DETERMINISM_CASES:
    fixture_seq += 1
    stem = "det%03d" % fixture_seq
    outputs = []
    for big, flag in ((False, []), (True, ["-be"])):
        path = write(stem + ("_be.bin" if big else "_le.bin"), pack(units, big) + extra)
        for _ in range(3):
            outputs.append((
                run(flag + [path]),
                run(flag + ["-simd", path]),
                run(flag + ["-emit-error-marks", path]),
                positions_of(run(flag + ["-emit-error-marks", "-scan-error-marks",
                                         "-thread-num=1", path])),
            ))
    le_runs, be_runs = outputs[:3], outputs[3:]
    if le_runs[0] == le_runs[1] == le_runs[2] and be_runs[0] == be_runs[1] == be_runs[2]:
        passed += 1
        print("  PASS %-54s 3 identical runs per encoding" % ("repeatable: " + label))
    else:
        failed += 1
        print("  FAIL %-54s runs differ between invocations" % ("repeatable: " + label))

print()
print("%d passed, %d failed" % (passed, failed))
if failed:
    print("MALFORMED / BOUNDARY TESTS FAILED")
    sys.exit(1)
print("ALL MALFORMED / BOUNDARY TESTS PASSED")
PY
