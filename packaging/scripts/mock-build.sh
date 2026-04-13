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
# there (e.g. build.log, root.log, state.log). Set MOCK_VERBOSE=2 for mock -v -v.
# MOCK_OPTS: extra mock CLI words (e.g. MOCK_OPTS="--trace" — use with care).

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

# MOCK_VERBOSE: 0 = quiet, 1 = -v (default), 2 = -v -v
case "${MOCK_VERBOSE:-1}" in
  0) MOCK_VERBOSITY=() ;;
  1) MOCK_VERBOSITY=(-v) ;;
  2) MOCK_VERBOSITY=(-v -v) ;;
  *) MOCK_VERBOSITY=(-v) ;;
esac
# shellcheck disable=SC2206 # intentional word split for extra mock flags
MOCK_USER_OPTS=( ${MOCK_OPTS-} )

rm -rf "${STAGING}"
mkdir -p "${STAGING}" "${OUTPUT_DIR}"

# mock(1) --define is not applied to the inner rpmbuild -bb during --rebuild, so the spec
# inside the SRPM would see pkg_version unset and fall back to 1.0.0 (wrong Source0).
# --macrofile is installed in the chroot and loaded for every rpmbuild phase.
MOCK_MACROFILE="${STAGING}/slurm-quota-version.mac"
{
  echo "%pkg_version ${VERSION}"
  echo "%pkg_release ${RELEASE}"
} > "${MOCK_MACROFILE}"

SPEC_STAGING="${STAGING}/slurm-quota.spec"
cp "${SPEC_FILE}" "${SPEC_STAGING}"

ARCHIVE="${STAGING}/${NAME}-${VERSION}.tar.gz"
git -C "${ROOT_DIR}" archive --format=tar.gz --prefix="${NAME}-${VERSION}/" -o "${ARCHIVE}" HEAD

echo "mock --buildsrpm target=${MOCK_TARGET} resultdir=${OUTPUT_DIR}"
mock "${MOCK_VERBOSITY[@]}" "${MOCK_USER_OPTS[@]}" \
  --macrofile "${MOCK_MACROFILE}" \
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
  mock "${MOCK_VERBOSITY[@]}" "${MOCK_USER_OPTS[@]}" \
    --macrofile "${MOCK_MACROFILE}" \
    --rebuild "${SRPM_PATH}" \
    -r "${MOCK_TARGET}" \
    --resultdir "${OUTPUT_DIR}"
fi

echo "Mock build completed."
echo "Full mock/rpmbuild logs: ${OUTPUT_DIR}/*.log (especially build.log and root.log)"
