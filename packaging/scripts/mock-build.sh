#!/usr/bin/env bash
set -euo pipefail

# Rebuild SRPM in mock.
# Usage: packaging/scripts/mock-build.sh [el_target] [srpm_path]
# Default el_target is el9.
# Override mock profile if needed:
#   MOCK_TARGET=rocky+epel-9-x86_64 packaging/scripts/mock-build.sh el9

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EL_TARGET="${1:-el9}"
SRPM_PATH="${2:-}"
OUTPUT_DIR="${MOCK_OUTPUT_DIR:-${ROOT_DIR}/build/mock}"

case "${EL_TARGET}" in
  el9) DEFAULT_MOCK_TARGET="rocky+epel-9-x86_64" ;;
  el8) DEFAULT_MOCK_TARGET="rocky+epel-8-x86_64" ;;
  *)
    echo "Unsupported EL target: ${EL_TARGET}" >&2
    echo "Supported values: el8, el9" >&2
    exit 1
    ;;
esac

MOCK_TARGET="${MOCK_TARGET:-${DEFAULT_MOCK_TARGET}}"

if ! command -v mock >/dev/null 2>&1; then
  echo "mock command not found. Install mock before running this script." >&2
  exit 1
fi

if [[ -z "${SRPM_PATH}" ]]; then
  SRPM_PATH="$(ls "${ROOT_DIR}"/build/rpm/SRPMS/slurm-quota-*.src.rpm 2>/dev/null | sort | tail -n 1 || true)"
fi

if [[ -z "${SRPM_PATH}" ]]; then
  VERSION_INPUT="${SRPM_VERSION:-${PKG_VERSION:-}}"
  if [[ -z "${VERSION_INPUT}" ]]; then
    echo "No SRPM provided and no SRPM available in build/rpm/SRPMS." >&2
    echo "Provide SRPM path as 2nd argument, or set SRPM_VERSION/PKG_VERSION." >&2
    exit 1
  fi
  "${ROOT_DIR}/packaging/scripts/build-srpm.sh" "${VERSION_INPUT}"
  SRPM_PATH="$(ls "${ROOT_DIR}"/build/rpm/SRPMS/slurm-quota-*.src.rpm | sort | tail -n 1)"
fi

if [[ ! -f "${SRPM_PATH}" ]]; then
  echo "SRPM not found: ${SRPM_PATH}" >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"
cp -f "${SRPM_PATH}" "${OUTPUT_DIR}/"

echo "Running mock target: ${MOCK_TARGET}"
echo "Using SRPM: ${SRPM_PATH}"
echo "Output directory: ${OUTPUT_DIR}"

if command -v sudo >/dev/null 2>&1; then
  sudo mock --rebuild "${SRPM_PATH}" -r "${MOCK_TARGET}" --resultdir "${OUTPUT_DIR}"
else
  mock --rebuild "${SRPM_PATH}" -r "${MOCK_TARGET}" --resultdir "${OUTPUT_DIR}"
fi

echo "Mock build completed."
