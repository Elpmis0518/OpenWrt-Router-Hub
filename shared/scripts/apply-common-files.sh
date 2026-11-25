#!/usr/bin/env bash
# Install shared rootfs overlay files and pre-built OpenClash Meta arm64 core.
set -euo pipefail

OPENWRT_DIR="${1:?Usage: apply-common-files.sh <openwrt_dir>}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SHARED_FILES="$(cd -- "${SCRIPT_DIR}/../files" && pwd)"

META_CORE_URL="https://raw.githubusercontent.com/vernesong/OpenClash/core/master/meta/clash-linux-arm64.tar.gz"
FILES_DIR="${OPENWRT_DIR}/files"

mkdir -p "${FILES_DIR}"
cp -a "${SHARED_FILES}/." "${FILES_DIR}/"
chmod 0777 "${FILES_DIR}/usr/sbin/auto-whu.sh"
chmod 0755 "${FILES_DIR}/etc/uci-defaults/99-auto-whu-perm"
chmod 0755 "${FILES_DIR}/etc/uci-defaults/99-whu-dns-whitelist"
chmod 0644 "${FILES_DIR}/lib/upgrade/keep.d/luci-app-openclash"

# Fetch latest OpenClash Meta linux-arm64 core
CORE_DIR="${FILES_DIR}/etc/openclash/core"
mkdir -p "${CORE_DIR}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

echo "Downloading OpenClash Meta arm64 core from ${META_CORE_URL}..."
curl -fsSL --retry 3 --retry-delay 5 "${META_CORE_URL}" -o "${TMP_DIR}/clash-meta.tar.gz"
tar -xzf "${TMP_DIR}/clash-meta.tar.gz" -C "${TMP_DIR}"

CLASH_BIN="$(find "${TMP_DIR}" -maxdepth 2 -type f -name 'clash*' ! -name '*.tar.gz' | head -n 1)"
if [ -z "${CLASH_BIN}" ] || [ ! -s "${CLASH_BIN}" ]; then
  echo "ERROR: Failed to locate extracted clash binary in tarball." >&2
  exit 1
fi

install -m 0755 "${CLASH_BIN}" "${CORE_DIR}/clash_meta"
ls -lh "${CORE_DIR}/clash_meta"
