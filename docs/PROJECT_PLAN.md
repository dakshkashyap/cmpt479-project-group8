# Project Plan

## Goal

Implement a byte-oriented UTF-16 validator using a Parabix multiblock kernel.

## Main comparison

We will compare:

1. Clausecker-Lemire implementation
2. Parabix with `--thread-num=1`
3. Parabix with the default thread count

## Core work

- Build and understand the Parabix `base64` multiblock-kernel example
- Create a UTF-16 validator kernel
- Test valid and invalid UTF-16 input
- Verify correctness
- Measure single-threaded and multi-threaded performance
- Compare results with Clausecker-Lemire

## Optional work

- Compare against a bitwise data-parallel Parabix implementation
- Study whether transposition cost makes the bitwise approach worthwhile
