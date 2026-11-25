#!/usr/bin/env bash
# Unconditionally sync all three upstream repositories and apply RouteLink patches.
# Never touches .github/workflows/.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

UP_TR3000_URL="https://github.com/VIKINGYFY/OpenWRT-CI.git"
UP_AX3000T_URL="https://github.com/zc360/Xiaomi-ax3000t-openwrt.git"
UP_ROUTELINK_URL="https://github.com/tsix2019/RouteLink.git"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

fetch_upstream() {
  local name="$1"
  local url="$2"
  local branch="$3"
  local dest="${TMP_DIR}/${name}"
  echo "Cloning ${url} (${branch})..."
  git clone --depth=1 --single-branch --branch "${branch}" "${url}" "${dest}"
}

fetch_upstream "OpenWRT-CI" "${UP_TR3000_URL}" "main"
fetch_upstream "Xiaomi-ax3000t-openwrt" "${UP_AX3000T_URL}" "main"
fetch_upstream "RouteLink" "${UP_ROUTELINK_URL}" "main"

# 1. Sync VIKINGYFY/OpenWRT-CI (TR3000-relevant Config only, plus Scripts/ & .github/scripts/)
TR_DEST="${REPO_ROOT}/devices/tr3000-256mb/upstream"
rm -rf "${TR_DEST}/Config" "${TR_DEST}/Scripts" "${TR_DEST}/ci-scripts"
mkdir -p "${TR_DEST}/Config" "${TR_DEST}/Scripts" "${TR_DEST}/ci-scripts"
cp -f "${TMP_DIR}/OpenWRT-CI/Config/GENERAL.txt" "${TR_DEST}/Config/"
cp -f "${TMP_DIR}/OpenWRT-CI/Config/MEDIATEK-WIFI-YES.txt" "${TR_DEST}/Config/"
cp -a "${TMP_DIR}/OpenWRT-CI/Scripts/." "${TR_DEST}/Scripts/"
cp -a "${TMP_DIR}/OpenWRT-CI/.github/scripts/." "${TR_DEST}/ci-scripts/"
chmod +x "${TR_DEST}/Scripts/"*.sh "${TR_DEST}/ci-scripts/"*.sh

# 2. Sync zc360/Xiaomi-ax3000t-openwrt (.config, diy-part1.sh)
AX_DEST="${REPO_ROOT}/devices/ax3000t/upstream"
mkdir -p "${AX_DEST}"
cp -f "${TMP_DIR}/Xiaomi-ax3000t-openwrt/.config" "${AX_DEST}/.config"
cp -f "${TMP_DIR}/Xiaomi-ax3000t-openwrt/diy-part1.sh" "${AX_DEST}/diy-part1.sh"
chmod +x "${AX_DEST}/diy-part1.sh"

# 3. Sync tsix2019/RouteLink (full app + openwrt/ plugin; exclude .git, .github, docs/screenshots)
RL_DEST="${REPO_ROOT}/routelink/upstream"
rm -rf "${RL_DEST}"
mkdir -p "${RL_DEST}"
rsync -a --delete \
  --exclude='.git/' \
  --exclude='.github/' \
  --exclude='docs/screenshots/' \
  --exclude='node_modules/' \
  "${TMP_DIR}/RouteLink/" "${RL_DEST}/"

# 4. Apply modular RouteLink patches (router block.c + mobile app compatibility)
"${REPO_ROOT}/routelink/patches/apply-all.sh" "${RL_DEST}"

echo "Upstream sync and RouteLink patch application completed."
