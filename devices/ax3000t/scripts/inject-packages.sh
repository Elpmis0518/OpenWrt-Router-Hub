#!/usr/bin/env bash
# Inject AX3000T upstream-preserved packages (UA3F, ddns-go, argon), OpenClash (dev), and RouteLink.
set -euo pipefail

OPENWRT_DIR="${1:?Usage: inject-packages.sh <openwrt_dir> <pre-feeds|post-feeds>}"
STAGE="${2:?Usage: inject-packages.sh <openwrt_dir> <pre-feeds|post-feeds>}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEV_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd -- "${DEV_DIR}/../.." && pwd)"

case "${STAGE}" in
  pre-feeds)
    # 1. Upstream UA3F
    rm -rf "${OPENWRT_DIR}/package/UA3F"
    git clone --depth=1 https://github.com/SunBK201/UA3F.git "${OPENWRT_DIR}/package/UA3F"
    test -f "${OPENWRT_DIR}/package/UA3F/openwrt/Makefile"

    # 2. Upstream diy-part1.sh (adds passwall2 feed)
    (
      cd "${OPENWRT_DIR}"
      bash "${DEV_DIR}/upstream/diy-part1.sh"
    )
    ;;

  post-feeds)
    # 1. Upstream ddns-go
    rm -rf "${OPENWRT_DIR}/package/ddns-go"
    git clone --depth=1 https://github.com/sirpdboy/luci-app-ddns-go.git "${OPENWRT_DIR}/package/ddns-go"

    # 2. Upstream Argon theme
    mkdir -p "${OPENWRT_DIR}/package/lean"
    rm -rf "${OPENWRT_DIR}/package/lean/luci-theme-argon"
    git clone --depth=1 -b master https://github.com/jerrykuku/luci-theme-argon.git "${OPENWRT_DIR}/package/lean/luci-theme-argon"

    # 3. OpenClash (dev branch)
    TMP_OC="$(mktemp -d)"
    trap 'rm -rf "${TMP_OC}"' EXIT
    git clone --depth=1 --single-branch -b dev https://github.com/vernesong/OpenClash.git "${TMP_OC}/OpenClash"
    rm -rf "${OPENWRT_DIR}/package/luci-app-openclash"
    cp -a "${TMP_OC}/OpenClash/luci-app-openclash" "${OPENWRT_DIR}/package/luci-app-openclash"

    # 4. RouteLink router plugin (with compile-time iptables fallback patch for firewall3)
    RL_OWRT="${REPO_ROOT}/routelink/upstream/openwrt"
    for pkg in routelinkd luci-app-routelink; do
      if [ ! -d "${RL_OWRT}/${pkg}" ]; then
        echo "ERROR: Missing RouteLink OpenWrt package: ${RL_OWRT}/${pkg}" >&2
        exit 1
      fi
      rm -rf "${OPENWRT_DIR}/package/${pkg}"
      cp -a "${RL_OWRT}/${pkg}" "${OPENWRT_DIR}/package/${pkg}"
    done
    python3 "${REPO_ROOT}/routelink/patches/patch-block-c.py" "${OPENWRT_DIR}/package/routelinkd"
    ;;

  *)
    echo "ERROR: Unknown stage '${STAGE}' (expected pre-feeds or post-feeds)." >&2
    exit 2
    ;;
esac
