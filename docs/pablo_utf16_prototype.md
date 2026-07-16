# Bitwise / Pablo UTF-16 validation: prototype study (issue #34)

**Was a real Pablo prototype implemented and kept? No — and this document explains, with
evidence, why not.** The optional bitwise/Pablo path was genuinely attempted end to end in
Parabix. It compiled, linked, and ran, but the design did not reach correctness within a
reasonable effort for an *optional* prototype, and the underlying reason confirms the
professor's hypothesis that transposition overhead makes Pablo the wrong tool for this problem.
Rather than ship a knowingly-incorrect `--pablo` mode (which would violate "do not fake a
successful implementation"), the experimental Parabix changes were **reverted**: the tool is
byte-identical to the issue #33 milestone, and all existing suites still pass (67 + 49 + 54 + 35).

This is a study/negative-result document, which the issue explicitly permits: *"If the
implementation is not feasible in the time/structure, create a clear study document instead."*

Scope: UTF-16LE was the target for the attempt; BE was out of scope for the prototype.

---

## 1. What was attempted (a real pipeline, not a paper design)

The design was Option A from the issue: byte stream → basis bits → Pablo kernel → errorMarks.
Concretely, a `--pablo` flag selected this pipeline:

```
ReadSourceKernel  -> U16Stream : StreamSet(1,16)   (read the file as 16-bit code units)
S2PKernel         -> basis     : StreamSet(16)     (transpose to 16 basis bits)
UTF16PabloValidatorKernel(basis) -> errorMarks : StreamSet(1), errorCount : scalar
```

The Pablo kernel (`pablo::PabloKernel` subclass) classified surrogates and applied the same
position-accurate marker rule as the issue #32 SIMD producer, expressed in pure Pablo:

```cpp
cc::Parabix_CC_Compiler_Builder ccc(basis);
isHigh = ccc.compileCC(re::makeCC(0xD800, 0xDBFF), pb);
isLow  = ccc.compileCC(re::makeCC(0xDC00, 0xDFFF), pb);
// nextLow: look each basis bit ahead one code unit, then classify (Lookahead is only
// legal on input streamsets, not on derived values like isLow):
for (i) laBasis[i] = pb.createLookahead(basis[i], 1);
nextLow = Parabix_CC_Compiler_Builder(laBasis).compileCC(re::makeCC(0xDC00,0xDFFF), pb);
prevHigh = pb.createAdvance(isHigh, 1);
bad = pb.createInFile(pb.createOr(pb.createAnd(isLow,  pb.createNot(prevHigh)),
                                  pb.createAnd(isHigh, pb.createNot(nextLow))));
createAssign(getOutputStreamVar("errorMarks")[0], bad);
createAssign(getOutputScalarVar("errorCount"), pb.createCount(bad));
```

**This built and ran.** The full machinery worked: the S2P transposition kernel, a real
`PabloKernel` with Advance / Lookahead / InFile / Count, a code-unit-indexed `errorMarks`
output, a scalar count output, and even reuse of the issue #39 `TwoLevelScanKernel` consumer on
the Pablo-produced marks. So the *integration* is feasible — the scaffold is sound.

---

## 2. Why it did not reach correctness: 16-bit S2P is not the idiom

The design assumed `S2PKernel(StreamSet(1,16))` would yield **16 code-unit-indexed basis bits**,
so a code unit's value could be tested directly (`makeCC(0xD800, 0xDBFF)`). It does not behave
that way here, and empirically the classification was wrong:

| input | expected | `--pablo` produced |
|---|---|---|
| 512 lone low surrogates (`0xDC00`) | 512 | **0** |
| 512 lone high surrogates (`0xD800`) | 512 | **0** |
| 512 plain `'A'` | 0 | 0 |
| 32 MiB malformed multilingual | 2208 | **203692** |

A uniform stream of `0xDC00` producing **zero** low-surrogate hits — while varied multilingual
text produces a large spurious count — is the signature of a basis layout that is not the
value-bit indexing the CC test assumes. Several correct-by-construction attempts (the CC
compiler, and every manual bit-order mapping) failed the uniform-surrogate test; one manual
mapping produced marks only at **odd** code-unit positions with exactly **half** the count — a
classic byte-vs-code-unit indexing mismatch in the transposition.

**The root cause, confirmed from the Parabix sources:** the tools that process UTF-16 (e.g.
`tools/transcoders/x8u16.cpp`) do **not** transpose to 16 code-unit-indexed basis bits. They read
`StreamSet(1,8)` and `Selected_S2P` to **8 byte-indexed** basis bits; 16-bit streams appear only
on the *output* side via `P2S16Kernel`. There is no "bytes → 16 code-unit-indexed basis bits"
transpose idiom to reuse. A correct Pablo UTF-16 validator would therefore have to work on
**8 byte-indexed** basis bits and handle the two-byte code-unit structure explicitly — selecting
high-byte (odd, for LE) positions with a parity stream and advancing by two byte positions for
pairing. That is precisely the byte-lane bookkeeping the byte-oriented SIMD validator already
does directly, so Pablo would add transposition without removing any complexity.

---

## 3. Why it is not worth pursuing (the professor was right)

Even a *correct* Pablo validator would not pay off, for a reason independent of the bug above:

1. **Transposition is a full extra pass the SIMD validator avoids entirely.** The byte-oriented
   SIMD validator (issues #1/#38) classifies surrogates directly on the raw byte stream with a
   couple of `simd_eq`s. A Pablo path must first run S2P over every byte to produce basis bits
   before any classification can begin. Issue #31 already found the validator is close to
   bandwidth-bound; adding a whole transpose pass over the input is exactly the overhead the
   professor flagged.
2. **Surrogate classification does not benefit from bit-parallelism.** Pablo/basis-bit
   processing wins when the predicate is *complex* — multi-byte UTF-8 validation, regex, Unicode
   property classes — where testing many bit positions in parallel amortizes the transpose. UTF-16
   well-formedness is a **single high-byte compare** (`(hi & 0xFC) == 0xD8 / 0xDC`). There is no
   complex classification for bit-parallelism to accelerate, so the transpose cost buys nothing.
3. **The problem is inherently 2-byte-structured, which fights the bitstream model.** UTF-16's
   high/low-byte parity and one-code-unit pairing map cleanly onto the byte-oriented SIMD kernel
   (a lane mask + a `mvmd_dslli` shift) but awkwardly onto byte-indexed basis bits (a parity
   stream + a 2-position advance). Pablo makes this case *harder*, not easier.

The measurement, then, is predictable without a working prototype: Pablo would be **slower** than
the byte-oriented SIMD path (extra transpose, no classification speedup) while being **more
complex** to make correct. That is a clear negative result.

---

## 4. Environment note (a secondary, honest obstacle)

Independent of the design conclusion, the build environment made even the experiment
disproportionately costly. This machine's Boost was upgraded to 1.90, which removed
`libboost_system`; the project's top-level `find_package(Boost ... system ...)` now fails, so a
full CMake **reconfigure** is broken (this predates issue #34 and affects any dependency change).
Adding the Pablo path pulled in new libraries (`kernel.basis`, `pablo`, `re.cc` → `re.adt` →
`re.toolchain` → `unicode.core`), each of which had to be built by hand through its sub-Makefile
and linked in manually because the reconfigure that would normally do this could not run. For an
*optional* prototype whose expected result is "slower than what we have," this cost is not
justified. It is recorded here so the effort is not mistaken for the design being trivial.

---

## 5. What was reverted / left in place

- **Reverted:** all `.deps/parabix/tools/utf16validate` changes (the `--pablo` flag, the
  `UTF16PabloValidatorKernel`, the S2P wiring, and the CMake `kernel.basis`/`pablo`/`re.cc`
  deps). The tool source is now **byte-identical** to the issue #33 milestone patch, verified by
  regenerating the patch and diffing. `patches/utf16-simd-milestone.patch` is unchanged.
- **Not shipped:** no `--pablo` mode, because it did not produce correct results and shipping a
  wrong-answer mode is not acceptable.
- **Unaffected:** the scalar/SIMD count paths, the errorMarks producer, the TwoLevelScanKernel
  consumer, UTF-16BE, and the Clausecker–Lemire baseline. All four suites pass:
  `test_utf16validate.sh` 67/67, `test_errormarks.sh` 49/49, `test_scan_consumer.sh` 54/54,
  `test_utf16be.sh` 35/35.

No benchmark is reported: there is no correct Pablo path to benchmark, and reporting timings for
a known-incorrect kernel would be misleading.

---

## 6. Correctness / performance results

- **Correctness of the attempt:** the Pablo pipeline classified surrogates incorrectly (§2), so
  it is not correct and was removed. The scaffold (S2P + PabloKernel + errorMarks + scan + count)
  is functional; only the 16-bit basis semantics are wrong.
- **Performance:** not measured, by design (§5). The predicted result (§3) is that a correct
  Pablo path would be slower than the byte-oriented SIMD validator.

---

## 7. Recommendation for the final report

**Do not pursue a Pablo/bitwise UTF-16 validator.** State it as a considered, evidence-backed
decision, not an untried option:

- The byte-oriented SIMD validator already classifies surrogates directly on the byte stream;
  Pablo would add a full S2P transposition pass with no offsetting benefit, because UTF-16
  well-formedness is a single high-byte compare with no complex predicate for bit-parallelism to
  accelerate.
- Parabix offers no bytes→16-code-unit-indexed-basis transpose; a correct Pablo validator would
  have to work on 8 byte-indexed basis bits with explicit high-byte-parity and 2-position pairing
  logic — reintroducing exactly the byte-lane bookkeeping the SIMD kernel already handles.
- This matches the professor's hypothesis that transposition overhead dominates. The project's
  contribution is the portable byte-oriented SIMD validator plus the errorMarks/two-level-scan
  error-location pipeline (issues #29–#39), not a Pablo rewrite.

If a bitwise path is ever revisited, the correct starting point is 8 byte-indexed basis bits from
`Selected_S2P(ByteStream(1,8))`, a parity/high-byte-selection stream, and pairing by an Advance
of two byte positions — and it should be justified by a *complex* validation need (e.g. combined
transcoding + validation) that actually amortizes the transpose, which plain UTF-16 validation
does not have.

---

## 8. Reproduction of the negative result

The revert leaves nothing to run. To reproduce the finding that Parabix has no 16-bit
code-unit-indexed S2P idiom, inspect `tools/transcoders/x8u16.cpp` (reads `StreamSet(1,8)`,
`Selected_S2P` to 8 basis bits; 16-bit only on output via `P2S16Kernel`). The existing suites
confirm nothing regressed:

```bash
./scripts/test_utf16validate.sh   # 67/67
./scripts/test_errormarks.sh      # 49/49
./scripts/test_scan_consumer.sh   # 54/54
./scripts/test_utf16be.sh         # 35/35
```
