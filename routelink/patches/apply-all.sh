#!/usr/bin/env bash
# Apply all modular RouteLink patches (router plugin + mobile app).
set -euo pipefail

RL_DIR="${1:?Usage: apply-all.sh <routelink_upstream_dir>}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

python3 "${SCRIPT_DIR}/patch-block-c.py" "${RL_DIR}/openwrt/routelinkd"
python3 "${SCRIPT_DIR}/patch-app-compat.py" "${RL_DIR}"
python3 "${SCRIPT_DIR}/patch-hub-updates.py" "${RL_DIR}"

echo "All RouteLink patches applied cleanly to ${RL_DIR}."
