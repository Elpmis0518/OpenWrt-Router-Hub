#!/usr/bin/env python3
"""Idempotently apply declarative Kconfig entries from packages.conf to an OpenWrt .config file."""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINE_RE = re.compile(r"^(CONFIG_[A-Za-z0-9_+-]+)=(.*)$")
UNSET_RE = re.compile(r"^# (CONFIG_[A-Za-z0-9_+-]+) is not set$")


def parse_packages_conf(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = LINE_RE.match(line)
        if not m:
            raise ValueError(f"{path}:{lineno}: invalid directive: {line!r}")
        key, val = m.group(1), m.group(2).strip()
        rendered = f"# {key} is not set" if val == "n" else f"{key}={val}"
        if key in seen:
            entries[seen[key]] = (key, rendered)
        else:
            seen[key] = len(entries)
            entries.append((key, rendered))
    return entries


def apply_config(config_path: Path, conf_path: Path) -> None:
    entries = parse_packages_conf(conf_path)
    managed_keys = {k for k, _ in entries}

    existing_lines = config_path.read_text(encoding="utf-8").splitlines()
    filtered: list[str] = []
    for raw in existing_lines:
        m_set = LINE_RE.match(raw)
        if m_set and m_set.group(1) in managed_keys:
            continue
        m_unset = UNSET_RE.match(raw)
        if m_unset and m_unset.group(1) in managed_keys:
            continue
        if raw.strip() == "# --- OpenWrt-Router-Hub managed entries ---":
            continue
        filtered.append(raw)

    while filtered and filtered[-1] == "":
        filtered.pop()

    filtered.append("")
    filtered.append("# --- OpenWrt-Router-Hub managed entries ---")
    for _, rendered in entries:
        filtered.append(rendered)

    config_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
    print(f"Applied {len(entries)} declarative entries from {conf_path} to {config_path}.")


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <openwrt/.config> <packages.conf>", file=sys.stderr)
        return 2
    apply_config(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
