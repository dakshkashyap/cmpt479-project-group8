#!/usr/bin/env bash
#
# test_utf16be.sh -- correctness suite for UTF-16BE support (issue #33).
#
#     ./scripts/test_utf16be.sh
#
# UTF-16BE puts the high byte of each code unit at byte 2k (UTF-16LE at 2k+1). The --be flag
# selects it; UTF-16LE remains the default and is covered by test_utf16validate.sh /
# test_errormarks.sh / test_scan_consumer.sh, which this suite does not touch.
#
# Each fixture is generated as BIG-ENDIAN bytes and checked five ways, at four segment sizes
# (default, 1, 13, 64, which force the cross-segment carry and the stride boundaries):
#
#   1. scalar --be count       == the BE-aware reference error count
#   2. SIMD --be count         == the same
#   3. errorMarks --be count   == the same
#   4. scan --be positions     == benchmarks/llmask_reference.py --positions --be
#   5. cross-endian identity    -- because BE bytes are exactly the byte-swap of the LE
#      encoding of the same code units, validating the BE file with --be must give the SAME
#      count as validating the LE-encoded file WITHOUT --be. This ties BE back to the already
#      trusted LE path, independent of the reference.
#
# All fixtures are written to a temporary directory that is removed on exit.

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

BIN="$BIN" WORK="$WORK" REF="$REPO_ROOT/benchmarks/llmask_reference.py" python3 - <<'PY'
import os, struct, subprocess, sys

BIN = os.environ["BIN"]
WORK = os.environ["WORK"]
REF = os.environ["REF"]

A, B_ = 0x0041, 0x0042
HI, LO = 0xD83D, 0xDE00
LONE_HI, LONE_LO = 0xD800, 0xDC00

passed = failed = 0


def write_be(name, units, extra=b""):
    path = os.path.join(WORK, name)
    with open(path, "wb") as f:
        f.write(b"".join(struct.pack(">H", u) for u in units) + extra)   # BIG-ENDIAN
    return path


def write_le(name, units, extra=b""):
    path = os.path.join(WORK, name)
    with open(path, "wb") as f:
        f.write(b"".join(struct.pack("<H", u) for u in units) + extra)   # LITTLE-ENDIAN
    return path


def run(args):
    return subprocess.run([BIN] + args, capture_output=True, text=True, check=True).stdout


def count(path, extra=None):
    return int(run((extra or []) + [path]).strip().split("=")[-1])


def scan_positions(path, extra):
    out = run(["-be", "-emit-error-marks", "-scan-error-marks", "-thread-num=1"] + extra + [path])
    return sorted(int(l.split("=")[-1].strip(), 16) for l in out.splitlines() if l.startswith("errpos"))


def reference_positions_be(path):
    out = subprocess.run([sys.executable, REF, "--positions", path, "--be"],
                         capture_output=True, text=True, check=True).stdout
    return sorted(int(l) for l in out.splitlines()[3:])


def reference_count_be(path):
    out = subprocess.run([sys.executable, REF, "--check", path, "--be"],
                         capture_output=True, text=True, check=True).stdout
    # "<path>: units=.. masks=.. errorbits=E oddtrailingbyte=O"
    d = dict(tok.split("=") for tok in out.strip().split()[1:])
    return int(d["errorbits"]) + int(d["oddtrailingbyte"])


def check(name, units, extra_bytes=b"", segment_sizes=(None, 1, 13, 64)):
    global passed, failed
    be = write_be(name + "_be.bin", units, extra_bytes)
    le = write_le(name + "_le.bin", units, extra_bytes)     # same code units, LE encoding
    want_pos = reference_positions_be(be)
    want_count = reference_count_be(be)
    le_count = count(le)                                     # trusted LE path, no --be
    problems = []
    if le_count != want_count:
        problems.append("cross-endian: LE count %d != BE reference %d" % (le_count, want_count))
    for ss in segment_sizes:
        seg = [] if ss is None else ["-segment-size=%d" % ss]
        tag = "" if ss is None else " (ss=%d)" % ss
        for mode, args in (("scalar", ["-be"]),
                           ("simd", ["-be", "-simd"]),
                           ("marks", ["-be", "-emit-error-marks"])):
            c = count(be, args + seg)
            if c != want_count:
                problems.append("%s%s: %d vs %d" % (mode, tag, c, want_count))
        sp = scan_positions(be, seg)
        if sp != want_pos:
            problems.append("scan%s: %s vs %s" % (tag, sp[:8], want_pos[:8]))
    if problems:
        failed += 1
        print("  FAIL %-50s %s" % (name, "; ".join(problems)))
    else:
        passed += 1
        shown = want_pos[:5] if len(want_pos) <= 5 else str(want_pos[:5]) + "..."
        print("  PASS %-50s errors=%-3d positions=%s" % (name, want_count, shown))


print("UTF-16BE (--be) correctness suite")
print("  BE count/positions vs a BE-aware reference, AND vs the LE path on byte-swapped data.")
print()

check("valid_bmp", [A, B_, 0x03A9, 0x4E2D])
check("valid_pair", [A, HI, LO, B_])
check("lone_high", [A, LONE_HI, B_, B_])
check("lone_low", [A, B_, LONE_LO, B_])
check("reversed_pair", [A, LONE_LO, LONE_HI, B_])
check("dangling_high_eof", [A, A, LONE_HI])
check("high_at_0", [LONE_HI, A])
check("empty", [])
check("odd_trailing_byte", [A, A, A], b"\x00")
check("odd_trailing_byte_lone_high", [A, LONE_HI, A], b"\x00")
check("multilingual", [0x0048, 0x00E9, 0x03A9, 0x0915, 0x0A15, 0x4E2D, 0x6587,
                       HI, LO, 0x0062, 0xFFFD])

# 64-code-unit group boundary and 4096-code-unit scan-stride boundary.
for n in (63, 64, 65, 4095, 4096, 4097, 8191, 8192, 8193):
    check("valid_pair_at_%d" % n, [A] * (n - 1) + [HI, LO] + [A] * 3)
    check("malformed_at_%d" % n, [A] * (n - 1) + [LONE_HI, A, LONE_LO] + [A] * 3)

# A clean scan stride between two dirty ones.
check("sparse_strides", [LONE_LO] + [A] * (4096 * 2 + 100) + [LONE_HI, A])

import random
for seed in (1, 2, 3, 4, 5):
    rng = random.Random(seed)
    units = []
    while len(units) < 5000:
        r = rng.random()
        if r < 0.80:
            units.append(rng.choice([A, B_, 0x03A9, 0x4E2D, 0x0915]))
        elif r < 0.90:
            units.extend([HI, LO])
        elif r < 0.95:
            units.append(rng.randint(0xD800, 0xDBFF))
        else:
            units.append(rng.randint(0xDC00, 0xDFFF))
    extra = b"\x00" if seed % 2 == 0 else b""
    check("randomized_seed_%d" % seed, units, extra)

print()
print("%d passed, %d failed" % (passed, failed))
if failed:
    print("UTF-16BE TESTS FAILED")
    sys.exit(1)
print("ALL UTF-16BE TESTS PASSED")
PY
