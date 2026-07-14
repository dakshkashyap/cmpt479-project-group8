#!/usr/bin/env python3
"""Independent reference implementation of UTF-16LE LLmask generation (issue #29).

An LLmask is a 64-bit word covering 64 consecutive UTF-16 code units. Bit i is set
iff code unit i of that group is itself ill-formed:

    bad[k] = (isLow[k]  and not isHigh[k-1])     # low surrogate with no high before it
          or (isHigh[k] and not isLow[k+1])      # high surrogate with no low after it

Code unit -1 does not exist (so isHigh[-1] is False) and code unit `units` does not
exist (so isLow[units] is False, which makes a high surrogate at EOF ill-formed).

An odd trailing byte is not a code unit, so it cannot be represented in a code-unit
indexed mask. It is reported separately, exactly as the validator handles it at EOF.

This file is deliberately written the slow, obvious way, unit by unit, with no shared
code with benchmarks/prototype_llmask_generation.cpp. It exists so the C++ prototype can
be differentially checked against something written from the definition rather than from
the same algorithm.

    python3 benchmarks/llmask_reference.py --dump FILE     # canonical dump, diffable
                                                           # against the C++ --dump
    python3 benchmarks/llmask_reference.py --check FILE    # summary only
"""

import argparse
import sys

UNITS_PER_MASK = 64

HIGH_LO, HIGH_HI = 0xD800, 0xDBFF
LOW_LO, LOW_HI = 0xDC00, 0xDFFF


def llmasks(data):
    """Return (masks, units, error_bits, odd_trailing_byte) for raw UTF-16LE bytes."""
    n = len(data)
    odd = n & 1
    units = n // 2

    # Code unit values, little-endian, built from the raw bytes (never via a codec:
    # Python's codecs refuse to decode lone surrogates).
    values = [data[2 * k] | (data[2 * k + 1] << 8) for k in range(units)]

    def is_high(k):
        return 0 <= k < units and HIGH_LO <= values[k] <= HIGH_HI

    def is_low(k):
        return 0 <= k < units and LOW_LO <= values[k] <= LOW_HI

    masks = [0] * ((units + UNITS_PER_MASK - 1) // UNITS_PER_MASK)
    error_bits = 0
    for k in range(units):
        lone_low = is_low(k) and not is_high(k - 1)
        lone_high = is_high(k) and not is_low(k + 1)
        if lone_low or lone_high:
            masks[k // UNITS_PER_MASK] |= 1 << (k % UNITS_PER_MASK)
            error_bits += 1
    return masks, units, error_bits, odd


def dump(masks, units, error_bits, odd, out):
    out.write("units=%d\n" % units)
    out.write("masks=%d\n" % len(masks))
    out.write("errorbits=%d\n" % error_bits)
    out.write("oddtrailingbyte=%d\n" % odd)
    for g, m in enumerate(masks):
        if m:
            out.write("%d %016x\n" % (g, m))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dump", metavar="FILE",
                      help="print the canonical LLmask dump (diffable against the C++ --dump)")
    mode.add_argument("--check", metavar="FILE", help="print a one-line summary")
    args = ap.parse_args()

    path = args.dump or args.check
    with open(path, "rb") as f:
        data = f.read()
    masks, units, error_bits, odd = llmasks(data)

    if args.dump:
        dump(masks, units, error_bits, odd, sys.stdout)
    else:
        print("%s: units=%d masks=%d errorbits=%d oddtrailingbyte=%d"
              % (path, units, len(masks), error_bits, odd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
