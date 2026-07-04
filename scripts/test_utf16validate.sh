#!/usr/bin/env bash
#
# test_utf16validate.sh -- scalar-vs-SIMD correctness suite for the UTF-16 validator.
#
#     ./scripts/test_utf16validate.sh
#
# Verifies fixed expected counts, boundary cases (full SIMD block + scalar tail,
# exact-block-sized input, surrogate pairs / malformed sequences crossing a SIMD
# block boundary, odd trailing byte after a large input), forced segment sizes
# (1, 13, 64), and deterministic randomized inputs checked against an independent
# Python reference validator. Fails immediately on any crash, missing output,
# scalar/SIMD disagreement, or wrong count.

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

# The Python driver generates fixtures in $WORK, runs both modes of $BIN, checks
# every result, prints a readable summary, and exits non-zero on any failure.
BIN="$BIN" WORK="$WORK" python3 - <<'PY'
import os, struct, subprocess, random, sys

BIN  = os.environ["BIN"]
WORK = os.environ["WORK"]

npass = 0
nfail = 0

def ref(units, trailing_odd=False):
    """Independent reference validator (same well-formedness rule as the kernel)."""
    pend = False; e = 0
    for u in units:
        hi = 0xD800 <= u <= 0xDBFF
        lo = 0xDC00 <= u <= 0xDFFF
        e += (0 if pend else 1) if lo else (1 if pend else 0)
        pend = hi
    if pend:         e += 1     # dangling final high surrogate
    if trailing_odd: e += 1     # incomplete final code unit
    return e

def write_fixture(name, units, trailing_odd=False):
    data = b"".join(struct.pack("<H", u) for u in units)
    if trailing_odd:
        data += b"\x41"
    path = os.path.join(WORK, name + ".bin")
    with open(path, "wb") as f:
        f.write(data)
    return path

def run(path, simd, seg=None):
    cmd = [BIN]
    if seg is not None:
        cmd.append("-segment-size=%d" % seg)
    if simd:
        cmd.append("--simd")
    cmd.append(path)
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("crash (rc=%d) for %s: %s" % (p.returncode, " ".join(cmd), p.stderr.strip()))
    marker = "errorCount = "
    if marker not in p.stdout:
        raise RuntimeError("missing errorCount in output for %s: %r" % (" ".join(cmd), p.stdout))
    return int(p.stdout.split(marker)[1].split()[0])

def check(label, path, expected, trailing_odd=False, segs=(None,)):
    global npass, nfail
    try:
        for seg in segs:
            s = run(path, False, seg)
            v = run(path, True, seg)
            tag = label if seg is None else "%s[seg=%d]" % (label, seg)
            if s != expected or v != expected:
                print("FAIL %-34s scalar=%s simd=%s expected=%s" % (tag, s, v, expected))
                nfail += 1
            else:
                print("PASS %-34s scalar=%s simd=%s" % (tag, s, v))
                npass += 1
    except Exception as ex:
        print("FAIL %-34s %s" % (label, ex))
        nfail += 1

BMP = 0x0041   # 'A'
HI  = 0xD800   # high surrogate
LO  = 0xDC00   # low surrogate

# ---- 1. Fixed expected results ----------------------------------------------
fixed = [
    ("bmp",         [BMP, 0x0042],       0, False),
    ("valid_pair",  [HI, LO],            0, False),
    ("high_alone",  [HI],                1, False),
    ("low_alone",   [LO],                1, False),
    ("high_normal", [HI, BMP],           1, False),
    ("normal_low",  [BMP, LO],           1, False),
    ("two_highs",   [HI, 0xD801],        2, False),
    ("two_lows",    [LO, LO],            2, False),
    ("reversed",    [LO, HI],            2, False),
    ("odd_byte",    [BMP],               1, True),   # + 1 trailing byte
    ("empty",       [],                  0, False),
]
print("== fixed cases ==")
for name, units, exp, odd in fixed:
    check(name, write_fixture(name, units, odd), exp, odd)

# ---- 2. Boundary cases (checked against the reference) -----------------------
# Exercise candidate boundaries at 128, 256, and 512 UTF-16 code units.
print("== boundary cases ==")
def check_ref(label, units, trailing_odd=False, segs=(None,)):
    global npass, nfail
    path = write_fixture(label, units, trailing_odd)
    expected = ref(units, trailing_odd)
    check(label, path, expected, trailing_odd, segs)

for B in (128, 256, 512):
    # full SIMD block + scalar tail, all valid
    check_ref("boundary_tail_%d" % B, [BMP] * (B + 40))
    # exact-block-sized input (no tail)
    check_ref("exact_units_%d" % B, [BMP] * B)
    # valid surrogate pair crossing the block boundary (unit B-1 .. B)
    u = [BMP] * (B + 8); u[B-1], u[B] = HI, LO
    check_ref("pair_cross_units_%d" % B, u)
    # malformed sequence crossing the block boundary (high at B-1, BMP at B)
    u = [BMP] * (B + 8); u[B-1], u[B] = HI, BMP
    check_ref("bad_cross_units_%d" % B, u)

# odd trailing byte after a large (multi-block) input
check_ref("large_oddbyte", [BMP] * 600, trailing_odd=True)

# ---- 3. Forced segment sizes 1, 13, 64 --------------------------------------
print("== forced segment sizes ==")
random.seed(20240704)
seg_units = []
for _ in range(700):
    r = random.random()
    seg_units.append(random.randint(0, 0xD7FF) if r < 0.75
                     else random.randint(HI, 0xDBFF) if r < 0.87
                     else random.randint(LO, 0xDFFF))
check_ref("segsizes", seg_units, segs=(1, 13, 64))

# ---- 4. Deterministic randomized cases vs reference -------------------------
print("== randomized (reference-checked) ==")
for seed in (1, 42, 1234, 99999):
    random.seed(seed)
    n = random.randint(300, 4000)
    units = []
    for _ in range(n):
        r = random.random()
        units.append(random.randint(0, 0xD7FF) if r < 0.70
                     else random.randint(0xE000, 0xFFFF) if r < 0.80
                     else random.randint(HI, 0xDBFF) if r < 0.90
                     else random.randint(LO, 0xDFFF))
    check_ref("random_seed_%d_n%d" % (seed, n), units)

print()
print("%d passed, %d failed" % (npass, nfail))
if nfail:
    print("SOME TESTS FAILED")
    sys.exit(1)
print("ALL TESTS PASSED")
PY
