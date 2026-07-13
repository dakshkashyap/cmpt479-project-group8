# Clausecker–Lemire UTF-16 SIMD validation baseline

This directory holds the integration point for the external, specialized SIMD UTF-16
baseline that the project compares against. It contains **our wrapper only**. No
upstream source is vendored here.

## What the baseline actually is

The baseline is [**simdutf**](https://github.com/simdutf/simdutf), the library that
implements the SIMD UTF-16 validation/transcoding techniques associated with
Robert Clausecker and Daniel Lemire (Lemire is one of simdutf's authors). We link
against its `validate_utf16le` entry points.

simdutf dispatches at runtime to the best kernel for the host. On this project's
arm64 machines it selects the **`arm64` (NEON)** implementation, which classifies
surrogates from the **high byte** of each code unit — the same strategy our
byte-oriented Parabix kernel uses. That makes it a fair, like-for-like competitor
rather than a generic scalar reference.

Run `utf16validate_cl --impl` to print which kernel simdutf selected on your machine.

> Bibliographic note: cite the upstream papers from the simdutf repository's own
> reference list when writing the final report. This file deliberately does not
> reproduce a citation from memory.

## Attribution and licensing

- **Upstream:** https://github.com/simdutf/simdutf
- **Pinned tag:** `v9.0.0`
- **Pinned commit:** `ca7acbcea967b5dcbab490066e99e3a6e6925539`
- **Licence:** dual **Apache-2.0 OR MIT** (`LICENSE-APACHE`, `LICENSE-MIT` in the
  upstream tree). Copyright 2021 The simdutf authors.

**No simdutf code is copied into this repository.** `scripts/setup_clausecker_lemire.sh`
clones the pinned commit into `.deps/simdutf/`, which is git-ignored, and compiles our
wrapper against simdutf's own single-header amalgamation. The licence text ships with
the checkout. Only `utf16validate_cl.cpp` (our code, covered by this project's terms)
lives in the repository.

## Output semantics — important limitation

Our validators and this baseline answer **different questions**, and we do not paper
over the difference:

| Tool | Question answered | Output |
| --- | --- | --- |
| Parabix scalar / SIMD | *How many* code units are ill-formed? | `errorCount = N` |
| simdutf baseline | *Is the buffer well-formed?* (and where does it first break?) | `valid = true/false` (+ index of the **first** ill-formed code unit) |

simdutf's `validate_utf16le` returns a boolean, and `validate_utf16le_with_errors`
returns an error code plus the position of the **first** error. It does **not** count
every malformed code unit. The wrapper therefore prints a validity verdict and
**never fabricates an error count**.

Consequence for benchmarking: the fair comparison is **accept/reject throughput on the
same input**, not a per-error count. On valid input (the normal benchmark workload)
both tools do the same job — scan every code unit and conclude "well-formed" — so
throughput is directly comparable. On malformed input, simdutf may exit early at the
first error while our validator keeps counting, so timings on malformed data are
**not** comparable without care. Handle that when the comparison benchmark is wired up.

One more difference the wrapper handles itself: a file with an **odd trailing byte**
ends mid-code-unit. simdutf only sees whole code units, so the wrapper checks the byte
length and reports `valid = false` rather than silently dropping the stray byte.

## Build

```bash
./scripts/setup_clausecker_lemire.sh
```

This checks out the pinned commit, generates simdutf's single-header amalgamation with
upstream's own script, and builds the wrapper to
`.deps/baselines/bin/utf16validate_cl`. It installs nothing and never resets your tree.

## Run

```bash
BIN=.deps/baselines/bin/utf16validate_cl

$BIN --impl                                   # which SIMD kernel was selected
$BIN benchmarks/data/valid_utf16le_1MiB.bin   # -> valid = true
$BIN file_a.bin file_b.bin                    # one line per file
```

Exit status is `0` even for malformed input — a malformed file is a legitimate test
case, not a tool failure. A nonzero status means an I/O or usage error.

## Status

Built and cross-checked against our validators on valid and malformed input. **No
performance comparison has been run yet** — that is deliberately left to the
benchmark issue, and no speed numbers are claimed here.
