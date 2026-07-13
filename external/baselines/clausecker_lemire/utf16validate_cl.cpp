// utf16validate_cl -- Clausecker-Lemire (simdutf) UTF-16LE validation baseline.
//
// This wrapper is part of the CMPT 479 project. It is our own code; it links
// against simdutf, which is fetched and pinned by scripts/setup_clausecker_lemire.sh
// and is NOT vendored into this repository. See README.md in this directory for
// attribution and licensing.
//
// It exists so the benchmark harness can call a specialized SIMD UTF-16 validator
// through the same kind of command-line interface as our Parabix tool.
//
// IMPORTANT -- output semantics differ from our validator:
//   Our Parabix/scalar validators report `errorCount = N`, the total number of
//   ill-formed UTF-16 code units. simdutf answers a different question: it reports
//   whether the buffer is well-formed, and (with the *_with_errors entry point) the
//   position of the FIRST ill-formed code unit. It does not count every error.
//   We therefore print a validity verdict and never fabricate a count.
//
// Usage:
//   utf16validate_cl [--impl] <file> [<file> ...]
//
// Output (one line per file):
//   <path>: valid = true
//   <path>: valid = false  (first ill-formed code unit at index N, reason=...)
//   <path>: valid = false  (odd trailing byte: incomplete final code unit)
//
// Exit status is 0 even for malformed input (a malformed file is a valid test case,
// not a tool failure); a nonzero status means an I/O or usage error.

#include "simdutf.h"

#include <cstddef>
#include <cstdio>
#include <fstream>
#include <ios>
#include <string>
#include <vector>

namespace {

const char *reason_name(simdutf::error_code code) {
  switch (code) {
  case simdutf::error_code::SUCCESS:          return "SUCCESS";
  case simdutf::error_code::HEADER_BITS:      return "HEADER_BITS";
  case simdutf::error_code::TOO_SHORT:        return "TOO_SHORT";
  case simdutf::error_code::TOO_LONG:         return "TOO_LONG";
  case simdutf::error_code::OVERLONG:         return "OVERLONG";
  case simdutf::error_code::TOO_LARGE:        return "TOO_LARGE";
  case simdutf::error_code::SURROGATE:        return "SURROGATE";
  default:                                    return "OTHER";
  }
}

// Reads the whole file as UTF-16LE code units. `odd_trailing_byte` reports a file
// whose length is not a multiple of two, i.e. it ends mid-code-unit.
bool read_file(const std::string &path, std::vector<char16_t> &units,
               bool &odd_trailing_byte) {
  std::ifstream in(path, std::ios::binary);
  if (!in) {
    std::fprintf(stderr, "utf16validate_cl: cannot open %s\n", path.c_str());
    return false;
  }
  in.seekg(0, std::ios::end);
  const std::streamoff end = in.tellg();
  if (end < 0) {
    std::fprintf(stderr, "utf16validate_cl: cannot size %s\n", path.c_str());
    return false;
  }
  in.seekg(0, std::ios::beg);

  const std::size_t nbytes = static_cast<std::size_t>(end);
  odd_trailing_byte = (nbytes % 2u) != 0u;

  // Read into a char16_t vector so the buffer is correctly aligned for simdutf.
  units.assign(nbytes / 2u, 0);
  if (!units.empty()) {
    in.read(reinterpret_cast<char *>(units.data()),
            static_cast<std::streamsize>(units.size() * sizeof(char16_t)));
    if (!in) {
      std::fprintf(stderr, "utf16validate_cl: cannot read %s\n", path.c_str());
      return false;
    }
  }
  return true;
}

} // namespace

int main(int argc, char **argv) {
  std::vector<std::string> files;
  bool show_impl = false;

  for (int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if (arg == "--impl") {
      show_impl = true;
    } else if (arg == "--help" || arg == "-h") {
      std::printf("usage: utf16validate_cl [--impl] <file> [<file> ...]\n");
      return 0;
    } else {
      files.push_back(arg);
    }
  }

  if (show_impl) {
    // Which SIMD kernel simdutf selected on this host (e.g. arm64, haswell, icelake).
    // name() is a std::string_view, so print it with an explicit length.
    const auto impl_name = simdutf::get_active_implementation()->name();
    std::printf("simdutf implementation = %.*s\n",
                static_cast<int>(impl_name.size()), impl_name.data());
    if (files.empty()) {
      return 0;
    }
  }

  if (files.empty()) {
    std::fprintf(stderr, "usage: utf16validate_cl [--impl] <file> [<file> ...]\n");
    return 2;
  }

  for (const std::string &path : files) {
    std::vector<char16_t> units;
    bool odd_trailing_byte = false;
    if (!read_file(path, units, odd_trailing_byte)) {
      return 1;
    }

    // A file that ends mid-code-unit cannot be well-formed UTF-16. simdutf only sees
    // whole code units, so we check this ourselves rather than silently dropping the
    // stray byte.
    if (odd_trailing_byte) {
      std::printf("%s: valid = false  (odd trailing byte: incomplete final code unit)\n",
                  path.c_str());
      continue;
    }

    const simdutf::result r =
        simdutf::validate_utf16le_with_errors(units.data(), units.size());

    if (r.error == simdutf::error_code::SUCCESS) {
      std::printf("%s: valid = true\n", path.c_str());
    } else {
      // r.count is the index of the first ill-formed code unit. simdutf does not
      // report a total error count, so we do not print one.
      std::printf("%s: valid = false  (first ill-formed code unit at index %zu, reason=%s)\n",
                  path.c_str(), static_cast<std::size_t>(r.count), reason_name(r.error));
    }
  }

  return 0;
}
