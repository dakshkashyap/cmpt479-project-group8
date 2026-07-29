#!/usr/bin/env python3
"""Deterministic fuzz / property suite against the independent oracle (issue #42).

Every case is a code-unit sequence (optionally plus one odd trailing byte) built by a
seeded generator, materialised in BOTH endianness, and then compared against
scripts/utf16_oracle.py on every validator path:

    oracle  vs  scalar  vs  --simd  vs  --emit-error-marks  vs  --print-positions
            vs  --emit-error-marks --scan-error-marks (TwoLevelScanKernel)

and, on a deterministic subset, against `--repair`.

This is the *generated* counterpart to scripts/test_utf16_malformed_boundaries.sh: that
suite is hand-curated, every case chosen and its expected positions declared by hand;
this one produces hundreds of cases nobody wrote down, and gets its expectations from an
oracle written from the definition of UTF-16 rather than from any implementation.

Determinism
-----------
Every case is generated from `random.Random("utf16-fuzz|<seed>|<index>|<category>")`, so a
case depends only on the seed, its own index and its category -- never on how many cases
ran before it or in what order. The same --seed therefore reproduces the same bytes on any
machine, and --only-case N reproduces exactly one case without generating the rest.

Properties checked
------------------
    P1 count-agreement     oracle == scalar == --simd == errorMarks == scan
    P2 position-agreement  oracle == --print-positions == --scan-error-marks
    P3 repair-agreement    oracle repaired bytes == --repair bytes (byte for byte)
    P4 repair-validates    validate(repair(x)) == 0
    P5 repair-idempotent   repair(repair(x)) == repair(x)
    P6 valid-unchanged     x well-formed => repair(x) == x
    P7 endian-equivalence  LE and BE of the same code units give the same count and the
                           same positions, and the BE bytes are the byte swap of the LE
    P8 generator-reproducible  regenerating the case list from the seed is byte-identical
    P9 run-deterministic   repeated runs of the same path on the same file agree

Known defect (KNOWN-XFAIL)
--------------------------
One defect in `--scan-error-marks` is currently accepted so that the suite stays a usable
regression gate: on an input whose length is a positive exact multiple of the
4096-code-unit scan stride, the two-level scan can print extra positions *past the end of
the input*. Counts stay correct on every path -- including the scan's own errorCount, which
is what makes that output self-inconsistent -- and `--print-positions` stays correct.

The classification is deliberately narrow (see classify_scan_tail_defect): it applies only
to P2, only when the scan is the sole disagreeing path, only when the oracle and the linear
printer agree exactly, only when every count agrees, only for even-length input that is a
positive exact multiple of 4096 code units, only when no real position is missing, and only
when every unexpected position is outside the valid code-unit range. It suppresses nothing
else -- count, repair, linear-position and any other scan failure still fail the run.

    --strict-known-defects   treat it as an ordinary failure and exit non-zero

Usage
-----
    python3 scripts/test_utf16_oracle_fuzz.py                  # default run
    python3 scripts/test_utf16_oracle_fuzz.py --quick          # fast subset
    python3 scripts/test_utf16_oracle_fuzz.py --strict-known-defects   # reproduce it
    python3 scripts/test_utf16_oracle_fuzz.py --seed 1234 --cases 400
    python3 scripts/test_utf16_oracle_fuzz.py --seed 479 --only-case 137   # one case

All fixtures are written into a temporary directory that is removed on exit.
"""

import argparse
import os
import random
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utf16_oracle as oracle                                   # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BIN = os.path.join(
    os.environ.get("PARABIX_DIR", os.path.join(REPO_ROOT, ".deps", "parabix")),
    "build", "bin", "utf16validate")

DEFAULT_SEED = 479
DEFAULT_CASES = 200
DEFAULT_MAX_UNITS = 600
QUICK_CASES = 40
QUICK_MAX_UNITS = 200
DEFAULT_REPAIR_EVERY = 5        # repair-check every Nth case, plus REPAIR_ALWAYS below

# Categories whose repair behaviour is interesting enough to check on every case rather
# than on the every-Nth sample: the odd trailing byte policy, the degenerate sizes, and
# the placements that put a replacement next to a boundary. The broad repair campaign is
# issue #43; what is proven here is that the oracle's repaired bytes are the implementation's.
REPAIR_ALWAYS = frozenset(["empty", "one_byte", "tiny", "odd_length", "edges",
                           "reversed_pairs", "boundary_offsets", "segment_crossing"])

# Ordinary well-formed BMP code units, from several scripts. None of these is a surrogate.
BMP_POOL = [0x0041, 0x0042, 0x007A, 0x0030, 0x0020, 0x000A, 0x00E9, 0x03A9,
            0x0915, 0x0A15, 0x05D0, 0x0645, 0x0E01, 0x4E2D, 0x6587, 0xAC00,
            0xD7A3, 0xE000, 0xFFFD, 0xFFFF]
# Supplementary code points, as explicit surrogate pairs.
SUPPLEMENTARY = [0x10000, 0x1F600, 0x1F1E8, 0x20000, 0x2A6B2, 0x10400, 0x1D11E, 0x10FFFF]

BOUNDARY_OFFSETS = (15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 129)
SEGMENT_SIZES = (1, 13, 64)


def surrogate_pair(code_point):
    offset = code_point - 0x10000
    return [0xD800 + (offset >> 10), 0xDC00 + (offset & 0x3FF)]


def a_high(rng):
    return rng.randint(oracle.HIGH_FIRST, oracle.HIGH_LAST)


def a_low(rng):
    return rng.randint(oracle.LOW_FIRST, oracle.LOW_LAST)


def a_bmp(rng):
    return rng.choice(BMP_POOL)


def a_pair(rng):
    return surrogate_pair(rng.choice(SUPPLEMENTARY))


# --- Case generators -----------------------------------------------------------
# Each returns (units, odd_byte_or_None, segment_sizes). segment_sizes is the list of
# forced -segment-size values to run in addition to the pipeline default.

def gen_valid_bmp(rng, max_units):
    return [a_bmp(rng) for _ in range(rng.randint(1, max_units))], None, ()


def gen_valid_supplementary(rng, max_units):
    units = []
    while len(units) < max(2, rng.randint(2, max_units)):
        units.extend(a_pair(rng))
    return units, None, ()


def gen_mixed_valid(rng, max_units):
    units = []
    target = rng.randint(2, max_units)
    while len(units) < target:
        units.extend(a_pair(rng) if rng.random() < 0.35 else [a_bmp(rng)])
    return units, None, ()


def _sprinkle(rng, max_units, make_bad, density=0.08):
    """Well-formed filler with `make_bad(rng)` units sprinkled in at random."""
    units = []
    target = max(2, rng.randint(2, max_units))
    while len(units) < target:
        roll = rng.random()
        if roll < density:
            units.extend(make_bad(rng))
        elif roll < density + 0.25:
            units.extend(a_pair(rng))
        else:
            units.append(a_bmp(rng))
    return units


def gen_lone_high(rng, max_units):
    return _sprinkle(rng, max_units, lambda r: [a_high(r)]), None, ()


def gen_lone_low(rng, max_units):
    return _sprinkle(rng, max_units, lambda r: [a_low(r)]), None, ()


def gen_reversed_pairs(rng, max_units):
    return _sprinkle(rng, max_units, lambda r: [a_low(r), a_high(r)]), None, ()


def gen_consecutive_highs(rng, max_units):
    return _sprinkle(rng, max_units,
                     lambda r: [a_high(r) for _ in range(r.randint(2, 6))]), None, ()


def gen_consecutive_lows(rng, max_units):
    return _sprinkle(rng, max_units,
                     lambda r: [a_low(r) for _ in range(r.randint(2, 6))]), None, ()


def gen_alternating(rng, max_units):
    """Strict alternation of a well-formed run and an ill-formed unit."""
    units = []
    target = max(4, rng.randint(4, max_units))
    while len(units) < target:
        units.extend(a_pair(rng) if rng.random() < 0.5 else [a_bmp(rng)])
        units.append(a_high(rng) if rng.random() < 0.5 else a_low(rng))
    return units, None, ()


def gen_odd_length(rng, max_units):
    """Any shape, plus a trailing byte that cannot form a code unit."""
    units = _sprinkle(rng, max_units, lambda r: [a_high(r)] if r.random() < 0.5
                      else [a_low(r)], density=0.10)
    return units, rng.randint(0, 255), ()


def gen_empty(rng, max_units):
    return [], None, SEGMENT_SIZES


def gen_one_byte(rng, max_units):
    return [], rng.randint(0, 255), SEGMENT_SIZES


def gen_tiny(rng, max_units):
    """One to four code units, freely chosen from valid and invalid alike."""
    pool = [lambda r: [a_bmp(r)], lambda r: [a_high(r)], lambda r: [a_low(r)],
            lambda r: a_pair(r)]
    units = []
    for _ in range(rng.randint(1, 4)):
        units.extend(rng.choice(pool)(rng))
    odd = rng.randint(0, 255) if rng.random() < 0.3 else None
    return units, odd, SEGMENT_SIZES


def gen_medium_random(rng, max_units):
    """Unconstrained: every code unit drawn independently, surrogates included."""
    units = []
    for _ in range(rng.randint(20, max_units)):
        roll = rng.random()
        if roll < 0.72:
            units.append(a_bmp(rng))
        elif roll < 0.86:
            units.extend(a_pair(rng))
        elif roll < 0.93:
            units.append(a_high(rng))
        else:
            units.append(a_low(rng))
    odd = rng.randint(0, 255) if rng.random() < 0.25 else None
    return units, odd, ()


def gen_large(rng, max_units):
    """A large deterministic input: several scan strides' worth of code units."""
    target = rng.choice([4096, 6000, 8192, 12000])
    units = []
    while len(units) < target:
        roll = rng.random()
        if roll < 0.80:
            units.append(a_bmp(rng))
        elif roll < 0.94:
            units.extend(a_pair(rng))
        elif roll < 0.97:
            units.append(a_high(rng))
        else:
            units.append(a_low(rng))
    odd = rng.randint(0, 255) if rng.random() < 0.3 else None
    return units, odd, ()


def gen_edges(rng, max_units):
    """Ill-formed units placed at the beginning, the middle and the end."""
    body = max(8, rng.randint(8, max_units))
    units = [a_bmp(rng) for _ in range(body)]
    bad = [a_high, a_low]
    units[0] = rng.choice(bad)(rng)
    units[body // 2] = rng.choice(bad)(rng)
    units[-1] = rng.choice(bad)(rng)
    return units, None, ()


def gen_boundary(rng, max_units):
    """A valid pair or an ill-formed unit placed exactly on a boundary offset."""
    offset = rng.choice(BOUNDARY_OFFSETS)
    shape = rng.randrange(4)
    units = [a_bmp(rng) for _ in range(offset + 8)]
    if shape == 0:                                  # valid pair straddling the offset
        units[offset - 1:offset + 1] = a_pair(rng)
    elif shape == 1:                                # lone high just below the offset
        units[offset - 1] = a_high(rng)
    elif shape == 2:                                # lone low exactly on the offset
        units[offset] = a_low(rng)
    else:                                           # errors on both sides of the offset
        units[offset - 1] = a_high(rng)
        units[offset + 1] = a_low(rng)
    return units, None, ()


def gen_segment_crossing(rng, max_units):
    """A pair or an ill-formed unit on a forced segment boundary, run at that size."""
    size = rng.choice(SEGMENT_SIZES)
    where = size * rng.randint(1, 3)
    units = [a_bmp(rng) for _ in range(where + 8)]
    shape = rng.randrange(3)
    if shape == 0:                                  # pair split across the boundary
        units[where - 1:where + 1] = a_pair(rng)
    elif shape == 1:                                # ill-formed high ends the segment
        units[where - 1] = a_high(rng)
    else:                                           # ill-formed low starts the segment
        units[where] = a_low(rng)
    odd = rng.randint(0, 255) if rng.random() < 0.2 else None
    return units, odd, (size,)


GENERATORS = [
    ("valid_bmp", gen_valid_bmp),
    ("valid_supplementary", gen_valid_supplementary),
    ("mixed_valid", gen_mixed_valid),
    ("lone_high", gen_lone_high),
    ("lone_low", gen_lone_low),
    ("reversed_pairs", gen_reversed_pairs),
    ("consecutive_highs", gen_consecutive_highs),
    ("consecutive_lows", gen_consecutive_lows),
    ("alternating", gen_alternating),
    ("odd_length", gen_odd_length),
    ("empty", gen_empty),
    ("one_byte", gen_one_byte),
    ("tiny", gen_tiny),
    ("medium_random", gen_medium_random),
    ("large", gen_large),
    ("edges", gen_edges),
    ("boundary_offsets", gen_boundary),
    ("segment_crossing", gen_segment_crossing),
]


class Case(object):
    def __init__(self, index, category, units, odd_byte, segment_sizes):
        self.index = index
        self.category = category
        self.units = units
        self.odd_byte = odd_byte
        self.segment_sizes = tuple(segment_sizes)

    def data(self, big_endian):
        blob = oracle.encode_code_units(self.units, big_endian)
        if self.odd_byte is not None:
            blob += bytes([self.odd_byte])
        return blob


def build_case(seed, index, max_units):
    """One case, fully determined by (seed, index) -- never by what ran before it."""
    category, generator = GENERATORS[index % len(GENERATORS)]
    rng = random.Random("utf16-fuzz|%d|%d|%s" % (seed, index, category))
    units, odd_byte, segment_sizes = generator(rng, max_units)
    return Case(index, category, units, odd_byte, segment_sizes)


def build_cases(seed, count, max_units):
    return [build_case(seed, index, max_units) for index in range(count)]


# --- Running the implementation ------------------------------------------------

class Runner(object):
    def __init__(self, binary, workdir):
        self.binary = binary
        self.workdir = workdir
        self.invocations = 0

    def _run(self, args, binary_stdout=False):
        self.invocations += 1
        proc = subprocess.run([self.binary] + args, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError("utf16validate failed (rc=%d): %s :: %s"
                               % (proc.returncode, " ".join(args),
                                  proc.stderr.decode("utf-8", "replace").strip()))
        return proc.stdout if binary_stdout else proc.stdout.decode("utf-8", "replace")

    def write(self, name, data):
        path = os.path.join(self.workdir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def count(self, path, be, extra=(), seg=None):
        out = self._run((["-be"] if be else []) + list(extra)
                        + ([] if seg is None else ["-segment-size=%d" % seg]) + [path])
        return int(out.split("errorCount = ")[1].split()[0])

    def positions(self, path, be, mode, seg=None):
        """mode is 'print' (linear printer) or 'scan' (TwoLevelScanKernel)."""
        flag = "-print-positions" if mode == "print" else "-scan-error-marks"
        out = self._run((["-be"] if be else [])
                        + ["-emit-error-marks", flag, "-thread-num=1"]
                        + ([] if seg is None else ["-segment-size=%d" % seg]) + [path])
        found = sorted(int(line.split("=")[-1].strip(), 16)
                       for line in out.splitlines() if line.startswith("errpos"))
        return found, int(out.split("errorCount = ")[1].split()[0])

    def repair(self, path, be):
        return self._run((["-be"] if be else []) + ["--repair", path],
                         binary_stdout=True)


# --- Property bookkeeping ------------------------------------------------------

PROPERTIES = [
    ("P1", "count-agreement", "oracle == scalar == simd == errorMarks == scan"),
    ("P2", "position-agreement", "oracle == print-positions == scan positions"),
    ("P3", "repair-agreement", "oracle repaired bytes == --repair bytes"),
    ("P4", "repair-validates", "validate(repair(x)) == 0"),
    ("P5", "repair-idempotent", "repair(repair(x)) == repair(x)"),
    ("P6", "valid-unchanged", "x valid => repair(x) == x"),
    ("P7", "endian-equivalence", "LE and BE give the same count and positions"),
    ("P8", "generator-reproducible", "same seed => same case bytes"),
    ("P9", "run-deterministic", "repeated runs of a path agree"),
]


class Stats(object):
    def __init__(self):
        self.checks = dict((key, 0) for key, _, _ in PROPERTIES)
        self.passed = dict((key, 0) for key, _, _ in PROPERTIES)
        self.xfails = dict((key, 0) for key, _, _ in PROPERTIES)
        self.failures = dict((key, 0) for key, _, _ in PROPERTIES)

    def record(self, key, ok, known=False):
        """Record one property check. `known` marks an accepted known-defect failure."""
        self.checks[key] += 1
        if ok:
            self.passed[key] += 1
        elif known:
            self.xfails[key] += 1
        else:
            self.failures[key] += 1
        return ok

    def totals(self):
        return (sum(self.passed.values()), sum(self.xfails.values()),
                sum(self.failures.values()))


# --- The one known defect, classified as narrowly as it can be stated ----------
# `--scan-error-marks` (the TwoLevelScanKernel consumer) can print positions PAST THE END
# of the input when the input is a positive exact multiple of the 4096-code-unit scan
# stride. Counts stay correct on every path -- including the scan's own errorCount, which
# is why the scan output is internally inconsistent -- and `--print-positions` stays
# correct, so only the two-level scan's position stream is affected.
#
# A P2 mismatch is accepted as KNOWN-XFAIL only when EVERY condition below holds. Anything
# else -- a count mismatch, a repair mismatch, a wrong linear position, a missing scan
# position, an in-range spurious scan position, an odd-length input, or a length that is
# not a multiple of 4096 -- is an ordinary failure and still fails the run.

KNOWN_DEFECT_STRIDE = 4096
KNOWN_DEFECT_NAME = "scan-tail-out-of-range (exact-multiple-of-4096 stride)"


def classify_scan_tail_defect(oracle_pos, print_pos, scan_pos, counts_ok,
                              unit_count, odd_trailing_byte):
    """Is this P2 mismatch the one known scan-tail defect? Returns (bool, reason).

    The reason is returned either way: on a match it describes the accepted anomaly, and
    on a non-match it names the first condition that ruled the classification out, so a
    genuinely new defect can never be quietly absorbed into this bucket.
    """
    # The mismatching path must be the two-level scan, and nothing else.
    if print_pos != oracle_pos:
        return False, "--print-positions disagrees with the oracle"
    if scan_pos == oracle_pos:
        return False, "scan positions agree (not a mismatch)"
    if not counts_ok:
        return False, "validator counts do not all agree"
    if odd_trailing_byte:
        return False, "input has an odd byte length"
    if unit_count <= 0 or unit_count % KNOWN_DEFECT_STRIDE != 0:
        return False, ("input is %d code units, not a positive exact multiple of %d"
                       % (unit_count, KNOWN_DEFECT_STRIDE))

    # The scan must report every real position, and every extra one must be out of range.
    expected = set(oracle_pos)
    missing = [p for p in oracle_pos if p not in set(scan_pos)]
    if missing:
        return False, "scan is missing real position(s) %s" % missing[:4]
    unexpected = [p for p in scan_pos if p not in expected]
    if not unexpected:
        return False, "scan differs but reports no unexpected position"
    in_range = [p for p in unexpected if 0 <= p < unit_count]
    if in_range:
        return False, ("unexpected scan position(s) %s are INSIDE the valid code-unit "
                       "range 0..%d" % (in_range[:4], unit_count - 1))

    return True, ("%d unexpected scan position(s) %s, all >= the %d code units of input"
                  % (len(unexpected), unexpected[:4], unit_count))


def hexdump(data, limit=64):
    if len(data) <= 2 * limit:
        return data.hex()
    return "%s ... %s (%d bytes total)" % (data[:limit].hex(), data[-limit:].hex(),
                                           len(data))


def units_summary(units, limit=32):
    shown = " ".join("%04X" % u for u in units[:limit])
    if len(units) > limit:
        shown += " ... (%d units total)" % len(units)
    return shown or "(none)"


def describe_position_diff(oracle_pos, print_pos, scan_pos):
    """Name the first index where the three position lists diverge.

    Printing three truncated prefixes is useless when they agree on the prefix and differ
    later, so this reports the first disagreeing element and what each path said there.
    """
    for index in range(max(len(oracle_pos), len(print_pos), len(scan_pos))):
        got = [lst[index] if index < len(lst) else None
               for lst in (oracle_pos, print_pos, scan_pos)]
        if got[0] != got[1] or got[0] != got[2]:
            return ("first divergence at element %d: oracle=%s print=%s scan=%s "
                    "(lengths %d/%d/%d)"
                    % (index, got[0], got[1], got[2],
                       len(oracle_pos), len(print_pos), len(scan_pos)))
    return ("lengths %d/%d/%d" % (len(oracle_pos), len(print_pos), len(scan_pos)))


def report_failure(args, case, encoding, data, diag, observed, problems):
    """Everything needed to understand and reproduce one failing case."""
    print("")
    print("  " + "-" * 76)
    print("  FAILING CASE")
    print("    seed              : %d" % args.seed)
    print("    case number       : %d" % case.index)
    print("    category          : %s" % case.category)
    print("    encoding          : %s" % encoding)
    print("    raw bytes (hex)   : %s" % hexdump(data))
    print("    code units        : %s" % units_summary(diag.code_units))
    print("    odd trailing byte : %s" % ("yes" if diag.odd_trailing_byte else "no"))
    print("    oracle errorCount : %d" % diag.error_count)
    print("    oracle positions  : %s" % (diag.malformed_positions[:32]
                                          or "[]"))
    for label, value in observed:
        print("    %-18s: %s" % (label, value))
    for problem in problems:
        print("    PROBLEM           : %s" % problem)
    print("    rerun             : python3 scripts/test_utf16_oracle_fuzz.py "
          "--seed %d --only-case %d --max-units %d"
          % (args.seed, case.index, args.max_units))
    print("  " + "-" * 76)
    print("")


# --- The per-case check --------------------------------------------------------

def check_case(args, case, runner, stats, do_repair):
    problems = []
    known_notes = []
    le_data = case.data(False)
    be_data = case.data(True)
    le_diag = oracle.analyze(le_data, False)
    be_diag = oracle.analyze(be_data, True)

    # P7: the same intended code units in two encodings must be diagnosed identically,
    # and must differ only by the byte swap of each whole code unit.
    body = len(le_data) - (1 if case.odd_byte is not None else 0)
    swapped = bytearray(le_data[:body])
    swapped[0::2], swapped[1::2] = le_data[1:body:2], le_data[0:body:2]
    tail = le_data[body:]
    stats.record("P7", le_diag.error_count == be_diag.error_count
                 and le_diag.malformed_positions == be_diag.malformed_positions
                 and bytes(swapped) + tail == be_data)
    if (le_diag.error_count != be_diag.error_count
            or le_diag.malformed_positions != be_diag.malformed_positions):
        problems.append("oracle disagrees between LE and BE")
    if bytes(swapped) + tail != be_data:
        problems.append("BE bytes are not the byte swap of the LE bytes")

    for encoding, data, diag, be in (("UTF-16LE", le_data, le_diag, False),
                                     ("UTF-16BE", be_data, be_diag, True)):
        stem = "case%05d_%s" % (case.index, "be" if be else "le")
        path = runner.write(stem + ".bin", data)
        observed = []
        case_problems = []

        for seg in (None,) + case.segment_sizes:
            tag = "" if seg is None else " ss=%d" % seg

            scalar = runner.count(path, be, seg=seg)
            simd = runner.count(path, be, extra=["-simd"], seg=seg)
            marks = runner.count(path, be, extra=["-emit-error-marks"], seg=seg)
            print_pos, print_count = runner.positions(path, be, "print", seg=seg)
            scan_pos, scan_count = runner.positions(path, be, "scan", seg=seg)

            counts_ok = (scalar == simd == marks == scan_count == print_count
                         == diag.error_count)
            stats.record("P1", counts_ok)
            if not counts_ok:
                case_problems.append(
                    "count mismatch%s: oracle=%d scalar=%d simd=%d marks=%d "
                    "print=%d scan=%d" % (tag, diag.error_count, scalar, simd, marks,
                                          print_count, scan_count))

            positions_ok = (print_pos == scan_pos == diag.malformed_positions)
            known = False
            if not positions_ok:
                known, reason = classify_scan_tail_defect(
                    diag.malformed_positions, print_pos, scan_pos, counts_ok,
                    len(diag.code_units), diag.odd_trailing_byte)
                if known and not args.strict_known_defects:
                    known_notes.append("%s%s: %s" % (encoding, tag, reason))
                else:
                    case_problems.append(
                        "position mismatch%s: %s" % (tag, describe_position_diff(
                            diag.malformed_positions, print_pos, scan_pos)))
                    if known:
                        case_problems.append(
                            "classified as the known defect (%s) but --strict-known-defects "
                            "is set: %s" % (KNOWN_DEFECT_NAME, reason))
                    else:
                        case_problems.append("NOT the known defect: %s" % reason)
            stats.record("P2", positions_ok,
                         known=known and not args.strict_known_defects)

            if seg is None:
                observed = [("scalar count", scalar), ("simd count", simd),
                            ("errorMarks count", marks),
                            ("print-positions", "count=%d positions=%s"
                             % (print_count, print_pos[:32])),
                            ("scan positions", "count=%d positions=%s"
                             % (scan_count, scan_pos[:32]))]

        if do_repair:
            repaired = runner.repair(path, be)
            observed.append(("repair bytes", hexdump(repaired)))

            # P3: byte-for-byte agreement with the oracle's repaired output.
            if not stats.record("P3", repaired == diag.repaired):
                case_problems.append("repair mismatch: impl=%s oracle=%s"
                                     % (hexdump(repaired), hexdump(diag.repaired)))

            repaired_path = runner.write(stem + ".rep", repaired)

            # P4: the repaired output is well-formed, per the implementation itself.
            repaired_count = runner.count(repaired_path, be)
            if not stats.record("P4", repaired_count == 0):
                case_problems.append("validate(repair(x)) = %d, expected 0"
                                     % repaired_count)

            # P5: idempotence.
            again = runner.repair(repaired_path, be)
            if not stats.record("P5", again == repaired):
                case_problems.append("repair is not idempotent")

            # P6: valid input must come back untouched.
            if diag.error_count == 0:
                if not stats.record("P6", repaired == data):
                    case_problems.append("valid input was modified by repair")

        if case_problems:
            problems.extend(case_problems)
            report_failure(args, case, encoding, data, diag, observed, case_problems)

    return problems, known_notes


# --- Targeted regression for the known scan-tail defect ------------------------
# Fixed, seed-independent inputs sized to exact multiples of the 4096-code-unit scan
# stride. These pin the defect down so it cannot change shape unnoticed: whatever the scan
# does, the oracle and --print-positions must agree, the counts must be right on every
# path, and any scan divergence must still satisfy the narrow known-defect predicate.

TARGETED_CASES = [
    ("4096 units, one lone high at 10", 4096, [10]),
    ("4096 units, ill-formed every 20th unit", 4096, list(range(0, 4096, 20))),
    ("8192 units, ill-formed every 20th unit", 8192, list(range(0, 8192, 20))),
]


def check_targeted_defect(args, runner, stats):
    """Deterministic 4096/8192-code-unit regression for the known scan-tail defect."""
    problems = []
    for label, unit_count, bad_indices in TARGETED_CASES:
        units = [0x0041] * unit_count
        for index in bad_indices:
            units[index] = 0xD800                   # a lone high surrogate
        for encoding, be in (("UTF-16LE", False), ("UTF-16BE", True)):
            data = oracle.encode_code_units(units, be)
            diag = oracle.analyze(data, be)
            path = runner.write("targeted_%d_%d_%s.bin"
                                % (unit_count, len(bad_indices),
                                   "be" if be else "le"), data)

            scalar = runner.count(path, be)
            simd = runner.count(path, be, extra=["-simd"])
            marks = runner.count(path, be, extra=["-emit-error-marks"])
            print_pos, print_count = runner.positions(path, be, "print")
            scan_pos, scan_count = runner.positions(path, be, "scan")

            # Counts must be correct on every path, defect or not.
            counts_ok = (scalar == simd == marks == print_count == scan_count
                         == diag.error_count)
            stats.record("P1", counts_ok)
            if not counts_ok:
                problems.append("%s %s: counts oracle=%d scalar=%d simd=%d marks=%d "
                                "print=%d scan=%d" % (label, encoding, diag.error_count,
                                                      scalar, simd, marks, print_count,
                                                      scan_count))

            # The oracle and the linear printer must agree exactly, defect or not.
            linear_ok = print_pos == diag.malformed_positions
            if not linear_ok:
                problems.append("%s %s: --print-positions disagrees with the oracle"
                                % (label, encoding))

            positions_ok = (print_pos == scan_pos == diag.malformed_positions)
            known = False
            if not positions_ok:
                known, reason = classify_scan_tail_defect(
                    diag.malformed_positions, print_pos, scan_pos, counts_ok,
                    len(diag.code_units), diag.odd_trailing_byte)
                if known and not args.strict_known_defects:
                    print("  KNOWN-XFAIL %-42s %s  %s" % (label, encoding, reason))
                else:
                    problems.append("%s %s: %s -- %s"
                                    % (label, encoding,
                                       describe_position_diff(diag.malformed_positions,
                                                              print_pos, scan_pos),
                                       ("known defect, but --strict-known-defects is set"
                                        if known else "NOT the known defect: " + reason)))
                    print("  FAIL        %-42s %s  scan positions diverge (%s)"
                          % (label, encoding,
                             "known defect, strict mode" if known
                             else "unexpected: " + reason))
                    print("              reproduce: ./scripts/test_utf16_oracle_fuzz.sh "
                          "--strict-known-defects")
            else:
                print("  PASS        %-42s %s  errors=%-5d all paths agree"
                      % (label, encoding, diag.error_count))
            stats.record("P2", positions_ok,
                         known=known and not args.strict_known_defects)
    return problems


def check_determinism(args, cases, runner, stats):
    """P9: the same file through the same path must give the same answer every time."""
    sample = [case for case in cases if len(case.units) <= 4096][:6]
    problems = []
    for case in sample:
        for be in (False, True):
            data = case.data(be)
            path = runner.write("det%05d_%s.bin" % (case.index, "be" if be else "le"),
                                data)
            seen = set()
            for _ in range(3):
                counts = (runner.count(path, be),
                          runner.count(path, be, extra=["-simd"]),
                          tuple(runner.positions(path, be, "scan")[0]))
                seen.add(counts)
            if not stats.record("P9", len(seen) == 1):
                problems.append("case %d (%s): repeated runs differ"
                                % (case.index, "BE" if be else "LE"))
    return problems


def check_generator_reproducible(args, cases, stats):
    """P8: rebuilding the case list from the same seed reproduces the same bytes."""
    rebuilt = [build_case(args.seed, case.index, args.max_units) for case in cases]
    problems = []
    for original, again in zip(cases, rebuilt):
        same = (original.units == again.units
                and original.odd_byte == again.odd_byte
                and original.segment_sizes == again.segment_sizes)
        if not stats.record("P8", same):
            problems.append("case %d is not reproducible from the seed" % original.index)
    return problems


# --- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="generator seed (default: %d)" % DEFAULT_SEED)
    parser.add_argument("--cases", type=int, default=None,
                        help="number of cases (default: %d, or %d with --quick)"
                             % (DEFAULT_CASES, QUICK_CASES))
    parser.add_argument("--max-units", type=int, default=None,
                        help="upper bound on generated code units per case "
                             "(default: %d, or %d with --quick)"
                             % (DEFAULT_MAX_UNITS, QUICK_MAX_UNITS))
    parser.add_argument("--quick", action="store_true",
                        help="fast subset: fewer and smaller cases")
    parser.add_argument("--only-case", type=int, default=None,
                        help="run exactly one case index (for reproducing a failure)")
    parser.add_argument("--repair-every", type=int, default=DEFAULT_REPAIR_EVERY,
                        help="repair-check every Nth case (default: %d; 1 = all)"
                             % DEFAULT_REPAIR_EVERY)
    parser.add_argument("--strict-known-defects", action="store_true",
                        help="treat the known scan-tail defect as an ordinary failure "
                             "(exits non-zero); use this to reproduce it")
    parser.add_argument("--bin", default=os.environ.get("UTF16VALIDATE_BIN", DEFAULT_BIN),
                        help="path to utf16validate")
    args = parser.parse_args()

    if args.cases is None:
        args.cases = QUICK_CASES if args.quick else DEFAULT_CASES
    if args.max_units is None:
        args.max_units = QUICK_MAX_UNITS if args.quick else DEFAULT_MAX_UNITS
    if args.repair_every < 1:
        raise SystemExit("ERROR: --repair-every must be >= 1")

    if not os.access(args.bin, os.X_OK):
        print("ERROR: utf16validate not found at %s" % args.bin, file=sys.stderr)
        print("       Run ./scripts/setup_parabix.sh first.", file=sys.stderr)
        return 1

    print("UTF-16 oracle fuzz / property suite (issue #42)")
    print("  oracle    : scripts/utf16_oracle.py (written from the UTF-16 definition)")
    print("  binary    : %s" % args.bin)
    print("  seed      : %d" % args.seed)
    print("  cases     : %d%s" % (args.cases, "  (quick mode)" if args.quick else ""))
    print("  max units : %d" % args.max_units)
    print("  repair    : every %d case(s)" % args.repair_every)
    print("  paths     : scalar, --simd, --emit-error-marks, --print-positions, "
          "--scan-error-marks, --repair")
    print("  encodings : UTF-16LE and UTF-16BE, from the same code units")
    print("  known     : %s" % ("STRICT -- the known defect below counts as a failure"
                                if args.strict_known_defects
                                else "%s -> KNOWN-XFAIL" % KNOWN_DEFECT_NAME))
    print()

    print("== oracle self test ==")
    if oracle.self_test():
        print("ORACLE SELF TEST FAILED -- not running the fuzz suite")
        return 1
    print("  %d hand-worked vectors x 2 encodings OK" % len(oracle.SELF_TEST_VECTORS))
    print()

    cases = build_cases(args.seed, args.cases, args.max_units)
    if args.only_case is not None:
        cases = [case for case in cases if case.index == args.only_case]
        if not cases:
            cases = [build_case(args.seed, args.only_case, args.max_units)]

    stats = Stats()
    failed_cases = 0
    xfail_cases = 0
    workdir = tempfile.mkdtemp(prefix="utf16-fuzz-")
    runner = Runner(args.bin, workdir)
    try:
        print("== cases ==")
        for case in cases:
            do_repair = (case.category in REPAIR_ALWAYS
                         or case.index % args.repair_every == 0
                         or args.only_case is not None)
            problems, known_notes = check_case(args, case, runner, stats, do_repair)
            diag = oracle.analyze(case.data(False), False)
            if problems:
                failed_cases += 1
                status = "FAIL       "
            elif known_notes:
                xfail_cases += 1
                status = "KNOWN-XFAIL"
            else:
                status = "PASS       "
            print("  %s case %-5d %-20s units=%-6d odd=%d errors=%-4d repair=%s"
                  % (status, case.index, case.category, len(case.units),
                     1 if case.odd_byte is not None else 0, diag.error_count,
                     "yes" if do_repair else "no"))
            for note in known_notes:
                print("              known defect (%s): %s" % (KNOWN_DEFECT_NAME, note))

        if args.only_case is None:
            print()
            print("== targeted regression for the known scan-tail defect ==")
            for problem in check_targeted_defect(args, runner, stats):
                failed_cases += 1
                print("  FAIL %s" % problem)

        print()
        print("== P8 generator reproducibility ==")
        for problem in check_generator_reproducible(args, cases, stats):
            failed_cases += 1
            print("  FAIL %s" % problem)
        print("  %d cases regenerate identically from seed %d" % (len(cases), args.seed))

        print()
        print("== P9 run determinism ==")
        for problem in check_determinism(args, cases, runner, stats):
            failed_cases += 1
            print("  FAIL %s" % problem)
        print("  repeated runs agree on the sampled cases")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    print("== properties ==")
    print("  %-3s %-24s %-46s %8s %8s %8s"
          % ("", "property", "", "passed", "xfail", "failed"))
    for key, name, description in PROPERTIES:
        passed = stats.passed[key]
        xfail = stats.xfails[key]
        failures = stats.failures[key]
        state = "FAILED" if failures else ("known-xfail" if xfail else "ok")
        print("  %-3s %-24s %-46s %8d %8d %8d  %s"
              % (key, name, description, passed, xfail, failures, state))

    total_passed, total_xfail, total_failed = stats.totals()
    print()
    print("%d cases (%d clean, %d known-xfail, %d failed), %d property checks, "
          "%d validator invocations"
          % (len(cases), len(cases) - xfail_cases - failed_cases, xfail_cases,
             failed_cases, total_passed + total_xfail + total_failed,
             runner.invocations))
    print("%d passed, %d known-xfail, %d failed"
          % (total_passed, total_xfail, total_failed))

    if total_failed or failed_cases:
        print()
        if args.strict_known_defects:
            print("ORACLE FUZZ TESTS FAILED (seed %d) -- --strict-known-defects is set, "
                  "so the known" % args.seed)
            print("%s defect counts as a failure." % KNOWN_DEFECT_NAME)
            print("Reproduce: ./scripts/test_utf16_oracle_fuzz.sh --strict-known-defects")
        else:
            print("ORACLE FUZZ TESTS FAILED (seed %d) -- these are NOT the known defect."
                  % args.seed)
        print("Reproduce a single case with: "
              "python3 scripts/test_utf16_oracle_fuzz.py --seed %d --only-case N"
              % args.seed)
        return 1

    if total_xfail:
        print()
        print("ALL ORACLE FUZZ TESTS PASSED (%d known-xfail: %s)"
              % (total_xfail, KNOWN_DEFECT_NAME))
        print("Reproduce the known defect with: "
              "./scripts/test_utf16_oracle_fuzz.sh --strict-known-defects")
        return 0
    print("ALL ORACLE FUZZ TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
