#!/usr/bin/env python3
"""Generate deterministic UTF-16LE benchmark input files, valid or malformed.

Valid datasets
--------------
  default              the original synthetic mix (ordinary BMP, upper BMP and
                       surrogate pairs). This is what the benchmark runner uses,
                       and its bytes are unchanged so existing results stay
                       comparable.

  multilingual modes   realistic text built from built-in Unicode repertoires:
                       english_ascii_heavy, european_accented, south_asian, cjk,
                       emoji_heavy, mixed_multilingual.

Malformed datasets
------------------
Passing --error-patterns switches on error injection. Any dataset above can be
corrupted at a controlled error rate. Invalid surrogate sequences are written as
raw UTF-16LE bytes (Python's string codecs will not encode a lone surrogate), and
the expected error count is computed from the final bytes by an independent
reference validator, so the metadata can never drift from reality.

Every file is accompanied by a JSON sidecar describing how it was made. No text is
downloaded and no corpus is stored in the repository.

Examples:
    # valid: default dataset used by the benchmark runner (backward compatible)
    python3 benchmarks/generate_utf16_benchmark.py --sizes-mb 1,8,32,64

    # valid: one multilingual dataset
    python3 benchmarks/generate_utf16_benchmark.py --datasets cjk --sizes-mb 1

    # malformed: 0.01% randomly distributed errors in multilingual text
    python3 benchmarks/generate_utf16_benchmark.py --datasets mixed_multilingual \\
        --error-patterns random_mix --error-rates 0.01 --sizes-mb 1

    # malformed: the full error-rate sweep, every pattern
    python3 benchmarks/generate_utf16_benchmark.py --datasets mixed_multilingual \\
        --error-patterns all --error-rates all --sizes-mb 1
"""

import argparse
import array
import json
import os
import random
import struct
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

# --- Error injection -----------------------------------------------------------

ERROR_PATTERNS = {
    "unpaired_high": "lone high surrogates replacing BMP code units",
    "unpaired_low": "lone low surrogates replacing BMP code units",
    "reversed_pair": "a low surrogate followed by a high surrogate (reversed pair)",
    "odd_trailing_byte": "lone high surrogates, plus the final byte removed so the "
                         "file ends in an incomplete code unit",
    "random_mix": "randomly distributed mix of unpaired high, unpaired low and "
                  "reversed pairs",
    "clustered_mix": "the same mix, concentrated in a few contiguous clusters",
}

# Error rates are percentages of the total code units.
STANDARD_ERROR_RATES = [0, 0.0001, 0.001, 0.01, 0.1, 1]

MIX_KINDS = ("unpaired_high", "unpaired_low", "reversed_pair")


# --- Independent reference validator -------------------------------------------

def count_utf16le_errors(data):
    """Count ill-formed UTF-16LE code units in raw bytes.

    Independent of the C++ validators: it walks the byte structure directly, so it
    also handles blobs no string encoder would produce (lone surrogates, an odd
    trailing byte). Used to fill in the expected error count of every file.
    """
    n = len(data)
    units = array.array("H")
    units.frombytes(bytes(data[:n - (n & 1)]))
    if sys.byteorder == "big":
        units.byteswap()
    pend = False        # previous code unit was an unmatched high surrogate
    errors = 0
    for unit in units:
        hi = HIGH_LO <= unit <= HIGH_HI
        lo = LOW_LO <= unit <= LOW_HI
        errors += (0 if pend else 1) if lo else (1 if pend else 0)
        pend = hi
    if pend:
        errors += 1     # dangling final high surrogate
    if n & 1:
        errors += 1     # incomplete final code unit
    return errors


# --- Argument parsing ----------------------------------------------------------

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


def _dedupe(values):
    seen = set()
    unique = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


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
    return _dedupe(names)


def parse_patterns(text):
    names = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "all":
            names.extend(ERROR_PATTERNS.keys())
        elif part in ERROR_PATTERNS:
            names.append(part)
        else:
            raise ValueError("unknown error pattern %r (choose from: %s)"
                             % (part, ", ".join(["all"] + sorted(ERROR_PATTERNS))))
    if not names:
        raise ValueError("no error patterns provided")
    return _dedupe(names)


def parse_rates(text):
    rates = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "all":
            rates.extend(STANDARD_ERROR_RATES)
            continue
        value = float(part)
        if value < 0 or value > 100:
            raise ValueError("error rate must be a percentage in [0, 100] (got %r)" % part)
        rates.append(value)
    if not rates:
        raise ValueError("no error rates provided")
    return _dedupe(rates)


def rate_tag(rate):
    """Filename-friendly rate, e.g. 0 -> '0', 0.0001 -> '0.0001', 1 -> '1'."""
    return "%g" % rate


# --- Valid dataset generation --------------------------------------------------

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


def iter_default_units(target_bytes, seed):
    """Original synthetic dataset, yielded in chunks of code units.

    Kept byte-for-byte stable so existing benchmark results remain comparable.
    target_bytes is even; every code unit is 2 bytes and every surrogate pair is
    4 bytes, so the exact size is always reachable without splitting a unit or pair.
    """
    rng = random.Random(seed)
    produced = 0
    chunk = []
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
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def pick_source(rng, sources, total_weight):
    r = rng.random() * total_weight
    upto = 0.0
    for source in sources:
        upto += source[0]
        if r <= upto:
            return source
    return sources[-1]


def iter_text_units(target_bytes, seed, spec):
    """Word-oriented multilingual text of exactly target_bytes, in chunks.

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
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def iter_units(dataset, target_bytes, seed):
    if dataset == DEFAULT_MODE:
        return iter_default_units(target_bytes, seed)
    return iter_text_units(target_bytes, seed, MODES[dataset])


def dataset_description(dataset):
    if dataset == DEFAULT_MODE:
        return DEFAULT_DESCRIPTION
    return MODES[dataset]["description"]


def write_valid_dataset(path, dataset, target_bytes, seed):
    """Stream a valid dataset to disk without holding it all in memory."""
    with open(path, "wb") as out:
        for chunk in iter_units(dataset, target_bytes, seed):
            out.write(units_to_le_bytes(chunk))


def build_dataset(dataset, target_bytes, seed):
    """Same bytes as write_valid_dataset, but returned as a mutable buffer."""
    buf = bytearray()
    for chunk in iter_units(dataset, target_bytes, seed):
        buf += units_to_le_bytes(chunk)
    return buf


# --- Malformed dataset generation ----------------------------------------------
# Invalid surrogate sequences are patched straight into the raw bytes. Python's
# codecs refuse to encode a lone surrogate, so string encoding is never used here.

def unit_at(buf, index):
    return buf[2 * index] | (buf[2 * index + 1] << 8)


def put_unit(buf, index, value):
    struct.pack_into("<H", buf, 2 * index, value)


def is_surrogate(unit):
    return HIGH_LO <= unit <= LOW_HI


def site_is_free(buf, used, index, width, total_units):
    """A site is usable if it is in range, unused, and holds only BMP units.

    Existing surrogate pairs (real emoji, for example) are left alone so that an
    injected error is an addition rather than the destruction of valid text.
    """
    if index < 0 or index + width > total_units:
        return False
    for k in range(index, index + width):
        if k in used or is_surrogate(unit_at(buf, k)):
            return False
    return True


def apply_error(buf, rng, index, kind):
    """Write one malformed construct; returns how many code units it occupies."""
    if kind == "unpaired_high":
        put_unit(buf, index, rng.randint(HIGH_LO, HIGH_HI))
        return 1
    if kind == "unpaired_low":
        put_unit(buf, index, rng.randint(LOW_LO, LOW_HI))
        return 1
    # reversed_pair: a low surrogate first, then a high surrogate
    put_unit(buf, index, rng.randint(LOW_LO, LOW_HI))
    put_unit(buf, index + 1, rng.randint(HIGH_LO, HIGH_HI))
    return 2


def kind_for(pattern, rng):
    if pattern in ("random_mix", "clustered_mix"):
        return rng.choice(MIX_KINDS)
    if pattern == "odd_trailing_byte":
        return "unpaired_high"
    return pattern


def inject_errors(buf, seed, rate, pattern):
    """Corrupt buf in place at the requested rate. Returns the number of sites.

    A "site" is one malformed construct; a reversed pair occupies two code units and
    contributes two errors, so the site count is not the error count. The authoritative
    error count is always recomputed from the final bytes by count_utf16le_errors().
    """
    total_units = len(buf) // 2
    if rate <= 0 or total_units == 0:
        return 0

    # Deterministic, and distinct per (seed, pattern, rate). Seeding a Random with a
    # str is stable across runs (it hashes the value, not id()).
    rng = random.Random("utf16-inject|%d|%s|%.10g" % (seed, pattern, rate))
    target_sites = max(1, int(round(total_units * rate / 100.0)))
    used = set()
    placed = 0

    if pattern == "clustered_mix":
        # Concentrate the errors into a few contiguous runs instead of spreading them.
        guard = 0
        while placed < target_sites and guard < target_sites * 50 + 1000:
            guard += 1
            run = min(target_sites - placed, rng.randint(8, 64))
            index = rng.randrange(total_units)
            for _ in range(run):
                if index >= total_units:
                    break
                kind = kind_for(pattern, rng)
                width = 2 if kind == "reversed_pair" else 1
                if site_is_free(buf, used, index, width, total_units):
                    apply_error(buf, rng, index, kind)
                    used.update(range(index, index + width))
                    placed += 1
                    index += width + 1      # a gap keeps each error separately countable
                else:
                    index += 1
    else:
        guard = 0
        while placed < target_sites and guard < target_sites * 50 + 1000:
            guard += 1
            kind = kind_for(pattern, rng)
            width = 2 if kind == "reversed_pair" else 1
            index = rng.randrange(total_units)
            if not site_is_free(buf, used, index, width, total_units):
                continue
            apply_error(buf, rng, index, kind)
            used.update(range(index, index + width))
            placed += 1

    if pattern == "odd_trailing_byte":
        del buf[-1]     # file now ends in an incomplete code unit

    return placed


# --- Metadata ------------------------------------------------------------------

def write_metadata(bin_path, meta):
    meta_path = bin_path + ".json"
    with open(meta_path, "w") as out:
        json.dump(meta, out, indent=2)
        out.write("\n")
    return meta_path


def base_metadata(bin_path, dataset, requested_bytes, actual_bytes, seed):
    return {
        "file": os.path.basename(bin_path),
        "dataset_type": dataset,
        "requested_bytes": requested_bytes,
        "requested_mib": requested_bytes // MIB,
        "actual_bytes": actual_bytes,
        "seed": seed,
        "encoding": "UTF-16LE",
        "byte_order_mark": False,
        "composition": dataset_description(dataset),
    }


# --- Main ----------------------------------------------------------------------

def generate_valid(output_dir, dataset, size_mb, seed):
    target = size_mb * MIB
    if dataset == DEFAULT_MODE:
        # Unchanged name and bytes: the benchmark runner looks for this file.
        name = "valid_utf16le_%dMiB.bin" % size_mb
    else:
        name = "valid_utf16le_%s_%dMiB.bin" % (dataset, size_mb)
    path = os.path.join(output_dir, name)
    write_valid_dataset(path, dataset, target, seed)
    actual = os.path.getsize(path)

    meta = base_metadata(path, dataset, target, actual, seed)
    meta["validity"] = "valid: well-formed UTF-16LE, no lone surrogates"
    meta["error_rate_percent"] = 0
    meta["error_pattern"] = None
    meta["error_sites_injected"] = 0
    meta["expected_error_count"] = 0
    write_metadata(path, meta)
    print("%s  %d bytes  [%s, valid]" % (path, actual, dataset))


def generate_malformed(output_dir, dataset, size_mb, seed, pattern, rate):
    target = size_mb * MIB
    name = "malformed_utf16le_%s_%s_err%s_%dMiB.bin" % (
        dataset, pattern, rate_tag(rate), size_mb)
    path = os.path.join(output_dir, name)

    buf = build_dataset(dataset, target, seed)
    sites = inject_errors(buf, seed, rate, pattern)
    with open(path, "wb") as out:
        out.write(buf)
    actual = os.path.getsize(path)

    # The expected count is measured from the bytes we actually wrote, so it cannot
    # disagree with what a validator will see.
    expected = count_utf16le_errors(buf)

    meta = base_metadata(path, dataset, target, actual, seed)
    meta["validity"] = ("valid: well-formed UTF-16LE, no lone surrogates" if expected == 0
                        else "malformed: contains ill-formed UTF-16LE code units")
    meta["error_rate_percent"] = rate
    meta["error_pattern"] = pattern
    meta["error_sites_injected"] = sites
    meta["expected_error_count"] = expected
    meta["error_description"] = (
        "no errors injected (0% control file)" if rate <= 0
        else ERROR_PATTERNS[pattern])
    write_metadata(path, meta)
    print("%s  %d bytes  [%s, %s @ %s%%, sites=%d, expected_errors=%d]"
          % (path, actual, dataset, pattern, rate_tag(rate), sites, expected))


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
    parser.add_argument("--error-patterns", default=None,
                        help="enable error injection with these patterns: %s, or 'all'"
                             % ", ".join(sorted(ERROR_PATTERNS)))
    parser.add_argument("--error-rates", default="0.01",
                        help="comma-separated error rates in percent, or 'all' for the "
                             "standard sweep %s (default: 0.01; only used with "
                             "--error-patterns)"
                             % ",".join(rate_tag(r) for r in STANDARD_ERROR_RATES))
    args = parser.parse_args()

    try:
        sizes = parse_sizes(args.sizes_mb)
        datasets = parse_datasets(args.datasets)
        patterns = parse_patterns(args.error_patterns) if args.error_patterns else None
        rates = parse_rates(args.error_rates) if patterns else None
    except ValueError as ex:
        raise SystemExit("ERROR: %s" % ex)

    try:
        os.makedirs(args.output_dir, exist_ok=True)
    except OSError as ex:
        raise SystemExit("ERROR: could not create %s: %s" % (args.output_dir, ex))

    try:
        for dataset in datasets:
            for size_mb in sizes:
                if patterns is None:
                    generate_valid(args.output_dir, dataset, size_mb, args.seed)
                else:
                    for pattern in patterns:
                        for rate in rates:
                            generate_malformed(args.output_dir, dataset, size_mb,
                                               args.seed, pattern, rate)
    except OSError as ex:
        raise SystemExit("ERROR: could not write to %s: %s" % (args.output_dir, ex))


if __name__ == "__main__":
    main()
