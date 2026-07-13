# Parabix thread-scaling analysis

When does Parabix multithreading help, where does it plateau, and what most plausibly
explains the result?

This follows [`benchmark_methodology.md`](benchmark_methodology.md), **comparison group C**:
only Parabix modes are compared, always against `parabix_simd_t1`. The
Clausecker–Lemire/simdutf baseline is single-threaded and is deliberately excluded —
comparing multi-threaded Parabix against it would conflate parallelism with SIMD quality.

---

## 1. Headline result

**Multithreading does not meaningfully help this validator.** On a 128 MiB input the best
measured gain from extra threads is **~1.09×** (2 threads), and the third thread makes
things *worse* than the second. Thread efficiency is ~0.55 at 2 threads and ~0.35 at 3.

This is a flat/negative result, and it is reported as such.

**A second, more serious finding fell out of the same runs:** with the current
byte-oriented (`fw=8`) SIMD kernel, **the SIMD path is slower than the scalar path at
every size we measured at or above 8 MiB.** See §4 — this contradicts the numbers in the
committed `results/utf16_benchmark_summary.md`, which were produced by an earlier kernel.

---

## 2. Measured data

Real measurements, not a smoke run: **128 MiB** inputs, 2 warmups, 5 measured
repetitions, median wall clock, whole-process timing, arm64 macOS.

### Thread scaling at 128 MiB (throughput MiB/s; baseline `parabix_simd_t1`)

| Dataset | t1 | t2 | t3 | default | Best speedup | Plateau |
|---|---|---|---|---|---|---|
| `default` | 1115.6 | 1217.0 | 1180.6 | 1063.3 | **1.09×** (t2) | 2 threads |
| `english_ascii_heavy` | 1114.2 | 1215.9 | 1180.5 | 1179.7 | **1.09×** (t2) | 2 threads |
| `cjk` | 1112.3 | 1204.0 | 1179.4 | 1069.2 | **1.08×** (t2) | 2 threads |
| `emoji_heavy` | 1112.7 | 1199.3 | 1176.0 | 1160.9 | **1.08×** (t2) | 2 threads |
| `mixed_multilingual` | 1112.6 | 1096.8 | 1160.9 | 1187.9 | **1.07×** (default) | not clean |

Thread efficiency: **~0.55 at 2 threads**, **~0.35 at 3 threads**.

Two things stand out:

1. **The plateau is at 2 threads.** The third thread does not pay for itself; on four of
   five datasets `t3` is *slower* than `t2`.
2. **`parabix_simd_default` is erratic** (0.95×–1.07× relative to `t1`) and is sometimes
   the slowest configuration. Whatever thread count it picks is not reliably a good one
   for this workload.

### Dataset effect: essentially none

Single-thread throughput is ~1112–1116 MiB/s across *all five* datasets — ASCII, CJK, and
emoji-heavy are within noise of each other. This is the expected behaviour of the
byte-oriented kernel: it classifies every byte with the same branch-free `fw=8` work
regardless of content, so **surrogate density does not change the cost**. Emoji-heavy
input is not a stress case for this design. (It *would* be for an implementation with
data-dependent branching.)

### Size sweep (`default` dataset, current kernel, throughput MiB/s)

| Mode | 1 MiB | 8 MiB | 32 MiB | 64 MiB | 128 MiB |
|---|---|---|---|---|---|
| `scalar` | 49.9 | 350.8 | 951.5 | 1336.0 | **1675.0** |
| `parabix_simd_t1` | 48.9 | 314.1 | 740.2 | 940.1 | 1103.7 |
| `parabix_simd_t2` | 49.6 | 321.4 | 738.9 | 1002.2 | 1195.9 |
| `parabix_simd_t3` | 49.9 | 311.9 | 758.7 | 958.2 | 1149.0 |
| `parabix_simd_default` | 50.6 | 317.7 | 754.9 | 968.2 | 1158.3 |

At 1 MiB every mode reads ~50 MiB/s — that is the fixed per-process cost (~0.019 s,
measured separately on a tiny input) dominating, not validation. It is a measurement of
startup, and no conclusion should be drawn from it.

---

## 3. Why multithreading does not help — candidate explanations

Stated as candidates. The data supports the first two most strongly; we have not run the
experiments that would isolate the others.

**Memory bandwidth saturation (most likely).** UTF-16 validation is a streaming scan with
very low arithmetic intensity: for every 16 bytes read, the kernel does a handful of
compares, a shift, an XOR and a popcount, and retains almost nothing. A single core can
already issue enough loads to approach the memory system's streaming limit, so extra
threads contend for bandwidth they cannot use. The flat ~1.08× ceiling and the *negative*
return on the third thread are the classic signature of a bandwidth-bound kernel.

**Scheduling / pipeline coordination overhead.** Parabix's segment threading has real
per-segment coordination cost. When each thread's work per segment is small (a few compares
per byte), that overhead is a meaningful fraction of the total, which is why adding the
third thread can cost more than it returns.

**Cross-segment carry dependency.** The kernel threads a one-bit `pendingHigh` carry (the
"previous code unit was an unmatched high surrogate" state) across packs, blocks and
segments. It is handled correctly, but it is a genuine serial dependency in an otherwise
embarrassingly parallel scan, and it constrains how freely segments can be reordered or
overlapped. We have not measured its cost in isolation.

**Fixed per-process cost.** ~0.019 s of startup/pipeline load is paid once per run. At
small inputs it swamps everything (see the 1 MiB row). At 128 MiB it is ~15% of the SIMD
runtime — no longer dominant, but not negligible either.

**Not the explanation: dataset content.** Ruled out above — all five datasets scale
identically.

---

## 4. The SIMD-vs-scalar regression (important)

The same runs show something that is not a threading result but must not be buried:

| Measurement | Committed summary (earlier kernel) | This run (current kernel) |
|---|---|---|
| `scalar` @ 64 MiB | 1354.8 MiB/s | 1336.0 MiB/s — **reproduces** |
| `parabix_simd_default` @ 64 MiB | 2176.4 MiB/s (1.61× scalar) | **968.2 MiB/s (0.72× scalar)** |

The **scalar** number reproduces almost exactly, which is expected: the scalar oracle has
not been touched. The **SIMD** number does not — it is roughly **2.2× slower** than the
committed figure, and the current SIMD path is now **slower than scalar** at every size
≥ 8 MiB.

The most likely cause is that the committed summary predates the byte-oriented (`fw=8`)
rewrite of the SIMD kernel. The rewrite was done for **portability** (removing the
host-endian 16-bit-lane assumption), and portability was the right goal — but it appears to
have cost throughput, and that trade-off was never measured at the time.

Consequences, which this issue does **not** act on (they are out of scope here):

- The **1.61× SIMD speedup claimed in the Project Update 2 report and slides is no longer
  reproducible** with the current code. It should not be repeated without re-measuring.
- `results/utf16_benchmark_summary.md` is stale with respect to the current kernel.
- A follow-up should (a) re-run the benchmark to refresh the summary, and (b) investigate
  the `fw=8` kernel's cost — e.g. whether the per-pack `hsimd_signmask` + 64-bit mask
  bookkeeping is the bottleneck versus the previous lane-based approach.

Nothing here was fixed or hidden: the validator was not modified, and the committed summary
was not overwritten.

---

## 5. How to interpret the numbers

- **Speedup(n) = median(t1) / median(tn).** The baseline is *our own single-thread SIMD
  run* — never scalar, never simdutf.
- **Thread efficiency(n) = Speedup(n) / n.** Perfect scaling is 1.0. Our 0.55 at two
  threads means the second thread returns a bit over half of what it theoretically could;
  0.35 at three means the third is mostly wasted.
- **Plateau** = the thread count beyond which another thread buys less than 5%. Here: **2**.
- Efficiency well below 1.0 is *normal* for a streaming, bandwidth-bound scan. It is not a
  bug, and it should be reported rather than hidden.
- Anything measured at 1–8 MiB is dominated by fixed startup cost and is **not** a
  performance conclusion.

---

## 6. How to reproduce

```bash
# 1. Build the validator (once)
./scripts/setup_parabix.sh

# 2. Generate the datasets used above (128 MiB each; ~100 s, ~640 MB)
python3 benchmarks/generate_utf16_benchmark.py \
    --output-dir benchmarks/data \
    --datasets default,english_ascii_heavy,cjk,emoji_heavy,mixed_multilingual \
    --sizes-mb 128

# 3. Thread scaling across datasets at 128 MiB (write outside results/)
python3 benchmarks/run_utf16_benchmark.py \
    --datasets default,english_ascii_heavy,cjk,emoji_heavy,mixed_multilingual \
    --sizes-mb 128 --warmups 2 --repetitions 5 \
    --output /tmp/t27/scaling_128.csv

# 4. Size sweep (shows the startup-amortization curve)
python3 benchmarks/run_utf16_benchmark.py \
    --datasets default --sizes-mb 1,8,32,64,128 \
    --warmups 2 --repetitions 5 \
    --output /tmp/t27/sweep.csv

# 5. Analyze
python3 benchmarks/analyze_thread_scaling.py --input /tmp/t27/scaling_128.csv
```

The full final matrix (256 and 512 MiB) was **not** run here — generation alone is
~0.17 s/MiB, so 256+512 MiB across five datasets costs roughly 10 minutes and ~4 GB of
disk. To run it:

```bash
python3 benchmarks/generate_utf16_benchmark.py --output-dir benchmarks/data \
    --datasets default,english_ascii_heavy,cjk,emoji_heavy,mixed_multilingual \
    --sizes-mb 256,512

python3 benchmarks/run_utf16_benchmark.py \
    --datasets default,english_ascii_heavy,cjk,emoji_heavy,mixed_multilingual \
    --sizes-mb 128,256,512 --warmups 2 --repetitions 7 \
    --output /tmp/t27/scaling_final.csv

python3 benchmarks/analyze_thread_scaling.py \
    --input /tmp/t27/scaling_final.csv --output /tmp/t27/thread_scaling.md
```

Benchmark CSVs and generated datasets are git-ignored and must not be committed.

---

## 7. Threats to validity

- **Whole-process timing.** Every number includes process startup and pipeline load
  (~0.019 s). At 128 MiB that is ~15% of the SIMD runtime, so it inflates times and
  slightly *understates* the true per-byte cost. It affects all Parabix modes equally, so
  the thread-scaling *ratios* are still meaningful.
- **One machine, one run.** arm64 macOS laptop, 5 repetitions. The thread-scaling numbers
  are stable and consistent across five datasets, which is reassuring, but they have not
  been reproduced on another machine or on x86.
- **Laptop thermal/scheduler effects.** A 2–3 thread result on a laptop under macOS's
  scheduler may not represent a server. This is the main reason not to over-generalize the
  "2 threads" plateau.
- **We did not measure memory bandwidth directly.** Bandwidth saturation is the most
  plausible explanation, not a demonstrated one. A roofline or bandwidth measurement would
  settle it.
- **`default` thread count is not recorded.** We log `default` as a label, not the number
  of threads Parabix actually chose, so its efficiency cannot be computed.
