#!/usr/bin/env python3
"""Generate the deterministic multilingual / emoji UTF-16 corpus (issue #40).

Every dataset is written twice -- once as UTF-16LE and once as UTF-16BE -- from the
same curated source text, with no byte order mark. The two encodings therefore hold
exactly the same code points and are byte swaps of each other, which is what lets the
test suite tie the `--be` path back to the LE path.

Everything here is VALID by construction: the source text is ordinary Python text, so
no lone surrogate can appear (Python's codecs refuse to encode one), and each file is
re-validated against the project's independent reference validator
(`benchmarks/llmask_reference.py`) before it is written. Malformed input is out of
scope for this corpus; it is produced by `benchmarks/generate_utf16_benchmark.py
--error-patterns` instead.

What is being tested is UTF-16 WELL-FORMEDNESS -- surrogate-pair structure -- not
grapheme clustering or emoji semantics. A ZWJ family sequence is interesting here
because it is a long run of BMP joiners between supplementary-plane code points, not
because it renders as one glyph; the validator has no opinion on how it renders.

Two profiles:

  fixtures   small, representative files, committed under tests/corpus/ so that the
             suite has fixed inputs that do not have to be regenerated to run.
  bench      the same categories grown to a benchmark-sized target, written to
             benchmarks/data/multilingual_corpus/ (git-ignored, reproducible from
             this script plus the seed).

Both profiles write a single `corpus_manifest.json` describing every dataset: byte
size, code-unit count, code-point count, surrogate-pair count, source category, seed,
digest, and the expected validation error count (always 0).

Examples:
    # regenerate the committed fixtures (default)
    python3 scripts/generate_multilingual_corpus.py

    # verify the committed fixtures still match this script, writing nothing
    python3 scripts/generate_multilingual_corpus.py --check

    # benchmark-sized datasets, 8 MiB per encoding, git-ignored
    python3 scripts/generate_multilingual_corpus.py --profile bench --size-mib 8
"""

import argparse
import hashlib
import json
import os
import random
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The project's independent reference validator. Reused rather than re-implemented so
# the corpus is checked by the same code the correctness suites trust.
sys.path.insert(0, os.path.join(REPO_ROOT, "benchmarks"))
import llmask_reference                                     # noqa: E402

MIB = 1024 * 1024
SCHEMA_VERSION = 1
DEFAULT_SEED = 479
FIXTURE_TARGET_UNITS = 512          # ~1 KiB per encoding: small enough to commit
DEFAULT_BENCH_MIB = 1

ENCODINGS = (
    ("utf16le", "UTF-16LE", "utf-16-le"),
    ("utf16be", "UTF-16BE", "utf-16-be"),
)


# --- Source text ---------------------------------------------------------------
# Written with explicit \u / \U escapes so this file stays pure ASCII and cannot be
# damaged by a re-encoding round trip (same convention as scripts/test_utf16validate.sh).
# An ASCII gloss above each block says what the text is.

ENGLISH = [
    "The quick brown fox jumps over the lazy dog.",
    "Pack my box with five dozen liquor jugs, 0123456789!",
    "UTF-16 validation checks surrogate structure, not glyph shaping.",
]

# "Zurich, Koln und Geneve ..." / "Une naive facon ..." / "AEro, Lodz, Nandu, Strasse ..."
LATIN_ACCENTED = [
    "Z\u00fcrich, K\u00f6ln und Gen\u00e8ve liegen nicht weit auseinander.",
    "Une na\u00efve fa\u00e7on de pr\u00e9parer un caf\u00e9 \u00e0 Montr\u00e9al.",
    "\u00c6r\u00f8, \u0141\u00f3d\u017a, \u00d1and\u00fa, Stra\u00dfe, Portugu\u00eas, espa\u00f1ol.",
]

# Gurmukhi (Punjabi): "sat sri akal, duniya" / "punjabi is written in the Gurmukhi
# script" / "thank you very much"
PUNJABI = [
    "\u0a38\u0a24 \u0a38\u0a4d\u0a30\u0a40 \u0a05\u0a15\u0a3e\u0a32, \u0a26\u0a41\u0a28\u0a40\u0a06\u0964",
    "\u0a2a\u0a70\u0a1c\u0a3e\u0a2c\u0a40 \u0a2d\u0a3e\u0a38\u0a3c\u0a3e \u0a17\u0a41\u0a30\u0a2e\u0a41\u0a16\u0a40 "
    "\u0a32\u0a3f\u0a2a\u0a40 \u0a35\u0a3f\u0a71\u0a1a \u0a32\u0a3f\u0a16\u0a40 \u0a1c\u0a3e\u0a02\u0a26\u0a40 \u0a39\u0a48\u0964",
    "\u0a2c\u0a39\u0a41\u0a24 \u0a2c\u0a39\u0a41\u0a24 \u0a27\u0a70\u0a28\u0a35\u0a3e\u0a26\u0964",
]

# Devanagari (Hindi): "namaste duniya" / "Hindi is written in the Devanagari script" /
# "thank you very much"
HINDI = [
    "\u0928\u092e\u0938\u094d\u0924\u0947 \u0926\u0941\u0928\u093f\u092f\u093e\u0964",
    "\u0939\u093f\u0928\u094d\u0926\u0940 \u092d\u093e\u0937\u093e \u0926\u0947\u0935\u0928\u093e\u0917\u0930\u0940 "
    "\u0932\u093f\u092a\u093f \u092e\u0947\u0902 \u0932\u093f\u0916\u0940 \u091c\u093e\u0924\u0940 \u0939\u0948\u0964",
    "\u092c\u0939\u0941\u0924 \u092c\u0939\u0941\u0924 \u0927\u0928\u094d\u092f\u0935\u093e\u0926\u0964",
]

# Arabic (right-to-left): "hello world" / "Arabic is written right to left" / "thank you"
ARABIC = [
    "\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645.",
    "\u0627\u0644\u0644\u063a\u0629 \u0627\u0644\u0639\u0631\u0628\u064a\u0629 \u062a\u0643\u062a\u0628 "
    "\u0645\u0646 \u0627\u0644\u064a\u0645\u064a\u0646 \u0625\u0644\u0649 \u0627\u0644\u064a\u0633\u0627\u0631.",
    "\u0634\u0643\u0631\u0627 \u062c\u0632\u064a\u0644\u0627 \u0644\u0643.",
]

# Hebrew (right-to-left): "hello world" / "Hebrew is written right to left" / "thank you"
HEBREW = [
    "\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd.",
    "\u05d4\u05e9\u05e4\u05d4 \u05d4\u05e2\u05d1\u05e8\u05d9\u05ea \u05e0\u05db\u05ea\u05d1\u05ea "
    "\u05de\u05d9\u05de\u05d9\u05df \u05dc\u05e9\u05de\u05d0\u05dc.",
    "\u05ea\u05d5\u05d3\u05d4 \u05e8\u05d1\u05d4 \u05dc\u05da.",
]

# Chinese (Han, all BMP): "hello world" / "this is a Chinese test corpus for validation" /
# "Han characters belong to the Basic Multilingual Plane"
CHINESE = [
    "\u4f60\u597d\uff0c\u4e16\u754c\u3002",
    "\u8fd9\u662f\u4e00\u4e2a\u7528\u4e8e\u9a8c\u8bc1\u7684\u4e2d\u6587\u6d4b\u8bd5\u8bed\u6599\u3002",
    "\u6c49\u5b57\u5c5e\u4e8e\u57fa\u672c\u591a\u6587\u79cd\u5e73\u9762\u3002",
]

# Japanese (hiragana, katakana, kanji): "hello world" / "this is a Japanese test
# sentence" / "it contains hiragana, katakana and kanji"
JAPANESE = [
    "\u3053\u3093\u306b\u3061\u306f\u3001\u4e16\u754c\u3002",
    "\u3053\u308c\u306f\u65e5\u672c\u8a9e\u306e\u30c6\u30b9\u30c8\u6587\u3067\u3059\u3002",
    "\u3072\u3089\u304c\u306a\u3001\u30ab\u30bf\u30ab\u30ca\u3001\u6f22\u5b57\u3092\u542b\u307f\u307e\u3059\u3002",
]

# Korean (Hangul syllables, U+AC00..U+D7A3 -- BMP, just below the surrogate block):
# "hello world" / "this is a Korean test sentence" / "Hangul syllables are in the BMP"
KOREAN = [
    "\uc548\ub155\ud558\uc138\uc694, \uc138\uacc4.",
    "\uc774\uac83\uc740 \ud55c\uad6d\uc5b4 \uc2dc\ud5d8 \ubb38\uc7a5\uc785\ub2c8\ub2e4.",
    "\ud55c\uae00 \uc74c\uc808\uc740 \uae30\ubcf8 \ub2e4\uad6d\uc5b4 \ud3c9\uba74\uc5d0 \uc788\uc2b5\ub2c8\ub2e4.",
]

# Thai (no word spacing, combining vowels/tone marks): "hello world" / "this is Thai
# test text" / "thank you very much"
THAI = [
    "\u0e2a\u0e27\u0e31\u0e2a\u0e14\u0e35\u0e0a\u0e32\u0e27\u0e42\u0e25\u0e01",
    "\u0e19\u0e35\u0e48\u0e04\u0e37\u0e2d\u0e02\u0e49\u0e2d\u0e04\u0e27\u0e32\u0e21\u0e17\u0e14\u0e2a\u0e2d\u0e1a"
    "\u0e20\u0e32\u0e29\u0e32\u0e44\u0e17\u0e22",
    "\u0e02\u0e2d\u0e1a\u0e04\u0e38\u0e13\u0e21\u0e32\u0e01\u0e04\u0e23\u0e31\u0e1a",
]

# --- Emoji and other supplementary-plane material ------------------------------
# Names are given so the escapes stay readable; each U+1xxxx code point is one
# surrogate pair in UTF-16.

ZWJ = "\u200d"                          # ZERO WIDTH JOINER
VS15, VS16 = "\ufe0e", "\ufe0f"         # text / emoji presentation selectors

# GRINNING FACE, PARTY POPPER, ROCKET, EARTH GLOBE EUROPE-AFRICA, SNAKE, FIRE,
# SPARKLES (BMP!), LIGHT BULB, DIRECT HIT, SLICE OF PIZZA, TROPHY, ROBOT, BOOKS
EMOJI_SINGLE = "\U0001f600"
EMOJI_HEAVY = [
    "\U0001f600\U0001f389\U0001f680\U0001f30d\U0001f40d\U0001f525",
    "\U0001f4a1\U0001f3af\U0001f355\U0001f3c6\U0001f916\U0001f4da",
    "build \U0001f680 test \u2728 ship \U0001f389 repeat \U0001f501",
    "\U0001f9ea\U0001f9ee\U0001f9f0\U0001f9ff\U0001fa78\U0001fa90",
]

# EMOJI MODIFIER FITZPATRICK TYPE-1-2 .. TYPE-6 (U+1F3FB..U+1F3FF) applied to
# supplementary bases (THUMBS UP, WAVING HAND, RAISING HANDS, HANDSHAKE) and to a
# BMP base (RAISED HAND, U+270B) -- so a modifier pair follows a single code unit too.
SKIN_TONE_BASES = ["\U0001f44d", "\U0001f44b", "\U0001f64c", "\U0001f91d", "\u270b"]
SKIN_TONES = ["\U0001f3fb", "\U0001f3fc", "\U0001f3fd", "\U0001f3fe", "\U0001f3ff"]

# Regional indicator pairs (U+1F1E6..U+1F1FF): CA, JP, IN, BR, DE, KR, TH, MX --
# four surrogate pairs' worth of code units per flag.
FLAG_PAIRS = [
    ("\U0001f1e8\U0001f1e6", "CA"), ("\U0001f1ef\U0001f1f5", "JP"),
    ("\U0001f1ee\U0001f1f3", "IN"), ("\U0001f1e7\U0001f1f7", "BR"),
    ("\U0001f1e9\U0001f1ea", "DE"), ("\U0001f1f0\U0001f1f7", "KR"),
    ("\U0001f1f9\U0001f1ed", "TH"), ("\U0001f1f2\U0001f1fd", "MX"),
]
# Tag sequences: WAVING BLACK FLAG (U+1F3F4) + TAG LATIN letters (U+E0020 block) +
# CANCEL TAG (U+E007F). Every tag character is itself a surrogate pair, so these are
# long uninterrupted runs of pairs.
TAG_FLAGS = [
    ("gbsct", "Scotland"), ("gbwls", "Wales"), ("gbeng", "England"),
]

# Variation selectors: emoji presentation (U+FE0F) and text presentation (U+FE0E) on
# BMP bases, a keycap sequence (digit + U+FE0F + U+20E3), and an ideographic variation
# sequence using U+E0100 -- a supplementary-plane selector, i.e. a surrogate pair.
VARIATION_ITEMS = [
    "\u2764" + VS16, "\u2600" + VS16, "\u26a0" + VS16, "\u2708" + VS16,
    "\u2764" + VS15, "\u2709" + VS15, "\u260e" + VS15,
    "1" + VS16 + "\u20e3", "7" + VS16 + "\u20e3", "#" + VS16 + "\u20e3",
    "\u845b\U000e0100", "\u908a\U000e0101",
]

# ZWJ sequences. Family and couple sequences alternate supplementary-plane people with
# the BMP joiner; profession sequences add a skin tone modifier before the joiner.
ZWJ_SEQUENCES = [
    ("family (man, woman, girl, boy)",
     ZWJ.join(["\U0001f468", "\U0001f469", "\U0001f467", "\U0001f466"])),
    ("family (woman, woman, girl)",
     ZWJ.join(["\U0001f469", "\U0001f469", "\U0001f467"])),
    ("family (man, boy, boy)",
     ZWJ.join(["\U0001f468", "\U0001f466", "\U0001f466"])),
    ("couple with heart",
     "\U0001f469" + ZWJ + "\u2764" + VS16 + ZWJ + "\U0001f468"),
    ("technologist", "\U0001f469" + ZWJ + "\U0001f4bb"),
    ("firefighter", "\U0001f468" + ZWJ + "\U0001f692"),
    ("astronaut, medium skin tone",
     "\U0001f469\U0001f3fd" + ZWJ + "\U0001f680"),
    ("scientist", "\U0001f9d1" + ZWJ + "\U0001f52c"),
    ("farmer, dark skin tone", "\U0001f468\U0001f3ff" + ZWJ + "\U0001f33e"),
    ("rainbow flag", "\U0001f3f3" + VS16 + ZWJ + "\U0001f308"),
]

# Supplementary-plane characters that are NOT emoji: CJK Extension B, Deseret, Gothic,
# Linear B, Cuneiform, Egyptian hieroglyphs, mathematical alphanumerics, musical symbols.
SUPPLEMENTARY_NON_EMOJI = [
    "\U00020000\U0002a6b2\U00029e3d",   # CJK Ext B / C
    "\U00010400\U00010428",             # Deseret
    "\U00010330\U00010331",             # Gothic
    "\U00010000\U00010001",             # Linear B syllables
    "\U00012000\U00012001",             # Cuneiform
    "\U00013000\U00013001",             # Egyptian hieroglyphs
    "\U0001d400\U0001d49c\U0001d55a",   # mathematical bold / script / double-struck
    "\U0001d11e\U0001d122",             # musical symbols
]


def flag(code):
    """Regional-indicator flag for a two-letter ASCII country code."""
    return "".join(chr(0x1F1E6 + ord(c) - ord("a")) for c in code.lower())


def tag_flag(tag):
    """Waving black flag + tag characters + cancel tag (e.g. 'gbsct' -> Scotland)."""
    return ("\U0001f3f4" + "".join(chr(0xE0000 + ord(c)) for c in tag)
            + "\U000e007f")


def skin_tone_paragraphs():
    lines = []
    for base in SKIN_TONE_BASES:
        lines.append(" ".join(base + tone for tone in SKIN_TONES) + " " + base)
    lines.append("no modifier: " + " ".join(SKIN_TONE_BASES))
    return lines


def flag_paragraphs():
    lines = [" ".join(pair for pair, _ in FLAG_PAIRS),
             " ".join("%s=%s" % (code, pair) for pair, code in FLAG_PAIRS),
             " ".join(tag_flag(tag) for tag, _ in TAG_FLAGS),
             " ".join("%s %s" % (name, tag_flag(tag)) for tag, name in TAG_FLAGS)]
    return lines


def variation_paragraphs():
    return [" ".join(VARIATION_ITEMS),
            "emoji presentation: " + "".join(VARIATION_ITEMS[:4]),
            "text presentation: " + "".join(VARIATION_ITEMS[4:7]),
            "keycaps: " + " ".join(VARIATION_ITEMS[7:10]),
            "ideographic variation: " + " ".join(VARIATION_ITEMS[10:])]


def zwj_paragraphs():
    return ([" ".join(seq for _, seq in ZWJ_SEQUENCES)]
            + ["%s: %s" % (name, seq) for name, seq in ZWJ_SEQUENCES])


def mixed_paragraphs():
    """One paragraph per language plus a sentence that interleaves all of them."""
    blocks = [ENGLISH[0], LATIN_ACCENTED[0], PUNJABI[0], HINDI[0], ARABIC[0],
              HEBREW[0], CHINESE[0], JAPANESE[0], KOREAN[0], THAI[0]]
    return blocks + [" | ".join(blocks),
                     " ".join([ENGLISH[2], CHINESE[2], HINDI[2], ARABIC[2]])]


def plane_mix_paragraphs():
    """BMP text, non-emoji supplementary characters, and emoji in the same lines."""
    lines = []
    bmp = [ENGLISH[0], CHINESE[0], KOREAN[0], HINDI[0], LATIN_ACCENTED[0]]
    for i, supp in enumerate(SUPPLEMENTARY_NON_EMOJI):
        lines.append("%s %s %s" % (bmp[i % len(bmp)], supp,
                                   EMOJI_HEAVY[i % len(EMOJI_HEAVY)]))
    lines.append(" ".join(SUPPLEMENTARY_NON_EMOJI))
    lines.append(ENGLISH[2] + " " + EMOJI_SINGLE + " " + CHINESE[2]
                 + " " + SUPPLEMENTARY_NON_EMOJI[0] + " " + THAI[0])
    return lines


# --- Dataset table -------------------------------------------------------------
# "exact" datasets use their text verbatim (empty and very small inputs, and the
# single-emoji case); the rest are grown to the profile's target size by drawing
# paragraphs with a seeded RNG.

def dataset(name, category, description, paragraphs, exact=False):
    return {"name": name, "category": category, "description": description,
            "paragraphs": paragraphs, "exact": exact}


DATASETS = [
    dataset("empty", "degenerate", "Empty input: zero bytes, zero code units", [""],
            exact=True),
    dataset("minimal_ascii", "degenerate",
            "Single ASCII code point (one code unit)", ["A"], exact=True),
    dataset("minimal_bmp", "degenerate",
            "Three BMP code points from three scripts (three code units)",
            ["A\u00e9\u4e2d"], exact=True),
    dataset("emoji_single", "emoji",
            "A single non-BMP emoji: exactly one surrogate pair, nothing else",
            [EMOJI_SINGLE], exact=True),
    dataset("english_ascii", "language", "English text, ASCII only", ENGLISH),
    dataset("latin_accented", "language",
            "Accented Latin (Latin-1 Supplement and Latin Extended-A)",
            LATIN_ACCENTED),
    dataset("punjabi", "language", "Punjabi in the Gurmukhi script", PUNJABI),
    dataset("hindi", "language", "Hindi in the Devanagari script", HINDI),
    dataset("arabic", "language", "Arabic, a right-to-left script", ARABIC),
    dataset("hebrew", "language", "Hebrew, a right-to-left script", HEBREW),
    dataset("chinese", "language", "Chinese Han ideographs (BMP)", CHINESE),
    dataset("japanese", "language", "Japanese hiragana, katakana and kanji", JAPANESE),
    dataset("korean", "language",
            "Korean Hangul syllables (U+AC00..U+D7A3, immediately below the "
            "surrogate block)", KOREAN),
    dataset("thai", "language",
            "Thai, unspaced with combining vowel signs and tone marks", THAI),
    dataset("mixed_multilingual", "mixed",
            "Paragraphs from all ten languages, plus lines that interleave them",
            mixed_paragraphs()),
    dataset("emoji_heavy", "emoji",
            "Emoji-dense text: mostly supplementary-plane emoji (surrogate pairs) "
            "with some BMP emoji and ASCII", EMOJI_HEAVY),
    dataset("emoji_skin_tone", "emoji",
            "Emoji modifier (Fitzpatrick) sequences on supplementary and BMP bases",
            skin_tone_paragraphs()),
    dataset("emoji_flags", "emoji",
            "Regional-indicator flag pairs and tag-sequence flags (long runs of "
            "consecutive surrogate pairs)", flag_paragraphs()),
    dataset("variation_selectors", "emoji",
            "Variation selectors U+FE0E/U+FE0F, keycap sequences, and "
            "supplementary-plane ideographic variation selectors (U+E0100+)",
            variation_paragraphs()),
    dataset("emoji_zwj", "emoji",
            "ZWJ family and profession sequences: supplementary-plane people joined "
            "by the BMP joiner U+200D, some with skin tone modifiers",
            zwj_paragraphs()),
    dataset("plane_mix", "mixed",
            "BMP text, non-emoji supplementary-plane characters (CJK Ext B, Deseret, "
            "Gothic, Linear B, Cuneiform, hieroglyphs, math, music) and emoji in the "
            "same lines", plane_mix_paragraphs()),
]

CATEGORY_ORDER = ("degenerate", "language", "mixed", "emoji")


# --- Text construction ---------------------------------------------------------

def code_unit_length(text):
    """UTF-16 code units: one per BMP code point, two per supplementary code point."""
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


def build_text(spec, target_units, seed):
    """Deterministic text of at least target_units code units.

    Paragraphs are drawn with a seeded RNG keyed by the dataset name, so a dataset's
    bytes depend only on (source text, seed, target size) -- never on iteration order
    elsewhere in the run. Growth always stops on a paragraph boundary, so no code
    point and no surrogate pair is ever split.
    """
    if spec["exact"]:
        return "\n".join(spec["paragraphs"])

    rng = random.Random("utf16-corpus|%s|%d" % (spec["name"], seed))
    paragraphs = spec["paragraphs"]
    parts = []
    units = 0
    while units < target_units:
        chunk = paragraphs[rng.randrange(len(paragraphs))]
        parts.append(chunk)
        units += code_unit_length(chunk) + 1     # +1 for the newline separator
    return "\n".join(parts) + "\n"


# --- Metadata ------------------------------------------------------------------

def measure(text):
    code_points = len(text)
    surrogate_pairs = sum(1 for ch in text if ord(ch) > 0xFFFF)
    return {
        "code_points": code_points,
        "bmp_code_points": code_points - surrogate_pairs,
        "supplementary_code_points": surrogate_pairs,
        "surrogate_pairs": surrogate_pairs,
        "code_units": code_points + surrogate_pairs,
    }


def verify_wellformed(data, big_endian, label):
    """Independent check that the bytes contain no ill-formed UTF-16.

    Uses benchmarks/llmask_reference.py, which walks the raw byte structure rather
    than going through a codec, so it would also catch a lone surrogate or an odd
    trailing byte that no string encoder could have produced.
    """
    _, units, error_bits, odd = llmask_reference.llmasks(data, big_endian)
    if error_bits or odd:
        raise SystemExit("ERROR: %s is not well-formed UTF-16 "
                         "(units=%d errorbits=%d oddtrailingbyte=%d)"
                         % (label, units, error_bits, odd))
    return units


def build_dataset(spec, target_units, seed):
    """Return (metadata, {suffix: bytes}) for one dataset, verified well-formed."""
    text = build_text(spec, target_units, seed)
    counts = measure(text)

    files = {}
    payloads = {}
    for suffix, encoding_name, codec in ENCODINGS:
        data = text.encode(codec)               # explicit LE/BE codec: never a BOM
        name = "%s.%s.bin" % (spec["name"], suffix)
        units = verify_wellformed(data, suffix == "utf16be", name)
        if units != counts["code_units"] or len(data) != 2 * counts["code_units"]:
            raise SystemExit("ERROR: %s: code-unit count disagrees with encoded bytes"
                             % name)
        payloads[name] = data
        files[suffix] = {
            "file": name,
            "encoding": encoding_name,
            "byte_order_mark": False,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    meta = {
        "dataset": spec["name"],
        "description": spec["description"],
        "source_category": spec["category"],
        "seed": None if spec["exact"] else seed,
        "generated_by_repetition": not spec["exact"],
        "bytes": 2 * counts["code_units"],
        "expected_error_count": 0,
        "files": files,
    }
    meta.update(counts)
    return meta, payloads


def build_corpus(profile, target_units, seed):
    """Build every dataset for a profile. Returns (manifest, {filename: bytes})."""
    datasets = []
    payloads = {}
    for spec in DATASETS:
        if profile == "bench" and spec["exact"]:
            continue                # degenerate sizes are fixtures, not benchmarks
        meta, files = build_dataset(spec, target_units, seed)
        datasets.append(meta)
        payloads.update(files)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator": "scripts/generate_multilingual_corpus.py",
        "issue": 40,
        "profile": profile,
        "seed": seed,
        "target_code_units": target_units,
        "encodings": [name for _, name, _ in ENCODINGS],
        "byte_order_mark": False,
        "validates_as": "UTF-16 well-formedness (surrogate structure); grapheme and "
                        "emoji semantics are explicitly out of scope",
        "expected_error_count": 0,
        "total_bytes": sum(len(d) for d in payloads.values()),
        "datasets": datasets,
    }
    return manifest, payloads


# --- Writing / checking --------------------------------------------------------

MANIFEST_NAME = "corpus_manifest.json"


def manifest_bytes(manifest):
    return (json.dumps(manifest, indent=2, sort_keys=False) + "\n").encode("ascii")


def write_corpus(output_dir, manifest, payloads):
    os.makedirs(output_dir, exist_ok=True)
    for name in sorted(payloads):
        with open(os.path.join(output_dir, name), "wb") as out:
            out.write(payloads[name])
    with open(os.path.join(output_dir, MANIFEST_NAME), "wb") as out:
        out.write(manifest_bytes(manifest))


def check_corpus(output_dir, manifest, payloads):
    """Compare what is on disk with what this script would generate. Returns problems."""
    problems = []
    expected = dict(payloads)
    expected[MANIFEST_NAME] = manifest_bytes(manifest)
    for name in sorted(expected):
        path = os.path.join(output_dir, name)
        if not os.path.exists(path):
            problems.append("missing: %s" % name)
            continue
        with open(path, "rb") as handle:
            if handle.read() != expected[name]:
                problems.append("differs: %s" % name)
    if os.path.isdir(output_dir):
        for name in sorted(os.listdir(output_dir)):
            if name not in expected and not name.startswith("."):
                problems.append("unexpected file: %s" % name)
    return problems


def summarize(manifest):
    print("%-22s %-12s %10s %10s %8s %s"
          % ("dataset", "category", "bytes/enc", "codeunits", "pairs", "errors"))
    for meta in manifest["datasets"]:
        print("%-22s %-12s %10d %10d %8d %6d"
              % (meta["dataset"], meta["source_category"], meta["bytes"],
                 meta["code_units"], meta["surrogate_pairs"],
                 meta["expected_error_count"]))
    print("%d datasets, %d files, %d bytes total"
          % (len(manifest["datasets"]), 2 * len(manifest["datasets"]),
             manifest["total_bytes"]))


DEFAULT_DIRS = {
    "fixtures": os.path.join("tests", "corpus"),
    "bench": os.path.join("benchmarks", "data", "multilingual_corpus"),
}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=("fixtures", "bench"), default="fixtures",
                        help="fixtures: small committed files under tests/corpus; "
                             "bench: benchmark-sized files under benchmarks/data "
                             "(git-ignored). Default: fixtures")
    parser.add_argument("--output-dir", default=None,
                        help="where to write (default: %s for fixtures, %s for bench)"
                             % (DEFAULT_DIRS["fixtures"], DEFAULT_DIRS["bench"]))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED,
                        help="deterministic seed (default: %d)" % DEFAULT_SEED)
    parser.add_argument("--size-mib", type=float, default=None,
                        help="bench profile: approximate size per encoding, in MiB "
                             "(default: %g)" % DEFAULT_BENCH_MIB)
    parser.add_argument("--check", action="store_true",
                        help="write nothing; report whether the files on disk are "
                             "byte-identical to what this script generates")
    parser.add_argument("--quiet", action="store_true", help="suppress the summary")
    args = parser.parse_args()

    if args.profile == "fixtures":
        if args.size_mib is not None:
            raise SystemExit("ERROR: --size-mib applies to --profile bench only")
        target_units = FIXTURE_TARGET_UNITS
    else:
        size_mib = DEFAULT_BENCH_MIB if args.size_mib is None else args.size_mib
        if size_mib <= 0:
            raise SystemExit("ERROR: --size-mib must be positive")
        target_units = int(size_mib * MIB) // 2

    output_dir = args.output_dir or os.path.join(REPO_ROOT, DEFAULT_DIRS[args.profile])
    manifest, payloads = build_corpus(args.profile, target_units, args.seed)

    if args.check:
        problems = check_corpus(output_dir, manifest, payloads)
        if problems:
            print("CORPUS OUT OF DATE (%s):" % output_dir)
            for problem in problems:
                print("  %s" % problem)
            raise SystemExit(1)
        print("corpus in %s matches the generator (%d datasets, %d files)"
              % (output_dir, len(manifest["datasets"]), 2 * len(manifest["datasets"])))
        return

    try:
        write_corpus(output_dir, manifest, payloads)
    except OSError as ex:
        raise SystemExit("ERROR: could not write to %s: %s" % (output_dir, ex))

    if not args.quiet:
        summarize(manifest)
    print("wrote %s" % os.path.join(output_dir, MANIFEST_NAME))


if __name__ == "__main__":
    main()
