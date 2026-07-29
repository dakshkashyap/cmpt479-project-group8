#!/usr/bin/env python3
"""Generate deterministic UTF-16 datasets with controlled malformed-unit density (issue #44).

Each dataset is realistic UTF-16 text into which an EXACT number of ill-formed surrogate
code units has been injected, spread approximately uniformly. The same code-unit sequence
is written in both UTF-16LE and UTF-16BE, so the two files are byte swaps of each other and
report identical diagnostics.

These are inputs for later benchmarking and validation work; nothing here measures anything.

Density
-------
Density is a percentage of **UTF-16 code units**, never of bytes:

    target_errors = floor(code_units * density / 100 + 0.5)      # round half up

and the generator guarantees the file contains exactly that many ill-formed code units --
verified, not assumed. A density of 0 produces a wholly well-formed file.

Determinism
-----------
Everything derives from `--seed`. Each dataset is built from
`random.Random("utf16-density|<seed>|<size>|<density>")`, so a dataset depends only on the
seed, its own size and its own density -- never on what else was generated in the same run,
or in what order. Re-running with the same arguments reproduces byte-identical files.

Verification
------------
Before a dataset is accepted, its error count is checked three ways -- the scalar validator,
the SIMD validator (`--simd`), and scripts/utf16_oracle.py -- in both encodings. They must
all equal the target. Any disagreement aborts the run and nothing further is written.

Examples
--------
    python3 scripts/generate_error_density_datasets.py                    # full matrix
    python3 scripts/generate_error_density_datasets.py --quick            # small sizes only
    python3 scripts/generate_error_density_datasets.py --sizes 64KiB,1MiB --densities 0,1,5
    python3 scripts/generate_error_density_datasets.py --encodings utf16le --overwrite
"""

import argparse
import csv
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utf16_oracle as oracle                                   # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PARABIX = os.environ.get("PARABIX_DIR", os.path.join(REPO_ROOT, ".deps", "parabix"))
DEFAULT_BIN = os.path.join(DEFAULT_PARABIX, "build", "bin", "utf16validate")
DEFAULT_OUTPUT_DIR = os.path.join(REPO_ROOT, "datasets", "error_density")

DEFAULT_SEED = 479
KIB = 1024
MIB = 1024 * 1024

# (label, bytes). Every size is even, so no dataset ends in a half code unit.
SIZES = [("4KiB", 4 * KIB), ("16KiB", 16 * KIB), ("64KiB", 64 * KIB),
         ("256KiB", 256 * KIB), ("1MiB", MIB), ("4MiB", 4 * MIB)]
QUICK_SIZES = ["4KiB", "16KiB", "64KiB"]

DENSITIES = [0.0, 0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0]
QUICK_DENSITIES = [0.0, 0.1, 1.0, 10.0, 50.0]

ENCODINGS = [("utf16le", False), ("utf16be", True)]

HIGH_FIRST, HIGH_LAST = 0xD800, 0xDBFF
LOW_FIRST, LOW_LAST = 0xDC00, 0xDFFF


# --- Valid content -------------------------------------------------------------
# Realistic UTF-16: ASCII words, accented Latin, several BMP scripts, supplementary
# characters and emoji. Words are drawn from repertoires rather than repeated verbatim, so
# the stream does not degenerate into a repeating pattern.

def code_points(*ranges):
    values = []
    for low, high in ranges:
        values.extend(range(low, high + 1))
    return values


ASCII_LOWER = code_points((0x0061, 0x007A))
ASCII_UPPER = code_points((0x0041, 0x005A))
ASCII_DIGIT = code_points((0x0030, 0x0039))
LATIN_ACCENTED = code_points((0x00C0, 0x00D6), (0x00D8, 0x00F6), (0x00F8, 0x017F))
GREEK = code_points((0x0391, 0x03A9), (0x03B1, 0x03C9))
CYRILLIC = code_points((0x0410, 0x044F))
HEBREW = code_points((0x05D0, 0x05EA))
ARABIC = code_points((0x0621, 0x063A), (0x0641, 0x064A))
DEVANAGARI = code_points((0x0905, 0x0939))
GURMUKHI = code_points((0x0A05, 0x0A28))
THAI = code_points((0x0E01, 0x0E2E))
CJK = code_points((0x4E00, 0x9FA5))
KANA = code_points((0x3041, 0x3096), (0x30A1, 0x30FA))
HANGUL = code_points((0xAC00, 0xD7A3))
# Supplementary planes: each becomes a surrogate pair.
EMOJI = code_points((0x1F300, 0x1F5FF), (0x1F600, 0x1F64F), (0x1F900, 0x1F9FF))
SUPPLEMENTARY_TEXT = code_points((0x10400, 0x1044F), (0x1D400, 0x1D454),
                                 (0x20000, 0x2A000))

# (weight, repertoire, min word length, max word length)
SOURCES = [
    (0.26, ASCII_LOWER, 2, 9),
    (0.05, ASCII_UPPER, 1, 3),
    (0.04, ASCII_DIGIT, 1, 4),
    (0.12, LATIN_ACCENTED, 2, 8),
    (0.05, GREEK, 2, 7),
    (0.05, CYRILLIC, 2, 8),
    (0.04, HEBREW, 2, 6),
    (0.04, ARABIC, 2, 7),
    (0.06, DEVANAGARI, 2, 7),
    (0.03, GURMUKHI, 2, 6),
    (0.03, THAI, 2, 7),
    (0.09, CJK, 1, 4),
    (0.04, KANA, 2, 6),
    (0.04, HANGUL, 1, 4),
    (0.04, EMOJI, 1, 3),                # supplementary: surrogate pairs
    (0.02, SUPPLEMENTARY_TEXT, 1, 3),   # supplementary: non-emoji
]
TOTAL_WEIGHT = sum(weight for weight, _, _, _ in SOURCES)


def pick_source(rng):
    roll = rng.random() * TOTAL_WEIGHT
    upto = 0.0
    for source in SOURCES:
        upto += source[0]
        if roll <= upto:
            return source
    return SOURCES[-1]


def utf16_units(code_point):
    if code_point < 0x10000:
        return (code_point,)
    offset = code_point - 0x10000
    return (HIGH_FIRST + (offset >> 10), LOW_FIRST + (offset & 0x3FF))


def build_valid_units(total_units, rng):
    """A well-formed stream of exactly `total_units` code units.

    Returns (units, pair_member) where pair_member[k] is True if unit k is half of a
    surrogate pair. Injection never disturbs a pair without replacing both halves, which is
    what keeps the injected error count exact.
    """
    units = []
    pair_member = []
    while len(units) < total_units:
        _, repertoire, min_len, max_len = pick_source(rng)
        for _ in range(rng.randint(min_len, max_len)):
            if len(units) >= total_units:
                break
            encoded = utf16_units(repertoire[rng.randrange(len(repertoire))])
            if len(units) + len(encoded) > total_units:
                encoded = (ASCII_LOWER[rng.randrange(len(ASCII_LOWER))],)
            units.extend(encoded)
            pair_member.extend([len(encoded) == 2] * len(encoded))
        if len(units) < total_units:
            units.append(0x000A if rng.random() < 0.03 else 0x0020)
            pair_member.append(False)
    return units[:total_units], pair_member[:total_units]


# --- Malformed patterns --------------------------------------------------------
# Each pattern is a run of code units that, when both neighbours are non-surrogate,
# contributes exactly `errors` ill-formed units -- no more, no less.

def pattern_lone_high(rng):
    return [rng.randint(HIGH_FIRST, HIGH_LAST)], 1


def pattern_lone_low(rng):
    return [rng.randint(LOW_FIRST, LOW_LAST)], 1


def pattern_reversed(rng):
    # A low followed by a high: the low has no high before it, the high no low after it.
    return [rng.randint(LOW_FIRST, LOW_LAST), rng.randint(HIGH_FIRST, HIGH_LAST)], 2


def pattern_high_high(rng):
    return [rng.randint(HIGH_FIRST, HIGH_LAST), rng.randint(HIGH_FIRST, HIGH_LAST)], 2


def pattern_low_low(rng):
    return [rng.randint(LOW_FIRST, LOW_LAST), rng.randint(LOW_FIRST, LOW_LAST)], 2


def pattern_broken_mixed(rng):
    # low, low, high: none of the three can pair with anything.
    return ([rng.randint(LOW_FIRST, LOW_LAST), rng.randint(LOW_FIRST, LOW_LAST),
             rng.randint(HIGH_FIRST, HIGH_LAST)], 3)


PATTERNS = [
    ("lone_high", pattern_lone_high, 1),
    ("lone_low", pattern_lone_low, 1),
    ("reversed_pair", pattern_reversed, 2),
    ("high_high", pattern_high_high, 2),
    ("low_low", pattern_low_low, 2),
    ("broken_mixed", pattern_broken_mixed, 3),
]
GUARD_UNIT = 0x0020         # a plain space: never a surrogate


def choose_patterns(target_errors, rng, dense=False):
    """A deterministic multiset of patterns whose error counts sum to exactly target.

    Every pattern occupies exactly as many code units as the errors it contributes, plus one
    shared guard unit between neighbouring patterns. High densities therefore need longer
    patterns (fewer guards per error), which `dense` selects for; the last few errors still
    fall back to the short patterns so the total lands exactly on the target.
    """
    chosen = []
    remaining = target_errors
    while remaining > 0:
        usable = [p for p in PATTERNS if p[2] <= remaining]
        if dense:
            longest = max(p[2] for p in usable)
            preferred = [p for p in usable if p[2] == longest]
            usable = preferred if rng.random() < 0.85 else usable
        name, builder, errors = usable[rng.randrange(len(usable))]
        chosen.append((name, builder, errors))
        remaining -= errors
    return chosen


def inject(units, pair_member, target_errors, rng):
    """Overwrite `units` in place so it holds exactly `target_errors` ill-formed units.

    Patterns are spread over equal-sized slots, one per pattern, with a deterministic offset
    inside each slot -- so the malformed units are approximately uniform rather than
    clustered. A pattern is only written where it and its two neighbours are free of
    surrogate-pair halves; when a slot's candidate window overlaps a pair, that pair is first
    flattened to two plain BMP units (both halves at once, so no lone surrogate is created).
    """
    if target_errors <= 0:
        return {}

    total = len(units)
    chosen = choose_patterns(target_errors, rng,
                             dense=target_errors * 3 > total)
    rng.shuffle(chosen)
    slot = total / float(len(chosen))
    used = [False] * total
    mix = {}

    for index, (name, builder, errors) in enumerate(chosen):
        body, _ = builder(rng)
        width = len(body)
        # Where this pattern would like to sit: inside its own slot, offset deterministically.
        low = int(index * slot)
        high = max(low, int((index + 1) * slot) - width - 1)
        start = low + 1 if high <= low + 1 else rng.randint(low + 1, high)

        placed = False
        for candidate in _candidate_positions(start, total, width):
            # The pattern's own units must be free, and the single unit on each side must
            # not belong to another pattern -- it is the guard that keeps this pattern's
            # error count exactly `errors`. Guards are shared between neighbours, so the
            # cost of one pattern is `errors` units plus one guard.
            if any(used[k] for k in range(candidate, candidate + width)):
                continue
            if used[candidate - 1] or used[candidate + width]:
                continue
            _flatten_pairs(units, pair_member, used, candidate - 1, candidate + width)
            units[candidate:candidate + width] = body
            for k in range(candidate, candidate + width):
                used[k] = True
                pair_member[k] = False
            placed = True
            break
        if not placed:
            raise RuntimeError(
                "could not place a %s pattern at density %.4f%%; every pattern needs its "
                "own code units plus one guard unit, so the reachable maximum is below 100%%"
                % (name, 100.0 * target_errors / total))
        mix[name] = mix.get(name, 0) + 1

    return mix


def _candidate_positions(start, total, width):
    """`start` first, then positions spiralling outwards from it."""
    yield start
    for step in range(1, total):
        for candidate in (start + step, start - step):
            if 1 <= candidate <= total - width - 1:
                yield candidate


def _flatten_pairs(units, pair_member, used, first, last):
    """Replace every surrogate pair overlapping [first, last] with two plain BMP units.

    Both halves are always replaced together -- including a partner that sits just outside
    the window -- so the stream stays well-formed and no unintended lone surrogate can
    appear. Units already claimed by a placed pattern are never touched.
    """
    total = len(units)
    for k in range(max(0, first), min(total, last + 1)):
        if not pair_member[k] or used[k]:
            continue
        partner = k + 1 if HIGH_FIRST <= units[k] <= HIGH_LAST else k - 1
        for position in (k, partner):
            if 0 <= position < total and not used[position]:
                units[position] = GUARD_UNIT
                pair_member[position] = False


# --- Dataset construction ------------------------------------------------------

def target_error_count(code_units, density):
    """Round half up, so 0.5 errors becomes 1 rather than 0."""
    return int(code_units * density / 100.0 + 0.5)


def density_tag(density):
    return ("%g" % density).replace(".", "p")


def dataset_name(size_label, density, encoding):
    return "errdens_%s_d%spct.%s.bin" % (size_label, density_tag(density), encoding)


def build_dataset(size_bytes, density, seed):
    """Return (units, target_errors, pattern mix) for one size/density combination."""
    code_units = size_bytes // 2
    rng = random.Random("utf16-density|%d|%d|%.10g" % (seed, size_bytes, density))
    units, pair_member = build_valid_units(code_units, rng)
    target = target_error_count(code_units, density)
    mix = inject(units, pair_member, target, rng)
    return units, target, mix


# --- Verification --------------------------------------------------------------

def oracle_error_count(data, big_endian):
    """Error count from scripts/utf16_oracle.py, without building the repaired bytes."""
    positions = oracle.malformed_positions(oracle.decode_code_units(data, big_endian))
    return len(positions) + (1 if oracle.has_odd_trailing_byte(data) else 0)


def validator_count(binary, path, big_endian, simd):
    import subprocess
    args = [binary] + (["-be"] if big_endian else []) + (["-simd"] if simd else []) + [path]
    proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError("utf16validate failed (rc=%d): %s"
                           % (proc.returncode,
                              proc.stderr.decode("utf-8", "replace").strip()))
    return int(proc.stdout.decode().split("errorCount = ")[1].split()[0])


def verify(binary, path, data, big_endian, expected):
    """Scalar, --simd and the Python oracle must all report `expected`."""
    results = {
        "oracle": oracle_error_count(data, big_endian),
        "scalar": validator_count(binary, path, big_endian, simd=False),
        "simd": validator_count(binary, path, big_endian, simd=True),
    }
    disagree = dict((k, v) for k, v in results.items() if v != expected)
    return results, disagree


# --- Argument parsing ----------------------------------------------------------

def parse_sizes(text, quick):
    known = dict(SIZES)
    if text is None:
        labels = QUICK_SIZES if quick else [label for label, _ in SIZES]
    else:
        labels = [part.strip() for part in text.split(",") if part.strip()]
    chosen = []
    for label in labels:
        if label not in known:
            raise ValueError("unknown size %r (choose from: %s)"
                             % (label, ", ".join(l for l, _ in SIZES)))
        chosen.append((label, known[label]))
    if not chosen:
        raise ValueError("no sizes selected")
    return chosen


def parse_densities(text, quick):
    if text is None:
        return QUICK_DENSITIES if quick else list(DENSITIES)
    values = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        value = float(part)
        if value < 0 or value > 100:
            raise ValueError("density must be a percentage in [0, 100] (got %r)" % part)
        values.append(value)
    if not values:
        raise ValueError("no densities selected")
    return values


def parse_encodings(text):
    known = dict((name, be) for name, be in ENCODINGS)
    if text is None:
        return list(ENCODINGS)
    chosen = []
    for part in text.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part not in known:
            raise ValueError("unknown encoding %r (choose from: utf16le, utf16be)" % part)
        chosen.append((part, known[part]))
    if not chosen:
        raise ValueError("no encodings selected")
    return chosen


MANIFEST_FIELDS = ["filename", "encoding", "size_bytes", "code_units", "target_density",
                   "actual_error_count", "seed"]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="where to write (default: datasets/error_density)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="deterministic seed (default: %d)" % DEFAULT_SEED)
    parser.add_argument("--sizes", default=None,
                        help="comma-separated sizes: %s (default: all, or %s with --quick)"
                             % (",".join(l for l, _ in SIZES), ",".join(QUICK_SIZES)))
    parser.add_argument("--densities", default=None,
                        help="comma-separated percentages of code units (default: %s, "
                             "or %s with --quick)"
                             % (",".join("%g" % d for d in DENSITIES),
                                ",".join("%g" % d for d in QUICK_DENSITIES)))
    parser.add_argument("--encodings", default=None,
                        help="comma-separated: utf16le,utf16be (default: both)")
    parser.add_argument("--overwrite", action="store_true",
                        help="regenerate datasets that already exist (default: keep them)")
    parser.add_argument("--quick", action="store_true",
                        help="small sizes and a reduced density sweep")
    parser.add_argument("--bin", default=os.environ.get("UTF16VALIDATE_BIN", DEFAULT_BIN),
                        help="path to utf16validate (used for verification)")
    args = parser.parse_args()

    try:
        sizes = parse_sizes(args.sizes, args.quick)
        densities = parse_densities(args.densities, args.quick)
        encodings = parse_encodings(args.encodings)
    except ValueError as ex:
        raise SystemExit("ERROR: %s" % ex)

    if not os.access(args.bin, os.X_OK):
        raise SystemExit("ERROR: utf16validate not found at %s\n"
                         "       Run ./scripts/setup_parabix.sh first." % args.bin)

    print("Controlled error-density datasets (issue #44)")
    print("  output    : %s" % args.output_dir)
    print("  seed      : %d" % args.seed)
    print("  sizes     : %s" % ", ".join(label for label, _ in sizes))
    print("  densities : %s" % ", ".join("%g%%" % d for d in densities))
    print("  encodings : %s" % ", ".join(name for name, _ in encodings))
    print("  verify    : scalar, --simd, and scripts/utf16_oracle.py must all agree")
    print()

    for name, _ in encodings:
        os.makedirs(os.path.join(args.output_dir, name), exist_ok=True)

    rows = []
    generated = skipped = 0
    print("%-42s %-9s %10s %12s %8s" % ("dataset", "encoding", "bytes", "errors", "status"))
    for size_label, size_bytes in sizes:
        for density in densities:
            units = target = mix = None
            for encoding_name, big_endian in encodings:
                name = dataset_name(size_label, density, encoding_name)
                path = os.path.join(args.output_dir, encoding_name, name)

                if os.path.exists(path) and not args.overwrite:
                    data = open(path, "rb").read()
                    expected = target_error_count(len(data) // 2, density)
                    results, disagree = verify(args.bin, path, data, big_endian, expected)
                    if disagree:
                        raise SystemExit(
                            "ERROR: existing %s disagrees with its target (%d): %s\n"
                            "       Regenerate with --overwrite."
                            % (name, expected, disagree))
                    rows.append(manifest_row(name, encoding_name, data, density,
                                             results["oracle"], args.seed))
                    skipped += 1
                    print("%-42s %-9s %10d %12d %8s"
                          % (name, encoding_name, len(data), results["oracle"], "kept"))
                    continue

                if units is None:
                    units, target, mix = build_dataset(size_bytes, density, args.seed)
                data = oracle.encode_code_units(units, big_endian)

                # Write to a temporary file first: a dataset that fails verification must
                # never be left behind for someone else to pick up.
                handle, temp_path = tempfile.mkstemp(
                    dir=os.path.join(args.output_dir, encoding_name), suffix=".tmp")
                try:
                    with os.fdopen(handle, "wb") as out:
                        out.write(data)
                    results, disagree = verify(args.bin, temp_path, data, big_endian, target)
                    if disagree:
                        raise SystemExit(
                            "ERROR: %s: expected %d ill-formed code units, but %s\n"
                            "       Nothing further was written."
                            % (name, target,
                               ", ".join("%s reported %d" % (k, v)
                                         for k, v in sorted(disagree.items()))))
                    os.replace(temp_path, path)
                    temp_path = None
                finally:
                    if temp_path is not None and os.path.exists(temp_path):
                        os.remove(temp_path)

                rows.append(manifest_row(name, encoding_name, data, density, target,
                                         args.seed))
                generated += 1
                print("%-42s %-9s %10d %12d %8s"
                      % (name, encoding_name, len(data), target, "ok"))
            if mix:
                print("    pattern mix: %s"
                      % ", ".join("%s x%d" % (k, v) for k, v in sorted(mix.items())))
            elif density > 0 and target == 0:
                print("    note: %g%% of %d code units rounds to 0 ill-formed units, so "
                      "this dataset is well-formed" % (density, size_bytes // 2))

    manifest_path = os.path.join(args.output_dir, "manifest.csv")
    rows = merge_manifest(manifest_path, rows, args.output_dir, encodings)
    with open(manifest_path, "w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print()
    print("%d datasets generated, %d kept, %d rows in %s"
          % (generated, skipped, len(rows), manifest_path))

    listed = set(row["filename"] for row in rows)
    on_disk = set()
    for folder in ("utf16le", "utf16be"):
        directory = os.path.join(args.output_dir, folder)
        if os.path.isdir(directory):
            on_disk.update(n for n in os.listdir(directory) if n.endswith(".bin"))
    unlisted = sorted(on_disk - listed)
    if unlisted:
        print("NOTE: %d dataset file(s) on disk are not described by the manifest, e.g. %s."
              % (len(unlisted), ", ".join(unlisted[:3])))
        print("      Re-run over those sizes/densities (or delete them) to refresh it.")
    print("every dataset verified: scalar == --simd == oracle == target error count")
    return 0


def merge_manifest(manifest_path, rows, output_dir, encodings):
    """Describe every dataset present, not just the ones this invocation selected.

    A narrow run (say --quick, or a single density) must not drop rows for datasets that are
    still sitting on disk -- downstream consumers read this file to find out what exists.
    Rows for files that have since been deleted are dropped.
    """
    merged = dict((row["filename"], row) for row in rows)
    if os.path.exists(manifest_path):
        with open(manifest_path, newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("filename") in merged:
                    continue
                folder = "utf16be" if row.get("encoding") == "UTF-16BE" else "utf16le"
                if os.path.exists(os.path.join(output_dir, folder, row["filename"])):
                    merged[row["filename"]] = row
    return [merged[name] for name in sorted(merged)]


def manifest_row(name, encoding, data, density, errors, seed):
    return {"filename": name,
            "encoding": "UTF-16LE" if encoding == "utf16le" else "UTF-16BE",
            "size_bytes": len(data),
            "code_units": len(data) // 2,
            "target_density": "%g" % density,
            "actual_error_count": errors,
            "seed": seed}


if __name__ == "__main__":
    sys.exit(main())
