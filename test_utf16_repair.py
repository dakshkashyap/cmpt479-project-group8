#!/usr/bin/env python3

import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

BIN = Path(".deps/parabix/build/bin/utf16validate")
RANDOM_SEED = 479
RANDOM_CASES = 100


def run_validator(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(BIN), str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_repair(input_path: Path, output_path: Path) -> subprocess.CompletedProcess:
    with output_path.open("wb") as output:
        return subprocess.run(
            [str(BIN), "--repair", str(input_path)],
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
        )


def combined_output(result: subprocess.CompletedProcess) -> str:
    stdout = result.stdout if isinstance(result.stdout, bytes) else b""
    stderr = result.stderr if isinstance(result.stderr, bytes) else b""

    return (stdout + stderr).decode(errors="replace")


def write_units(path: Path, units: list[int]) -> None:
    with path.open("wb") as output:
        for unit in units:
            output.write(unit.to_bytes(2, byteorder="little"))


def assert_repair(
    workdir: Path,
    name: str,
    input_bytes: bytes,
    expected_bytes: bytes | None = None,
) -> None:
    input_path = workdir / f"{name}.bin"
    repaired_path = workdir / f"{name}-repaired.bin"

    input_path.write_bytes(input_bytes)

    original_validation = run_validator(input_path)
    if original_validation.returncode != 0:
        raise AssertionError(
            f"{name}: original validator process failed\n"
            f"{combined_output(original_validation)}"
        )

    repair = run_repair(input_path, repaired_path)
    if repair.returncode != 0:
        raise AssertionError(
            f"{name}: repair process failed\n"
            f"{combined_output(repair)}"
        )

    repaired_bytes = repaired_path.read_bytes()

    if len(repaired_bytes) != len(input_bytes):
        raise AssertionError(
            f"{name}: output size changed from "
            f"{len(input_bytes)} to {len(repaired_bytes)} bytes"
        )

    if expected_bytes is not None and repaired_bytes != expected_bytes:
        raise AssertionError(
            f"{name}: repaired bytes do not match expected output\n"
            f"expected: {expected_bytes.hex(' ')}\n"
            f"actual:   {repaired_bytes.hex(' ')}"
        )

    repaired_validation = run_validator(repaired_path)
    message = combined_output(repaired_validation)

    if repaired_validation.returncode != 0:
        raise AssertionError(
            f"{name}: repaired validator process failed\n{message}"
        )

    if "errorCount = 0" not in message:
        raise AssertionError(
            f"{name}: repaired output is still malformed\n{message}"
        )


def deterministic_tests(workdir: Path) -> None:
    cases = [
        (
            "isolated-high",
            bytes.fromhex("41 00 00 d8 42 00"),
            bytes.fromhex("41 00 fd ff 42 00"),
        ),
        (
            "isolated-low",
            bytes.fromhex("41 00 00 dc 42 00"),
            bytes.fromhex("41 00 fd ff 42 00"),
        ),
        (
            "consecutive-high",
            bytes.fromhex("00 d8 01 d8 41 00"),
            bytes.fromhex("fd ff fd ff 41 00"),
        ),
        (
            "consecutive-low",
            bytes.fromhex("00 dc 01 dc 41 00"),
            bytes.fromhex("fd ff fd ff 41 00"),
        ),
        (
            "reversed-pair",
            bytes.fromhex("00 dc 00 d8"),
            bytes.fromhex("fd ff fd ff"),
        ),
        (
            "valid-pair",
            bytes.fromhex("41 00 3d d8 00 de 42 00"),
            bytes.fromhex("41 00 3d d8 00 de 42 00"),
        ),
        (
            "error-at-start",
            bytes.fromhex("00 d8 41 00 42 00"),
            bytes.fromhex("fd ff 41 00 42 00"),
        ),
        (
            "error-at-end",
            bytes.fromhex("41 00 42 00 00 d8"),
            bytes.fromhex("41 00 42 00 fd ff"),
        ),
        (
            "valid-ascii",
            bytes.fromhex("41 00 42 00 43 00"),
            bytes.fromhex("41 00 42 00 43 00"),
        ),
        (
            "empty",
            b"",
            b"",
        ),
    ]

    for name, input_bytes, expected_bytes in cases:
        assert_repair(
            workdir,
            name,
            input_bytes,
            expected_bytes,
        )
        print(f"PASS: {name}")

    # Large valid input crossing many internal buffers.
    large_valid = b"\x41\x00" * 100_000
    assert_repair(
        workdir,
        "large-valid",
        large_valid,
        large_valid,
    )
    print("PASS: large-valid")

    # Isolated high surrogate positioned at a block boundary.
    boundary_input = (
        b"\x41\x00" * 127
        + b"\x00\xd8"
        + b"\x42\x00" * 127
    )

    boundary_expected = (
        b"\x41\x00" * 127
        + b"\xfd\xff"
        + b"\x42\x00" * 127
    )

    assert_repair(
        workdir,
        "boundary-error",
        boundary_input,
        boundary_expected,
    )
    print("PASS: boundary-error")


def random_units(rng: random.Random, target_count: int) -> list[int]:
    units: list[int] = []

    while len(units) < target_count:
        remaining = target_count - len(units)
        choice = rng.random()

        if choice < 0.10 and remaining >= 2:
            # Valid surrogate pair.
            units.append(rng.randint(0xD800, 0xDBFF))
            units.append(rng.randint(0xDC00, 0xDFFF))

        elif choice < 0.20:
            # Isolated high surrogate. Add a non-low-surrogate afterward
            # when space permits so it cannot accidentally become valid.
            units.append(rng.randint(0xD800, 0xDBFF))

            if len(units) < target_count:
                units.append(rng.randint(0x0000, 0xD7FF))

        elif choice < 0.30:
            # Isolated low surrogate.
            units.append(rng.randint(0xDC00, 0xDFFF))

        else:
            # Valid non-surrogate BMP code unit.
            if rng.random() < 0.5:
                units.append(rng.randint(0x0000, 0xD7FF))
            else:
                units.append(rng.randint(0xE000, 0xFFFF))

    return units[:target_count]


def randomized_tests(workdir: Path) -> None:
    rng = random.Random(RANDOM_SEED)

    for case_number in range(RANDOM_CASES):
        unit_count = rng.randint(1, 20_000)
        units = random_units(rng, unit_count)

        input_path = workdir / f"random-{case_number}.bin"
        repaired_path = workdir / f"random-{case_number}-repaired.bin"

        write_units(input_path, units)

        repair = run_repair(input_path, repaired_path)
        if repair.returncode != 0:
            raise AssertionError(
                f"random case {case_number}: repair failed\n"
                f"{combined_output(repair)}"
            )

        if input_path.stat().st_size != repaired_path.stat().st_size:
            raise AssertionError(
                f"random case {case_number}: output size changed"
            )

        validation = run_validator(repaired_path)
        message = combined_output(validation)

        if validation.returncode != 0:
            raise AssertionError(
                f"random case {case_number}: validator process failed\n"
                f"{message}"
            )

        if "errorCount = 0" not in message:
            raise AssertionError(
                f"random case {case_number}: repaired output invalid\n"
                f"{message}"
            )

        if (case_number + 1) % 10 == 0:
            print(
                f"PASS: randomized cases "
                f"{case_number - 8}-{case_number}"
            )


def main() -> int:
    if not BIN.is_file():
        print(f"ERROR: executable not found: {BIN}", file=sys.stderr)
        return 1

    try:
        with tempfile.TemporaryDirectory(
            prefix="utf16-repair-test-"
        ) as temp_directory:
            workdir = Path(temp_directory)

            print("Running deterministic tests...")
            deterministic_tests(workdir)

            print(
                f"\nRunning {RANDOM_CASES} randomized stress tests "
                f"with seed {RANDOM_SEED}..."
            )
            randomized_tests(workdir)

    except AssertionError as error:
        print(f"\nFAIL: {error}", file=sys.stderr)
        return 1

    print("\nPASS: all UTF-16 repair tests completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
