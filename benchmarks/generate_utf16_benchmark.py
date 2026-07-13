#!/usr/bin/env python3
"""Generate deterministic, valid UTF-16LE benchmark input files.

Two families of datasets are available:

  default              the original synthetic mix (ordinary BMP, upper BMP and
                       surrogate pairs). This is what the benchmark runner uses,
                       and its bytes are unchanged so existing results stay
                       comparable.

  multilingual modes   realistic text built from built-in Unicode repertoires:
                       english_ascii_heavy, european_accented, south_asian, cjk,
                       emoji_heavy, mixed_multilingual.

Every dataset is valid UTF-16LE: code points are encoded explicitly, non-BMP
characters always become a well-formed surrogate pair, and a file never ends in a
half-written code unit. A JSON sidecar with the generation metadata is written next
to each file. No text is downloaded and no corpus is stored in the repository.

Examples:
    # default dataset used by the benchmark runner (backward compatible)
    python3 benchmarks/generate_utf16_benchmark.py --sizes-mb 1,8,32,64

    # one multilingual dataset
    python3 benchmarks/generate_utf16_benchmark.py --datasets cjk --sizes-mb 1

    # every multilingual dataset at 1 MiB
    python3 benchmarks/generate_utf16_benchmark.py --datasets all --sizes-mb 1
"""

import argparse
import array
import json
import os
import random
import sys

MIB = 1024 * 1024

# Value ranges for the default synthetic dataset (all valid, non-surrogate except
# the explicit surrogate pairs).
ORDINARY_LO, ORDINARY_HI = 0x0020, 0xD7FF   # ordinary BMP characters
UPPER_LO, UPPER_HI = 0xE000, 0xFFFD         # non-surrogate BMP above U+E000
HIGH_LO, HIGH_HI = 0xD800, 0xDBFF           # high surrogate
LOW_LO, LOW_HI = 0xDC00, 0xDFFF             # low surrogate

# How many code units to buffer before writing, to avoid holding a whole file
# in memory.
CHUNK_UNITS = 1 << 20


# --- Character repertoires -----------------------------------------------------
# Built from explicit code point ranges. None of these ranges overlap the surrogate
# block (U+D800-U+DFFF), so every character encodes to well-formed UTF-16.

def code_points(*ranges):
    values = []
    for lo, hi in ranges:
        values.extend(range(lo, hi + 1))
    return values


ASCII_LOWER = code_points((0x0061, 0x007A))
ASCII_UPPER = code_points((0x0041, 0x005A))
ASCII_DIGIT = code_points((0x0030, 0x0039))
# Latin-1 Supplement letters + Latin Extended-A (accented European)
LATIN_ACCENTED = code_points((0x00C0, 0x00D6), (0x00D8, 0x00F6),
                             (0x00F8, 0x00FF), (0x0100, 0x017F))
# Devanagari (Hindi): consonants/vowels + vowel signs
DEVANAGARI = code_points((0x0905, 0x0939), (0x093E, 0x094C))
# Gurmukhi (Punjabi)
GURMUKHI = code_points((0x0A05, 0x0A28), (0x0A2A, 0x0A30), (0x0A3E, 0x0A42))
CJK_HAN = code_points((0x4E00, 0x9FA5))                       # CJK ideographs
KANA = code_points((0x3041, 0x3096), (0x30A1, 0x30FA))        # hiragana + katakana
HANGUL = code_points((0xAC00, 0xD7A3))                        # BMP: below U+D800
# Non-BMP emoji: each becomes a surrogate pair in UTF-16
EMOJI = code_points((0x1F300, 0x1F5FF), (0x1F600, 0x1F64F),
                    (0x1F680, 0x1F6FF), (0x1F900, 0x1F9FF))

# Each source is (weight, repertoire, min_word_len, max_word_len).
MODES = {
    "english_ascii_heavy": {
        "description": "ASCII English-like words, capitals and digits; every "
                       "character is a single BMP code unit",
        "sources": [(0.86, ASCII_LOWER, 2, 9),
                    (0.09, ASCII_UPPER, 1, 3),
                    (0.05, ASCII_DIGIT, 1, 4)],
    },
    "european_accented": {
        "description": "Latin words with accented European characters "
                       "(Latin-1 Supplement and Latin Extended-A); all BMP",
        "sources": [(0.55, ASCII_LOWER, 2, 9),
                    (0.40, LATIN_ACCENTED, 2, 8),
                    (0.05, ASCII_UPPER, 1, 3)],
    },
    "south_asian": {
        "description": "Devanagari (Hindi) and Gurmukhi (Punjabi) words; all BMP",
        "sources": [(0.55, DEVANAGARI, 3, 9),
                    (0.40, GURMUKHI, 3, 8),
                    (0.05, ASCII_LOWER, 2, 5)],
    },
    "cjk": {
        "description": "CJK Han ideographs with Japanese kana and Korean Hangul; "
                       "all BMP",
        "sources": [(0.55, CJK_HAN, 1, 4),
                    (0.25, KANA, 2, 6),
                    (0.20, HANGUL, 1, 4)],
    },
    "emoji_heavy": {
        "description": "Non-BMP emoji (each encoded as a surrogate pair) "
                       "interleaved with ASCII words",
        "sources": [(0.75, EMOJI, 1, 5),
                    (0.25, ASCII_LOWER, 2, 6)],
    },
    "mixed_multilingual": {
        "description": "Blend of ASCII, accented European, Devanagari, Gurmukhi, "
                       "CJK, kana and non-BMP emoji",
        "sources": [(0.30, ASCII_LOWER, 2, 8),
                    (0.15, LATIN_ACCENTED, 2, 7),
                    (0.15, DEVANAGARI, 3, 8),
                    (0.10, GURMUKHI, 3, 7),
                    (0.15, CJK_HAN, 1, 4),
                    (0.05, KANA, 2, 5),
                    (0.10, EMOJI, 1, 3)],
    },
}

DEFAULT_MODE = "default"
DEFAULT_DESCRIPTION = ("Synthetic mix: ordinary BMP characters, non-surrogate BMP "
                       "above U+E000, and explicit surrogate pairs")


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


def parse_datasets(text):
    names = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "all":
            names.extend(MODES.keys())
        elif part == DEFAULT_MODE or part in MODES:
            names.append(part)
        else:
            raise ValueError("unknown dataset %r (choose from: %s)"
                             % (part, ", ".join([DEFAULT_MODE, "all"] + sorted(MODES))))
    if not names:
        raise ValueError("no datasets provided")
    # de-duplicate while keeping order
    seen = set()
    unique = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique.append(name)
    return unique


def units_to_le_bytes(units):
    """Pack a list of uint16 code units as little-endian bytes on any host."""
    packed = array.array("H", units)
    if sys.byteorder == "big":
        packed.byteswap()
    return packed.tobytes()


def utf16_units(code_point):
    """UTF-16 code units for a scalar value: 1 unit, or an explicit surrogate pair."""
    if code_point < 0x10000:
        return (code_point,)
    offset = code_point - 0x10000
    return (HIGH_LO + (offset >> 10), LOW_LO + (offset & 0x3FF))


def generate_default_file(path, target_bytes, seed):
    """Original synthetic dataset. Kept byte-for-byte stable so existing benchmark
    results remain comparable.

    target_bytes is even; every code unit is 2 bytes and every surrogate pair is
    4 bytes, so the exact size is always reachable without splitting a unit or a
    pair.
    """
    rng = random.Random(seed)
    produced = 0
    chunk = []
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


def pick_source(rng, sources, total_weight):
    r = rng.random() * total_weight
    upto = 0.0
    for source in sources:
        upto += source[0]
        if r <= upto:
            return source
    return sources[-1]


def generate_text_file(path, target_bytes, seed, spec):
    """Word-oriented multilingual text of exactly target_bytes.

    Words are drawn from the mode's repertoires and separated by spaces (with the
    occasional newline). A character is only emitted if its whole encoding fits in
    the remaining space, so the file never ends in a half-written code unit and no
    surrogate pair is ever split.
    """
    rng = random.Random(seed)
    sources = spec["sources"]
    total_weight = sum(source[0] for source in sources)
    produced = 0
    chunk = []
    with open(path, "wb") as out:
        while produced < target_bytes:
            _, repertoire, min_len, max_len = pick_source(rng, sources, total_weight)
            for _ in range(rng.randint(min_len, max_len)):
                remaining = target_bytes - produced
                if remaining < 2:
                    break
                units = utf16_units(repertoire[rng.randrange(len(repertoire))])
                if 2 * len(units) > remaining:
                    # A surrogate pair would not fit; use a single-unit filler.
                    units = (ASCII_LOWER[rng.randrange(len(ASCII_LOWER))],)
                chunk.extend(units)
                produced += 2 * len(units)
            remaining = target_bytes - produced
            if remaining >= 2:
                chunk.append(0x000A if rng.random() < 0.02 else 0x0020)
                produced += 2
            if len(chunk) >= CHUNK_UNITS:
                out.write(units_to_le_bytes(chunk))
                chunk = []
        if chunk:
            out.write(units_to_le_bytes(chunk))


def write_metadata(bin_path, dataset, requested_bytes, actual_bytes, seed, description):
    meta = {
        "file": os.path.basename(bin_path),
        "dataset_type": dataset,
        "requested_bytes": requested_bytes,
        "requested_mib": requested_bytes // MIB,
        "actual_bytes": actual_bytes,
        "seed": seed,
        "encoding": "UTF-16LE",
        "byte_order_mark": False,
        "validity": "valid: well-formed UTF-16LE, no lone surrogates",
        "composition": description,
    }
    meta_path = bin_path + ".json"
    with open(meta_path, "w") as out:
        json.dump(meta, out, indent=2)
        out.write("\n")
    return meta_path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default="benchmarks/data",
                        help="directory to write the .bin files into")
    parser.add_argument("--seed", type=int, default=479,
                        help="deterministic random seed (default: 479)")
    parser.add_argument("--sizes-mb", default="1,8,32,64",
                        help="comma-separated sizes in MiB (default: 1,8,32,64)")
    parser.add_argument("--datasets", default=DEFAULT_MODE,
                        help="comma-separated dataset types: %s, or 'all' for every "
                             "multilingual mode (default: %s)"
                             % (", ".join([DEFAULT_MODE] + sorted(MODES)), DEFAULT_MODE))
    args = parser.parse_args()

    try:
        sizes = parse_sizes(args.sizes_mb)
        datasets = parse_datasets(args.datasets)
    except ValueError as ex:
        raise SystemExit("ERROR: %s" % ex)

    try:
        os.makedirs(args.output_dir, exist_ok=True)
    except OSError as ex:
        raise SystemExit("ERROR: could not create %s: %s" % (args.output_dir, ex))

    for dataset in datasets:
        for size_mb in sizes:
            target = size_mb * MIB
            if dataset == DEFAULT_MODE:
                # Unchanged name and bytes: the benchmark runner looks for this file.
                name = "valid_utf16le_%dMiB.bin" % size_mb
                description = DEFAULT_DESCRIPTION
            else:
                name = "valid_utf16le_%s_%dMiB.bin" % (dataset, size_mb)
                description = MODES[dataset]["description"]
            path = os.path.join(args.output_dir, name)
            try:
                if dataset == DEFAULT_MODE:
                    generate_default_file(path, target, args.seed)
                else:
                    generate_text_file(path, target, args.seed, MODES[dataset])
            except OSError as ex:
                raise SystemExit("ERROR: could not write %s: %s" % (path, ex))
            actual = os.path.getsize(path)
            write_metadata(path, dataset, target, actual, args.seed, description)
            print("%s  %d bytes  [%s]" % (path, actual, dataset))


if __name__ == "__main__":
    main()
