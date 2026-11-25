#!/usr/bin/env python3
"""Assemble the GitHub Pages static site for Elpmis0518/OpenWrt-Router-Hub:
  - /index.html                                  Hub overview & feed quick-setup guide
  - /firmware/manifest.json                      TR3000 & AX3000T online firmware catalog
  - /agent/manifest.json                         Official-compatible RouteLink plugin manifest
  - /agent/keys/{routelink-apk.pem,<usign_id>}   Public signing keys for apk & opkg feeds
  - /agent/SNAPSHOT/aarch64_cortex-a53/          TR3000 signed apk feed (*routelink*.apk + packages.adb)
  - /agent/24.10/aarch64_cortex-a53/             AX3000T signed opkg feed (*routelink*.ipk + Packages*)

Reads directly from published GitHub Releases so every deployment is atomic and branch-free.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGES_ORDER = ("routelinkd", "luci-app-routelink", "luci-i18n-routelink-zh-cn")
API_VERSION = 1

DEVICE_SPECS = {
    "tr3000": {
        "prefix": "tr3000-",
        "board": "cudy,tr3000-v1-256mb",
        "model": "Cudy TR3000 v1 (256M)",
        "target": "mediatek/filogic",
        "distribution": "ImmortalWRT",
        "release_key": "SNAPSHOT",
        "arch": "aarch64_cortex-a53",
        "format": "apk",
        "sysupgrade_re": re.compile(r"cudy_tr3000-v1-256mb-squashfs-sysupgrade\.bin$"),
    },
    "ax3000t": {
        "prefix": "ax3000t-",
        "board": "xiaomi,mi-router-ax3000t",
        "model": "Xiaomi Mi Router AX3000T",
        "target": "mediatek/filogic",
        "distribution": "OpenWrt",
        "release_key": "24.10",
        "arch": "aarch64_cortex-a53",
        "format": "ipk",
        "sysupgrade_re": re.compile(r"xiaomi_mi-router-ax3000t-squashfs-sysupgrade\.bin$"),
    },
}


def github_headers(url: str, accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers = {"Accept": accept, "User-Agent": "OpenWrt-Router-Hub-Pages"}
    if url.startswith("https://api.github.com/"):
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_bytes(url: str, accept: str = "application/octet-stream") -> bytes:
    req = urllib.request.Request(url, headers=github_headers(url, accept))
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_json(url: str) -> Any:
    return json.loads(fetch_bytes(url, accept="application/vnd.github+json").decode("utf-8"))


def parse_sha256sums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line.strip())
        if m:
            out[m.group(2).strip()] = m.group(1).lower()
    return out


def asset_sha256(asset: dict[str, Any], sums: dict[str, str]) -> str | None:
    name = str(asset.get("name", ""))
    if name in sums:
        return sums[name]
    digest = asset.get("digest")
    if isinstance(digest, str):
        m = re.match(r"^sha256:([0-9a-fA-F]{64})$", digest)
        if m:
            return m.group(1).lower()
    return None


def parse_plugin_pkg(name: str) -> tuple[str, str, str] | None:
    """Return (package_name, version, format) for RouteLink packages."""
    for pkg in sorted(PACKAGES_ORDER, key=len, reverse=True):
        m_ipk = re.match(rf"^{re.escape(pkg)}_([^_]+)_[^.]+\.ipk$", name)
        if m_ipk:
            return pkg, m_ipk.group(1), "ipk"
        m_apk = re.match(rf"^{re.escape(pkg)}-(\d[^/]*)\.apk$", name)
        if m_apk:
            return pkg, m_apk.group(1), "apk"
    return None


def load_release_sha256sums(release: dict[str, Any]) -> dict[str, str]:
    for asset in release.get("assets", []):
        if str(asset.get("name", "")).upper() == "SHA256SUMS":
            try:
                raw = fetch_bytes(asset["browser_download_url"]).decode("utf-8", errors="replace")
                return parse_sha256sums(raw)
            except Exception as exc:
                print(f"Warning: failed to fetch SHA256SUMS for {release.get('tag_name')}: {exc}", file=sys.stderr)
    return {}


def build_firmware_entry(release: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any] | None:
    tag = str(release.get("tag_name", ""))
    prefix = str(spec["prefix"])
    version = tag[len(prefix):] if tag.startswith(prefix) else tag
    sums = load_release_sha256sums(release)
    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        if not spec["sysupgrade_re"].search(name):
            continue
        sha = asset_sha256(asset, sums)
        if not sha:
            continue
        return {
            "tag": tag,
            "version": version,
            "name": name,
            "url": asset["browser_download_url"],
            "sha256": sha,
            "size": int(asset.get("size", 0)),
            "publishedAt": release.get("published_at") or "",
            "htmlUrl": release.get("html_url") or "",
        }
    return None


def collect_agent_target(
    release: dict[str, Any],
    spec: dict[str, Any],
    agent_dir: Path,
) -> tuple[str, dict[str, Any], str | None]:
    """Download standalone RouteLink packages & feed indexes into /agent/<rel>/<arch>/."""
    rel_key = str(spec["release_key"])
    arch = str(spec["arch"])
    fmt = str(spec["format"])
    target_dir = agent_dir / rel_key / arch
    target_dir.mkdir(parents=True, exist_ok=True)

    files_meta: list[dict[str, Any]] = []
    daemon_version: str | None = None
    index_names = {"packages.adb"} if fmt == "apk" else {"Packages", "Packages.gz", "Packages.sig"}

    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        parsed = parse_plugin_pkg(name)
        if parsed and parsed[2] == fmt:
            pkg_name, pkg_ver, _ = parsed
            data = fetch_bytes(url)
            (target_dir / name).write_bytes(data)
            sha = hashlib.sha256(data).hexdigest()
            files_meta.append(
                {
                    "package": pkg_name,
                    "name": name,
                    "url": url,
                    "sha256": sha,
                    "size": len(data),
                }
            )
            if pkg_name == "routelinkd":
                daemon_version = re.sub(r"-(?:r)?\d+$", "", pkg_ver)
        elif name in index_names:
            data = fetch_bytes(url)
            (target_dir / name).write_bytes(data)

    files_meta.sort(key=lambda item: PACKAGES_ORDER.index(item["package"]))
    return f"{rel_key}/{arch}", {"format": fmt, "files": files_meta}, daemon_version


def render_index_html(
    repo: str,
    site_url: str,
    firmware_manifest: dict[str, Any],
    agent_manifest: dict[str, Any],
    app_release: dict[str, Any] | None,
    usign_key_id: str,
) -> str:
    tr = firmware_manifest["boards"].get("cudy,tr3000-v1-256mb", {}).get("latest")
    ax = firmware_manifest["boards"].get("xiaomi,mi-router-ax3000t", {}).get("latest")
    tr_label = html.escape(tr["tag"]) if tr else "N/A"
    ax_label = html.escape(ax["tag"]) if ax else "N/A"
    app_label = html.escape(app_release["tag_name"]) if app_release else "N/A"
    agent_ver = html.escape(agent_manifest.get("version", "1.0.0"))
    generated = html.escape(firmware_manifest["generatedAt"])

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>OpenWrt-Router-Hub · 固件与 RouteLink 软件源</title>
  <style>
    :root {{ color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ max-width: 860px; margin: 2rem auto; padding: 0 1.25rem; line-height: 1.6; }}
    h1 {{ margin-bottom: 0.25rem; }}
    .meta {{ opacity: 0.72; font-size: 0.92rem; margin-bottom: 1.5rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 1rem; margin: 1.25rem 0; }}
    .card {{ border: 1px solid rgba(128,128,128,.28); border-radius: 12px; padding: 1rem 1.1rem; }}
    .card h2 {{ margin: 0 0 0.4rem; font-size: 1.05rem; }}
    pre {{ overflow-x: auto; padding: 0.85rem 1rem; border-radius: 10px; background: rgba(128,128,128,.12); font-size: 0.88rem; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    a {{ color: #0a5bff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>OpenWrt-Router-Hub</h1>
  <div class="meta">
    仓库：<a href="https://github.com/{html.escape(repo)}">{html.escape(repo)}</a> · 更新时间：{generated}
  </div>

  <div class="grid">
    <div class="card">
      <h2>Cudy TR3000 v1 (256MB)</h2>
      <div>最新固件：<code>{tr_label}</code></div>
      <div><a href="{site_url}/firmware/manifest.json">firmware/manifest.json</a></div>
    </div>
    <div class="card">
      <h2>Xiaomi AX3000T (LEDE)</h2>
      <div>最新固件：<code>{ax_label}</code></div>
      <div><a href="{site_url}/firmware/manifest.json">firmware/manifest.json</a></div>
    </div>
    <div class="card">
      <h2>RouteLink 插件 &amp; App</h2>
      <div>插件版本：<code>{agent_ver}</code> · App：<code>{app_label}</code></div>
      <div><a href="{site_url}/agent/manifest.json">agent/manifest.json</a></div>
    </div>
  </div>

  <h2>TR3000（ImmortalWrt / apk）RouteLink 软件源</h2>
  <pre><code>wget -O /etc/apk/keys/routelink.pem {site_url}/agent/keys/routelink-apk.pem
echo "{site_url}/agent/SNAPSHOT/aarch64_cortex-a53/packages.adb" &gt;&gt; /etc/apk/repositories.d/customfeeds.list
apk update &amp;&amp; apk upgrade routelinkd luci-app-routelink luci-i18n-routelink-zh-cn</code></pre>

  <h2>AX3000T（LEDE / opkg）RouteLink 软件源</h2>
  <pre><code>wget -O /etc/opkg/keys/{html.escape(usign_key_id)} {site_url}/agent/keys/{html.escape(usign_key_id)}
echo "src/gz routelink {site_url}/agent/24.10/aarch64_cortex-a53" &gt;&gt; /etc/opkg/customfeeds.conf
opkg update &amp;&amp; opkg upgrade routelinkd luci-app-routelink luci-i18n-routelink-zh-cn</code></pre>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build GitHub Pages site from GitHub Releases")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "Elpmis0518/OpenWrt-Router-Hub"))
    parser.add_argument("--site-url", default="https://elpmis0518.github.io/OpenWrt-Router-Hub")
    parser.add_argument("--keys-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--releases-json", help="Optional local releases JSON file for offline testing")
    args = parser.parse_args()

    out_dir = Path(args.out)
    keys_dir = Path(args.keys_dir)
    site_url = args.site_url.rstrip("/")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "agent" / "keys").mkdir(parents=True, exist_ok=True)
    (out_dir / "firmware").mkdir(parents=True, exist_ok=True)
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")

    # Copy public signing keys
    usign_key_id = "d39736bb5c3eb4ef"
    for key_file in sorted(keys_dir.iterdir()):
        if key_file.is_file():
            shutil.copy2(key_file, out_dir / "agent" / "keys" / key_file.name)
            if not key_file.name.endswith(".pem") and re.fullmatch(r"[0-9a-f]{16}", key_file.name):
                usign_key_id = key_file.name

    if args.releases_json:
        releases = json.loads(Path(args.releases_json).read_text(encoding="utf-8"))
    else:
        releases = fetch_json(f"https://api.github.com/repos/{args.repo}/releases?per_page=30")

    published = [r for r in releases if not r.get("draft") and not r.get("prerelease")]
    published.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    firmware_manifest: dict[str, Any] = {
        "repo": args.repo,
        "site": site_url,
        "generatedAt": now_iso,
        "boards": {},
    }

    agent_targets: dict[str, Any] = {}
    agent_version = "1.0.0"
    agent_tag = ""

    for dev_key, spec in DEVICE_SPECS.items():
        prefix = str(spec["prefix"])
        matching = [r for r in published if str(r.get("tag_name", "")).startswith(prefix)]
        fw_entries: list[dict[str, Any]] = []
        for rel in matching[:3]:
            entry = build_firmware_entry(rel, spec)
            if entry:
                fw_entries.append(entry)
        if fw_entries:
            firmware_manifest["boards"][spec["board"]] = {
                "device": dev_key,
                "model": spec["model"],
                "target": spec["target"],
                "distribution": spec["distribution"],
                "latest": fw_entries[0],
                "releases": fw_entries,
            }

        if matching and not args.releases_json:
            newest_rel = matching[0]
            target_key, target_data, ver = collect_agent_target(newest_rel, spec, out_dir / "agent")
            if target_data["files"]:
                agent_targets[target_key] = target_data
                if ver:
                    agent_version = ver
                if not agent_tag:
                    agent_tag = str(newest_rel.get("tag_name", ""))

    agent_manifest = {
        "version": agent_version,
        "api": API_VERSION,
        "tag": agent_tag or f"agent-v{agent_version}",
        "released": now_iso,
        "prerelease": False,
        "targets": agent_targets,
    }

    app_releases = [r for r in published if str(r.get("tag_name", "")).startswith("routelink-")]
    latest_app = app_releases[0] if app_releases else None

    (out_dir / "firmware" / "manifest.json").write_text(
        json.dumps(firmware_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "agent" / "manifest.json").write_text(
        json.dumps(agent_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "index.html").write_text(
        render_index_html(args.repo, site_url, firmware_manifest, agent_manifest, latest_app, usign_key_id),
        encoding="utf-8",
    )

    print(
        f"Built Pages site in {out_dir}: "
        f"{len(firmware_manifest['boards'])} firmware board(s), "
        f"{len(agent_manifest['targets'])} RouteLink feed target(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
