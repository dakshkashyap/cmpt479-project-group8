#!/usr/bin/env bash
#
# test_errormarks.sh -- correctness suite for the PROTOTYPE errorMarks producer (issue #32).
#
#     ./scripts/test_errormarks.sh
#
# The errorMarks path (--emit-error-marks) is a SEPARATE pipeline that emits a real Parabix
# StreamSet(1) bitstream, one bit per UTF-16 code unit, set iff that code unit is ill-formed.
# It does not replace the count-only validator; scripts/test_utf16validate.sh remains the
# regression gate for that. There is no repair and no TwoLevelScanKernel subclass.
#
# Every fixture is checked two ways:
#
#   1. COUNT   --emit-error-marks reports the same errorCount as the scalar oracle. Because
#              the kernel derives errorCount from the marker bits it actually emitted
#              (popcount) plus the odd trailing byte, a matching count already constrains the
#              marker semantics.
#   2. POSITION  --print-positions prints the code-unit index of every set bit in the emitted
#              bitstream, and that list must be IDENTICAL to benchmarks/llmask_reference.py
#              --positions, which derives positions straight from the definition with no
#              LLmask, no maskHL and no Parabix. This is what actually validates the STREAM
#              (and its produced item counts), not just the count.
#
# Coverage: valid text, every malformed shape, valid and malformed surrogate pairs straddling
# 64-code-unit, block (128/256/512-unit) and pack boundaries, a dangling high surrogate at EOF
# (which only marks itself because LookAhead(2) is zero-filled on the final segment), the odd
# trailing byte (which has NO code-unit position and so must appear in the count but never in
# the position list), multilingual text, and forced pipeline segment sizes 1/13/64 that stress
# the cross-segment pendingHigh carry and the LookAhead region.
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
HI, LO = 0xD83D, 0xDE00          # a valid surrogate pair (an emoji)
LONE_HI, LONE_LO = 0xD800, 0xDC00

passed = failed = 0


def write(name, units, extra=b""):
    path = os.path.join(WORK, name)
    with open(path, "wb") as f:
        f.write(b"".join(struct.pack("<H", u) for u in units) + extra)
    return path


def run(args):
    out = subprocess.run([BIN] + args, capture_output=True, text=True, check=True)
    return out.stdout


def error_count(path, extra=None):
    out = run((extra or []) + [path])
    return int(out.strip().split("=")[-1])


def kernel_positions(path, extra=None):
    """The code-unit index of every set bit in the emitted errorMarks bitstream."""
    out = run(["-emit-error-marks", "-print-positions", "-thread-num=1"] + (extra or []) + [path])
    pos = []
    for line in out.splitlines():
        if line.startswith("errpos"):
            pos.append(int(line.split("=")[-1].strip(), 16))
    return sorted(pos)


def reference_positions(path):
    """Positions straight from the definition -- no LLmask, no maskHL, no Parabix."""
    out = subprocess.run([sys.executable, REF, "--positions", path],
                         capture_output=True, text=True, check=True).stdout
    return sorted(int(l) for l in out.splitlines()[3:])


def check(name, path, segment_sizes=(None,)):
    global passed, failed
    want_count = error_count(path)                 # the scalar oracle
    want_pos = reference_positions(path)
    problems = []
    for ss in segment_sizes:
        extra = [] if ss is None else ["-segment-size=%d" % ss]
        tag = "" if ss is None else " (segment-size=%d)" % ss
        got_count = error_count(path, ["-emit-error-marks"] + extra)
        if got_count != want_count:
            problems.append("count%s: got %d, oracle %d" % (tag, got_count, want_count))
        got_pos = kernel_positions(path, extra)
        if got_pos != want_pos:
            problems.append("positions%s: got %s, reference %s"
                            % (tag, got_pos[:8], want_pos[:8]))
    if problems:
        failed += 1
        print("  FAIL %-52s %s" % (name, "; ".join(problems)))
    else:
        passed += 1
        print("  PASS %-52s errors=%-3d positions=%s"
              % (name, want_count, want_pos[:5] if len(want_pos) <= 5 else str(want_pos[:5]) + "..."))


SEGS = (None, 1, 13, 64)

print("errorMarks producer (--emit-error-marks) correctness suite")
print("  count  : must equal the scalar oracle")
print("  stream : printed positions must equal benchmarks/llmask_reference.py --positions")
print()

# --- fixed cases ------------------------------------------------------------------
check("valid BMP", write("valid_bmp.bin", [A, B_, 0x03A9, 0x4E2D]), SEGS)
check("valid surrogate pair", write("valid_pair.bin", [A, HI, LO, B_]), SEGS)
check("lone high surrogate", write("lone_high.bin", [A, LONE_HI, B_, B_]), SEGS)
check("lone low surrogate", write("lone_low.bin", [A, B_, LONE_LO, B_]), SEGS)
check("reversed pair (low then high)", write("reversed.bin", [A, LONE_LO, LONE_HI, B_]), SEGS)
check("two adjacent high surrogates", write("two_high.bin", [A, LONE_HI, LONE_HI, B_]), SEGS)
check("high surrogate at position 0", write("high_at_0.bin", [LONE_HI, A]), SEGS)
check("dangling high at EOF", write("dangling.bin", [A, A, LONE_HI]), SEGS)
check("empty file", write("empty.bin", []), SEGS)

# --- the odd trailing byte: an error with NO code-unit position --------------------
check("odd trailing byte", write("odd.bin", [A, A, A], b"\x41"), SEGS)
check("odd trailing byte + lone high", write("odd_high.bin", [A, LONE_HI, A], b"\x41"), SEGS)
check("odd trailing byte after a dangling high",
      write("odd_dangling.bin", [A, A, LONE_HI], b"\x41"), SEGS)

# --- multilingual valid text ------------------------------------------------------
check("multilingual valid (Latin/Greek/Hindi/CJK/emoji)",
      write("multi.bin", [0x0048, 0x00E9, 0x03A9, 0x0915, 0x0A15, 0x4E2D, 0x6587,
                          HI, LO, 0x0062, 0xFFFD]), SEGS)

# --- boundary crossings: 64-code-unit groups, and 128/256/512 blocks/packs ---------
for n in (7, 8, 63, 64, 65, 127, 128, 129, 255, 256, 257, 511, 512, 513):
    # A VALID surrogate pair straddling the boundary at n must produce NO marker.
    units = [A] * (n - 1) + [HI, LO] + [A] * 3
    check("valid pair straddling unit %d" % n, write("pair_%d.bin" % n, units), SEGS)
    # A lone high at n-1 and a lone low at n+1 are two separate errors in different groups.
    units = [A] * (n - 1) + [LONE_HI, A, LONE_LO] + [A] * 3
    check("malformed straddling unit %d" % n, write("bad_%d.bin" % n, units), SEGS)

# --- a dangling high exactly at a block boundary (only marked because LookAhead(2) --
# --- is zero-filled on the final segment) -----------------------------------------
for n in (64, 128, 256):
    check("dangling high as the last of %d units" % n,
          write("dangle_%d.bin" % n, [A] * (n - 1) + [LONE_HI]), SEGS)

# --- randomized, checked against the reference ------------------------------------
import random
for seed in (1, 2, 3, 4, 5):
    rng = random.Random(seed)
    units = []
    while len(units) < 900:
        r = rng.random()
        if r < 0.70:
            units.append(rng.choice([A, B_, 0x03A9, 0x4E2D, 0x0915]))
        elif r < 0.85:
            units.extend([HI, LO])
        elif r < 0.92:
            units.append(rng.randint(0xD800, 0xDBFF))
        else:
            units.append(rng.randint(0xDC00, 0xDFFF))
    extra = b"\x41" if seed % 2 == 0 else b""
    check("randomized seed=%d (%d units%s)" % (seed, len(units), ", odd byte" if extra else ""),
          write("rand_%d.bin" % seed, units, extra), SEGS)

print()
print("%d passed, %d failed" % (passed, failed))
if failed:
    print("ERRORMARKS TESTS FAILED")
    sys.exit(1)
print("ALL ERRORMARKS TESTS PASSED")
PY
