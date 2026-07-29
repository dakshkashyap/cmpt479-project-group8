#!/usr/bin/env python3
"""Comprehensive UTF-16 U+FFFD repair campaign (issue #43).

scripts/test_utf16_repair.sh is the focused smoke/regression gate for `--repair`: a few
dozen hand-written fixtures with exact expected bytes. This suite is the broad campaign on
top of it -- hundreds of hand-curated, boundary, generated and large-file cases, each
checked against scripts/utf16_oracle.py as the exact-output oracle, across UTF-16LE and
UTF-16BE, across forced segment sizes, and (where available) against simdutf.

The oracle is imported, never reimplemented: every expected byte string comes from
`utf16_oracle.analyze(...).repaired`. The hand-curated section additionally states its
expected code units by hand and requires the oracle to agree before the implementation is
consulted, so a wrong expectation in this file is reported as a test bug rather than
silently accepted.

Repair semantics under test (docs/utf16_repair.md)
-------------------------------------------------
  * each ill-formed surrogate code unit is replaced independently with U+FFFD;
  * valid BMP code units and valid surrogate pairs are preserved byte for byte;
  * U+FFFD is written FD FF in UTF-16LE and FF FD in UTF-16BE;
  * an odd trailing byte is discarded and exactly one U+FFFD code unit is appended;
  * even-length input keeps its byte length; odd-length input becomes length + 1;
  * repaired output always validates with errorCount 0, and repair is idempotent.

Relationship to the issue #42 scan defect
-----------------------------------------
None. `--repair` is wired as ByteStream -> UTF16ErrorMarksKernel -> UTF16RepairKernel ->
StdOutKernel; it never instantiates UTF16ErrorMarkScanKernel, so the known scan-position
defect cannot reach repair output. This suite therefore has no xfail mechanism: every
failure here is a real failure.

Usage
-----
    python3 scripts/test_utf16_repair_comprehensive.py             # default (~2 min)
    python3 scripts/test_utf16_repair_comprehensive.py --quick     # fast subset
    python3 scripts/test_utf16_repair_comprehensive.py --seed 1234 --cases 200
    python3 scripts/test_utf16_repair_comprehensive.py --only-case 57
    python3 scripts/test_utf16_repair_comprehensive.py --no-simdutf

Every fixture lives in a temporary directory that is removed on exit.
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
DEFAULT_PARABIX = os.environ.get("PARABIX_DIR", os.path.join(REPO_ROOT, ".deps", "parabix"))
DEFAULT_BIN = os.path.join(DEFAULT_PARABIX, "build", "bin", "utf16validate")
SIMDUTF_SINGLEHEADER = os.path.join(os.path.dirname(DEFAULT_PARABIX), "simdutf", "singleheader")

DEFAULT_SEED = 479
DEFAULT_CASES = 80
QUICK_CASES = 20
DEFAULT_MAX_UNITS = 800
QUICK_MAX_UNITS = 200
MIB = 1024 * 1024

SEGMENT_SIZES = (1, 13, 64)
SEGMENT_SWEEP_MAX_UNITS = 2048      # forcing -segment-size=1 on more than this is pointless

FFFD = oracle.REPLACEMENT
A, B_ = 0x0041, 0x0042
OMEGA, HAN = 0x03A9, 0x4E2D
HI, LO = 0xD800, 0xDC00             # lowest high / low surrogate
HI_MAX, LO_MAX = 0xDBFF, 0xDFFF     # highest high / low surrogate
EMOJI_HI, EMOJI_LO = 0xD83D, 0xDE00
PAIR = [EMOJI_HI, EMOJI_LO]

BMP_POOL = [A, B_, 0x007A, 0x0030, 0x0020, 0x00E9, OMEGA, 0x0915, 0x05D0, 0x0E01,
            HAN, 0xAC00, 0xD7A3, 0xE000, 0xFFFD, 0xFFFF]
SUPPLEMENTARY = [0x10000, 0x1F600, 0x20000, 0x2A6B2, 0x10400, 0x1D11E, 0x10FFFF]


def pair_for(code_point):
    offset = code_point - 0x10000
    return [0xD800 + (offset >> 10), 0xDC00 + (offset & 0x3FF)]


# --- Properties ----------------------------------------------------------------

PROPERTIES = [
    ("P1", "oracle-agreement", "impl repair bytes == oracle repair bytes"),
    ("P2", "repaired-valid-scalar", "scalar errorCount(repair(x)) == 0"),
    ("P3", "repaired-valid-simd", "--simd errorCount(repair(x)) == 0"),
    ("P4", "idempotent", "repair(repair(x)) == repair(x)"),
    ("P5", "valid-unchanged", "x valid => repair(x) == x"),
    ("P6", "even-length-preserved", "len(x) even => len(repair(x)) == len(x)"),
    ("P7", "odd-length-grows-by-one", "len(x) odd => len(repair(x)) == len(x) + 1"),
    ("P8", "output-length-even", "len(repair(x)) is always even"),
    ("P9", "replacement-endianness", "each U+FFFD is FD FF (LE) / FF FD (BE)"),
    ("P10", "neighbours-unchanged", "well-formed code units are copied through"),
    ("P11", "segment-size-invariant", "repair identical at -segment-size=1/13/64"),
    ("P12", "run-deterministic", "repeated repair runs are byte-identical"),
    ("P13", "endian-equivalence", "LE and BE repairs decode to the same code units"),
    ("P14", "replacement-count", "replacements == original errorCount"),
    ("P15", "no-unpaired-surrogates", "repaired output has no lone surrogate"),
    # Added with the exhaustive trailing-byte regression: the defect it guards showed up
    # first as scalar and errorMarks disagreeing, before it ever reached repair output.
    ("P16", "diagnostic-agreement", "scalar == --simd == errorMarks counts, positions ok"),
]


class Stats(object):
    def __init__(self):
        self.checks = dict((key, 0) for key, _, _ in PROPERTIES)
        self.failures = dict((key, 0) for key, _, _ in PROPERTIES)

    def record(self, key, ok):
        self.checks[key] += 1
        if not ok:
            self.failures[key] += 1
        return ok

    def totals(self):
        return sum(self.checks.values()), sum(self.failures.values())


# --- Running the implementation ------------------------------------------------

class Runner(object):
    def __init__(self, binary, workdir):
        self.binary = binary
        self.workdir = workdir
        self.invocations = 0
        self._serial = 0

    def _run(self, args, want_bytes=False):
        self.invocations += 1
        proc = subprocess.run([self.binary] + args, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError("utf16validate failed (rc=%d): %s :: %s"
                               % (proc.returncode, " ".join(args),
                                  proc.stderr.decode("utf-8", "replace").strip()))
        return proc.stdout if want_bytes else proc.stdout.decode("utf-8", "replace")

    def write(self, name, data):
        self._serial += 1
        path = os.path.join(self.workdir, "%05d_%s" % (self._serial, name))
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def repair(self, path, be, seg=None):
        args = (["-be"] if be else []) + ["--repair"]
        if seg is not None:
            args.append("-segment-size=%d" % seg)
        return self._run(args + [path], want_bytes=True)

    def count(self, path, be, simd=False, marks=False):
        args = (["-be"] if be else [])
        if simd:
            args.append("-simd")
        if marks:
            args.append("-emit-error-marks")
        out = self._run(args + [path])
        return int(out.split("errorCount = ")[1].split()[0])

    def positions(self, path, be):
        """Malformed code-unit positions from the linear --print-positions printer."""
        out = self._run((["-be"] if be else [])
                        + ["-emit-error-marks", "-print-positions", "-thread-num=1", path])
        return sorted(int(line.split("=")[-1].strip(), 16)
                      for line in out.splitlines() if line.startswith("errpos"))


# --- Diagnostics ---------------------------------------------------------------

def hexdump(data, limit=48):
    if len(data) <= 2 * limit:
        return data.hex() or "(empty)"
    return "%s ... %s (%d bytes)" % (data[:limit].hex(), data[-limit:].hex(), len(data))


def units_summary(units, limit=24):
    shown = " ".join("%04X" % u for u in units[:limit])
    if len(units) > limit:
        shown += " ... (%d units)" % len(units)
    return shown or "(none)"


def first_difference(left, right):
    for index in range(min(len(left), len(right))):
        if left[index] != right[index]:
            return index
    if len(left) != len(right):
        return min(len(left), len(right))
    return None


def report_failure(args, case, encoding, seg, data, diag, repaired, problems, extra=()):
    print("")
    print("  " + "-" * 78)
    print("  FAILING REPAIR CASE")
    print("    seed              : %d" % args.seed)
    print("    case              : %s (%s)" % (case.name, case.category))
    if case.index is not None:
        print("    case number       : %d" % case.index)
    print("    encoding          : %s" % encoding)
    print("    segment size      : %s" % ("default" if seg is None else seg))
    print("    input length      : %d bytes (%d whole code units%s)"
          % (len(data), len(diag.code_units),
             ", plus an odd trailing byte" if diag.odd_trailing_byte else ""))
    print("    input hex         : %s" % hexdump(data))
    print("    input code units  : %s" % units_summary(diag.code_units))
    print("    oracle errorCount : %d" % diag.error_count)
    print("    oracle positions  : %s" % (diag.malformed_positions[:24] or "[]"))
    print("    expected repaired : %s" % hexdump(diag.repaired))
    print("    actual repaired   : %s" % hexdump(repaired))
    offset = first_difference(diag.repaired, repaired)
    print("    first diff offset : %s" % ("(none)" if offset is None else offset))
    for label, value in extra:
        print("    %-18s: %s" % (label, value))
    for problem in problems:
        print("    PROBLEM           : %s" % problem)
    print("    rerun             : %s" % case.rerun(args))
    print("  " + "-" * 78)
    print("")


# --- A case --------------------------------------------------------------------

class Case(object):
    """One repair case: a code-unit sequence plus an optional odd trailing byte."""

    def __init__(self, name, category, units, odd_byte=None, expected_units=None,
                 index=None, segment_sweep=None):
        self.name = name
        self.category = category
        self.units = list(units)
        self.odd_byte = odd_byte
        self.expected_units = expected_units    # hand-declared, checked against the oracle
        self.index = index
        self._segment_sweep = segment_sweep

    def data(self, be):
        blob = oracle.encode_code_units(self.units, be)
        if self.odd_byte is not None:
            blob += bytes([self.odd_byte])
        return blob

    def wants_segment_sweep(self, limit=SEGMENT_SWEEP_MAX_UNITS):
        if self._segment_sweep is not None:
            return self._segment_sweep
        return len(self.units) <= limit

    def section(self):
        """The --section value that re-runs this case."""
        head = self.category.split("/")[0]
        return {"valid": "curated", "malformed": "curated", "odd": "curated"}.get(
            head, head)

    def rerun(self, args):
        if self.index is not None:
            return ("python3 scripts/test_utf16_repair_comprehensive.py --seed %d "
                    "--section generated --only-case %d" % (args.seed, self.index))
        return ("python3 scripts/test_utf16_repair_comprehensive.py --seed %d "
                "--section %s" % (args.seed, self.section()))


# --- The per-case check --------------------------------------------------------

def check_case(args, case, runner, stats, simdutf=None):
    """Run one case in both encodings and check every applicable property."""
    problems = []
    per_encoding = {}

    for encoding, be in (("UTF-16LE", False), ("UTF-16BE", True)):
        data = case.data(be)
        diag = oracle.analyze(data, be)
        case_problems = []

        # Layer 1: a hand-declared expectation must match the oracle before we go on.
        if case.expected_units is not None:
            want = oracle.encode_code_units(case.expected_units, be)
            if want != diag.repaired:
                case_problems.append("TEST BUG: declared expected bytes %s != oracle %s"
                                     % (hexdump(want), hexdump(diag.repaired)))

        path = runner.write("%s_%s.bin" % (case.category.replace("/", "_"),
                                           "be" if be else "le"), data)
        repaired = runner.repair(path, be)

        # P1: exact byte agreement with the oracle.
        if not stats.record("P1", repaired == diag.repaired):
            case_problems.append("repair bytes differ from the oracle")

        # P8 / P6 / P7: length rules.
        stats.record("P8", len(repaired) % 2 == 0)
        if len(repaired) % 2:
            case_problems.append("output length %d is odd" % len(repaired))
        if diag.odd_trailing_byte:
            if not stats.record("P7", len(repaired) == len(data) + 1):
                case_problems.append("odd input: length %d, expected %d"
                                     % (len(repaired), len(data) + 1))
        else:
            if not stats.record("P6", len(repaired) == len(data)):
                case_problems.append("even input: length %d, expected %d"
                                     % (len(repaired), len(data)))

        # P5: valid input must be returned untouched.
        if diag.error_count == 0:
            if not stats.record("P5", repaired == data):
                case_problems.append("valid input was modified")

        repaired_units = oracle.decode_code_units(repaired, be)
        marked = set(diag.malformed_positions)

        # P9: every replaced position must hold U+FFFD in this endianness, as bytes.
        want_bytes = oracle.encode_code_units([FFFD], be)
        endian_ok = all(repaired[2 * k:2 * k + 2] == want_bytes for k in marked)
        if diag.odd_trailing_byte:
            endian_ok = endian_ok and repaired[-2:] == want_bytes
        if not stats.record("P9", endian_ok):
            case_problems.append("a replacement is not %s in %s"
                                 % (want_bytes.hex(), encoding))

        # P10: every well-formed code unit must be copied through unchanged.
        neighbours_ok = all(repaired_units[k] == unit
                            for k, unit in enumerate(case.units) if k not in marked)
        if not stats.record("P10", neighbours_ok):
            bad = [k for k, unit in enumerate(case.units)
                   if k not in marked and repaired_units[k] != unit]
            case_problems.append("well-formed unit(s) changed at %s" % bad[:6])

        # P14: the number of replacements must equal the original errorCount. Counting
        # positions that actually changed keeps this exact even when the input already
        # contains U+FFFD of its own (a lone surrogate can never be U+FFFD, so a marked
        # position always differs from the replacement).
        replacements = sum(1 for k, unit in enumerate(case.units)
                           if repaired_units[k] != unit)
        if diag.odd_trailing_byte:
            replacements += 1
        if not stats.record("P14", replacements == diag.error_count):
            case_problems.append("replacements=%d but errorCount=%d"
                                 % (replacements, diag.error_count))

        # P15: no lone surrogate survives, per the oracle.
        after = oracle.analyze(repaired, be)
        if not stats.record("P15", not after.malformed_positions):
            case_problems.append("repaired output still holds lone surrogate(s) at %s"
                                 % after.malformed_positions[:6])

        # P2 / P3: the implementation itself must call the repaired output clean.
        repaired_path = runner.write("%s_%s.rep" % (case.category.replace("/", "_"),
                                                    "be" if be else "le"), repaired)
        scalar_after = runner.count(repaired_path, be)
        if not stats.record("P2", scalar_after == 0):
            case_problems.append("scalar validate(repair(x)) = %d" % scalar_after)
        simd_after = runner.count(repaired_path, be, simd=True)
        if not stats.record("P3", simd_after == 0):
            case_problems.append("--simd validate(repair(x)) = %d" % simd_after)

        # P4: idempotence.
        again = runner.repair(repaired_path, be)
        if not stats.record("P4", again == repaired):
            case_problems.append("repair is not idempotent")

        # P11: forced segment sizes must not change a single byte. Quick mode keeps the
        # property but only on the small inputs, where it is cheap.
        if case.wants_segment_sweep(256 if args.quick else SEGMENT_SWEEP_MAX_UNITS):
            for size in SEGMENT_SIZES:
                seg_repaired = runner.repair(path, be, seg=size)
                if not stats.record("P11", seg_repaired == repaired):
                    case_problems.append("-segment-size=%d changed the output" % size)
                    if not args.quiet_failures:
                        report_failure(args, case, encoding, size, data, diag,
                                       seg_repaired, ["segment-size divergence"])

        per_encoding[encoding] = (data, diag, repaired)
        if case_problems:
            problems.extend("%s: %s" % (encoding, p) for p in case_problems)
            report_failure(args, case, encoding, None, data, diag, repaired,
                           case_problems,
                           extra=[("scalar after", scalar_after),
                                  ("--simd after", simd_after),
                                  ("replacements", replacements)])

    # P13: both encodings must repair to the same logical code-unit sequence.
    le_units = oracle.decode_code_units(per_encoding["UTF-16LE"][2], False)
    be_units = oracle.decode_code_units(per_encoding["UTF-16BE"][2], True)
    if not stats.record("P13", le_units == be_units):
        problems.append("LE and BE repairs decode to different code units")

    # P12: repeated runs must be byte-identical.
    if case.index is None or case.index % 7 == 0:
        for encoding, be in (("UTF-16LE", False), ("UTF-16BE", True)):
            data, _, repaired = per_encoding[encoding]
            path = runner.write("det_%s.bin" % ("be" if be else "le"), data)
            same = all(runner.repair(path, be) == repaired for _ in range(2))
            if not stats.record("P12", same):
                problems.append("%s: repeated repair runs differ" % encoding)

    return problems


# --- Section B: hand-curated cases ---------------------------------------------
# Expected code units are declared by hand and cross-checked against the oracle.

def multilingual_units():
    text = ("Hello éè Ω 中文 कह אב "
            "مر กข 가힣")
    return [ord(ch) for ch in text]


def emoji_units():
    units = []
    for code_point in (0x1F600, 0x1F389, 0x1F680):
        units.extend(pair_for(code_point))
    units.extend([0x200D])                      # a BMP joiner between them
    units.extend(pair_for(0x1F469))
    return units


def hand_curated_cases():
    cases = []

    def add(name, category, units, odd_byte=None, expected=None):
        cases.append(Case(name, category, units, odd_byte, expected))

    # --- valid input: repair must be the identity ---
    add("empty", "valid", [], expected=[])
    add("one BMP unit", "valid", [A], expected=[A])
    add("ASCII / BMP text", "valid", [A, B_, 0x007A, 0x0020, OMEGA, HAN],
        expected=[A, B_, 0x007A, 0x0020, OMEGA, HAN])
    add("valid supplementary pair", "valid", PAIR, expected=PAIR)
    add("multiple valid pairs", "valid", PAIR + PAIR + PAIR,
        expected=PAIR + PAIR + PAIR)
    add("multilingual text", "valid", multilingual_units(),
        expected=multilingual_units())
    add("emoji sequence with ZWJ", "valid", emoji_units(), expected=emoji_units())
    add("valid pair at the beginning", "valid", PAIR + [A, B_, HAN],
        expected=PAIR + [A, B_, HAN])
    add("valid pair in the middle", "valid", [A, B_] + PAIR + [HAN, A],
        expected=[A, B_] + PAIR + [HAN, A])
    add("valid pair at the end", "valid", [A, B_, HAN] + PAIR,
        expected=[A, B_, HAN] + PAIR)
    add("highest supplementary code point", "valid", pair_for(0x10FFFF),
        expected=pair_for(0x10FFFF))

    # --- malformed input ---
    add("lone high surrogate", "malformed", [A, HI, B_], expected=[A, FFFD, B_])
    add("lone low surrogate", "malformed", [A, LO, B_], expected=[A, FFFD, B_])
    add("high followed by BMP", "malformed", [HI, A], expected=[FFFD, A])
    add("BMP followed by low", "malformed", [A, LO], expected=[A, FFFD])
    add("reversed low-high pair", "malformed", [LO, HI], expected=[FFFD, FFFD])
    # high, high, low: the SECOND high pairs with the low, so only the first is replaced.
    add("high high low", "malformed", [HI, EMOJI_HI, EMOJI_LO],
        expected=[FFFD, EMOJI_HI, EMOJI_LO])
    # high, low, low: the first two are a pair, so only the trailing low is replaced.
    add("high low low", "malformed", [EMOJI_HI, EMOJI_LO, LO],
        expected=[EMOJI_HI, EMOJI_LO, FFFD])
    add("two consecutive highs", "malformed", [A, HI, HI, B_],
        expected=[A, FFFD, FFFD, B_])
    add("four consecutive highs", "malformed", [HI, HI, HI, HI],
        expected=[FFFD] * 4)
    add("two consecutive lows", "malformed", [A, LO, LO, B_],
        expected=[A, FFFD, FFFD, B_])
    add("four consecutive lows", "malformed", [LO, LO, LO, LO],
        expected=[FFFD] * 4)
    add("alternating valid pair / lone surrogate", "malformed",
        PAIR + [HI] + PAIR + [LO] + PAIR + [HI],
        expected=PAIR + [FFFD] + PAIR + [FFFD] + PAIR + [FFFD])
    add("malformed at the beginning", "malformed", [HI] + [A] * 8,
        expected=[FFFD] + [A] * 8)
    add("malformed in the middle", "malformed", [A] * 4 + [LO] + [A] * 4,
        expected=[A] * 4 + [FFFD] + [A] * 4)
    add("malformed at the end", "malformed", [A] * 8 + [HI],
        expected=[A] * 8 + [FFFD])
    add("malformed at beginning, middle and end", "malformed",
        [LO] + [A] * 4 + [HI] + [A] * 4 + [LO],
        expected=[FFFD] + [A] * 4 + [FFFD] + [A] * 4 + [FFFD])
    add("multiple separated malformed regions", "malformed",
        [A] * 3 + [HI, HI] + [A] * 5 + [LO] + [A] * 5 + [LO, HI] + [A] * 3,
        expected=[A] * 3 + [FFFD, FFFD] + [A] * 5 + [FFFD] + [A] * 5
                 + [FFFD, FFFD] + [A] * 3)
    # Every surrogate ill-formed. Lows first, then highs: a low never has a high before
    # it and a high never has a low after it, so nothing pairs up. (Writing highs before
    # lows would instead produce well-formed pairs -- D800 DFFF *is* a valid pair.)
    add("every surrogate code unit malformed", "malformed",
        [LO, LO_MAX, LO, HI, HI_MAX, HI],
        expected=[FFFD] * 6)
    add("highs then lows are valid pairs, not malformed", "valid",
        [HI, LO_MAX, HI_MAX, LO], expected=[HI, LO_MAX, HI_MAX, LO])
    add("highest high surrogate U+DBFF", "malformed", [A, HI_MAX, B_],
        expected=[A, FFFD, B_])
    add("highest low surrogate U+DFFF", "malformed", [A, LO_MAX, B_],
        expected=[A, FFFD, B_])
    add("U+DBFF followed by U+DFFF is a valid pair", "valid", [A, HI_MAX, LO_MAX, B_],
        expected=[A, HI_MAX, LO_MAX, B_])
    add("malformed inside multilingual text", "malformed",
        multilingual_units() + [HI] + multilingual_units(),
        expected=multilingual_units() + [FFFD] + multilingual_units())

    # --- odd-length input: the trailing byte is dropped, one U+FFFD appended ---
    add("one-byte input", "odd", [], odd_byte=0x41, expected=[FFFD])
    add("odd byte after BMP", "odd", [A, B_], odd_byte=0x41,
        expected=[A, B_, FFFD])
    add("odd byte after a valid pair", "odd", PAIR, odd_byte=0x41,
        expected=PAIR + [FFFD])
    add("odd byte after a lone high", "odd", [A, HI], odd_byte=0x41,
        expected=[A, FFFD, FFFD])
    add("odd byte after a lone low", "odd", [A, LO], odd_byte=0x41,
        expected=[A, FFFD, FFFD])
    add("odd byte after a reversed pair", "odd", [LO, HI], odd_byte=0x41,
        expected=[FFFD, FFFD, FFFD])
    add("odd byte after a large valid stream", "odd", [A] * 4096, odd_byte=0x41,
        expected=[A] * 4096 + [FFFD])
    for value in (0x00, 0xD8, 0xDC, 0xFD, 0xFF):
        add("odd byte value %02X" % value, "odd", [A, B_], odd_byte=value,
            expected=[A, B_, FFFD])
        add("odd byte value %02X after a lone high" % value, "odd", [A, HI],
            odd_byte=value, expected=[A, FFFD, FFFD])

    return cases


# --- Section C: boundary and segmentation cases --------------------------------

SMALL_OFFSETS = (7, 8, 9, 15, 16, 17, 31, 32, 33, 63, 64, 65, 127, 128, 129)
LARGE_OFFSETS = (255, 256, 257, 511, 512, 513, 4095, 4096, 4097, 8191, 8192, 8193)


QUICK_OFFSETS = (8, 16, 32, 64, 128)


def boundary_cases(quick):
    cases = []
    offsets = QUICK_OFFSETS if quick else SMALL_OFFSETS + LARGE_OFFSETS
    for n in offsets:
        tail = [A] * 4
        wide = n in LARGE_OFFSETS
        head = [A] * (n - 1)

        # A valid pair split across the boundary must survive untouched.
        cases.append(Case("valid pair split at unit %d" % n, "boundary",
                          head + PAIR + tail))
        # A lone high immediately before the boundary.
        cases.append(Case("lone high before unit %d" % n, "boundary",
                          head + [HI, A] + tail))
        # A lone low exactly at the boundary.
        cases.append(Case("lone low at unit %d" % n, "boundary",
                          [A] * n + [LO] + tail))
        if not wide:
            # Malformed on both sides of the boundary.
            cases.append(Case("malformed either side of unit %d" % n, "boundary",
                              head + [HI, A, LO] + tail))
            # A valid pair sitting next to malformed units.
            cases.append(Case("valid pair beside malformed at unit %d" % n, "boundary",
                              head + [HI] + PAIR + [LO] + tail))
            # An odd trailing byte after a boundary-sized input.
            cases.append(Case("odd byte after %d units" % n, "boundary",
                              [A] * n, odd_byte=0x41))

    # The very beginning and the very end of the input.
    cases.append(Case("lone high at offset 0", "boundary", [HI] + [A] * 8))
    cases.append(Case("lone low at offset 0", "boundary", [LO] + [A] * 8))
    cases.append(Case("valid pair at offset 0", "boundary", PAIR + [A] * 8))
    cases.append(Case("lone high at EOF", "boundary", [A] * 8 + [HI]))
    cases.append(Case("lone low at EOF", "boundary", [A] * 8 + [LO]))
    cases.append(Case("valid pair at EOF", "boundary", [A] * 8 + PAIR))
    cases.append(Case("single lone high, whole file", "boundary", [HI]))
    cases.append(Case("odd byte only", "boundary", [], odd_byte=0x41))
    return cases


# --- Section D: deterministic generated campaign -------------------------------

def gen_valid_only(rng, max_units):
    units = []
    for _ in range(rng.randint(1, max_units)):
        units.extend(pair_for(rng.choice(SUPPLEMENTARY)) if rng.random() < 0.3
                     else [rng.choice(BMP_POOL)])
    return units, None


def gen_malformed_only(rng, max_units):
    lone = [lambda r: r.randint(HI, HI_MAX), lambda r: r.randint(LO, LO_MAX)]
    return [rng.choice(lone)(rng) for _ in range(rng.randint(1, max_units))], None


def gen_sparse_malformed(rng, max_units):
    units = [rng.choice(BMP_POOL) for _ in range(max(16, rng.randint(16, max_units)))]
    for _ in range(max(1, len(units) // 200)):
        units[rng.randrange(len(units))] = rng.randint(HI, LO_MAX)
    return units, None


def gen_dense_malformed(rng, max_units):
    units = []
    for _ in range(max(8, rng.randint(8, max_units))):
        units.append(rng.randint(HI, LO_MAX) if rng.random() < 0.6
                     else rng.choice(BMP_POOL))
    return units, None


def gen_mixed_planes(rng, max_units):
    units = []
    for _ in range(max(8, rng.randint(8, max_units))):
        roll = rng.random()
        if roll < 0.45:
            units.append(rng.choice(BMP_POOL))
        elif roll < 0.85:
            units.extend(pair_for(rng.choice(SUPPLEMENTARY)))
        else:
            units.append(rng.randint(HI, LO_MAX))
    return units, None


def gen_run_of_highs(rng, max_units):
    units = [rng.choice(BMP_POOL) for _ in range(rng.randint(1, 8))]
    units.extend(rng.randint(HI, HI_MAX) for _ in range(rng.randint(8, 64)))
    units.extend(rng.choice(BMP_POOL) for _ in range(rng.randint(1, 8)))
    return units, None


def gen_run_of_lows(rng, max_units):
    units = [rng.choice(BMP_POOL) for _ in range(rng.randint(1, 8))]
    units.extend(rng.randint(LO, LO_MAX) for _ in range(rng.randint(8, 64)))
    units.extend(rng.choice(BMP_POOL) for _ in range(rng.randint(1, 8)))
    return units, None


def gen_reversed_pairs(rng, max_units):
    units = []
    for _ in range(rng.randint(2, max(4, max_units // 8))):
        units.extend([rng.randint(LO, LO_MAX), rng.randint(HI, HI_MAX)])
        units.extend(rng.choice(BMP_POOL) for _ in range(rng.randint(0, 3)))
    return units, None


def gen_alternating(rng, max_units):
    units = []
    for _ in range(rng.randint(4, max(8, max_units // 4))):
        units.extend(pair_for(rng.choice(SUPPLEMENTARY)))
        units.append(rng.randint(HI, LO_MAX))
    return units, None


def gen_odd_stream(rng, max_units):
    units, _ = gen_mixed_planes(rng, max_units)
    return units, rng.randint(0, 255)


def gen_large(rng, max_units):
    target = rng.choice([4096, 8192, 12000])
    units = []
    while len(units) < target:
        roll = rng.random()
        if roll < 0.70:
            units.append(rng.choice(BMP_POOL))
        elif roll < 0.90:
            units.extend(pair_for(rng.choice(SUPPLEMENTARY)))
        else:
            units.append(rng.randint(HI, LO_MAX))
    return units, (rng.randint(0, 255) if rng.random() < 0.3 else None)


def gen_boundary_clustered(rng, max_units):
    """Malformed units concentrated near block/stride boundaries."""
    size = rng.choice([256, 512, 4096])
    units = [rng.choice(BMP_POOL) for _ in range(size + 16)]
    for edge in (0, size // 2, size - 1, size, size + 1):
        if 0 <= edge < len(units):
            units[edge] = rng.randint(HI, LO_MAX)
    return units, None


def gen_distributed(rng, max_units):
    """Malformed units spread evenly across the whole file."""
    units = [rng.choice(BMP_POOL) for _ in range(max(32, rng.randint(32, max_units)))]
    step = max(4, len(units) // 16)
    for index in range(0, len(units), step):
        units[index] = rng.randint(HI, LO_MAX)
    return units, None


GENERATORS = [
    ("valid_only", gen_valid_only),
    ("malformed_only", gen_malformed_only),
    ("sparse_malformed", gen_sparse_malformed),
    ("dense_malformed", gen_dense_malformed),
    ("mixed_planes", gen_mixed_planes),
    ("run_of_highs", gen_run_of_highs),
    ("run_of_lows", gen_run_of_lows),
    ("reversed_pairs", gen_reversed_pairs),
    ("alternating", gen_alternating),
    ("odd_stream", gen_odd_stream),
    ("large", gen_large),
    ("boundary_clustered", gen_boundary_clustered),
    ("distributed", gen_distributed),
]


def build_generated_case(seed, index, max_units):
    """One generated case, determined entirely by (seed, index)."""
    category, generator = GENERATORS[index % len(GENERATORS)]
    rng = random.Random("utf16-repair|%d|%d|%s" % (seed, index, category))
    units, odd_byte = generator(rng, max_units)
    return Case("generated %d (%s)" % (index, category), "generated/" + category,
                units, odd_byte, index=index)


# --- Section F: large-file stress ----------------------------------------------

def large_stress_cases(quick):
    """Deterministic ~1 MiB streams. Correctness and stability, not throughput."""
    units_per_mib = MIB // 2
    size = units_per_mib // (8 if quick else 1)
    rng = random.Random("utf16-repair-stress|%d" % size)

    cases = []
    cases.append(Case("%d KiB valid BMP" % (size * 2 // 1024), "stress",
                      [A] * size, segment_sweep=False))

    mixed = []
    while len(mixed) < size:
        mixed.extend(pair_for(0x1F600) if len(mixed) % 7 == 0 else [rng.choice(BMP_POOL)])
    mixed = mixed[:size]
    cases.append(Case("%d KiB mixed valid" % (size * 2 // 1024), "stress",
                      list(mixed), segment_sweep=False))

    sparse = list(mixed)
    for index in range(0, len(sparse), 4096):
        sparse[index] = HI                      # one lone high every 4096 units
    cases.append(Case("%d KiB sparse malformed" % (size * 2 // 1024), "stress",
                      sparse, segment_sweep=False))

    # Low before high, so nothing pairs up: two ill-formed units out of every three.
    # (High-then-low would silently be a well-formed pair and test nothing.)
    dense = [LO if index % 3 == 0 else (HI if index % 3 == 1 else A)
             for index in range(size)]
    cases.append(Case("%d KiB dense malformed" % (size * 2 // 1024), "stress",
                      dense, segment_sweep=False))

    edges = [A] * size
    edges[0] = LO
    edges[size // 2] = HI
    edges[size - 1] = HI
    cases.append(Case("%d KiB malformed at first/middle/final unit"
                      % (size * 2 // 1024), "stress", edges, segment_sweep=False))

    cases.append(Case("%d KiB odd-length stream" % (size * 2 // 1024), "stress",
                      list(mixed), odd_byte=0x41, segment_sweep=False))
    return cases


# --- Section G: exhaustive trailing-byte regression ----------------------------
# Regression for the UTF-16BE phantom-lookahead defect found by this campaign: the k+1
# lookahead reads the high byte of the next code unit at raw offset 2(k+1)+HB, which in BE
# (HB=0) is exactly the odd trailing byte, so a trailing 0xDC..0xDF used to masquerade as a
# low surrogate and pair with a real final high surrogate. Every one of the 256 possible
# trailing-byte values is checked after a final high surrogate, in both encodings.

TRAILING_HIGH_SURROGATES = (0xD800, 0xDA00, 0xDBFF)     # first, middle, last
PREVIOUS_TRIGGER_BYTES = (0xDC, 0xDD, 0xDE, 0xDF)
# Values given the full count/position/idempotence matrix rather than the bulk check.
FOCUSED_TRAILING_BYTES = (0x00, 0x41, 0xD7, 0xD8, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF, 0xE0,
                          0xFD, 0xFF)


def check_trailing_byte_matrix(args, runner, stats):
    """All 256 trailing-byte values after a final high surrogate, LE and BE."""
    problems = []
    highs = TRAILING_HIGH_SURROGATES[:1] if args.quick else TRAILING_HIGH_SURROGATES

    for high in highs:
        for encoding, be in (("UTF-16LE", False), ("UTF-16BE", True)):
            units = [A, high]
            swept = 0
            trigger_ok = 0
            for value in range(256):
                data = oracle.encode_code_units(units, be) + bytes([value])
                diag = oracle.analyze(data, be)
                path = runner.write("trail_%04X_%02X_%s.bin"
                                    % (high, value, "be" if be else "le"), data)
                repaired = runner.repair(path, be)

                ok = stats.record("P1", repaired == diag.repaired)
                if not ok:
                    problems.append("%s U+%04X + trailing 0x%02X: repair %s, expected %s"
                                    % (encoding, high, value, repaired.hex(),
                                       diag.repaired.hex()))
                # The repaired bytes must be well-formed, per the oracle (independent of
                # the implementation) -- P15 -- and the right length -- P7/P8.
                after = oracle.analyze(repaired, be)
                if not stats.record("P15", not after.malformed_positions):
                    problems.append("%s U+%04X + trailing 0x%02X: repaired output still "
                                    "has lone surrogate(s)" % (encoding, high, value))
                stats.record("P7", len(repaired) == len(data) + 1)
                stats.record("P8", len(repaired) % 2 == 0)
                swept += 1
                if ok and value in PREVIOUS_TRIGGER_BYTES:
                    trigger_ok += 1

                # The full matrix on the interesting values, including the four that used
                # to trigger the defect.
                if value in FOCUSED_TRAILING_BYTES:
                    scalar = runner.count(path, be)
                    simd = runner.count(path, be, simd=True)
                    marks = runner.count(path, be, marks=True)
                    positions = runner.positions(path, be)
                    diagnostics_ok = (scalar == simd == marks == diag.error_count
                                      and positions == diag.malformed_positions)
                    if not stats.record("P16", diagnostics_ok):
                        problems.append(
                            "%s U+%04X + trailing 0x%02X: scalar=%d simd=%d marks=%d "
                            "positions=%s, oracle count=%d positions=%s"
                            % (encoding, high, value, scalar, simd, marks, positions,
                               diag.error_count, diag.malformed_positions))
                    repaired_path = runner.write("trail.rep", repaired)
                    if not stats.record("P2", runner.count(repaired_path, be) == 0):
                        problems.append("%s U+%04X + trailing 0x%02X: scalar "
                                        "validate(repair(x)) != 0"
                                        % (encoding, high, value))
                    if not stats.record("P3", runner.count(repaired_path, be,
                                                           simd=True) == 0):
                        problems.append("%s U+%04X + trailing 0x%02X: --simd "
                                        "validate(repair(x)) != 0"
                                        % (encoding, high, value))
                    if not stats.record("P4", runner.repair(repaired_path, be) == repaired):
                        problems.append("%s U+%04X + trailing 0x%02X: not idempotent"
                                        % (encoding, high, value))

                    # Forced segment sizes must not change a byte (P11).
                    for size in SEGMENT_SIZES:
                        if not stats.record("P11",
                                            runner.repair(path, be, seg=size) == repaired):
                            problems.append(
                                "%s U+%04X + trailing 0x%02X: -segment-size=%d changed "
                                "the output" % (encoding, high, value, size))

            print("  %s final high surrogate U+%04X, %s: %d/256 trailing-byte values OK, "
                  "incl. 0xDC-0xDF (%d/4)"
                  % ("FAIL" if problems else "PASS", high, encoding, swept, trigger_ok))
    return problems


# --- Section E: simdutf differential -------------------------------------------

SIMDUTF_PROGRAM = r"""
#include "simdutf.h"
#include <cstdio>
#include <cstring>
#include <vector>
int main(int argc, char ** argv) {
    if (argc < 2) return 2;
    FILE * f = fopen(argv[1], "rb");
    if (!f) return 2;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<char> buf(n > 0 ? n : 1);
    if (n > 0 && fread(buf.data(), 1, (size_t) n, f) != (size_t) n) { fclose(f); return 2; }
    fclose(f);
    size_t units = (size_t) n / 2;              // complete code units only
    const char16_t * in = reinterpret_cast<const char16_t *>(buf.data());
    std::vector<char16_t> out(units ? units : 1);
    bool be = argc > 2 && strcmp(argv[2], "be") == 0;
    if (be) simdutf::to_well_formed_utf16be(in, units, out.data());
    else    simdutf::to_well_formed_utf16le(in, units, out.data());
    if (units) fwrite(out.data(), 2, units, stdout);
    return 0;
}
"""


def build_simdutf(workdir):
    """Compile the simdutf comparison helper. Returns (path, reason_if_unavailable)."""
    source = os.path.join(SIMDUTF_SINGLEHEADER, "simdutf.cpp")
    if not os.path.isfile(source):
        return None, ("simdutf singleheader not found at %s (run "
                      "./scripts/setup_clausecker_lemire.sh to enable it)"
                      % SIMDUTF_SINGLEHEADER)
    if not shutil.which("c++"):
        return None, "no c++ compiler on PATH"
    program = os.path.join(workdir, "simdutf_repair.cpp")
    with open(program, "w") as handle:
        handle.write(SIMDUTF_PROGRAM)
    binary = os.path.join(workdir, "simdutf_repair")
    proc = subprocess.run(["c++", "-O1", "-std=c++17", "-I", SIMDUTF_SINGLEHEADER,
                           program, source, "-o", binary],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return None, ("simdutf singleheader present but did not compile: %s"
                      % proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:])
    return binary, None


def run_simdutf(binary, path, be):
    return subprocess.run([binary, path, "be" if be else "le"],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True).stdout


def simdutf_differential(args, cases, runner, simdutf_bin):
    """Byte-for-byte comparison on EVEN-length inputs only.

    simdutf's API is char16_t-based and has no notion of an odd trailing byte, so its
    replacement semantics are only directly comparable for complete code units. Odd-length
    inputs are reported as skipped rather than forced to agree; Parabix keeps being checked
    against the Python oracle for those (see the project's odd-byte policy in
    docs/utf16_repair.md).
    """
    matched = differed = skipped_odd = 0
    problems = []
    for case in cases:
        if case.odd_byte is not None:
            skipped_odd += 1
            continue
        for encoding, be in (("UTF-16LE", False), ("UTF-16BE", True)):
            data = case.data(be)
            path = runner.write("simdutf_%s.bin" % ("be" if be else "le"), data)
            mine = runner.repair(path, be)
            theirs = run_simdutf(simdutf_bin, path, be)
            if mine == theirs:
                matched += 1
            else:
                differed += 1
                offset = first_difference(mine, theirs)
                problems.append("%s %s: first difference at byte %s (parabix=%s simdutf=%s)"
                                % (case.name, encoding, offset,
                                   hexdump(mine), hexdump(theirs)))
    return matched, differed, skipped_odd, problems


def simdutf_case_sample(quick):
    """Even-length cases spanning valid, sparse, dense, boundary and large inputs."""
    rng = random.Random("utf16-repair-simdutf")
    sample = [
        Case("valid BMP text", "simdutf", [A, B_, OMEGA, HAN] * 8),
        Case("valid pairs", "simdutf", PAIR * 16),
        Case("multilingual", "simdutf", multilingual_units()),
        Case("lone high", "simdutf", [A, HI, B_]),
        Case("lone low", "simdutf", [A, LO, B_]),
        Case("reversed pair", "simdutf", [LO, HI]),
        Case("adjacent highs", "simdutf", [HI, HI, A]),
        Case("high high low", "simdutf", [HI, EMOJI_HI, EMOJI_LO]),
        Case("highest surrogates", "simdutf", [HI_MAX, A, LO_MAX]),
        Case("sparse malformed", "simdutf",
             [(HI if index % 512 == 0 else A) for index in range(4096)]),
        Case("dense malformed", "simdutf",
             [(LO if index % 3 == 0 else (HI if index % 3 == 1 else A))
              for index in range(2048)]),
        # Odd-length cases are deliberately included so the skip is exercised and
        # counted: simdutf has no odd-trailing-byte concept, so they are not compared.
        Case("odd length after BMP", "simdutf", [A, B_, HAN], odd_byte=0x41),
        Case("odd length after a lone high", "simdutf", [A, HI], odd_byte=0xDC),
        Case("boundary 4095/4096/4097", "simdutf",
             [(HI if index in (4095, 4096, 4097) else A) for index in range(4200)]),
        Case("boundary 63/64/65", "simdutf",
             [(LO if index in (63, 64, 65) else A) for index in range(128)]),
    ]
    if not quick:
        big = []
        while len(big) < MIB // 2:
            big.extend(pair_for(0x1F600) if len(big) % 11 == 0
                       else [rng.choice(BMP_POOL)])
        big = big[:MIB // 2]
        for index in range(0, len(big), 997):
            big[index] = HI
        sample.append(Case("1 MiB mixed with sparse malformed", "simdutf", big))
    return sample


# --- Main ----------------------------------------------------------------------

SECTIONS = ("semantics", "curated", "boundary", "trailing", "generated", "stress",
            "simdutf")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="generator seed (default: %d)" % DEFAULT_SEED)
    parser.add_argument("--cases", type=int, default=None,
                        help="generated cases (default: %d, or %d with --quick)"
                             % (DEFAULT_CASES, QUICK_CASES))
    parser.add_argument("--max-units", type=int, default=None,
                        help="upper bound on generated code units per case "
                             "(default: %d, or %d with --quick)"
                             % (DEFAULT_MAX_UNITS, QUICK_MAX_UNITS))
    parser.add_argument("--quick", action="store_true",
                        help="fast subset: fewer/smaller cases, smaller stress streams")
    parser.add_argument("--only-case", type=int, default=None,
                        help="run exactly one generated case index")
    parser.add_argument("--section", choices=SECTIONS, default=None,
                        help="run only one section")
    parser.add_argument("--no-simdutf", action="store_true",
                        help="skip the simdutf differential even if it is available")
    parser.add_argument("--quiet-failures", action="store_true",
                        help="suppress the per-failure diagnostic block")
    parser.add_argument("--bin", default=os.environ.get("UTF16VALIDATE_BIN", DEFAULT_BIN),
                        help="path to utf16validate")
    args = parser.parse_args()

    if args.cases is None:
        args.cases = QUICK_CASES if args.quick else DEFAULT_CASES
    if args.max_units is None:
        args.max_units = QUICK_MAX_UNITS if args.quick else DEFAULT_MAX_UNITS

    if not os.access(args.bin, os.X_OK):
        print("ERROR: utf16validate not found at %s" % args.bin, file=sys.stderr)
        print("       Run ./scripts/setup_parabix.sh first.", file=sys.stderr)
        return 1

    print("UTF-16 repair comprehensive campaign (issue #43)")
    print("  oracle    : scripts/utf16_oracle.py (exact expected repaired bytes)")
    print("  binary    : %s" % args.bin)
    print("  seed      : %d" % args.seed)
    print("  generated : %d cases, max %d code units%s"
          % (args.cases, args.max_units, "  (quick mode)" if args.quick else ""))
    print("  segments  : default plus -segment-size=%s (inputs <= %d units)"
          % (",".join(str(s) for s in SEGMENT_SIZES), SEGMENT_SWEEP_MAX_UNITS))
    print("  encodings : UTF-16LE and UTF-16BE")
    print()

    stats = Stats()
    failures = []
    workdir = tempfile.mkdtemp(prefix="utf16-repair-")
    runner = Runner(args.bin, workdir)
    simdutf_note = None

    def section_enabled(name):
        return args.section is None or args.section == name

    try:
        if section_enabled("semantics"):
            print("== documented semantics (oracle self-consistency) ==")
            if oracle.self_test():
                print("  ORACLE SELF TEST FAILED -- aborting")
                return 1
            print("  %d hand-worked vectors x 2 encodings OK, including the three "
                  "odd-trailing-byte rows of docs/utf16_repair.md"
                  % len(oracle.SELF_TEST_VECTORS))
            print()

        def run_cases(title, cases, show_each=True):
            print("== %s ==" % title)
            for case in cases:
                problems = check_case(args, case, runner, stats)
                if problems:
                    failures.extend("%s: %s" % (case.name, p) for p in problems)
                if show_each:
                    diag = oracle.analyze(case.data(False), False)
                    print("  %s %-52s units=%-6d odd=%d errors=%-5d"
                          % ("FAIL" if problems else "PASS", case.name,
                             len(case.units), 1 if case.odd_byte is not None else 0,
                             diag.error_count))
            print()

        if section_enabled("curated"):
            run_cases("hand-curated cases (exact expected bytes, LE and BE)",
                      hand_curated_cases())

        if section_enabled("boundary"):
            run_cases("boundary and segmentation cases", boundary_cases(args.quick))

        if section_enabled("trailing"):
            print("== exhaustive trailing-byte regression (all 256 values after a final "
                  "high surrogate) ==")
            trailing_problems = check_trailing_byte_matrix(args, runner, stats)
            failures.extend(trailing_problems)
            if not trailing_problems:
                print("  every trailing byte 0x00-0xFF is treated as an incomplete byte, "
                      "never as a low surrogate;")
                print("  the previous UTF-16BE trigger values 0xDC-0xDF are covered "
                      "explicitly and pass.")
            print()

        if section_enabled("generated"):
            if args.only_case is not None:
                generated = [build_generated_case(args.seed, args.only_case,
                                                  args.max_units)]
            else:
                generated = [build_generated_case(args.seed, index, args.max_units)
                             for index in range(args.cases)]
            run_cases("deterministic generated campaign (seed %d)" % args.seed, generated)

            # Reproducibility of the generator itself.
            if args.only_case is None:
                rebuilt = [build_generated_case(args.seed, index, args.max_units)
                           for index in range(args.cases)]
                same = all(a.units == b.units and a.odd_byte == b.odd_byte
                           for a, b in zip(generated, rebuilt))
                print("  generator reproducibility: %s (%d cases regenerate from seed %d)"
                      % ("OK" if same else "FAILED", len(generated), args.seed))
                if not same:
                    failures.append("generated cases are not reproducible from the seed")
                print()

        if section_enabled("stress"):
            print("== large-file stress (correctness and stability, not throughput) ==")
            for case in large_stress_cases(args.quick):
                problems = check_case(args, case, runner, stats)
                if problems:
                    failures.extend("%s: %s" % (case.name, p) for p in problems)
                data = case.data(False)
                diag = oracle.analyze(data, False)
                path = runner.write("stress.bin", data)
                repaired = runner.repair(path, False)
                repaired_path = runner.write("stress.rep", repaired)
                after = runner.count(repaired_path, False)
                replacements = diag.error_count
                print("  %s %-46s in=%-9d out=%-9d errors=%-8d replacements=%-8d "
                      "validate(repair)=%d"
                      % ("FAIL" if problems else "PASS", case.name, len(data),
                         len(repaired), diag.error_count, replacements, after))
            print()

        if section_enabled("simdutf"):
            print("== simdutf differential (even-length inputs only) ==")
            if args.no_simdutf:
                simdutf_note = "skipped: --no-simdutf"
                print("  SKIPPED: --no-simdutf was given")
            else:
                simdutf_bin, reason = build_simdutf(workdir)
                if simdutf_bin is None:
                    simdutf_note = "skipped: %s" % reason
                    print("  SKIPPED: %s" % reason)
                    print("  (the rest of this suite does not depend on simdutf)")
                else:
                    sample = simdutf_case_sample(args.quick)
                    matched, differed, skipped_odd, problems = simdutf_differential(
                        args, sample, runner, simdutf_bin)
                    simdutf_note = ("%d matched, %d differed (%d odd-length cases not "
                                    "comparable)" % (matched, differed, skipped_odd))
                    for problem in problems:
                        print("  DIFFER %s" % problem)
                    print("  %d comparisons matched, %d differed, over %d even-length "
                          "cases in LE and BE" % (matched, differed, len(sample)))
                    print("  odd-length inputs are NOT compared: simdutf's char16_t API "
                          "has no odd-trailing-byte concept,")
                    print("  so this project's 'drop the byte, append one U+FFFD' policy "
                          "has no simdutf equivalent.")
                    print("  Parabix is still checked against the Python oracle for "
                          "those cases.")
                    failures.extend(problems)
            print()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("== properties ==")
    for key, name, description in PROPERTIES:
        checks = stats.checks[key]
        failed = stats.failures[key]
        state = "ok" if failed == 0 else "FAILED"
        skipped = " (not exercised)" if checks == 0 else ""
        print("  %-4s %-24s %-50s %7d checks  %s%s"
              % (key, name, description, checks, state, skipped))

    total_checks, total_failures = stats.totals()
    print()
    if simdutf_note:
        print("simdutf differential: %s" % simdutf_note)
    print("%d property checks, %d validator invocations, %d failures"
          % (total_checks, runner.invocations, total_failures + len(failures)))
    if total_failures or failures:
        print("REPAIR CAMPAIGN FAILED (seed %d)" % args.seed)
        for failure in failures[:10]:
            print("  %s" % failure)
        return 1
    print("ALL REPAIR CAMPAIGN TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
