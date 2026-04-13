#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAME="slurm-quota"
SPEC_FILE="${ROOT_DIR}/packaging/slurm-quota.spec"

if [[ ! -f "${SPEC_FILE}" ]]; then
  echo "Spec file not found: ${SPEC_FILE}" >&2
  exit 1
fi

VERSION_INPUT="${1:-${PKG_VERSION:-}}"
if [[ -z "${VERSION_INPUT}" ]]; then
  echo "Missing package version." >&2
  echo "Usage: packaging/scripts/build-srpm.sh <version-or-tag>" >&2
  echo "Or set PKG_VERSION environment variable." >&2
  exit 1
fi

VERSION="${VERSION_INPUT#v}"
RELEASE="${RPM_RELEASE:-1}"
TOPDIR="${RPM_TOPDIR:-${ROOT_DIR}/build/rpm}"
SOURCES_DIR="${TOPDIR}/SOURCES"
SRPMS_DIR="${TOPDIR}/SRPMS"

mkdir -p "${SOURCES_DIR}" "${SRPMS_DIR}" "${TOPDIR}/BUILD" "${TOPDIR}/BUILDROOT" "${TOPDIR}/RPMS" "${TOPDIR}/SPECS"
cp "${SPEC_FILE}" "${TOPDIR}/SPECS/slurm-quota.spec"

ARCHIVE="${SOURCES_DIR}/${NAME}-${VERSION}.tar.gz"
git -C "${ROOT_DIR}" archive --format=tar.gz --prefix="${NAME}-${VERSION}/" -o "${ARCHIVE}" HEAD

rpmbuild \
  --define "_topdir ${TOPDIR}" \
  --define "pkg_version ${VERSION}" \
  --define "pkg_release ${RELEASE}" \
  -bs "${TOPDIR}/SPECS/slurm-quota.spec"

SRPM_PATH="$(ls "${SRPMS_DIR}"/${NAME}-${VERSION}-${RELEASE}*.src.rpm | sort | tail -n 1)"
echo "SRPM built: ${SRPM_PATH}"
