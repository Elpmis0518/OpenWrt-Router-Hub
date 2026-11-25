#!/usr/bin/env bash
# Generate single-device .config for Cudy TR3000 v1 256MB from upstream MEDIATEK-WIFI-YES.txt.
set -euo pipefail

WRT_DIR="${1:?Usage: prepare-config.sh <wrt_dir>}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
UP_CONFIG="${DEV_DIR}/upstream/Config"

BASE_CFG="${UP_CONFIG}/MEDIATEK-WIFI-YES.txt"
GEN_CFG="${UP_CONFIG}/GENERAL.txt"
PKG_CONF="${DEV_DIR}/packages.conf"
OUT_CFG="${WRT_DIR}/.config"

TARGET_DEVICE="CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_cudy_tr3000-v1-256mb=y"

for f in "${BASE_CFG}" "${GEN_CFG}" "${PKG_CONF}"; do
  if [ ! -f "$f" ]; then
    echo "ERROR: Missing required config input: $f" >&2
    exit 1
  fi
done

if ! grep -Fxq "${TARGET_DEVICE}" "${BASE_CFG}"; then
  echo "ERROR: ${TARGET_DEVICE} not found in ${BASE_CFG}" >&2
  exit 1
fi

# Filter MEDIATEK-WIFI-YES.txt to keep only cudy_tr3000-v1-256mb among mediatek_filogic devices
: > "${OUT_CFG}"
awk -v keep="${TARGET_DEVICE}" '
  /^CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_/ {
    if ($0 == keep) print $0
    next
  }
  { print $0 }
' "${BASE_CFG}" >> "${OUT_CFG}"

# Append upstream GENERAL.txt and declarative device packages.conf
cat "${GEN_CFG}" >> "${OUT_CFG}"
printf '\n' >> "${OUT_CFG}"
awk '!/^[[:space:]]*(#|$)/' "${PKG_CONF}" >> "${OUT_CFG}"

echo "Generated single-device TR3000 config at ${OUT_CFG}."
grep '^CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_' "${OUT_CFG}"
