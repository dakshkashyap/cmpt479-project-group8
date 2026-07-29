# Multilingual and emoji UTF-16 corpus (issue #40)

A deterministic corpus of **valid** UTF-16 text covering ten languages, mixed
multilingual paragraphs, the emoji constructs that produce interesting surrogate
patterns, non-emoji supplementary-plane characters, and the degenerate empty /
one-code-unit inputs. Every dataset exists in both **UTF-16LE and UTF-16BE**, and every
file validates with `errorCount = 0`.

Nothing about the validator changed for this issue. This is test material only.

## What it does and does not test

The corpus tests **UTF-16 well-formedness**: whether every high surrogate is followed by
a low surrogate, every low surrogate is preceded by a high one, and the input does not
end in half a code unit. That is the entire question `utf16validate` answers.

It does **not** test grapheme clustering or emoji semantics. A ZWJ family sequence is in
the corpus because it is a long alternation of supplementary-plane code points (surrogate
pairs) and BMP joiners, and a tag-sequence flag is there because it is an uninterrupted
run of seven surrogate pairs — not because either should render as a single glyph. The
validator has no opinion on how anything renders, and neither do these tests.

Malformed input is deliberately **out of scope here**: this corpus adds no ill-formed
surrogate sequences. Malformed data with controlled error rates comes from
`benchmarks/generate_utf16_benchmark.py --error-patterns` instead (see the README).

## Layout

```
scripts/generate_multilingual_corpus.py   the generator (deterministic, seeded)
scripts/test_multilingual_corpus.sh       the correctness suite
tests/corpus/                             committed fixtures: <dataset>.utf16{le,be}.bin
tests/corpus/corpus_manifest.json         metadata for every dataset
benchmarks/data/multilingual_corpus/      benchmark-sized corpus (git-ignored)
```

Files are written with the explicit `utf-16-le` / `utf-16-be` codecs, so **no byte order
mark is ever emitted** — the tool takes the encoding from `--be`, not from the bytes, and
a BOM would just be one more code point in the data.

Because both encodings come from the same source text, the BE file is exactly the byte
swap of the LE file. That is what lets the suite tie the `--be` path back to the already
trusted LE path without trusting either one.

## Datasets

| Dataset | Category | Covers |
| --- | --- | --- |
| `empty` | degenerate | Zero bytes |
| `minimal_ascii` | degenerate | One ASCII code unit |
| `minimal_bmp` | degenerate | Three BMP code points from three scripts |
| `emoji_single` | emoji | Exactly one surrogate pair and nothing else |
| `english_ascii` | language | English, ASCII only |
| `latin_accented` | language | Latin-1 Supplement and Latin Extended-A |
| `punjabi` | language | Gurmukhi |
| `hindi` | language | Devanagari |
| `arabic` | language | Arabic (right-to-left) |
| `hebrew` | language | Hebrew (right-to-left) |
| `chinese` | language | Han ideographs |
| `japanese` | language | Hiragana, katakana and kanji |
| `korean` | language | Hangul syllables (U+AC00–U+D7A3, just below the surrogate block) |
| `thai` | language | Thai, unspaced, with combining vowel signs and tone marks |
| `mixed_multilingual` | mixed | All ten languages, including lines that interleave them |
| `emoji_heavy` | emoji | Emoji-dense text, mostly surrogate pairs |
| `emoji_skin_tone` | emoji | Fitzpatrick modifiers U+1F3FB–U+1F3FF on supplementary and BMP bases |
| `emoji_flags` | emoji | Regional-indicator pairs and tag-sequence flags |
| `variation_selectors` | emoji | U+FE0E/U+FE0F, keycap sequences, ideographic variation selectors (U+E0100+) |
| `emoji_zwj` | emoji | ZWJ family and profession sequences, some with skin tone modifiers |
| `plane_mix` | mixed | BMP text + non-emoji supplementary planes (CJK Ext B, Deseret, Gothic, Linear B, Cuneiform, hieroglyphs, math, music) + emoji |

Two properties are worth calling out because they are intentional, not accidents:

- `mixed_multilingual` contains **no surrogate pairs at all** — ten scripts, every one of
  them BMP. It is the "long valid run with nothing for the surrogate logic to do" case.
- `korean` sits immediately below the surrogate block (U+D7A3 vs U+D800), which is where
  an off-by-one in a range compare would show up.

## Regenerating

```bash
# committed fixtures (~1 KiB per file; the default)
python3 scripts/generate_multilingual_corpus.py

# verify the committed fixtures still match the generator, writing nothing
python3 scripts/generate_multilingual_corpus.py --check

# benchmark-sized corpus, 8 MiB per encoding, into benchmarks/data (git-ignored)
python3 scripts/generate_multilingual_corpus.py --profile bench --size-mib 8
```

Determinism: the fixture text is fixed source text; the grown datasets draw paragraphs
from a `random.Random` seeded with `"utf16-corpus|<dataset>|<seed>"` (default seed 479),
so a dataset's bytes depend only on its own source text, seed and target size — never on
what else ran in the same invocation. Growth always stops on a paragraph boundary, so no
code point and no surrogate pair is ever split. Re-running the generator produces
byte-identical output, which the suite checks.

The generator refuses to write anything it cannot prove is well-formed: before each file
is written its bytes are re-validated with `benchmarks/llmask_reference.py`, the project's
independent reference validator, and any error bit or odd trailing byte aborts the run.

## Metadata

`corpus_manifest.json` records, per dataset: name, description, source category, seed,
byte size, UTF-16 code-unit count, Unicode code-point count, surrogate-pair count, the
BMP/supplementary split, per-encoding file name, encoding, byte size, SHA-256, the
`byte_order_mark: false` flag, and `expected_error_count: 0`.

```json
{
  "dataset": "emoji_zwj",
  "description": "ZWJ family and profession sequences: ...",
  "source_category": "emoji",
  "seed": 479,
  "bytes": 1066,
  "expected_error_count": 0,
  "files": {
    "utf16le": { "file": "emoji_zwj.utf16le.bin", "encoding": "UTF-16LE",
                 "byte_order_mark": false, "bytes": 1066, "sha256": "..." },
    "utf16be": { "file": "emoji_zwj.utf16be.bin", "encoding": "UTF-16BE",
                 "byte_order_mark": false, "bytes": 1066, "sha256": "..." }
  },
  "code_points": 486,
  "bmp_code_points": 439,
  "supplementary_code_points": 47,
  "surrogate_pairs": 47,
  "code_units": 533
}
```

(The benchmark generator writes a `.json` sidecar per file; a corpus is a set of related
datasets, so it gets one manifest instead. The metadata itself is checked against the
bytes on disk by the suite, so neither form can drift.)

## Verifying

```bash
./scripts/test_multilingual_corpus.sh        # the committed fixtures

# the same suite against a benchmark-sized corpus
CORPUS_DIR="$PWD/benchmarks/data/multilingual_corpus" ./scripts/test_multilingual_corpus.sh
```

Each dataset is checked ten ways:

1. **UTF-16LE zero errors** — scalar, `--simd`, `--emit-error-marks`, and the
   `TwoLevelScanKernel` consumer, which must also report *no* error position at all.
2. **UTF-16BE zero errors** — the same four paths under `--be`.
3. **Forced segment sizes** — the pair-dense datasets again at `-segment-size=1,13,64`,
   so a surrogate pair straddling a segment boundary still validates (skipped for
   benchmark-sized files, where it is pathologically slow and adds nothing).
4. **Cross-endian bytes** — the BE file is exactly the byte swap of the LE file.
5. **Cross-endian text** — both decode to the identical Unicode string.
6. **Reference agreement** — `benchmarks/llmask_reference.py` reports zero error bits and
   no odd trailing byte for both encodings.
7. **Surrogate structure** — an independent walk of the raw code units, written from the
   definition, confirming no lone surrogate exists in either encoding.
8. **No BOM** — neither file starts with U+FEFF.
9. **Manifest agreement** — byte size, SHA-256, code-unit / code-point / surrogate-pair
   counts, the BMP/supplementary split, and `expected_error_count = 0` all match the bytes
   on disk.
10. **Coverage** — the dataset really contains what its name claims: the skin tone dataset
    contains all five Fitzpatrick modifiers, the flag dataset contains both regional
    indicators and tag characters, the ZWJ dataset contains real family and profession
    sequences, each language dataset contains its script block, and so on. A fixture that
    quietly lost its content would still validate; this is what notices.

Corpus-wide, the generator is run twice into fresh temporary directories and both runs
must be byte-identical to each other and to the committed fixtures.

Current status: **48 passed, 0 failed** on the committed fixtures (39 passed, 0 failed
against a 1 MiB-per-encoding bench corpus, which omits the degenerate-size datasets).

## Benchmarking note

The bench profile exists so these categories can be timed later; nothing in the benchmark
runner consumes them yet. Wiring them into timed runs is separate work, and the numbers in
`results/` are unaffected by this issue.
