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
# Outputs go to MOCK_OUTPUT_DIR (default: build/mock).

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

# Passed through to rpmbuild inside mock so Version/Release/Source0 match the git archive
# (mock's internal rpmbuild -bs does not read host-only settings).
MOCK_RPM_DEFINES=(
  --define "pkg_version ${VERSION}"
  --define "pkg_release ${RELEASE}"
)

rm -rf "${STAGING}"
mkdir -p "${STAGING}" "${OUTPUT_DIR}"

SPEC_STAGING="${STAGING}/slurm-quota.spec"
cp "${SPEC_FILE}" "${SPEC_STAGING}"

ARCHIVE="${STAGING}/${NAME}-${VERSION}.tar.gz"
git -C "${ROOT_DIR}" archive --format=tar.gz --prefix="${NAME}-${VERSION}/" -o "${ARCHIVE}" HEAD

echo "mock --buildsrpm target=${MOCK_TARGET} resultdir=${OUTPUT_DIR}"
mock "${MOCK_RPM_DEFINES[@]}" \
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
  mock "${MOCK_RPM_DEFINES[@]}" \
    --rebuild "${SRPM_PATH}" \
    -r "${MOCK_TARGET}" \
    --resultdir "${OUTPUT_DIR}"
fi

echo "Mock build completed."
