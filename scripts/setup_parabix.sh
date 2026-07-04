#!/usr/bin/env bash
#
# setup_parabix.sh -- one-command reproducible setup for the UTF-16 SIMD milestone.
#
# From the team repository root:
#     ./scripts/setup_parabix.sh
#
# It clones the exact Parabix revision, applies the milestone patch, configures a
# Release build, and builds the `utf16validate` tool. Teammates do NOT clone or
# patch Parabix by hand.
#
# Environment overrides (all optional):
#     PARABIX_DIR=/custom/path      where to place the Parabix checkout (default: <repo>/.deps/parabix)
#     PARABIX_REMOTE=<url|path>     clone source (default: the SFU Parabix remote below)
#     LLVM_DIR=/path/to/llvm/lib/cmake/llvm     directory containing LLVMConfig.cmake
#     LLVM_CONFIG=/path/to/llvm-config          alternative to LLVM_DIR
#
# This script never installs packages and never runs a destructive git reset.

set -euo pipefail

# --- Reproducibility constants (mirrored in README "Reproducibility details") ---
PARABIX_REMOTE_DEFAULT="https://cs-git-research.cs.sfu.ca/cameron/parabix-devel.git"
PARABIX_COMMIT="f0369dd138e2e7a710566d5035f68b9cdc0bf305"
PATCH_REL="patches/utf16-simd-milestone.patch"

# --- Locate the team repository root relative to this script (no hardcoded paths) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PARABIX_REMOTE="${PARABIX_REMOTE:-$PARABIX_REMOTE_DEFAULT}"
PARABIX_DIR="${PARABIX_DIR:-$REPO_ROOT/.deps/parabix}"
PATCH="$REPO_ROOT/$PATCH_REL"

err()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

# Return 0 when $1 can compile and link a trivial C++ program.
compiler_can_link() {
    local tmp prog
    tmp="$(mktemp -d)"
    prog="$tmp/t"
    echo 'int main(){return 0;}' > "$tmp/t.cc"
    if "$1" "$tmp/t.cc" -o "$prog" 2>/dev/null; then
        rm -rf "$tmp"
        return 0
    fi
    rm -rf "$tmp"
    return 1
}

# --- Required programs ---
for tool in git cmake c++ python3; do
    command -v "$tool" >/dev/null 2>&1 \
        || err "'$tool' not found in PATH. Please install it. (This script does not install packages.)"
done
[ -f "$PATCH" ] || err "Milestone patch not found: $PATCH"

# --- Locate LLVM (prefer 16; fall back to newer distro packages on Linux) ---
LLVM_DIR_RESOLVED=""
LLVM_PREFIX=""
LLVM_VERSION=""
if [ -n "${LLVM_DIR:-}" ]; then
    LLVM_DIR_RESOLVED="$LLVM_DIR"
    LLVM_PREFIX="$(cd "$LLVM_DIR/../../.." 2>/dev/null && pwd || true)"
elif [ -n "${LLVM_CONFIG:-}" ]; then
    command -v "$LLVM_CONFIG" >/dev/null 2>&1 || err "LLVM_CONFIG='$LLVM_CONFIG' is not executable."
    LLVM_DIR_RESOLVED="$("$LLVM_CONFIG" --cmakedir)"
    LLVM_PREFIX="$("$LLVM_CONFIG" --prefix)"
elif command -v brew >/dev/null 2>&1 && brew --prefix llvm@16 >/dev/null 2>&1; then
    LLVM_PREFIX="$(brew --prefix llvm@16)"
    LLVM_DIR_RESOLVED="$LLVM_PREFIX/lib/cmake/llvm"
    LLVM_VERSION="16"
else
    for ver in 16 18 19 20; do
        if command -v "llvm-config-${ver}" >/dev/null 2>&1; then
            LLVM_DIR_RESOLVED="$(llvm-config-${ver} --cmakedir)"
            LLVM_PREFIX="$(llvm-config-${ver} --prefix)"
            LLVM_VERSION="$ver"
            break
        fi
    done
fi
[ -n "$LLVM_DIR_RESOLVED" ] \
    || err "Could not locate LLVM (need 16+, or set LLVM_DIR / LLVM_CONFIG).
  macOS : brew install llvm@16
  Linux : install llvm-16-dev / clang-16 (provides llvm-config-16)
  Or set one of:
    LLVM_DIR=/path/to/llvm/lib/cmake/llvm
    LLVM_CONFIG=/path/to/llvm-config"
[ -f "$LLVM_DIR_RESOLVED/LLVMConfig.cmake" ] \
    || err "LLVMConfig.cmake not found under '$LLVM_DIR_RESOLVED'.
  Set LLVM_DIR to the directory that contains LLVMConfig.cmake."
if [ -n "$LLVM_VERSION" ] && [ "$LLVM_VERSION" != "16" ]; then
    info "LLVM ${LLVM_VERSION} cmake dir: $LLVM_DIR_RESOLVED (16 preferred; using newer system LLVM)"
else
    info "LLVM cmake dir: $LLVM_DIR_RESOLVED"
fi

# --- Clone / reuse the Parabix checkout ---
if [ ! -d "$PARABIX_DIR/.git" ]; then
    info "Cloning Parabix into $PARABIX_DIR"
    mkdir -p "$(dirname "$PARABIX_DIR")"
    git clone "$PARABIX_REMOTE" "$PARABIX_DIR"
else
    info "Reusing existing Parabix checkout: $PARABIX_DIR"
fi
cd "$PARABIX_DIR"

# Make sure the pinned commit is available.
if ! git cat-file -e "${PARABIX_COMMIT}^{commit}" 2>/dev/null; then
    info "Fetching Parabix history for $PARABIX_COMMIT"
    git fetch --all --tags --quiet || true
fi
git cat-file -e "${PARABIX_COMMIT}^{commit}" 2>/dev/null \
    || err "Commit $PARABIX_COMMIT not found in $PARABIX_DIR (check network / remote access)."

# --- Apply the milestone patch idempotently ---
if git apply --reverse --check "$PATCH" 2>/dev/null; then
    if [ "$(git rev-parse HEAD)" != "$PARABIX_COMMIT" ]; then
        err "The milestone patch is already applied, but the Parabix checkout is not at the required commit $PARABIX_COMMIT."
    fi
    info "Milestone patch already applied -- skipping."
else
    if [ -n "$(git status --porcelain)" ]; then
        err "$PARABIX_DIR has unexpected local modifications.
  Refusing to reset so your work is never destroyed.
  Inspect it, or 'rm -rf \"$PARABIX_DIR\"' and re-run, or point PARABIX_DIR at a clean path."
    fi
    if [ "$(git rev-parse HEAD)" != "$PARABIX_COMMIT" ]; then
        info "Checking out base commit $PARABIX_COMMIT"
        git checkout -q "$PARABIX_COMMIT"
    fi
    git apply --check "$PATCH" \
        || err "Milestone patch does not apply cleanly to $PARABIX_COMMIT."
    info "Applying milestone patch"
    git apply "$PATCH"
fi

# --- Configure (Release) ---
BUILD_DIR="$PARABIX_DIR/build"
CMAKE_ARGS=(-S "$PARABIX_DIR" -B "$BUILD_DIR"
            -DCMAKE_BUILD_TYPE=Release
            -DLLVM_DIR="$LLVM_DIR_RESOLVED")
# Prefer the LLVM toolchain's clang++/clang (known-good for LLVM 16). On some Linux
# hosts (e.g. CSIL) distro clang++ cannot link against libstdc++; fall back to g++.
CXX_COMPILER=""
C_COMPILER=""
if [ -n "$LLVM_PREFIX" ] && [ -x "$LLVM_PREFIX/bin/clang++" ]; then
    if compiler_can_link "$LLVM_PREFIX/bin/clang++"; then
        CXX_COMPILER="$LLVM_PREFIX/bin/clang++"
        C_COMPILER="$LLVM_PREFIX/bin/clang"
        info "Using compiler: $CXX_COMPILER"
    else
        info "LLVM clang++ cannot link on this host; falling back to system c++."
    fi
fi
if [ -z "$CXX_COMPILER" ]; then
    CXX_COMPILER="$(command -v c++)"
    C_COMPILER="$(command -v cc)"
    info "Using compiler: $CXX_COMPILER"
fi
CMAKE_ARGS+=(-DCMAKE_CXX_COMPILER="$CXX_COMPILER" -DCMAKE_C_COMPILER="$C_COMPILER")

info "Configuring Release build in $BUILD_DIR"
if ! cmake "${CMAKE_ARGS[@]}"; then
    err "CMake configuration failed.
  Common causes: Boost not installed (macOS: brew install boost) or LLVM version mismatch.
  See the CMake output above."
fi

# --- Build only the required target ---
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
info "Building utf16validate (-j$JOBS)"
cmake --build "$BUILD_DIR" --target utf16validate -j"$JOBS"

BIN="$BUILD_DIR/bin/utf16validate"
[ -x "$BIN" ] || err "Build finished but the binary was not found at $BIN."

echo
info "Setup complete."
echo "    Binary : $BIN"
echo "    Test   : $REPO_ROOT/scripts/test_utf16validate.sh"
