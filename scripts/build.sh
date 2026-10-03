#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-host}"
# The service reports in /api/about the last commit that changed what the gateway is built
# from ("+changes" when those files differ): commits elsewhere (documentation, catalogs, tests,
# the toolchain's pinned recipe) leave its bytes as they were, so a release built later from
# the same sources is the build the guest accepted.
sources=(device/src device/vendor device/manager device/Makefile scripts/embed-manager.py)
build_id="$( (git log -1 --format=%H -- "${sources[@]}" 2>/dev/null || true) | cut -c1-12)"
build_id="${build_id:-unknown}"
git diff --quiet HEAD -- "${sources[@]}" 2>/dev/null || build_id="$build_id+changes"
case "$mode" in
  host)
    python3 scripts/embed-manager.py build/host/manager_assets.h
    make -C device OUT=../build/host DISC_BUILD="$build_id" all test
    ;;
  mips)
    python3 scripts/embed-manager.py build/mips/manager_assets.h
    # Two variants of one build: disc-service as built, with debug information (checks and analysis),
    # and disc-service-release, stripped, for release packages (owner, 2026-10-02).
    # The player kernel (Linux 4.4.94) runs FP branch delay slots from a stack
    # trampoline when it emulates the FPU; hard-float output crashed on the first
    # WebSocket. Refuse any toolchain whose executables are not soft-float.
    docker run --rm --platform linux/amd64 --network none -e DISC_BUILD="$build_id" \
      -v "$PWD:/src" -w /src/device "${DISC_TOOLCHAIN_IMAGE:-disc-native-toolchain}" \
      sh -c 'make OUT=../build/mips CC="${CROSS}gcc" LDFLAGS=-static DISC_BUILD="$DISC_BUILD" all ../build/mips/framing-test ../build/mips/app-install-tool ../build/mips/update-tool ../build/mips/sha256-test && "${CROSS}strip" --strip-all -o ../build/mips/disc-service-release ../build/mips/disc-service && "${CROSS}readelf" -h -l ../build/mips/disc-service && for exe in disc-service disc-service-release; do "${CROSS}readelf" -A ../build/mips/$exe | grep -q "FP ABI: Soft float" || { echo "$exe is not soft-float" >&2; exit 1; }; if "${CROSS}objdump" -d ../build/mips/$exe | grep -qE "\s(bc1[ft]|mtc1|mfc1|lwc1|swc1|ldc1|sdc1)\s"; then echo "$exe contains FPU instructions" >&2; exit 1; fi; done && "${CROSS}readelf" -A ../build/mips/disc-service | grep "FP ABI"'
    ;;
  *) echo 'Usage: scripts/build.sh host|mips' >&2; exit 2;;
esac
