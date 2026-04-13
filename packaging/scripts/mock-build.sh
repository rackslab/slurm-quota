#!/usr/bin/env bash
set -euo pipefail

# Build with mock: by default runs mock --buildsrpm then mock --rebuild (binary RPMs).
# Pass --srpm-only to stop after the SRPM (single mock --buildsrpm).
#
# Usage:
#   packaging/scripts/mock-build.sh <version-or-tag> [el_target] [--srpm-only]
# Or: PKG_VERSION=1.2.3 packaging/scripts/mock-build.sh
#
# Override mock profile:
#   MOCK_TARGET=rocky+epel-9-x86_64 packaging/scripts/mock-build.sh 1.2.3 el9
#
# Outputs go to MOCK_OUTPUT_DIR (default: build/mock). Mock also writes detailed logs
# there (e.g. build.log, root.log, state.log). MOCK_VERBOSE=0 stays quiet
# (default), MOCK_VERBOSE=1 adds -v. Use MOCK_TRACE=1 for --trace.
# MOCK_OPTS: extra mock CLI words (e.g. MOCK_OPTS="--enable-network" — use with care).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAME="slurm-quota"
SPEC_FILE="${ROOT_DIR}/packaging/slurm-quota.spec"

SRPM_ONLY=0
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --srpm-only) SRPM_ONLY=1; shift ;;
    *) POSITIONAL+=("$1"); shift ;;
  esac
done

VERSION_INPUT="${POSITIONAL[0]:-${PKG_VERSION:-}}"
EL_TARGET="${POSITIONAL[1]:-el9}"

if [[ -z "${VERSION_INPUT}" ]]; then
  echo "Missing package version." >&2
  echo "Usage: packaging/scripts/mock-build.sh <version-or-tag> [el_target] [--srpm-only]" >&2
  echo "Or set PKG_VERSION." >&2
  exit 1
fi

VERSION="${VERSION_INPUT#v}"
if [[ ! "${VERSION}" =~ ^[0-9]+(\.[0-9]+){2}([._+-][0-9A-Za-z.-]+)?$ ]]; then
  echo "Invalid version format: ${VERSION_INPUT}" >&2
  echo "Expected semantic version like 1.2.3 (optional suffix allowed)." >&2
  exit 1
fi
RELEASE="${RPM_RELEASE:-1}"
OUTPUT_DIR="${MOCK_OUTPUT_DIR:-${ROOT_DIR}/build/mock}"
STAGING="${MOCK_STAGING_DIR:-${ROOT_DIR}/build/mock-input}"

case "${EL_TARGET}" in
  el9) DEFAULT_MOCK_TARGET="rocky+epel-9-x86_64" ;;
  *)
    echo "Unsupported EL target: ${EL_TARGET}" >&2
    echo "Supported values: el9" >&2
    exit 1
    ;;
esac

MOCK_TARGET="${MOCK_TARGET:-${DEFAULT_MOCK_TARGET}}"

if ! command -v mock >/dev/null 2>&1; then
  echo "mock command not found. Install mock before running this script." >&2
  exit 1
fi

if [[ ! -f "${SPEC_FILE}" ]]; then
  echo "Spec file not found: ${SPEC_FILE}" >&2
  exit 1
fi

# MOCK_VERBOSE: 0 = quiet (default), 1 = -v
case "${MOCK_VERBOSE:-0}" in
  0) MOCK_VERBOSITY=() ;;
  1) MOCK_VERBOSITY=(-v) ;;
  *) MOCK_VERBOSITY=(-v) ;;
esac
if [[ "${MOCK_TRACE:-0}" = "1" ]]; then
  MOCK_TRACE_OPT=(--trace)
else
  MOCK_TRACE_OPT=()
fi
# shellcheck disable=SC2206 # intentional word split for extra mock flags
MOCK_USER_OPTS=( ${MOCK_OPTS-} )

rm -rf "${STAGING}"
mkdir -p "${STAGING}" "${OUTPUT_DIR}"

SPEC_STAGING="${STAGING}/slurm-quota.spec"
cp "${SPEC_FILE}" "${SPEC_STAGING}"

ARCHIVE="${STAGING}/${NAME}-${VERSION}.tar.gz"
git -C "${ROOT_DIR}" archive --format=tar.gz --prefix="${NAME}-${VERSION}/" -o "${ARCHIVE}" HEAD

echo "mock --buildsrpm target=${MOCK_TARGET} resultdir=${OUTPUT_DIR}"
mock "${MOCK_VERBOSITY[@]}" "${MOCK_TRACE_OPT[@]}" "${MOCK_USER_OPTS[@]}" \
  --define "pkg_version ${VERSION}" \
  --define "pkg_release ${RELEASE}" \
  --buildsrpm \
  --spec "${SPEC_STAGING}" \
  --sources "${STAGING}" \
  -r "${MOCK_TARGET}" \
  --resultdir "${OUTPUT_DIR}"

shopt -s nullglob
srpms=( "${OUTPUT_DIR}/${NAME}"-*.src.rpm )
shopt -u nullglob
if [[ ${#srpms[@]} -eq 0 ]]; then
  echo "No SRPM found in ${OUTPUT_DIR}" >&2
  exit 1
fi
# Prefer newest by mtime if multiple SRPMs are present in resultdir.
SRPM_PATH="${srpms[0]}"
for f in "${srpms[@]}"; do
  if [[ "${f}" -nt "${SRPM_PATH}" ]]; then
    SRPM_PATH="${f}"
  fi
done

echo "SRPM: ${SRPM_PATH}"

if [[ "${SRPM_ONLY}" -eq 0 ]]; then
  echo "mock --rebuild target=${MOCK_TARGET} resultdir=${OUTPUT_DIR}"
  mock "${MOCK_VERBOSITY[@]}" "${MOCK_TRACE_OPT[@]}" "${MOCK_USER_OPTS[@]}" \
    --define "pkg_version ${VERSION}" \
    --define "pkg_release ${RELEASE}" \
    --rebuild "${SRPM_PATH}" \
    -r "${MOCK_TARGET}" \
    --resultdir "${OUTPUT_DIR}"
fi

echo "Mock build completed."
echo "Full mock/rpmbuild logs: ${OUTPUT_DIR}/*.log (especially build.log and root.log)"
