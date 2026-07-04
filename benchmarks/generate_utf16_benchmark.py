#!/usr/bin/env python3
"""Generate deterministic, valid UTF-16LE benchmark input files.

The files contain a deterministic mix of ordinary BMP characters, non-surrogate
BMP characters above U+E000, and valid surrogate pairs. No malformed input is
produced -- the performance benchmark expects errorCount = 0.

Usage:
    python3 generate_utf16_benchmark.py --output-dir benchmarks/data \\
        --seed 479 --sizes-mb 1,8,32,64
"""

import argparse
import array
import os
import random
import sys

MIB = 1024 * 1024

# Value ranges (all valid, non-surrogate except the explicit surrogate pairs).
ORDINARY_LO, ORDINARY_HI = 0x0020, 0xD7FF   # ordinary BMP characters
UPPER_LO, UPPER_HI = 0xE000, 0xFFFD         # non-surrogate BMP above U+E000
HIGH_LO, HIGH_HI = 0xD800, 0xDBFF           # high surrogate
LOW_LO, LOW_HI = 0xDC00, 0xDFFF             # low surrogate

# How many code units to buffer before writing, to avoid holding a whole file
# in memory.
CHUNK_UNITS = 1 << 20


def parse_sizes(text):
    sizes = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        value = int(part)
        if value <= 0:
            raise ValueError("size must be a positive integer (got %r)" % part)
        sizes.append(value)
    if not sizes:
        raise ValueError("no sizes provided")
    return sizes


def units_to_le_bytes(units):
    """Pack a list of uint16 code units as little-endian bytes on any host."""
    packed = array.array("H", units)
    if sys.byteorder == "big":
        packed.byteswap()
    return packed.tobytes()


def generate_file(path, target_bytes, seed):
    """Write exactly target_bytes of valid UTF-16LE to path.

    target_bytes is even; every code unit is 2 bytes and every surrogate pair is
    4 bytes, so the exact size is always reachable without splitting a unit or a
    pair.
    """
    rng = random.Random(seed)
    produced = 0
    chunk = []
    try:
        with open(path, "wb") as out:
            while produced < target_bytes:
                remaining = target_bytes - produced
                if remaining < 4:
                    # Only room for one more 2-byte code unit.
                    chunk.append(rng.randint(ORDINARY_LO, ORDINARY_HI))
                    produced += 2
                else:
                    r = rng.random()
                    if r < 0.70:
                        chunk.append(rng.randint(ORDINARY_LO, ORDINARY_HI))
                        produced += 2
                    elif r < 0.85:
                        chunk.append(rng.randint(UPPER_LO, UPPER_HI))
                        produced += 2
                    else:
                        chunk.append(rng.randint(HIGH_LO, HIGH_HI))
                        chunk.append(rng.randint(LOW_LO, LOW_HI))
                        produced += 4
                if len(chunk) >= CHUNK_UNITS:
                    out.write(units_to_le_bytes(chunk))
                    chunk = []
            if chunk:
                out.write(units_to_le_bytes(chunk))
    except OSError as ex:
        raise SystemExit("ERROR: could not write %s: %s" % (path, ex))
    return os.path.getsize(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default="benchmarks/data",
                        help="directory to write the .bin files into")
    parser.add_argument("--seed", type=int, default=479,
                        help="deterministic random seed (default: 479)")
    parser.add_argument("--sizes-mb", default="1,8,32,64",
                        help="comma-separated sizes in MiB (default: 1,8,32,64)")
    args = parser.parse_args()

    try:
        sizes = parse_sizes(args.sizes_mb)
    except ValueError as ex:
        raise SystemExit("ERROR: invalid --sizes-mb: %s" % ex)

    try:
        os.makedirs(args.output_dir, exist_ok=True)
    except OSError as ex:
        raise SystemExit("ERROR: could not create %s: %s" % (args.output_dir, ex))

    for size_mb in sizes:
        name = "valid_utf16le_%dMiB.bin" % size_mb
        path = os.path.join(args.output_dir, name)
        actual = generate_file(path, size_mb * MIB, args.seed)
        print("%s  %d bytes" % (path, actual))


if __name__ == "__main__":
    main()
