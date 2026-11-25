#!/usr/bin/env python3
"""Conditionally patch mac80211 Build/Prepare for Linux 6.12+ wiphy_namespace ns_common signature."""

from __future__ import annotations

import re
import sys
from pathlib import Path

SENTINEL = "# OpenWrt-Router-Hub: adaptive wiphy_namespace ns_common compatibility"

PATCH_BLOCK = (
    f"\t{SENTINEL}\n"
    '\tif [ -f "$(LINUX_DIR)/include/linux/device/class.h" ] && \\\n'
    '\t   grep -qE "const struct ns_common \\*.*namespace" "$(LINUX_DIR)/include/linux/device/class.h" && \\\n'
    '\t   [ -f "$(PKG_BUILD_DIR)/net/wireless/sysfs.c" ] && \\\n'
    '\t   grep -q "static const void \\*wiphy_namespace" "$(PKG_BUILD_DIR)/net/wireless/sysfs.c"; then \\\n'
    "\t\tsed -i \\\n"
    "\t\t\t-e 's/static const void \\*wiphy_namespace/static const struct ns_common *wiphy_namespace/' \\\n"
    "\t\t\t-e 's/return wiphy_net(wiphy);/return \\&wiphy_net(wiphy)->ns;/' \\\n"
    '\t\t\t"$(PKG_BUILD_DIR)/net/wireless/sysfs.c"; \\\n'
    "\tfi\n"
)


def patch_makefile(makefile_path: Path) -> bool:
    if not makefile_path.is_file():
        raise FileNotFoundError(f"mac80211 Makefile not found: {makefile_path}")

    content = makefile_path.read_text(encoding="utf-8")
    if SENTINEL in content:
        print(f"mac80211 adaptive patch already present in {makefile_path}.")
        return False

    pattern = re.compile(r"(^define Build/Prepare\n.*?)(^endef\b)", re.MULTILINE | re.DOTALL)
    match = pattern.search(content)
    if not match:
        raise RuntimeError(f"Could not locate 'define Build/Prepare' block in {makefile_path}")

    updated = content[: match.start(2)] + PATCH_BLOCK + content[match.start(2) :]
    makefile_path.write_text(updated, encoding="utf-8")
    print(f"Patched Build/Prepare in {makefile_path}.")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <openwrt_dir_or_makefile>", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])
    makefile = target / "package/kernel/mac80211/Makefile" if target.is_dir() else target
    patch_makefile(makefile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
