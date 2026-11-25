#!/usr/bin/env bash
set -euo pipefail

# Inject OpenWrt-Router-Hub build identity and pre-configured signed RouteLink feed
# into an OpenWrt/ImmortalWrt source tree's files/ overlay.
# Usage: apply-hub-overlay.sh <openwrt_source_dir> <tr3000|ax3000t> <release_tag>

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <openwrt_source_dir> <tr3000|ax3000t> <release_tag>" >&2
  exit 2
fi

SRC_DIR="$1"
DEVICE="$2"
RELEASE_TAG="$3"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
KEYS_DIR="${REPO_ROOT}/routelink/feed/keys"
PAGES_BASE="https://elpmis0518.github.io/OpenWrt-Router-Hub"
USIGN_KEY_ID="d39736bb5c3eb4ef"

mkdir -p "${SRC_DIR}/files/etc/uci-defaults"

# 1. Embed exact Hub build tag into /usr/lib/os-release (BUILD_ID, read by procd ubus system.board)
# and /etc/openwrt_release (DISTRIB_REVISION). Named zzzz-* so it runs AFTER LEDE's zzz-default-settings.
cat > "${SRC_DIR}/files/etc/uci-defaults/zzzz-hub-build-id" <<EOF
#!/bin/sh
TAG="${RELEASE_TAG}"

update_field() {
	file="\$1"
	key="\$2"
	q="\$3"
	[ -f "\$file" ] || return 0
	cur="\$(sed -n "s#^\${key}=##p" "\$file" | head -n 1 | sed -E "s#^['\"]##; s#['\"]\\\$##")"
	base="\$(printf '%s' "\$cur" | sed -E 's#( / )?(tr3000|ax3000t)-[0-9]{8}-[0-9]{6}##g')"
	if [ -n "\$base" ]; then
		new="\${base} / \${TAG}"
	else
		new="\${TAG}"
	fi
	sed -i "/^\${key}=/d" "\$file"
	[ -s "\$file" ] && [ -n "\$(tail -c 1 "\$file")" ] && printf '\\n' >> "\$file"
	printf '%s=%s%s%s\\n' "\$key" "\$q" "\$new" "\$q" >> "\$file"
}

update_field /usr/lib/os-release BUILD_ID '"'
update_field /etc/openwrt_release DISTRIB_REVISION "'"
exit 0
EOF
chmod 0755 "${SRC_DIR}/files/etc/uci-defaults/zzzz-hub-build-id"

# 2. Pre-install RouteLink feed public key and custom feed repository URL
case "${DEVICE}" in
  tr3000)
    mkdir -p "${SRC_DIR}/files/etc/apk/keys"
    cp -f "${KEYS_DIR}/routelink-apk.pem" "${SRC_DIR}/files/etc/apk/keys/routelink.pem"
    chmod 0644 "${SRC_DIR}/files/etc/apk/keys/routelink.pem"

    FEED_URL="${PAGES_BASE}/agent/SNAPSHOT/aarch64_cortex-a53/packages.adb"
    cat > "${SRC_DIR}/files/etc/uci-defaults/98-routelink-feed" <<EOF
#!/bin/sh
mkdir -p /etc/apk/repositories.d
LIST="/etc/apk/repositories.d/customfeeds.list"
touch "\$LIST"
if ! grep -Fqx "${FEED_URL}" "\$LIST"; then
	[ -s "\$LIST" ] && [ -n "\$(tail -c 1 "\$LIST")" ] && printf '\\n' >> "\$LIST"
	printf '%s\\n' "${FEED_URL}" >> "\$LIST"
fi
exit 0
EOF
    chmod 0755 "${SRC_DIR}/files/etc/uci-defaults/98-routelink-feed"
    ;;

  ax3000t)
    mkdir -p "${SRC_DIR}/files/etc/opkg/keys"
    cp -f "${KEYS_DIR}/${USIGN_KEY_ID}" "${SRC_DIR}/files/etc/opkg/keys/${USIGN_KEY_ID}"
    chmod 0644 "${SRC_DIR}/files/etc/opkg/keys/${USIGN_KEY_ID}"

    FEED_LINE="src/gz routelink ${PAGES_BASE}/agent/24.10/aarch64_cortex-a53"
    cat > "${SRC_DIR}/files/etc/uci-defaults/98-routelink-feed" <<EOF
#!/bin/sh
mkdir -p /etc/opkg
CONF="/etc/opkg/customfeeds.conf"
touch "\$CONF"
sed -i '/^src\/gz routelink /d' "\$CONF"
[ -s "\$CONF" ] && [ -n "\$(tail -c 1 "\$CONF")" ] && printf '\\n' >> "\$CONF"
printf '%s\\n' "${FEED_LINE}" >> "\$CONF"
exit 0
EOF
    chmod 0755 "${SRC_DIR}/files/etc/uci-defaults/98-routelink-feed"
    ;;

  *)
    echo "Unsupported device: ${DEVICE} (expected tr3000 or ax3000t)" >&2
    exit 2
    ;;
esac

echo "Applied Hub overlay (${DEVICE}, ${RELEASE_TAG}) to ${SRC_DIR}/files."
