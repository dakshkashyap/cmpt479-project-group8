#!/usr/bin/env bash
#
# test_multilingual_corpus.sh -- correctness suite for the multilingual / emoji corpus
# (issue #40).
#
#     ./scripts/test_multilingual_corpus.sh
#
# The corpus under tests/corpus/ is a set of VALID UTF-16 fixtures: ten languages, mixed
# multilingual paragraphs, emoji (single, dense, skin tone modifiers, flags, variation
# selectors, ZWJ family/profession sequences), non-emoji supplementary-plane characters,
# and the degenerate empty / one-code-unit inputs. Every dataset exists in both UTF-16LE
# and UTF-16BE, generated from the same source text with no byte order mark by
# scripts/generate_multilingual_corpus.py.
#
# It tests UTF-16 WELL-FORMEDNESS -- surrogate-pair structure -- and nothing else. A ZWJ
# family sequence matters here because it is a run of BMP joiners between supplementary
# code points, not because it should render as one glyph; the validator has no opinion on
# grapheme clusters or emoji semantics.
#
# Every dataset is checked ten ways:
#
#   1. LE zero errors        scalar, --simd, --emit-error-marks, and the TwoLevelScan
#                            consumer (which must also print no error position at all)
#   2. BE zero errors        the same paths under --be
#   3. segment sizes         the emoji/mixed datasets again at -segment-size=1,13,64, so a
#                            surrogate pair split across a segment boundary still validates
#   4. cross-endian bytes    the BE file is exactly the byte swap of the LE file
#   5. cross-endian text     both files decode to the identical Unicode string
#   6. reference agreement   benchmarks/llmask_reference.py reports 0 error bits and no odd
#                            trailing byte for both encodings
#   7. surrogate structure   an independent walk of the raw code units: every high surrogate
#                            is followed by a low, every low is preceded by a high
#   8. no BOM                neither file starts with U+FEFF
#   9. manifest agreement    byte size, SHA-256, code-unit / code-point / surrogate-pair
#                            counts and expected_error_count=0 match the files on disk
#  10. coverage              the dataset actually contains what its category claims (skin
#                            tone modifiers, regional indicators, tag characters, variation
#                            selectors, ZWJ, the right script blocks, ...)
#
# Plus, corpus-wide: the generator is run twice into two fresh temporary directories and
# both runs must be byte-identical to each other AND to the committed fixtures, so the
# corpus is reproducible and cannot silently drift from the generator.
#
# Nothing is written outside a mktemp directory that is removed on exit; the committed
# fixtures are only ever read.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
BIN="$PARABIX_DIR/build/bin/utf16validate"
CORPUS="${CORPUS_DIR:-$REPO_ROOT/tests/corpus}"

[ -x "$BIN" ] || {
    echo "ERROR: utf16validate not found at $BIN" >&2
    echo "       Run ./scripts/setup_parabix.sh first." >&2
    exit 1
}
[ -f "$CORPUS/corpus_manifest.json" ] || {
    echo "ERROR: no corpus manifest at $CORPUS/corpus_manifest.json" >&2
    echo "       Run python3 scripts/generate_multilingual_corpus.py first." >&2
    exit 1
}
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required." >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

BIN="$BIN" WORK="$WORK" CORPUS="$CORPUS" REPO_ROOT="$REPO_ROOT" python3 - <<'PY'
import hashlib
import json
import os
import subprocess
import sys

BIN = os.environ["BIN"]
WORK = os.environ["WORK"]
CORPUS = os.environ["CORPUS"]
REPO_ROOT = os.environ["REPO_ROOT"]
GENERATOR = os.path.join(REPO_ROOT, "scripts", "generate_multilingual_corpus.py")
REF = os.path.join(REPO_ROOT, "benchmarks", "llmask_reference.py")

sys.path.insert(0, os.path.join(REPO_ROOT, "benchmarks"))
import llmask_reference

HIGH_LO, HIGH_HI = 0xD800, 0xDBFF
LOW_LO, LOW_HI = 0xDC00, 0xDFFF

passed = failed = 0


def ok(name, detail=""):
    global passed
    passed += 1
    print("  PASS %-40s %s" % (name, detail))


def bad(name, detail):
    global failed
    failed += 1
    print("  FAIL %-40s %s" % (name, detail))


def check_field(name, actual, expected):
    if actual == expected:
        ok(name, "%s" % (actual,))
    else:
        bad(name, "%s != expected %s" % (actual, expected))


def run(args):
    return subprocess.run([BIN] + args, capture_output=True, text=True,
                          check=True).stdout


def error_count(path, extra=None):
    return int(run((extra or []) + [path]).strip().split("=")[-1])


def scan_output(path, extra):
    return run(["-emit-error-marks", "-scan-error-marks", "-thread-num=1"]
               + (extra or []) + [path])


# --- Independent structural checks (no codec, no reference implementation) ------

def code_units(data, big_endian):
    if big_endian:
        return [(data[2 * k] << 8) | data[2 * k + 1] for k in range(len(data) // 2)]
    return [data[2 * k] | (data[2 * k + 1] << 8) for k in range(len(data) // 2)]


def surrogate_problems(units, label):
    """Walk the code units and report any ill-formed surrogate structure.

    Written from the definition and deliberately independent of both the validator and
    benchmarks/llmask_reference.py: this issue must not add malformed data, so a failure
    here means the corpus itself is wrong, not that a kernel is wrong.
    """
    problems = []
    pairs = 0
    k = 0
    while k < len(units):
        unit = units[k]
        if HIGH_LO <= unit <= HIGH_HI:
            if k + 1 < len(units) and LOW_LO <= units[k + 1] <= LOW_HI:
                pairs += 1
                k += 2
                continue
            problems.append("%s: high surrogate U+%04X at unit %d not followed by a low"
                            % (label, unit, k))
        elif LOW_LO <= unit <= LOW_HI:
            problems.append("%s: low surrogate U+%04X at unit %d with no high before it"
                            % (label, unit, k))
        k += 1
    return problems, pairs


def byteswap(data):
    out = bytearray(data)
    out[0::2], out[1::2] = data[1::2], data[0::2]
    return bytes(out)


# --- Coverage predicates -------------------------------------------------------
# What each category has to actually contain. These keep the corpus honest: a dataset
# named "emoji_flags" that lost its tag sequences would still validate, but would no
# longer be testing what it claims to test.

def has_range(text, lo, hi):
    return any(lo <= ord(ch) <= hi for ch in text)


COVERAGE = {
    "english_ascii":       lambda t: all(ord(c) < 0x80 for c in t),
    "latin_accented":      lambda t: has_range(t, 0x00C0, 0x017F),
    "punjabi":             lambda t: has_range(t, 0x0A00, 0x0A7F),
    "hindi":               lambda t: has_range(t, 0x0900, 0x097F),
    "arabic":              lambda t: has_range(t, 0x0600, 0x06FF),
    "hebrew":              lambda t: has_range(t, 0x0590, 0x05FF),
    "chinese":             lambda t: has_range(t, 0x4E00, 0x9FFF),
    "japanese":            lambda t: (has_range(t, 0x3040, 0x309F)
                                      and has_range(t, 0x30A0, 0x30FF)
                                      and has_range(t, 0x4E00, 0x9FFF)),
    "korean":              lambda t: has_range(t, 0xAC00, 0xD7A3),
    "thai":                lambda t: has_range(t, 0x0E00, 0x0E7F),
    "mixed_multilingual":  lambda t: all(has_range(t, lo, hi) for lo, hi in
                                         [(0x0041, 0x007A), (0x00C0, 0x017F),
                                          (0x0900, 0x097F), (0x0A00, 0x0A7F),
                                          (0x0590, 0x05FF), (0x0600, 0x06FF),
                                          (0x0E00, 0x0E7F), (0x3040, 0x30FF),
                                          (0x4E00, 0x9FFF), (0xAC00, 0xD7A3)]),
    "emoji_single":        lambda t: len(t) == 1 and ord(t) > 0xFFFF,
    "emoji_heavy":         lambda t: sum(1 for c in t if ord(c) > 0xFFFF) > 100,
    "emoji_skin_tone":     lambda t: all(chr(cp) in t for cp in range(0x1F3FB, 0x1F400)),
    "emoji_flags":         lambda t: (has_range(t, 0x1F1E6, 0x1F1FF)         # regional
                                      and "\U0001F3F4" in t                  # waving flag
                                      and has_range(t, 0xE0020, 0xE007F)),   # tags
    "variation_selectors": lambda t: ("\ufe0f" in t and "\ufe0e" in t
                                      and "\u20e3" in t                      # keycap
                                      and has_range(t, 0xE0100, 0xE01EF)),   # ideographic
    "emoji_zwj":           lambda t: (t.count("\u200d") > 10
                                      and "\U0001F468\u200d\U0001F469" in t   # family
                                      and "\U0001F469\u200d\U0001F4BB" in t   # profession
                                      and has_range(t, 0x1F3FB, 0x1F3FF)),    # + skin tone
    "plane_mix":           lambda t: (has_range(t, 0x0020, 0xFFFF)           # BMP
                                      and has_range(t, 0x20000, 0x2FFFF)     # CJK ext
                                      and has_range(t, 0x1D000, 0x1DFFF)     # math/music
                                      and has_range(t, 0x1F300, 0x1FAFF)),   # emoji
    "empty":               lambda t: t == "",
    "minimal_ascii":       lambda t: len(t) == 1 and ord(t) < 0x80,
    "minimal_bmp":         lambda t: len(t) == 3 and all(ord(c) <= 0xFFFF for c in t),
}

# Issue #40 asks for these categories by name; the corpus must cover all of them. The
# last three are fixture-only: a degenerate size is a validator edge case, not something
# to grow to a benchmark size, so the bench profile leaves them out on purpose.
REQUIRED = [
    ("English / ASCII", ["english_ascii"], "both"),
    ("Accented Latin", ["latin_accented"], "both"),
    ("Punjabi", ["punjabi"], "both"),
    ("Hindi", ["hindi"], "both"),
    ("Arabic", ["arabic"], "both"),
    ("Hebrew", ["hebrew"], "both"),
    ("Chinese", ["chinese"], "both"),
    ("Japanese", ["japanese"], "both"),
    ("Korean", ["korean"], "both"),
    ("Thai", ["thai"], "both"),
    ("Mixed multilingual", ["mixed_multilingual"], "both"),
    ("Emoji-heavy", ["emoji_heavy"], "both"),
    ("Skin-tone modifiers", ["emoji_skin_tone"], "both"),
    ("Flags", ["emoji_flags"], "both"),
    ("Variation selectors", ["variation_selectors"], "both"),
    ("ZWJ family / profession", ["emoji_zwj"], "both"),
    ("BMP + supplementary + emoji", ["plane_mix"], "both"),
    ("Single non-BMP emoji", ["emoji_single"], "fixtures"),
    ("Empty input", ["empty"], "fixtures"),
    ("Very small valid input", ["minimal_ascii", "minimal_bmp", "emoji_single"],
     "fixtures"),
]

SEGMENT_SIZES = (1, 13, 64)
# Datasets whose surrogate pairs must survive the cross-segment carry.
SEGMENTED = ("emoji_heavy", "emoji_flags", "emoji_zwj", "plane_mix", "emoji_single")
SEGMENT_SIZE_LIMIT = 64 * 1024      # only worth forcing tiny segments on small files


# --- Per-dataset suite ---------------------------------------------------------

manifest = json.load(open(os.path.join(CORPUS, "corpus_manifest.json")))

print("Multilingual / emoji UTF-16 corpus suite (issue #40)")
print("  corpus: %s  (%d datasets, profile=%s, seed=%s)"
      % (CORPUS, len(manifest["datasets"]), manifest["profile"], manifest["seed"]))
print("  UTF-16 well-formedness only: surrogate structure, not grapheme/emoji semantics.")
print()

print("== manifest ==")
check_field("manifest_expects_zero_errors", manifest["expected_error_count"], 0)
check_field("manifest_declares_no_bom", manifest["byte_order_mark"], False)
check_field("manifest_schema_version", manifest["schema_version"], 1)
check_field("manifest_encodings", manifest["encodings"], ["UTF-16LE", "UTF-16BE"])

print()
print("== datasets ==")
seen = set()
for meta in manifest["datasets"]:
    name = meta["dataset"]
    seen.add(name)
    problems = []

    le_info = meta["files"]["utf16le"]
    be_info = meta["files"]["utf16be"]
    le_path = os.path.join(CORPUS, le_info["file"])
    be_path = os.path.join(CORPUS, be_info["file"])
    if not (os.path.exists(le_path) and os.path.exists(be_path)):
        bad(name, "missing file(s) for dataset")
        continue
    le = open(le_path, "rb").read()
    be = open(be_path, "rb").read()

    # 1. UTF-16LE: every validation path reports zero errors.
    for mode, extra in (("scalar", []), ("simd", ["-simd"]),
                        ("marks", ["-emit-error-marks"])):
        count = error_count(le_path, extra)
        if count != 0:
            problems.append("LE %s: errorCount = %d" % (mode, count))
    scan = scan_output(le_path, [])
    if int(scan.strip().split("=")[-1]) != 0:
        problems.append("LE scan: errorCount != 0")
    if any(line.startswith("errpos") for line in scan.splitlines()):
        problems.append("LE scan: reported an error position in valid input")

    # 2. UTF-16BE: the same paths under --be.
    for mode, extra in (("scalar", ["-be"]), ("simd", ["-be", "-simd"]),
                        ("marks", ["-be", "-emit-error-marks"])):
        count = error_count(be_path, extra)
        if count != 0:
            problems.append("BE %s: errorCount = %d" % (mode, count))
    scan = scan_output(be_path, ["-be"])
    if int(scan.strip().split("=")[-1]) != 0:
        problems.append("BE scan: errorCount != 0")
    if any(line.startswith("errpos") for line in scan.splitlines()):
        problems.append("BE scan: reported an error position in valid input")

    # 3. Forced segment sizes on the pair-dense datasets. Skipped for a benchmark-sized
    #    corpus: -segment-size=1 on a megabyte of input is pathologically slow and adds
    #    nothing the fixture-sized run has not already covered.
    if name in SEGMENTED and len(le) <= SEGMENT_SIZE_LIMIT:
        for size in SEGMENT_SIZES:
            seg = ["-segment-size=%d" % size]
            if error_count(le_path, seg) != 0:
                problems.append("LE ss=%d: errorCount != 0" % size)
            if error_count(be_path, ["-be"] + seg) != 0:
                problems.append("BE ss=%d: errorCount != 0" % size)

    # 4/5. Cross-endian identity, at the byte level and after decoding.
    if byteswap(le) != be:
        problems.append("BE file is not the byte swap of the LE file")
    # surrogatepass keeps the decode lossless and, more importantly, keeps a broken
    # fixture reportable as a FAIL instead of a traceback: a lone surrogate decodes to
    # itself rather than raising, and the structural walk below is what rejects it.
    le_text = le.decode("utf-16-le", errors="surrogatepass")
    be_text = be.decode("utf-16-be", errors="surrogatepass")
    if le_text != be_text:
        problems.append("LE and BE decode to different text")

    # 6. The project's independent reference validator.
    for label, data, big in (("LE", le, False), ("BE", be, True)):
        _, _, error_bits, odd = llmask_reference.llmasks(data, big)
        if error_bits or odd:
            problems.append("%s reference: errorbits=%d oddtrailingbyte=%d"
                            % (label, error_bits, odd))

    # 7. Surrogate structure, walked directly.
    le_units = code_units(le, False)
    be_units = code_units(be, True)
    struct_problems, le_pairs = surrogate_problems(le_units, "LE")
    problems.extend(struct_problems)
    struct_problems, be_pairs = surrogate_problems(be_units, "BE")
    problems.extend(struct_problems)
    if le_units != be_units:
        problems.append("LE and BE code-unit sequences differ")

    # 8. No byte order mark on either encoding.
    if le[:2] == b"\xff\xfe" or be[:2] == b"\xfe\xff":
        problems.append("file starts with a byte order mark")

    # 9. Metadata consistency with the actual bytes.
    for label, data, info in (("LE", le, le_info), ("BE", be, be_info)):
        if len(data) != info["bytes"]:
            problems.append("%s bytes: %d on disk vs %d in manifest"
                            % (label, len(data), info["bytes"]))
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            problems.append("%s sha256 mismatch" % label)
    if meta["bytes"] != len(le):
        problems.append("dataset bytes %d != file size %d" % (meta["bytes"], len(le)))
    if meta["code_units"] != len(le) // 2:
        problems.append("code_units %d != %d" % (meta["code_units"], len(le) // 2))
    if meta["code_points"] != len(le_text):
        problems.append("code_points %d != %d" % (meta["code_points"], len(le_text)))
    if meta["surrogate_pairs"] != le_pairs or be_pairs != le_pairs:
        problems.append("surrogate_pairs %d != %d observed"
                        % (meta["surrogate_pairs"], le_pairs))
    if meta["supplementary_code_points"] != le_pairs:
        problems.append("supplementary_code_points != surrogate pairs")
    if meta["bmp_code_points"] + meta["supplementary_code_points"] != meta["code_points"]:
        problems.append("bmp + supplementary != code_points")
    if meta["code_points"] + meta["surrogate_pairs"] != meta["code_units"]:
        problems.append("code_points + surrogate_pairs != code_units")
    if meta["expected_error_count"] != 0:
        problems.append("expected_error_count != 0")

    # 10. The dataset contains what its name claims.
    predicate = COVERAGE.get(name)
    if predicate is None:
        problems.append("no coverage predicate for this dataset")
    elif not predicate(le_text):
        problems.append("content does not match its category")

    if not problems:
        ok(name, "%6d bytes %5d units %4d pairs  LE+BE errorCount = 0"
                 % (len(le), len(le) // 2, le_pairs))
    else:
        bad(name, "; ".join(problems))

print()
print("== required coverage ==")
for label, candidates, profiles in REQUIRED:
    if profiles != "both" and profiles != manifest["profile"]:
        print("  ---- %-40s (fixtures profile only)" % label)
        continue
    present = [c for c in candidates if c in seen]
    if present:
        ok(label, ", ".join(present))
    else:
        bad(label, "no dataset for: %s" % ", ".join(candidates))

print()
print("== reproducibility ==")
# The generator is re-run from the manifest's own profile/seed/size, so this works
# whether CORPUS points at the committed fixtures or at a benchmark-sized corpus.
PROFILE_ARGS = ["--seed", str(manifest["seed"])]
if manifest["profile"] == "bench":
    PROFILE_ARGS += ["--profile", "bench",
                     "--size-mib", repr(2.0 * manifest["target_code_units"] / (1 << 20))]


def snapshot(directory):
    return {n: open(os.path.join(directory, n), "rb").read()
            for n in sorted(os.listdir(directory))}


if manifest["profile"] == "fixtures":
    # Small enough to materialize twice: two fresh generations must be identical to
    # each other and to what is committed.
    runs = []
    for i in (1, 2):
        out = os.path.join(WORK, "gen%d" % i)
        subprocess.run([sys.executable, GENERATOR, "--output-dir", out, "--quiet"]
                       + PROFILE_ARGS, capture_output=True, text=True, check=True)
        runs.append(out)

    first, second = snapshot(runs[0]), snapshot(runs[1])
    committed = snapshot(CORPUS)
    if first == second:
        ok("regeneration_is_byte_identical",
           "%d files identical across two runs" % len(first))
    else:
        bad("regeneration_is_byte_identical", "two generator runs differ")

    drift = ([n for n in first if n not in committed]
             + [n for n in committed if n not in first]
             + [n for n in first if n in committed and first[n] != committed[n]])
    if not drift:
        ok("committed_fixtures_match_generator", "%d files" % len(committed))
    else:
        bad("committed_fixtures_match_generator", ", ".join(sorted(set(drift))[:6]))
else:
    # A benchmark-sized corpus is not copied twice into a temp directory; --check
    # regenerates it in memory and compares against the bytes on disk, which is the
    # same guarantee without tens of megabytes of scratch.
    print("  ---- %-40s (bench profile: covered by --check below)"
          % "regeneration_is_byte_identical")

check = subprocess.run([sys.executable, GENERATOR, "--check", "--output-dir", CORPUS]
                       + PROFILE_ARGS, capture_output=True, text=True)
if check.returncode == 0:
    ok("generator_check_mode", check.stdout.strip().splitlines()[-1])
else:
    bad("generator_check_mode", check.stdout.strip().replace("\n", " "))

print()
print("%d passed, %d failed" % (passed, failed))
if failed:
    print("MULTILINGUAL CORPUS TESTS FAILED")
    sys.exit(1)
print("ALL MULTILINGUAL CORPUS TESTS PASSED")
PY
