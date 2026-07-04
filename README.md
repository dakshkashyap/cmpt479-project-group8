# CMPT 479 Project — UTF-16 Validation with Parabix

This project implements a byte-oriented UTF-16 validator using a Parabix multiblock kernel.

The goal is to compare:

- a Parabix byte-oriented SIMD implementation
- the Clausecker-Lemire UTF-16 implementation
- Parabix in single-threaded mode using `--thread-num=1`
- Parabix in its default multi-threaded mode

The Parabix implementation will use portable SIMD through the Parabix framework instead of hard-coded architecture-specific intrinsics.

## Project status

Initial repository setup is in progress.
