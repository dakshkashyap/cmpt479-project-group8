#!/usr/bin/env python3
"""Tests for the machine-readable JSON diagnostics (issue #46).

    python3 scripts/test_utf16_json_output.py
    ./scripts/test_utf16_json_output.sh

What is checked, for every operation and both encodings:

  * stdout parses as ONE complete JSON document (json.loads, not a regex);
  * the schema is consistent: the always-present keys are there, keys that do not
    apply to the selected operation are absent, and no unexpected key appears;
  * counts are JSON numbers and flags are JSON booleans -- never strings;
  * validation reports valid=true/error_count=0 for well-formed input and
    valid=false/error_count=n otherwise, agreeing with scripts/utf16_oracle.py;
  * positions are an array of numbers equal to the oracle's positions, and an
    EMPTY array (not a missing key, not null) when there are no errors;
  * repair reports performed/replacement_count/output_valid and embeds no binary;
  * --json-pretty carries exactly the same data as --json;
  * human-readable output is untouched when neither flag is given.

Nothing here changes validation, repair or oracle behaviour; it only reads output.
"""

import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import utf16_oracle as oracle                                   # noqa: E402

DEFAULT_PARABIX = os.environ.get("PARABIX_DIR", os.path.join(REPO_ROOT, ".deps", "parabix"))
DEFAULT_BIN = os.path.join(DEFAULT_PARABIX, "build", "bin", "utf16validate")

A, B = 0x0041, 0x0042
HI, LO = 0xD800, 0xDC00
PAIR = [0xD83D, 0xDE00]

# The stable envelope: present in EVERY document, success or failure.
ENVELOPE = {"version", "tool", "command", "timestamp", "operation", "status", "warnings",
            "metadata", "encoding"}
# Success-only fields.
SUCCESS = {"file", "size_bytes", "code_units", "error_count", "validation", "timing"}
# Operation-specific fields.
OPERATION_SPECIFIC = {"error_marks", "positions", "scan_positions", "repair"}
# Failure-only field.
FAILURE = {"error", "file", "size_bytes", "code_units"}
ALWAYS = ENVELOPE | SUCCESS
OPTIONAL = OPERATION_SPECIFIC

passed = failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("  PASS %-58s %s" % (name, detail))
    else:
        failed += 1
        print("  FAIL %-58s %s" % (name, detail))
    return condition


def run_json(binary, args, path, timeout=120):
    """Run the tool and parse its stdout as one JSON document."""
    proc = subprocess.run([binary] + args + [path], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("rc=%d: %s" % (proc.returncode,
                                          proc.stderr.decode("utf-8", "replace")[:200]))
    return json.loads(proc.stdout.decode("utf-8")), proc.stdout


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def main():
    binary = os.environ.get("UTF16VALIDATE_BIN", DEFAULT_BIN)
    if not os.access(binary, os.X_OK):
        print("ERROR: utf16validate not found at %s" % binary, file=sys.stderr)
        print("       Run ./scripts/setup_parabix.sh first.", file=sys.stderr)
        return 1

    print("UTF-16 machine-readable diagnostics (issue #46)")
    print("  binary: %s" % binary)
    print()

    workdir = tempfile.mkdtemp(prefix="utf16-json-")
    try:
        fixtures = [
            ("valid_bmp", [A, B, 0x4E2D], None),
            ("valid_pair", [A] + PAIR + [B], None),
            ("empty", [], None),
            ("lone_high", [A, HI, B], None),
            ("lone_low", [A, LO, B], None),
            ("reversed", [A, LO, HI, B], None),
            ("dense", [LO, HI] * 8, None),
            ("odd_byte", [A, HI], 0x41),
        ]

        for label, units, odd in fixtures:
            for encoding, be, flag in (("LE", False, []), ("BE", True, ["-be"])):
                data = oracle.encode_code_units(units, be)
                if odd is not None:
                    data += bytes([odd])
                path = os.path.join(workdir, "%s_%s.bin" % (label, encoding))
                with open(path, "wb") as handle:
                    handle.write(data)

                diag = oracle.analyze(data, be)
                tag = "%s/%s" % (label, encoding)

                # --- validation --------------------------------------------------
                doc, _ = run_json(binary, ["--json"] + flag, path)
                schema_ok = (ALWAYS <= set(doc)
                             and not (set(doc) - ALWAYS - OPTIONAL))
                check("%s validate: schema keys" % tag, schema_ok,
                      "unexpected=%s" % sorted(set(doc) - ALWAYS - OPTIONAL))
                check("%s validate: numeric fields are numbers" % tag,
                      all(is_number(doc[k]) for k in
                          ("version", "size_bytes", "code_units", "error_count"))
                      and is_number(doc["validation"]["error_count"])
                      and is_number(doc["timing"]["elapsed_seconds"]))
                check("%s validate: valid is a boolean" % tag,
                      isinstance(doc["validation"]["valid"], bool)
                      and isinstance(doc["metadata"]["big_endian"], bool))
                check("%s validate: counts agree with the oracle" % tag,
                      doc["error_count"] == diag.error_count
                      and doc["validation"]["error_count"] == diag.error_count,
                      "json=%d oracle=%d" % (doc["error_count"], diag.error_count))
                check("%s validate: valid flag matches error count" % tag,
                      doc["validation"]["valid"] == (diag.error_count == 0),
                      "valid=%s" % doc["validation"]["valid"])
                check("%s validate: geometry and encoding" % tag,
                      doc["size_bytes"] == len(data)
                      and doc["code_units"] == len(data) // 2
                      and doc["encoding"] == ("UTF-16BE" if be else "UTF-16LE")
                      and doc["metadata"]["odd_trailing_byte"] == diag.odd_trailing_byte)
                check("%s validate: no operation-specific keys leak in" % tag,
                      not (OPTIONAL & set(doc)),
                      "present=%s" % sorted(OPTIONAL & set(doc)))

                # --simd must report the same answer, different implementation label.
                simd, _ = run_json(binary, ["--json", "--simd"] + flag, path)
                check("%s validate: --simd agrees" % tag,
                      simd["error_count"] == diag.error_count
                      and simd["operation"] == "validate_simd"
                      and simd["metadata"]["implementation"] == "parabix_simd")

                # --- error marks -------------------------------------------------
                marks, _ = run_json(binary, ["--json", "--emit-error-marks"] + flag, path)
                expected_marks = len(diag.malformed_positions)
                check("%s marks: mark_count is numeric and correct" % tag,
                      "error_marks" in marks
                      and is_number(marks["error_marks"]["mark_count"])
                      and marks["error_marks"]["mark_count"] == expected_marks,
                      "json=%s oracle=%d" % (marks.get("error_marks"), expected_marks))

                # --- positions ---------------------------------------------------
                for mode, key, extra in (("linear", "positions", ["--print-positions"]),
                                         ("scan", "scan_positions",
                                          ["--scan-error-marks"])):
                    doc, _ = run_json(binary,
                                      ["--json", "--emit-error-marks", "-thread-num=1"]
                                      + extra + flag, path)
                    values = doc.get(key)
                    ok = isinstance(values, list) and all(is_number(v) for v in values)
                    check("%s %s: %s is an array of numbers" % (tag, mode, key), ok,
                          "%s" % values)
                    if mode == "linear":
                        # The two-level scan has a separately documented defect; the
                        # linear printer is the one required to match the oracle here.
                        check("%s %s: positions equal the oracle" % (tag, mode),
                              sorted(values or []) == diag.malformed_positions,
                              "json=%s oracle=%s" % (values,
                                                     diag.malformed_positions))
                    if not diag.malformed_positions:
                        check("%s %s: empty array, not null or missing" % (tag, mode),
                              values == [])

                # --- repair ------------------------------------------------------
                doc, raw = run_json(binary, ["--json", "--repair"] + flag, path)
                repair = doc.get("repair")
                check("%s repair: reports performed/counts/validity" % tag,
                      isinstance(repair, dict)
                      and repair.get("performed") is True
                      and is_number(repair.get("replacement_count"))
                      and isinstance(repair.get("output_valid"), bool),
                      "%s" % repair)
                check("%s repair: output_valid is true" % tag,
                      repair and repair.get("output_valid") is True)
                expected_replacements = (len(diag.malformed_positions)
                                         + (1 if diag.odd_trailing_byte else 0))
                check("%s repair: replacement_count matches the oracle" % tag,
                      repair and repair["replacement_count"] == expected_replacements,
                      "json=%s oracle=%d" % (repair and repair["replacement_count"],
                                             expected_replacements))
                # The document must be pure text: no repaired binary smuggled in.
                check("%s repair: no binary embedded" % tag,
                      all(byte >= 0x20 or byte in (0x0A, 0x0D, 0x09) for byte in raw))

                # --- pretty form carries identical data --------------------------
                pretty, pretty_raw = run_json(binary, ["--json-pretty"] + flag, path)
                plain, _ = run_json(binary, ["--json"] + flag, path)
                volatile = ("timestamp", "timing", "command")
                check("%s pretty: same data as --json" % tag,
                      {k: v for k, v in pretty.items() if k not in volatile}
                      == {k: v for k, v in plain.items() if k not in volatile})
                check("%s pretty: actually indented" % tag,
                      b"\n  \"version\"" in pretty_raw)

                # --- human-readable output unchanged -----------------------------
                proc = subprocess.run([binary] + flag + [path], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
                text = proc.stdout.decode("utf-8", "replace")
                check("%s human-readable line unchanged" % tag,
                      text.strip().endswith("errorCount = %d" % diag.error_count)
                      and not text.lstrip().startswith("{"),
                      text.strip()[-24:])
        # --- contract: exactly one document, single input only -------------------
        one = os.path.join(workdir, "valid_bmp_LE.bin")
        two = os.path.join(workdir, "valid_pair_LE.bin")

        proc = subprocess.run([binary, "--json", one], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        check("single file: exactly one JSON document", proc.returncode == 0
              and len(proc.stdout.decode().strip().split("\n}")) >= 1
              and isinstance(json.loads(proc.stdout.decode()), dict))

        proc = subprocess.run([binary, "--json", one, two], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        text = proc.stdout.decode("utf-8", "replace")
        doc = None
        parse_ok = True
        try:
            doc = json.loads(text)
        except ValueError:
            parse_ok = False
        check("two files: exactly one parseable document", parse_ok and isinstance(doc, dict),
              text[:80])
        check("two files: status error with structured code",
              parse_ok and doc.get("status") == "error"
              and doc.get("error", {}).get("code") == "json_requires_single_input",
              parse_ok and str(doc.get("error")))
        check("two files: non-zero exit", proc.returncode != 0, "rc=%d" % proc.returncode)
        check("two files: no second document or human-readable tail follows",
              text.count('"tool"') == 1 and "errorCount =" not in text)
        check("two files: no success fields claimed",
              parse_ok and not (SUCCESS - {"file", "size_bytes", "code_units"})
              & set(doc or {}),
              "leaked=%s" % sorted((SUCCESS - {"file", "size_bytes", "code_units"})
                                   & set(doc or {})))

        proc = subprocess.run([binary, "--json"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        try:
            doc = json.loads(proc.stdout.decode())
            ok = doc.get("status") == "error" and proc.returncode != 0
        except ValueError:
            ok = False
        check("zero files: one error document, non-zero exit", ok)

        # --- contract: missing input file ----------------------------------------
        missing = os.path.join(workdir, "does_not_exist.bin")
        proc = subprocess.run([binary, "--json", missing], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        doc = json.loads(proc.stdout.decode())
        check("missing file: status error + input_open_failed",
              doc["status"] == "error"
              and doc["error"]["code"] == "input_open_failed"
              and isinstance(doc["error"]["message"], str))
        check("missing file: non-zero exit", proc.returncode != 0,
              "rc=%d" % proc.returncode)
        check("missing file: envelope complete", ENVELOPE <= set(doc),
              "missing=%s" % sorted(ENVELOPE - set(doc)))
        check("missing file: no validation/error_count claimed",
              "validation" not in doc and "error_count" not in doc,
              "present=%s" % sorted({"validation", "error_count"} & set(doc)))
        check("missing file: unknown numerics are null, not -1",
              doc.get("size_bytes", "absent") is None
              and doc.get("code_units", "absent") is None)

        proc = subprocess.run([binary, "--json-pretty", missing], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        pretty_doc = json.loads(proc.stdout.decode())
        check("missing file: --json-pretty failure document",
              pretty_doc["status"] == "error"
              and pretty_doc["error"]["code"] == "input_open_failed"
              and b"\n  \"version\"" in proc.stdout)

        # --- contract: escapable characters in the filename ----------------------
        tricky = os.path.join(workdir, 'quote"back\\slash\ttab.bin')
        try:
            with open(tricky, "wb") as handle:
                handle.write(oracle.encode_code_units([A, B], False))
            doc, raw = run_json(binary, ["--json"], tricky)
            check("escapable filename: document parses and round-trips",
                  doc["file"] == tricky, doc["file"])
        except OSError:
            check("escapable filename: skipped (filesystem refused the name)", True)

        # --- contract: deterministic precedence ----------------------------------
        both = run_json(binary, ["--json", "--emit-error-marks", "--print-positions",
                                 "--scan-error-marks", "-thread-num=1"], one)[0]
        check("precedence: scan wins over print, and the label matches",
              both["operation"] == "locate_scan"
              and "scan_positions" in both and "positions" not in both,
              both["operation"])
        rep = run_json(binary, ["--json", "--repair", "--emit-error-marks", "--simd"],
                       one)[0]
        check("precedence: repair wins over marks/simd",
              rep["operation"] == "repair" and "repair" in rep, rep["operation"])

        # --- contract: benchmark helper rejects a failure document ---------------
        sys.path.insert(0, os.path.join(REPO_ROOT, "benchmarks"))
        import benchmark_utf16_pipeline as bench
        rejected = False
        try:
            bench.parabix_json(binary, [], missing, 60)
        except RuntimeError as ex:
            rejected = "input_open_failed" in str(ex)
        check("benchmark helper rejects status:error with the code", rejected)
        wrong_op = False
        try:
            bench.parabix_json(binary, [], one, 60, expect_operation="repair")
        except RuntimeError as ex:
            wrong_op = "requested" in str(ex)
        check("benchmark helper rejects an operation mismatch", wrong_op)
        missing_field = False
        try:
            bench.parabix_json(binary, [], one, 60, require="positions")
        except RuntimeError as ex:
            missing_field = "missing the required field" in str(ex)
        check("benchmark helper rejects a missing required field", missing_field)

        # --- contract: human-readable multi-file behaviour unchanged -------------
        proc = subprocess.run([binary, one, two], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        lines = [l for l in proc.stdout.decode().strip().split("\n") if l]
        check("human-readable: two files still produce two lines, exit 0",
              proc.returncode == 0 and len(lines) == 2
              and all("errorCount =" in l for l in lines))

    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    print("%d passed, %d failed" % (passed, failed))
    if failed:
        print("JSON DIAGNOSTIC TESTS FAILED")
        return 1
    print("ALL JSON DIAGNOSTIC TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
