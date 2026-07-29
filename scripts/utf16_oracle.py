#!/usr/bin/env python3
"""Independent UTF-16 oracle: diagnostics and U+FFFD repair from raw bytes (issue #42).

This module answers, for a blob of raw bytes in either endianness, every question the
validator answers -- how many ill-formed code units there are, exactly where they are,
whether the file ends in half a code unit, and what the repaired bytes should be -- and it
answers them from the *definition* of UTF-16 well-formedness, not from any existing
implementation.

Independence
------------
Nothing here is derived from tools/utf16validate/utf16validate.cpp, from
benchmarks/llmask_reference.py, or from the scalar validator. The definition of UTF-16
says a supplementary code point is encoded as a high surrogate (U+D800..U+DBFF) followed
by a low surrogate (U+DC00..U+DFFF), so this module simply *decodes* left to right: it
consumes a high surrogate together with the low surrogate that follows it, and any
surrogate code unit that cannot be consumed as part of such a pair is ill-formed.

benchmarks/llmask_reference.py reaches the same answer a different way -- it evaluates a
per-code-unit predicate, `bad[k] = (isLow[k] and not isHigh[k-1]) or (isHigh[k] and not
isLow[k+1])`, with no notion of consuming a pair. Two independently written algorithms
that agree on every input is the point; a shared helper would defeat it. The only thing
this module takes from the standard rather than from itself is which byte of a code unit
is the high byte, which is what "UTF-16LE" and "UTF-16BE" mean.

Diagnostic conventions
----------------------
These match the validator and are asserted by the suites:

* **Positions are code-unit indices.** Position k refers to the k-th whole UTF-16 code
  unit, counting from 0. Because an index counts code units and not bytes, the position
  list for a given code-unit sequence is *identical* in UTF-16LE and UTF-16BE.
* **An odd trailing byte contributes exactly 1 to errorCount.**
* **An odd trailing byte has no code-unit position.** It is not a code unit, so it never
  appears in the position list -- not at any segment size, not in either encoding. A
  one-byte file therefore reports errorCount = 1 with an empty position list.
* **errorCount = number of ill-formed code units + (1 if the byte count is odd).**
* **Repair replaces each ill-formed code unit with U+FFFD**, in place, position-accurate:
  a lone high becomes one U+FFFD, a lone low becomes one U+FFFD, and each half of a
  reversed pair is replaced independently. Well-formed code units are copied through
  untouched, so repairing valid input returns it byte for byte.
* **Repair of an odd trailing byte follows the project's documented policy**
  (docs/utf16_repair.md): the incomplete trailing byte is *discarded* and exactly one
  U+FFFD code unit is appended. Repaired output is therefore always an even number of
  bytes: `len(repair(x)) == len(x)` for even-length input and `len(x) + 1` for odd-length
  input.
* **Repair preserves endianness**: U+FFFD is written FD FF in UTF-16LE and FF FD in
  UTF-16BE.

Command line
------------
    python3 scripts/utf16_oracle.py --count FILE [--be]
    python3 scripts/utf16_oracle.py --positions FILE [--be]
    python3 scripts/utf16_oracle.py --repair FILE [--be] > repaired.bin
    python3 scripts/utf16_oracle.py --self-test
"""

import argparse
import collections
import sys

HIGH_FIRST, HIGH_LAST = 0xD800, 0xDBFF
LOW_FIRST, LOW_LAST = 0xDC00, 0xDFFF
REPLACEMENT = 0xFFFD

#: Everything known about one blob of bytes, in one object.
#:
#: code_units          the whole code units, decoded in the requested endianness
#: malformed_positions ascending code-unit indices of the ill-formed units
#: malformed_count     len(malformed_positions)
#: odd_trailing_byte   True if the byte count is odd (the final byte is not a code unit)
#: error_count         malformed_count + (1 if odd_trailing_byte else 0)
#: repaired            the repaired bytes, in the same endianness
Diagnosis = collections.namedtuple(
    "Diagnosis",
    "code_units malformed_positions malformed_count odd_trailing_byte "
    "error_count repaired")


def is_high_surrogate(unit):
    return HIGH_FIRST <= unit <= HIGH_LAST


def is_low_surrogate(unit):
    return LOW_FIRST <= unit <= LOW_LAST


def decode_code_units(data, big_endian=False):
    """The whole UTF-16 code units in `data`. A trailing odd byte is not a code unit.

    This is the definition of the encoding, not a validation decision: UTF-16BE puts the
    high byte of each code unit first, UTF-16LE puts it second.
    """
    whole = len(data) - (len(data) & 1)
    if big_endian:
        return [(data[i] << 8) | data[i + 1] for i in range(0, whole, 2)]
    return [data[i] | (data[i + 1] << 8) for i in range(0, whole, 2)]


def encode_code_units(units, big_endian=False):
    """Raw bytes for a code-unit sequence. Lone surrogates are allowed: this is a byte
    packer, not a text encoder (Python's codecs refuse to encode a lone surrogate)."""
    out = bytearray()
    for unit in units:
        if big_endian:
            out.append((unit >> 8) & 0xFF)
            out.append(unit & 0xFF)
        else:
            out.append(unit & 0xFF)
            out.append((unit >> 8) & 0xFF)
    return bytes(out)


def has_odd_trailing_byte(data):
    """True if the blob ends in a byte that cannot be part of a whole code unit."""
    return bool(len(data) & 1)


def malformed_positions(units):
    """Code-unit indices that are ill-formed, ascending.

    Decoding left to right: a high surrogate claims the code unit after it if that unit is
    a low surrogate, and the pair is well-formed. Any surrogate left over -- a high with
    nothing usable after it, or a low that no high claimed -- is ill-formed. Non-surrogate
    code units are always well-formed.
    """
    positions = []
    total = len(units)
    index = 0
    while index < total:
        unit = units[index]
        if is_high_surrogate(unit):
            if index + 1 < total and is_low_surrogate(units[index + 1]):
                index += 2                  # a well-formed surrogate pair
                continue
            positions.append(index)         # high surrogate with no low after it
        elif is_low_surrogate(unit):
            positions.append(index)         # low surrogate no high surrogate claimed
        index += 1
    return positions


def repair_units(units, positions):
    """The code units after repair: U+FFFD at each ill-formed position, rest unchanged."""
    marked = set(positions)
    return [REPLACEMENT if k in marked else unit for k, unit in enumerate(units)]


def analyze(data, big_endian=False):
    """Full diagnosis of a raw blob: counts, positions, and the repaired bytes."""
    units = decode_code_units(data, big_endian)
    positions = malformed_positions(units)
    odd = has_odd_trailing_byte(data)

    repaired_units = repair_units(units, positions)
    if odd:
        # Project policy (docs/utf16_repair.md): drop the incomplete trailing byte and
        # append exactly one U+FFFD, so the repaired output stays whole code units.
        repaired_units.append(REPLACEMENT)

    return Diagnosis(
        code_units=units,
        malformed_positions=positions,
        malformed_count=len(positions),
        odd_trailing_byte=odd,
        error_count=len(positions) + (1 if odd else 0),
        repaired=encode_code_units(repaired_units, big_endian),
    )


def error_count(data, big_endian=False):
    """The validator's errorCount: ill-formed code units, plus 1 for an odd trailing byte."""
    return analyze(data, big_endian).error_count


def repair(data, big_endian=False):
    """The repaired bytes, in the same endianness as the input."""
    return analyze(data, big_endian).repaired


# --- Self test -----------------------------------------------------------------
# Hand-worked vectors, stated from the conventions above and from the tables in
# docs/utf16_repair.md. These anchor the oracle to the documented semantics without
# consulting any implementation, so `--self-test` is meaningful even with no build present.

A, B = 0x0041, 0x0042
PAIR_HI, PAIR_LO = 0xD83D, 0xDE00       # a real emoji
HI, LO = 0xD800, 0xDC00
F = REPLACEMENT

# (name, units, odd trailing byte or None, expected positions, expected repaired units)
SELF_TEST_VECTORS = [
    ("empty",                     [],                 None, [],        []),
    ("bmp only",                  [A, B],             None, [],        [A, B]),
    ("valid pair",                [PAIR_HI, PAIR_LO], None, [],        [PAIR_HI, PAIR_LO]),
    ("lone high",                 [A, HI, B],         None, [1],       [A, F, B]),
    ("lone low",                  [A, LO, B],         None, [1],       [A, F, B]),
    ("high then bmp",             [HI, A],            None, [0],       [F, A]),
    ("bmp then low",              [A, LO],            None, [1],       [A, F]),
    ("reversed pair",             [LO, HI],           None, [0, 1],    [F, F]),
    ("two adjacent highs",        [HI, HI],           None, [0, 1],    [F, F]),
    ("two adjacent lows",         [LO, LO],           None, [0, 1],    [F, F]),
    ("high high low",             [HI, PAIR_HI, PAIR_LO], None, [0],   [F, PAIR_HI, PAIR_LO]),
    ("high low low",              [PAIR_HI, PAIR_LO, LO], None, [2],   [PAIR_HI, PAIR_LO, F]),
    ("dangling high at eof",      [A, HI],            None, [1],       [A, F]),
    # docs/utf16_repair.md odd-trailing-byte table
    ("odd byte after bmp",        [A, B],             0x41, [],        [A, B, F]),
    ("odd byte after lone high",  [HI],               0x41, [0],       [F, F]),
    ("one stray byte",            [],                 0x41, [],        [F]),
    ("odd byte after valid pair", [PAIR_HI, PAIR_LO], 0x41, [],        [PAIR_HI, PAIR_LO, F]),
]


def self_test():
    """Check the oracle against the hand-worked vectors, in both encodings."""
    failures = 0
    for name, units, odd, want_pos, want_repaired_units in SELF_TEST_VECTORS:
        for label, big in (("LE", False), ("BE", True)):
            data = encode_code_units(units, big)
            if odd is not None:
                data += bytes([odd])
            diag = analyze(data, big)
            want_count = len(want_pos) + (1 if odd is not None else 0)
            want_repaired = encode_code_units(want_repaired_units, big)
            problems = []
            if diag.code_units != list(units):
                problems.append("code units %s != %s" % (diag.code_units, list(units)))
            if diag.malformed_positions != want_pos:
                problems.append("positions %s != %s" % (diag.malformed_positions, want_pos))
            if diag.error_count != want_count:
                problems.append("count %d != %d" % (diag.error_count, want_count))
            if diag.odd_trailing_byte != (odd is not None):
                problems.append("odd flag %s" % diag.odd_trailing_byte)
            if diag.repaired != want_repaired:
                problems.append("repaired %s != %s"
                                % (diag.repaired.hex(), want_repaired.hex()))
            # Repair must be idempotent and must clean the input, by construction.
            again = analyze(diag.repaired, big)
            if again.error_count != 0:
                problems.append("repaired output still has %d errors" % again.error_count)
            if again.repaired != diag.repaired:
                problems.append("repair is not idempotent")
            if problems:
                failures += 1
                print("  FAIL %-28s %s  %s" % (name, label, "; ".join(problems)))
            else:
                print("  PASS %-28s %s  errors=%d positions=%s repaired=%s"
                      % (name, label, diag.error_count, diag.malformed_positions,
                         diag.repaired.hex()))
    return failures


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--count", metavar="FILE", help="print 'errorCount = N'")
    mode.add_argument("--positions", metavar="FILE",
                      help="print the count, then one code-unit position per line")
    mode.add_argument("--repair", metavar="FILE",
                      help="write the repaired bytes to stdout")
    mode.add_argument("--self-test", action="store_true",
                      help="check the oracle against hand-worked vectors and exit")
    parser.add_argument("--be", action="store_true",
                        help="treat the input as UTF-16BE (default: UTF-16LE)")
    args = parser.parse_args()

    if args.self_test:
        print("utf16_oracle self test (hand-worked vectors, LE and BE)")
        failures = self_test()
        print()
        print("%d vectors x 2 encodings, %d failed"
              % (len(SELF_TEST_VECTORS), failures))
        return 1 if failures else 0

    path = args.count or args.positions or args.repair
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as ex:
        raise SystemExit("ERROR: %s" % ex)

    diag = analyze(data, args.be)
    if args.count:
        print("%s: errorCount = %d" % (path, diag.error_count))
    elif args.positions:
        print("units=%d" % len(diag.code_units))
        print("errorCount=%d" % diag.error_count)
        print("oddtrailingbyte=%d" % (1 if diag.odd_trailing_byte else 0))
        for position in diag.malformed_positions:
            print(position)
    else:
        out = getattr(sys.stdout, "buffer", sys.stdout)
        out.write(diag.repaired)
    return 0


if __name__ == "__main__":
    sys.exit(main())
