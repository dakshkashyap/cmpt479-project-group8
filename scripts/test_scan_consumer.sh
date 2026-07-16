#!/usr/bin/env bash
#
# test_scan_consumer.sh -- correctness suite for the TwoLevelScanKernel consumer (issue #39).
#
#     ./scripts/test_scan_consumer.sh
#
# --scan-error-marks consumes the errorMarks bitstream with a real subclass of Parabix's
# TwoLevelScanKernel: it builds a high-level index, skips clean 4096-code-unit regions, and
# scans only dirty scanwords with ctz / reset-lowest-bit. It prints the code-unit position of
# every ill-formed unit. This is the CONSUMER; the producer (issue #32) is exercised by
# scripts/test_errormarks.sh, and the count-only validator by scripts/test_utf16validate.sh --
# both of which remain the gates for their paths.
#
# Every fixture is checked three ways, at four pipeline segment sizes (default, 1, 13, 64,
# which force the cross-segment carry and the stride boundaries):
#
#   1. scan positions   == benchmarks/llmask_reference.py --positions (the definition, with no
#                          LLmask, no maskHL and no Parabix). This validates the scan output.
#   2. scan positions   == the issue #32 linear debug printer's positions. Same bitstream, two
#                          different scan strategies (skip-clean-regions vs visit-every-block):
#                          they must agree, which is what proves the region skipping is sound.
#   3. errorCount        unchanged from the scalar oracle (the producer still reports it).
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


def write(name, units, extra=b""):
    path = os.path.join(WORK, name)
    with open(path, "wb") as f:
        f.write(b"".join(struct.pack("<H", u) for u in units) + extra)
    return path


def run(args):
    return subprocess.run([BIN] + args, capture_output=True, text=True, check=True).stdout


def positions(args, path):
    out = run(args + ["-thread-num=1", path])
    return sorted(int(l.split("=")[-1].strip(), 16)
                  for l in out.splitlines() if l.startswith("errpos"))


def scan_positions(path, extra):
    return positions(["-emit-error-marks", "-scan-error-marks"] + extra, path)


def linear_positions(path, extra):
    return positions(["-emit-error-marks", "-print-positions"] + extra, path)


def error_count(path, extra=None):
    out = run((extra or []) + [path])
    return int(out.strip().split("=")[-1])


def reference_positions(path):
    out = subprocess.run([sys.executable, REF, "--positions", path],
                         capture_output=True, text=True, check=True).stdout
    return sorted(int(l) for l in out.splitlines()[3:])


def check(name, path, segment_sizes=(None,)):
    global passed, failed
    want_pos = reference_positions(path)
    want_count = error_count(path)                       # scalar oracle
    problems = []
    for ss in segment_sizes:
        extra = [] if ss is None else ["-segment-size=%d" % ss]
        tag = "" if ss is None else " (segment-size=%d)" % ss
        scan = scan_positions(path, extra)
        if scan != want_pos:
            problems.append("scan vs reference%s: %s vs %s" % (tag, scan[:8], want_pos[:8]))
        lin = linear_positions(path, extra)
        if scan != lin:
            problems.append("scan vs linear%s: %s vs %s" % (tag, scan[:8], lin[:8]))
        got_count = error_count(path, ["-emit-error-marks", "-scan-error-marks"] + extra)
        if got_count != want_count:
            problems.append("count%s: %d vs oracle %d" % (tag, got_count, want_count))
    if problems:
        failed += 1
        print("  FAIL %-52s %s" % (name, "; ".join(problems)))
    else:
        passed += 1
        shown = want_pos[:5] if len(want_pos) <= 5 else str(want_pos[:5]) + "..."
        print("  PASS %-52s errors=%-3d positions=%s" % (name, want_count, shown))


SEGS = (None, 1, 13, 64)

print("TwoLevelScanKernel consumer (--scan-error-marks) correctness suite")
print("  scan positions must equal (1) the definition-based reference and")
print("  (2) the issue #32 linear printer -- same bitstream, region-skipping vs linear scan.")
print()

check("valid BMP", write("valid_bmp.bin", [A, B_, 0x03A9, 0x4E2D]), SEGS)
check("valid surrogate pair", write("valid_pair.bin", [A, HI, LO, B_]), SEGS)
check("lone high surrogate", write("lone_high.bin", [A, LONE_HI, B_, B_]), SEGS)
check("lone low surrogate", write("lone_low.bin", [A, B_, LONE_LO, B_]), SEGS)
check("reversed pair (low then high)", write("reversed.bin", [A, LONE_LO, LONE_HI, B_]), SEGS)
check("two adjacent high surrogates", write("two_high.bin", [A, LONE_HI, LONE_HI, B_]), SEGS)
check("high surrogate at position 0", write("high_at_0.bin", [LONE_HI, A]), SEGS)
check("dangling high at EOF", write("dangling.bin", [A, A, LONE_HI]), SEGS)
check("empty file", write("empty.bin", []), SEGS)

# odd trailing byte: an error with NO code-unit position -- must never be a scan position.
check("odd trailing byte", write("odd.bin", [A, A, A], b"\x41"), SEGS)
check("odd trailing byte + lone high", write("odd_high.bin", [A, LONE_HI, A], b"\x41"), SEGS)

check("multilingual valid",
      write("multi.bin", [0x0048, 0x00E9, 0x03A9, 0x0915, 0x0A15, 0x4E2D, 0x6587,
                          HI, LO, 0x0062, 0xFFFD]), SEGS)

# Boundary crossings: 64-code-unit groups, 128/256/512 blocks, and the 4096-unit scan STRIDE.
for n in (63, 64, 65, 127, 128, 129, 255, 256, 257, 511, 512, 513,
          4095, 4096, 4097, 8191, 8192, 8193):
    check("valid pair straddling unit %d" % n,
          write("pair_%d.bin" % n, [A] * (n - 1) + [HI, LO] + [A] * 3), SEGS)
    check("malformed straddling unit %d" % n,
          write("bad_%d.bin" % n, [A] * (n - 1) + [LONE_HI, A, LONE_LO] + [A] * 3), SEGS)

# A clean region between two dirty ones: the scan must skip the middle stride and still find
# both errors. 3 strides = 12288 code units; error in stride 0 and stride 2, stride 1 clean.
check("errors in strides 0 and 2, stride 1 clean",
      write("sparse_strides.bin",
            [LONE_LO] + [A] * (4096 * 2 + 100) + [LONE_HI, A]), SEGS)

# Randomized, checked against the reference and the linear printer.
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
    extra = b"\x41" if seed % 2 == 0 else b""
    check("randomized seed=%d (%d units%s)" % (seed, len(units), ", odd byte" if extra else ""),
          write("rand_%d.bin" % seed, units, extra), SEGS)

print()
print("%d passed, %d failed" % (passed, failed))
if failed:
    print("SCAN CONSUMER TESTS FAILED")
    sys.exit(1)
print("ALL SCAN CONSUMER TESTS PASSED")
PY
