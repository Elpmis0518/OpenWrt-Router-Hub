#!/usr/bin/env bash
# Inject RouteLink router packages into TR3000 ImmortalWrt tree and ensure block.c patch is applied.
set -euo pipefail

WRT_DIR="${1:?Usage: inject-routelink.sh <wrt_dir>}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
RL_OWRT="${REPO_ROOT}/routelink/upstream/openwrt"

for pkg in routelinkd luci-app-routelink; do
  if [ ! -d "${RL_OWRT}/${pkg}" ]; then
    echo "ERROR: Missing RouteLink OpenWrt package: ${RL_OWRT}/${pkg}" >&2
    exit 1
  fi
  rm -rf "${WRT_DIR}/package/${pkg}"
  cp -a "${RL_OWRT}/${pkg}" "${WRT_DIR}/package/${pkg}"
done

# Ensure compile-time block.c + firewall hotplug patch is applied idempotently
python3 "${REPO_ROOT}/routelink/patches/patch-block-c.py" "${WRT_DIR}/package/routelinkd"
echo "Injected routelinkd and luci-app-routelink into ${WRT_DIR}/package/."
