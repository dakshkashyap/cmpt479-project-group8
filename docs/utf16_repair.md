# End-to-end U+FFFD repair (issue #40)

`--repair` rewrites every ill-formed UTF-16 code unit to the Unicode replacement character
**U+FFFD** and writes the repaired bytes to stdout. It is a real Parabix pipeline built on the
existing pieces: the issue #32 `errorMarks` producer locates the ill-formed code units, and a
new `UTF16RepairKernel` copies the input through, substituting U+FFFD at each marked position.

This is a **prototype** path. It does not change or regress the scalar/SIMD count validators, the
`errorMarks` producer, or the `TwoLevelScanKernel` consumer — those remain the default and are
covered by their own suites.

Scope: UTF-16LE (default) and UTF-16BE (`--be`), little-endian host.

---

## 1. User-facing command

```bash
utf16validate --repair FILE            # UTF-16LE: repaired bytes -> stdout, errorCount -> stderr
utf16validate --be --repair FILE       # UTF-16BE
```

Repaired UTF-16 goes to **stdout**; the diagnostic `errorCount = N` line goes to **stderr**, so
the output can be redirected cleanly:

```bash
utf16validate --repair in.bin > repaired.bin
```

`--repair` is a separate path from `--simd`, `--emit-error-marks`, and `--scan-error-marks`; it
reuses the same `errorMarks` producer internally but selects the repair kernel + `StdOutKernel`
instead of a scan/print consumer.

---

## 2. Repair semantics

Repair is **position-accurate**: it rewrites the actual ill-formed code unit, not its successor.
This is guaranteed by reusing the issue #32 `errorMarks` bitstream, whose bit *i* is set iff code
unit *i* is itself ill-formed (a lone high, a lone low, or either half of a reversed pair).

| input | repaired output |
|---|---|
| valid BMP code unit | unchanged |
| valid surrogate pair (high, low) | unchanged |
| lone high surrogate | U+FFFD |
| lone low surrogate | U+FFFD |
| reversed pair (low, high) | U+FFFD U+FFFD (each half independently) |
| two adjacent lone highs | U+FFFD U+FFFD (each independently) |

The guaranteed invariants (all tested in `scripts/test_utf16_repair.sh`):

- `repair(valid input) == input` — valid input is returned byte-for-byte unchanged.
- `validate(repair(input)) == 0` — the repaired output is always well-formed.
- `repair(repair(input)) == repair(input)` — repair is idempotent (U+FFFD is a valid BMP code
  unit, so a second pass finds nothing to fix).

### Odd trailing byte policy

A file with an **odd** byte count ends in one byte that is not a whole UTF-16 code unit. The
policy is: **discard the incomplete trailing byte and append exactly one U+FFFD code unit.** This
keeps the repaired output an even number of bytes and well-formed. Examples (UTF-16LE):

| input bytes | repaired bytes |
|---|---|
| `41 00 42 00 41` (`A`, `B`, then a stray `41`) | `41 00 42 00 FD FF` (`A`, `B`, U+FFFD) |
| `00 D8 41` (lone high, then a stray `41`) | `FD FF FD FF` (U+FFFD for the lone high, U+FFFD for the odd byte) |
| `41` (a single stray byte) | `FD FF` (one U+FFFD) |

Implementation note: the repair kernel only rewrites complete code units. For an odd-length
input the host (`utf16validate()`) captures the kernel's stdout, truncates to the whole-code-unit
prefix, and appends the U+FFFD — so the incomplete byte never reaches the output. Even-length
inputs take a direct path with no capture.

---

## 3. LE and BE behavior

Endianness controls both which byte of a code unit is the high byte (already handled by the
`errorMarks` producer, issue #33) **and** the bytes written for U+FFFD:

- UTF-16LE: U+FFFD = `FD FF`
- UTF-16BE: U+FFFD = `FF FD`

`repair(LE input)` outputs LE; `repair(BE input)` outputs BE. The repair kernel's name encodes
endianness (`utf16repair` / `utf16repair_be`) so Parabix's object cache never serves the LE
kernel for a BE request (an earlier prototype shared one name and emitted U+FDFF — a valid but
wrong character — for BE after an LE run; this is fixed and regression-tested by the
"BE after LE" case).

---

## 4. Correctness results

`./scripts/test_utf16_repair.sh` — **64/64 pass** (LE + BE), covering: valid BMP, valid
multilingual, valid surrogate pair, lone high, lone low, reversed pair, adjacent highs, high at
position 0, dangling high at EOF, empty, odd trailing byte, odd trailing byte after a malformed
unit, pure odd byte, valid/malformed pairs straddling the **64-code-unit** and **4096-code-unit
(scan-stride)** boundaries, 12 randomized inputs (LE + BE, some odd-length), a large
byte-for-byte "valid unchanged" check, and the 32 MiB malformed dataset (when present). Every
case verifies exact U+FFFD positions, `validate(repair)==0`, and idempotence.

Existing suites remain green: `test_utf16validate.sh` (67), `test_errormarks.sh` (49),
`test_scan_consumer.sh` (54), `test_utf16be.sh` (35).

### simdutf cross-check

For **even-length** inputs, `--repair` was compared byte-for-byte against simdutf v9.0.0
`simdutf::to_well_formed_utf16le` / `...be` (which also replaces mismatched surrogates with
U+FFFD on complete code units). All tested cases **match exactly**, including the 32 MiB malformed
dataset, in both LE and BE.

This comparison is only valid for even-length, complete-code-unit inputs: simdutf's API is
`char16_t`-based and has no notion of an odd trailing byte, so no equivalence is claimed for the
odd-byte case (that is this project's documented policy above, not a simdutf behavior).

---

## 5. Performance

Repair on the 32 MiB malformed dataset completes in well under a tenth of a second (the pipeline
is dominated by I/O and the `errorMarks` producer; the repair copy is a simple per-code-unit
byte substitution). No production benchmark summary is updated; this is a prototype.

---

## 6. Limitations

- **Prototype**, LE-host only (as with the rest of the project).
- The odd-length path captures stdout to an anonymous temp file (`tmpfile()`, auto-removed) to
  drop the incomplete byte and append U+FFFD. This only runs for odd-length inputs; even-length
  inputs stream directly. It is a host-side workaround for the awkwardness of variable-length
  output in a Parabix `MultiBlockKernel` (a fully in-kernel append would need block-structured
  writes to a `BoundedRate` output).
- Repair always replaces with U+FFFD; there is no option to drop or otherwise transform.
- No streaming across multiple input files is special-cased; each file is repaired independently.

---

## 7. Reproduction

```bash
./scripts/setup_parabix.sh          # applies patches/utf16-simd-milestone.patch (now includes repair)
./scripts/test_utf16_repair.sh      # 64/64

utf16validate --repair       in.bin > out_le.bin
utf16validate --be --repair  in.bin > out_be.bin
```
