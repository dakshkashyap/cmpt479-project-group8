#!/usr/bin/env bash
#
# test_utf16validate.sh -- scalar-vs-SIMD correctness suite for the UTF-16 validator.
#
#     ./scripts/test_utf16validate.sh
#
# Every fixture is validated three ways -- scalar kernel, Parabix SIMD kernel, and
# an independent Python reference that walks the raw UTF-16LE bytes -- and all
# three must agree. Coverage: fixed valid/malformed cases, valid multilingual text
# (Latin, accented European, Hindi, Punjabi, CJK, emoji and a mixed sample),
# malformed surrogate sequences (including malformed data embedded in multilingual
# text), boundaries around 64-code-unit groups and the larger 128/256/512 block and
# pack boundaries, forced pipeline segment sizes (1, 13, 64) that stress the
# cross-segment carry, and deterministic randomized inputs. Fails immediately on any
# crash, missing output, scalar/SIMD disagreement, or wrong count.
#
# Target is UTF-16LE only. All fixtures are written to a temporary directory that is
# removed on exit; nothing is left in the repository.

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

# --- Independent reference -----------------------------------------------------
# Validates the raw byte structure directly, so it also handles blobs that no
# string encoder would produce (lone surrogates, odd trailing bytes).

def ref_bytes(data):
    """Count ill-formed UTF-16LE code units in a raw byte string."""
    pend = False        # previous code unit was an unmatched high surrogate
    errors = 0
    n = len(data)
    for i in range(0, n - (n & 1), 2):
        unit = data[i] | (data[i + 1] << 8)          # little-endian code unit
        hi = 0xD800 <= unit <= 0xDBFF
        lo = 0xDC00 <= unit <= 0xDFFF
        errors += (0 if pend else 1) if lo else (1 if pend else 0)
        pend = hi
    if pend:
        errors += 1     # dangling final high surrogate
    if n & 1:
        errors += 1     # incomplete final code unit (odd trailing byte)
    return errors

# --- Fixture helpers -----------------------------------------------------------

def le(units):
    """Raw UTF-16LE bytes from code-unit values (lone surrogates allowed)."""
    return b"".join(struct.pack("<H", u) for u in units)

def text(s):
    """UTF-16LE bytes for well-formed text (non-BMP becomes a surrogate pair)."""
    return s.encode("utf-16-le")

def write_blob(name, data):
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

def check(label, data, expected=None, segs=(None,)):
    """Run scalar and SIMD on a byte blob; both must match the reference."""
    global npass, nfail
    if expected is None:
        expected = ref_bytes(data)
    try:
        path = write_blob(label, data)
        for seg in segs:
            s = run(path, False, seg)
            v = run(path, True, seg)
            tag = label if seg is None else "%s[seg=%d]" % (label, seg)
            if s != expected or v != expected:
                print("FAIL %-38s scalar=%s simd=%s expected=%s" % (tag, s, v, expected))
                nfail += 1
            else:
                print("PASS %-38s scalar=%s simd=%s" % (tag, s, v))
                npass += 1
    except Exception as ex:
        print("FAIL %-38s %s" % (label, ex))
        nfail += 1

BMP = 0x0041   # 'A'
HI  = 0xD800   # high surrogate
LO  = 0xDC00   # low surrogate

# --- 1. Fixed valid / malformed cases -----------------------------------------
print("== fixed cases ==")
fixed = [
    ("bmp",         le([BMP, 0x0042]),          0),
    ("valid_pair",  le([HI, LO]),               0),
    ("high_alone",  le([HI]),                   1),
    ("low_alone",   le([LO]),                   1),
    ("high_normal", le([HI, BMP]),              1),
    ("normal_low",  le([BMP, LO]),              1),
    ("two_highs",   le([HI, 0xD801]),           2),
    ("two_lows",    le([LO, LO]),               2),
    ("reversed",    le([LO, HI]),               2),
    ("odd_byte",    le([BMP]) + b"\x41",        1),
    ("empty",       b"",                        0),
]
for name, data, exp in fixed:
    # expected values are stated explicitly here and cross-checked by the reference
    assert ref_bytes(data) == exp, "reference disagrees with expected for %s" % name
    check(name, data, exp)

# --- 2. Valid multilingual text ------------------------------------------------
# Written with explicit \u escapes so this file stays pure ASCII and cannot be
# corrupted by re-encoding. All of these are well-formed: expected 0 errors.
ENGLISH  = "The quick brown fox jumps over the lazy dog. 0123456789!?"
# Accented European: "Zurich Koln naive cafe AEro Lodz Nandu strasse"
EUROPEAN = ("Z\u00fcrich K\u00f6ln na\u00efve caf\u00e9 \u00c6r\u00f8 "
            "\u0141\u00f3d\u017a \u00d1and\u00fa stra\u00dfe")
# Devanagari (Hindi): "namaste duniya"
HINDI    = "\u0928\u092e\u0938\u094d\u0924\u0947 \u0926\u0941\u0928\u093f\u092f\u093e"
# Gurmukhi (Punjabi): "sat sri akal"
PUNJABI  = "\u0a38\u0a24 \u0a38\u0a4d\u0a30\u0a40 \u0a05\u0a15\u0a3e\u0a32"
# Chinese + Japanese (kanji/katakana) + Korean (Hangul is BMP: U+D55C < U+D800)
CJK      = ("\u4e2d\u6587\u6d4b\u8bd5 \u65e5\u672c\u8a9e\u30c6\u30b9\u30c8 "
            "\ud55c\uad6d\uc5b4")
# Non-BMP emoji (each becomes a surrogate pair) plus a ZWJ (U+200D) family sequence
EMOJI    = ("\U0001f600\U0001f389\U0001f680\U0001f30d "
            "\U0001f468\u200d\U0001f469\u200d\U0001f467\u200d\U0001f466")
MIXED    = " ".join([ENGLISH, EUROPEAN, HINDI, PUNJABI, CJK, EMOJI])

print("== multilingual (valid) ==")
for name, sample in [
    ("lang_english",  ENGLISH),
    ("lang_european", EUROPEAN),
    ("lang_hindi",    HINDI),
    ("lang_punjabi",  PUNJABI),
    ("lang_cjk",      CJK),
    ("lang_emoji",    EMOJI),      # non-BMP: exercises real surrogate pairs
    ("lang_mixed",    MIXED),
]:
    check(name, text(sample), 0)

# The mixed sample also runs under forced segment sizes: real surrogate pairs must
# survive the cross-segment carry.
check("lang_mixed_segmented", text(MIXED), 0, segs=(1, 13, 64))

# --- 3. Malformed sequences ----------------------------------------------------
# Built from raw code-unit values, not string encoding: Python will not encode a
# lone surrogate, but these byte patterns must still be counted correctly.
print("== malformed sequences ==")
check("mal_unpaired_high",     text("abc") + le([0xD800]) + text("def"))
check("mal_unpaired_low",      text("abc") + le([0xDC00]) + text("def"))
check("mal_reversed_pair",     text("ab") + le([0xDC00, 0xD800]) + text("cd"))
check("mal_odd_trailing_byte", text("hello") + b"\x41")
check("mal_consecutive_highs", text("x") + le([0xD800, 0xD801, 0xD802]) + text("y"))
check("mal_consecutive_lows",  text("x") + le([0xDC00, 0xDC01, 0xDC02]) + text("y"))
check("mal_high_then_pair",    text("x") + le([0xD800, 0xD801, 0xDC00]) + text("y"))
check("mal_truncated_pair",    text("x") + le([0xD83D]))   # high surrogate at EOF

# Malformed data embedded in multilingual text, including an odd trailing byte.
check("mal_in_multilingual",
      text(HINDI) + le([0xD800]) + text(CJK) + le([0xDC00, 0xD800]) + text(EMOJI))
check("mal_in_multilingual_odd",
      text(MIXED) + le([0xD800]) + text(PUNJABI) + b"\x41")

# --- 4. Boundaries around 64-code-unit groups ----------------------------------
# The pack/mask logic works on groups of code units; place surrogate pairs exactly
# on, just before, and just after each group boundary.
print("== 64-code-unit group boundaries ==")
for G in (64, 128, 192):
    u = [BMP] * (G + 8); u[G - 1], u[G] = HI, LO
    check("pair_straddle_%d" % G, le(u))          # valid pair crosses the boundary
    u = [BMP] * (G + 8); u[G - 1], u[G] = HI, BMP
    check("bad_straddle_%d" % G, le(u))           # broken pair crosses the boundary
    u = [BMP] * (G + 8); u[G - 2], u[G - 1] = HI, LO
    check("pair_before_%d" % G, le(u))            # pair ends exactly at the boundary
    u = [BMP] * (G + 8); u[G], u[G + 1] = HI, LO
    check("pair_after_%d" % G, le(u))             # pair starts exactly at the boundary

# --- 5. Block / pack boundaries -------------------------------------------------
print("== block and pack boundaries ==")
for B in (64, 128, 256, 512):
    check("boundary_tail_%d" % B, le([BMP] * (B + 40)))   # full block + scalar tail
    check("exact_units_%d" % B,   le([BMP] * B))          # exact block-sized input
    u = [BMP] * (B + 8); u[B - 1], u[B] = HI, LO
    check("pair_cross_units_%d" % B, le(u))               # valid pair across a block
    u = [BMP] * (B + 8); u[B - 1], u[B] = HI, BMP
    check("bad_cross_units_%d" % B, le(u))                # malformed across a block

# odd trailing byte after a large (multi-block) input
check("large_oddbyte", le([BMP] * 600) + b"\x41")

# --- 6. Forced segment sizes ----------------------------------------------------
print("== forced segment sizes ==")
random.seed(20240704)
seg_units = []
for _ in range(700):
    r = random.random()
    seg_units.append(random.randint(0, 0xD7FF) if r < 0.75
                     else random.randint(HI, 0xDBFF) if r < 0.87
                     else random.randint(LO, 0xDFFF))
check("segsizes", le(seg_units), segs=(1, 13, 64))

# --- 7. Deterministic randomized inputs ----------------------------------------
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
    check("random_seed_%d_n%d" % (seed, n), le(units))

print()
print("%d passed, %d failed" % (npass, nfail))
if nfail:
    print("SOME TESTS FAILED")
    sys.exit(1)
print("ALL TESTS PASSED")
PY
