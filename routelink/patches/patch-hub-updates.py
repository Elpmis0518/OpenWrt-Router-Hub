#!/usr/bin/env python3
"""Apply idempotent OpenWrt-Router-Hub self-hosted update & RouteLink feed patches:
  1. RouteLink plugin feed & public apk verification key -> Elpmis0518/OpenWrt-Router-Hub Pages
  2. Mobile App self-update (Android APK / iOS IPA) -> Elpmis0518/OpenWrt-Router-Hub Releases (routelink-*)
  3. Router firmware online upgrade (TR3000 & AX3000T) -> Elpmis0518/OpenWrt-Router-Hub firmware/manifest.json
  4. Download mirror support for self-hosted firmware downloads
  5. Unit test coverage for all additions
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HUB_REPO = "Elpmis0518/OpenWrt-Router-Hub"
HUB_PAGES = "https://elpmis0518.github.io/OpenWrt-Router-Hub"
HUB_API = f"https://api.github.com/repos/{HUB_REPO}"
HUB_README = f"https://github.com/{HUB_REPO}#readme"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"[{label}] expected 1 occurrence, found {count}")
    return text.replace(old, new, 1)


def patch_feed_keys(rl_dir: Path, hub_keys_dir: Path) -> None:
    apk_pem_src = hub_keys_dir / "routelink-apk.pem"
    if not apk_pem_src.is_file():
        raise RuntimeError(f"Missing public APK key: {apk_pem_src}")
    pem = apk_pem_src.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not pem.endswith("\n"):
        pem += "\n"

    # 1. Sync upstream openwrt/feed/keys/ with our Hub public keys only (drop unused upstream key)
    dst_keys = rl_dir / "openwrt/feed/keys"
    if dst_keys.exists():
        shutil.rmtree(dst_keys)
    dst_keys.mkdir(parents=True, exist_ok=True)
    (dst_keys / "routelink-apk.pem").write_text(pem, encoding="utf-8")
    for f in hub_keys_dir.iterdir():
        if f.is_file() and f.name != "routelink-apk.pem":
            shutil.copy2(f, dst_keys / f.name)

    # 2. Sync src/features/agent/keys.ts so manifest.test.ts equality check stays green
    keys_ts = rl_dir / "src/features/agent/keys.ts"
    keys_ts.write_text(
        "/**\n"
        " * Public key of the plugin's apk packages (openwrt/feed/keys/routelink-apk.pem; a test keeps the two equal).\n"
        " * apk on OpenWrt 25.12 only installs local packages signed by a key in /etc/apk/keys.\n"
        " */\n"
        f"export const ROUTELINK_APK_PEM = `{pem}`;\n",
        encoding="utf-8",
    )


def patch_agent_manifest(rl_dir: Path) -> None:
    path = rl_dir / "src/features/agent/manifest.ts"
    text = path.read_text(encoding="utf-8")

    hub_manifest_url = f"{HUB_PAGES}/agent/manifest.json"
    if hub_manifest_url not in text:
        text = replace_once(
            text,
            "export const MANIFEST_URL = 'https://tsix2019.github.io/RouteLink/agent/manifest.json';",
            f"export const MANIFEST_URL = '{hub_manifest_url}';",
            "agent.manifest.url",
        )

    if "const byRelease = usable.filter((t) => t.release === env.release);" not in text:
        text = replace_once(
            text,
            "  const target = usable.find((t) => t.release === env.release && t.arch === env.arch);\n"
            "  if (!target) {\n"
            "    return usable.some((t) => t.release === env.release) ? 'unsupported-arch' : 'unsupported-release';\n"
            "  }",
            "  const byRelease = usable.filter((t) => t.release === env.release);\n"
            "  const target =\n"
            "    byRelease.find((t) => t.arch === env.arch) ?? (!env.arch && byRelease.length === 1 ? byRelease[0] : undefined);\n"
            "  if (!target) {\n"
            "    return byRelease.length ? 'unsupported-arch' : 'unsupported-release';\n"
            "  }",
            "agent.manifest.pickTarget",
        )

    path.write_text(text, encoding="utf-8")

    agent_page = rl_dir / "src/app/(tabs)/more/agent/index.tsx"
    atext = agent_page.read_text(encoding="utf-8")
    if HUB_README not in atext:
        atext = replace_once(
            atext,
            "export const MANUAL_URL = {\n"
            "  'zh-CN': 'https://github.com/tsix2019/RouteLink/blob/main/README.md#路由器插件',\n"
            "  en: 'https://github.com/tsix2019/RouteLink/blob/main/README.en.md#router-plugin',\n"
            "} as const;",
            "export const MANUAL_URL = {\n"
            f"  'zh-CN': '{HUB_README}',\n"
            f"  en: '{HUB_README}',\n"
            "} as const;",
            "agent.index.manualUrl",
        )
        agent_page.write_text(atext, encoding="utf-8")


def patch_app_config(rl_dir: Path) -> None:
    path = rl_dir / "app.config.ts"
    text = path.read_text(encoding="utf-8")
    if "EXPO_PUBLIC_HUB_RELEASE_TAG" in text:
        return

    text = replace_once(
        text,
        "const BUILD = VERSION.split('.').reduce((n, part) => n * 100 + Number(part), 0);",
        "const DEFAULT_BUILD = VERSION.split('.').reduce((n, part) => n * 100 + Number(part), 0);\n"
        "const ENV_BUILD = Number.parseInt(process.env.RL_VERSION_CODE ?? '', 10);\n"
        "const BUILD = Number.isInteger(ENV_BUILD) && ENV_BUILD > 0 ? ENV_BUILD : DEFAULT_BUILD;\n"
        "const HUB_RELEASE_TAG = process.env.EXPO_PUBLIC_HUB_RELEASE_TAG?.trim() || undefined;",
        "app.config.build",
    )
    text = replace_once(
        text,
        "  version: VERSION,",
        "  version: VERSION,\n"
        "  ...(HUB_RELEASE_TAG ? { extra: { hubReleaseTag: HUB_RELEASE_TAG } } : {}),",
        "app.config.extra",
    )
    path.write_text(text, encoding="utf-8")


def patch_app_update(rl_dir: Path) -> None:
    releases_path = rl_dir / "src/features/update/releases.ts"
    text = releases_path.read_text(encoding="utf-8")
    if HUB_API not in text:
        text = replace_once(
            text,
            "export const RELEASES_API = 'https://api.github.com/repos/tsix2019/RouteLink';",
            f"export const RELEASES_API = '{HUB_API}';",
            "update.releases.api",
        )

    if "HUB_TAG = /^routelink-(\\d{8}-\\d{6})$/;" not in text:
        old = (
            "const VERSION = /^v?(\\d+)\\.(\\d+)\\.(\\d+)$/;\n"
            "const TAG = /^v\\d+\\.\\d+\\.\\d+$/;\n"
            "const SHA256 = /^sha256:([0-9a-f]{64})$/i;\n"
            "\n"
            "export function parseVersion(v: string): [number, number, number] | null {\n"
            "  const m = VERSION.exec(v);\n"
            "  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;\n"
            "}\n"
            "\n"
            "/** Above 0 when `a` is newer, 0 when the same; null when either is not X.Y.Z. */\n"
            "export function compareVersions(a: string, b: string): number | null {\n"
            "  const x = parseVersion(a);\n"
            "  const y = parseVersion(b);\n"
            "  if (!x || !y) return null;\n"
            "  return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];\n"
            "}\n"
            "\n"
            "type Raw = Record<string, unknown>;\n"
            "const isObj = (v: unknown): v is Raw => !!v && typeof v === 'object' && !Array.isArray(v);\n"
            "\n"
            "function toApk(assets: unknown, version: string): AppRelease['apk'] {\n"
            "  if (!Array.isArray(assets)) return undefined;\n"
            "  const name = `RouteLink-v${version}.apk`;\n"
            "  const a = assets.find((x): x is Raw => isObj(x) && x.name === name);\n"
            "  if (!a) return undefined;\n"
            "  const { browser_download_url: url, size, digest } = a;\n"
            "  if (typeof url !== 'string' || !/^https?:\\/\\/\\S+$/.test(url) || typeof size !== 'number' || size <= 0) {\n"
            "    return undefined;\n"
            "  }\n"
            "  const sha256 = typeof digest === 'string' ? SHA256.exec(digest)?.[1]?.toLowerCase() : undefined;\n"
            "  return { name, url, size, sha256 };\n"
            "}\n"
            "\n"
            "/** A published app release (`vX.Y.Z`, not a draft or prerelease), or null for anything else. */\n"
            "function toRelease(raw: unknown): AppRelease | null {\n"
            "  if (!isObj(raw)) return null;\n"
            "  const { tag_name: tag, draft, prerelease, published_at: publishedAt, html_url: htmlUrl, body, assets } = raw;\n"
            "  if (typeof tag !== 'string' || !TAG.test(tag) || draft !== false || prerelease !== false) return null;\n"
            "  if (typeof publishedAt !== 'string' || typeof htmlUrl !== 'string') return null;\n"
            "  const version = tag.slice(1);\n"
            "  return {\n"
            "    version,\n"
            "    tag,\n"
            "    publishedAt,\n"
            "    htmlUrl,\n"
            "    notes: typeof body === 'string' ? body : '',\n"
            "    apk: toApk(assets, version),\n"
            "  };\n"
            "}"
        )
        new = (
            "const VERSION = /^v?(\\d+)\\.(\\d+)\\.(\\d+)(?:\\+(\\d{8}-\\d{6}))?$/;\n"
            "const STAMP_ONLY = /^(\\d{8}-\\d{6})$/;\n"
            "const TAG = /^v\\d+\\.\\d+\\.\\d+$/;\n"
            "const HUB_TAG = /^routelink-(\\d{8}-\\d{6})$/;\n"
            "const SHA256 = /^sha256:([0-9a-f]{64})$/i;\n"
            "\n"
            "export function parseVersion(v: string): [number, number, number] | null {\n"
            "  const m = VERSION.exec(v);\n"
            "  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;\n"
            "}\n"
            "\n"
            "function parseVersionParts(v: string): { semver: [number, number, number] | null; stamp: string } | null {\n"
            "  const m = VERSION.exec(v);\n"
            "  if (m) return { semver: [Number(m[1]), Number(m[2]), Number(m[3])], stamp: m[4] ?? '' };\n"
            "  const s = STAMP_ONLY.exec(v);\n"
            "  if (s) return { semver: null, stamp: s[1] };\n"
            "  return null;\n"
            "}\n"
            "\n"
            "/** Above 0 when `a` is newer, 0 when the same; supports X.Y.Z, X.Y.Z+YYYYMMDD-HHMMSS and YYYYMMDD-HHMMSS. */\n"
            "export function compareVersions(a: string, b: string): number | null {\n"
            "  const x = parseVersionParts(a);\n"
            "  const y = parseVersionParts(b);\n"
            "  if (!x || !y) return null;\n"
            "  if (x.semver && y.semver) {\n"
            "    const diff = x.semver[0] - y.semver[0] || x.semver[1] - y.semver[1] || x.semver[2] - y.semver[2];\n"
            "    if (diff !== 0) return diff;\n"
            "    if (x.stamp === y.stamp) return 0;\n"
            "    return x.stamp > y.stamp ? 1 : -1;\n"
            "  }\n"
            "  if (x.stamp && y.stamp) {\n"
            "    if (x.stamp === y.stamp) return 0;\n"
            "    return x.stamp > y.stamp ? 1 : -1;\n"
            "  }\n"
            "  return null;\n"
            "}\n"
            "\n"
            "type Raw = Record<string, unknown>;\n"
            "const isObj = (v: unknown): v is Raw => !!v && typeof v === 'object' && !Array.isArray(v);\n"
            "\n"
            "function toApk(assets: unknown, expectedNames: string[]): AppRelease['apk'] {\n"
            "  if (!Array.isArray(assets)) return undefined;\n"
            "  const a = assets.find((x): x is Raw => isObj(x) && typeof x.name === 'string' && expectedNames.includes(x.name));\n"
            "  if (!a || typeof a.name !== 'string') return undefined;\n"
            "  const { browser_download_url: url, size, digest } = a;\n"
            "  if (typeof url !== 'string' || !/^https?:\\/\\/\\S+$/.test(url) || typeof size !== 'number' || size <= 0) {\n"
            "    return undefined;\n"
            "  }\n"
            "  const sha256 = typeof digest === 'string' ? SHA256.exec(digest)?.[1]?.toLowerCase() : undefined;\n"
            "  return { name: a.name, url, size, sha256 };\n"
            "}\n"
            "\n"
            "/** A published app release (`vX.Y.Z` or `routelink-YYYYMMDD-HHMMSS`, not draft/prerelease). */\n"
            "function toRelease(raw: unknown): AppRelease | null {\n"
            "  if (!isObj(raw)) return null;\n"
            "  const { tag_name: tag, name: title, draft, prerelease, published_at: publishedAt, html_url: htmlUrl, body, assets } = raw;\n"
            "  if (typeof tag !== 'string' || draft !== false || prerelease !== false) return null;\n"
            "  if (typeof publishedAt !== 'string' || typeof htmlUrl !== 'string') return null;\n"
            "  const notes = typeof body === 'string' ? body : '';\n"
            "  if (TAG.test(tag)) {\n"
            "    const version = tag.slice(1);\n"
            "    return { version, tag, publishedAt, htmlUrl, notes, apk: toApk(assets, [`RouteLink-v${version}.apk`]) };\n"
            "  }\n"
            "  const hub = HUB_TAG.exec(tag);\n"
            "  if (!hub) return null;\n"
            "  const stamp = hub[1];\n"
            "  const semverMatch =\n"
            "    (typeof title === 'string' ? /\\bv(\\d+\\.\\d+\\.\\d+)\\b/.exec(title) : null) ??\n"
            "    /\\bv(\\d+\\.\\d+\\.\\d+)\\b/.exec(notes);\n"
            "  const version = semverMatch ? `${semverMatch[1]}+${stamp}` : stamp;\n"
            "  const expectedApks = [`RouteLink-${stamp}.apk`, ...(semverMatch ? [`RouteLink-v${semverMatch[1]}.apk`] : [])];\n"
            "  return { version, tag, publishedAt, htmlUrl, notes, apk: toApk(assets, expectedApks) };\n"
            "}"
        )
        text = replace_once(text, old, new, "update.releases.hubTag")

    releases_path.write_text(text, encoding="utf-8")

    check_path = rl_dir / "src/features/update/check.ts"
    ctext = check_path.read_text(encoding="utf-8")
    if "hubReleaseTag" not in ctext:
        ctext = replace_once(
            ctext,
            "/** This app's version (app.config.ts). */\n"
            "export const currentVersion = (): string => Constants.expoConfig?.version ?? '0.0.0';",
            "/** This app's version (app.config.ts), including the Hub build stamp when built in CI. */\n"
            "export const currentVersion = (): string => {\n"
            "  const base = Constants.expoConfig?.version ?? '0.0.0';\n"
            "  const extra = Constants.expoConfig?.extra as { hubReleaseTag?: unknown } | undefined;\n"
            "  const tag = typeof extra?.hubReleaseTag === 'string' ? /^routelink-(\\d{8}-\\d{6})$/.exec(extra.hubReleaseTag) : null;\n"
            "  return tag ? `${base}+${tag[1]}` : base;\n"
            "};",
            "update.check.currentVersion",
        )
        check_path.write_text(ctext, encoding="utf-8")


def patch_firmware_online(rl_dir: Path) -> None:
    fw_path = rl_dir / "src/api/services/firmware.ts"
    text = fw_path.read_text(encoding="utf-8")
    if "HUB_BOARDS" not in text:
        text = replace_once(
            text,
            "const SITES: Record<string, string> = {\n"
            "  OpenWrt: 'https://downloads.openwrt.org',\n"
            "  ImmortalWrt: 'https://downloads.immortalwrt.org',\n"
            "};",
            "const SITES: Record<string, string> = {\n"
            "  OpenWrt: 'https://downloads.openwrt.org',\n"
            "  ImmortalWrt: 'https://downloads.immortalwrt.org',\n"
            "};\n"
            f"export const HUB_SITE = '{HUB_PAGES}';\n"
            "const HUB_BOARDS = new Set(['cudy,tr3000-v1-256mb', 'xiaomi,mi-router-ax3000t']);\n"
            "const HUB_REV_TAG = /\\b((?:tr3000|ax3000t)-\\d{8}-\\d{6})\\b/;",
            "firmware.hubConstants",
        )

        text = replace_once(
            text,
            "/** Only final releases of the two projects that publish profiles.json. */\n"
            "export function onlineSupport(info: FirmwareInfo): OnlineSupport {\n"
            "  const site = SITES[info.distribution];\n"
            "  if (!site) return { ok: false, reason: 'distribution' };\n"
            "  if (!/^\\d+\\.\\d+\\.\\d+$/.test(info.version)) return { ok: false, reason: 'version' };\n"
            "  return { ok: true, site };\n"
            "}",
            "/** Supported boards from OpenWrt-Router-Hub, or final official OpenWrt / ImmortalWrt releases. */\n"
            "export function onlineSupport(info: FirmwareInfo): OnlineSupport {\n"
            "  if (HUB_BOARDS.has(info.board)) return { ok: true, site: HUB_SITE };\n"
            "  const site = SITES[info.distribution];\n"
            "  if (!site) return { ok: false, reason: 'distribution' };\n"
            "  if (!/^\\d+\\.\\d+\\.\\d+$/.test(info.version)) return { ok: false, reason: 'version' };\n"
            "  return { ok: true, site };\n"
            "}",
            "firmware.onlineSupport",
        )

        text = replace_once(
            text,
            "export async function checkOnline(info: FirmwareInfo, fetchJson: FetchJson): Promise<OnlineCheck> {\n"
            "  const support = onlineSupport(info);\n"
            "  if (!support.ok) return { status: 'unsupported', reason: support.reason };",
            "interface HubFirmwareEntry {\n"
            "  tag: string;\n"
            "  version: string;\n"
            "  name: string;\n"
            "  url: string;\n"
            "  sha256: string;\n"
            "}\n"
            "interface HubFirmwareManifest {\n"
            "  boards?: Record<string, { latest?: HubFirmwareEntry; releases?: HubFirmwareEntry[] }>;\n"
            "}\n"
            "\n"
            "const toOfficial = (e: HubFirmwareEntry): OfficialImage => ({\n"
            "  version: e.version || e.tag,\n"
            "  name: e.name,\n"
            "  url: e.url,\n"
            "  sha256: e.sha256,\n"
            "});\n"
            "\n"
            "async function checkHubOnline(info: FirmwareInfo, fetchJson: FetchJson): Promise<OnlineCheck> {\n"
            "  const manifest = (await fetchJson(`${HUB_SITE}/firmware/manifest.json`)) as HubFirmwareManifest;\n"
            "  const board = manifest?.boards?.[info.board];\n"
            "  const latest = board?.latest;\n"
            "  if (!latest?.url || !latest.sha256) return { status: 'no-image' };\n"
            "  const installedTag = HUB_REV_TAG.exec(info.revision)?.[1];\n"
            "  const latestImg = toOfficial(latest);\n"
            "  if (installedTag && installedTag === latest.tag) {\n"
            "    return { status: 'ok', current: latestImg, upgrades: [] };\n"
            "  }\n"
            "  const matched = installedTag ? board?.releases?.find((r) => r.tag === installedTag) : undefined;\n"
            "  const current = matched ? toOfficial(matched) : latestImg;\n"
            "  const isNewer = !installedTag || latest.tag > installedTag;\n"
            "  return { status: 'ok', current, upgrades: isNewer ? [latestImg] : [] };\n"
            "}\n"
            "\n"
            "export async function checkOnline(info: FirmwareInfo, fetchJson: FetchJson): Promise<OnlineCheck> {\n"
            "  const support = onlineSupport(info);\n"
            "  if (!support.ok) return { status: 'unsupported', reason: support.reason };\n"
            "  if (support.site === HUB_SITE) return checkHubOnline(info, fetchJson);",
            "firmware.checkOnline",
        )
        fw_path.write_text(text, encoding="utf-8")

    # Apply optional user mirror for GitHub / Hub firmware downloads
    dl_path = rl_dir / "src/features/maintenance/download.ts"
    dtext = dl_path.read_text(encoding="utf-8")
    if "applyMirror" not in dtext:
        dtext = replace_once(
            dtext,
            "import { sha256Hex } from '@/features/agent/download';",
            "import { sha256Hex } from '@/features/agent/download';\n"
            "import { applyMirror } from '@/features/agent/manifest';\n"
            "import { useSettings } from '@/state/settings';",
            "maintenance.download.imports",
        )
        dtext = replace_once(
            dtext,
            "    const res = await fetch(url, { signal: controller.signal });",
            "    const targetUrl = /^https:\\/\\/(github\\.com|elpmis0518\\.github\\.io)\\//.test(url)\n"
            "      ? applyMirror(url, useSettings.getState().agentMirror)\n"
            "      : url;\n"
            "    const res = await fetch(targetUrl, { signal: controller.signal });",
            "maintenance.download.fetchJson",
        )
        dtext = replace_once(
            dtext,
            "  const res = await fetch(url);",
            "  const targetUrl = /^https:\\/\\/(github\\.com|elpmis0518\\.github\\.io)\\//.test(url)\n"
            "    ? applyMirror(url, useSettings.getState().agentMirror)\n"
            "    : url;\n"
            "  const res = await fetch(targetUrl);",
            "maintenance.download.downloadFile",
        )
        dl_path.write_text(dtext, encoding="utf-8")


def patch_tests(rl_dir: Path) -> None:
    # 1. releases.test.ts: test routelink-YYYYMMDD-HHMMSS Hub releases & +stamp comparison
    rel_test = rl_dir / "src/features/update/releases.test.ts"
    rtext = rel_test.read_text(encoding="utf-8")
    if "parses OpenWrt-Router-Hub routelink-YYYYMMDD-HHMMSS releases" not in rtext:
        rtext += """
describe('OpenWrt-Router-Hub releases', () => {
  it('compares build stamps on identical semver', () => {
    expect(compareVersions('1.1.1+20261126-040000', '1.1.1+20261125-093500')).toBeGreaterThan(0);
    expect(compareVersions('1.1.1+20261125-093500', '1.1.1+20261125-093500')).toBe(0);
    expect(compareVersions('1.1.1+20261125-093500', '1.1.1')).toBeGreaterThan(0);
    expect(compareVersions('1.2.0', '1.1.1+20261125-093500')).toBeGreaterThan(0);
  });

  it('parses OpenWrt-Router-Hub routelink-YYYYMMDD-HHMMSS releases and ignores router tags', () => {
    const picked = pickRelease([
      {
        tag_name: 'tr3000-20261126-040000',
        name: 'TR3000 256MB',
        draft: false,
        prerelease: false,
        published_at: '2026-11-26T04:00:00Z',
        html_url: 'https://github.com/Elpmis0518/OpenWrt-Router-Hub/releases/tag/tr3000-20261126-040000',
        body: '',
        assets: [],
      },
      {
        tag_name: 'routelink-20261125-093500',
        name: 'RouteLink v1.1.1 (20261125-093500)',
        draft: false,
        prerelease: false,
        published_at: '2026-11-25T09:35:00Z',
        html_url: 'https://github.com/Elpmis0518/OpenWrt-Router-Hub/releases/tag/routelink-20261125-093500',
        body: '中文更新说明\\n---\\nEnglish notes',
        assets: [
          {
            name: 'RouteLink-20261125-093500.apk',
            browser_download_url: 'https://github.com/Elpmis0518/OpenWrt-Router-Hub/releases/download/routelink-20261125-093500/RouteLink-20261125-093500.apk',
            size: 38_000_000,
            digest: `sha256:${'a'.repeat(64)}`,
          },
        ],
      },
    ]);
    expect(picked).toMatchObject({
      tag: 'routelink-20261125-093500',
      version: '1.1.1+20261125-093500',
      apk: {
        name: 'RouteLink-20261125-093500.apk',
        size: 38_000_000,
        sha256: 'a'.repeat(64),
      },
    });
  });
});
"""
        rel_test.write_text(rtext, encoding="utf-8")

    # 2. firmware.test.ts: test TR3000 & AX3000T Hub online firmware check
    fw_test = rl_dir / "src/api/services/firmware.test.ts"
    ftext = fw_test.read_text(encoding="utf-8")
    if "OpenWrt-Router-Hub online firmware check" not in ftext:
        ftext += """
describe('OpenWrt-Router-Hub online firmware check', () => {
  const tr3000: FirmwareInfo = {
    distribution: 'ImmortalWRT',
    version: 'SNAPSHOT',
    revision: 'r33412-abcdef0123 / tr3000-20261125-074457',
    target: 'mediatek/filogic',
    board: 'cudy,tr3000-v1-256mb',
    model: 'Cudy TR3000 v1 (256M)',
    rootfs: 'squashfs',
    efi: false,
  };
  const manifest = {
    boards: {
      'cudy,tr3000-v1-256mb': {
        latest: {
          tag: 'tr3000-20261126-040000',
          version: '20261126-040000',
          name: 'immortalwrt-mediatek-filogic-cudy_tr3000-v1-256mb-squashfs-sysupgrade.bin',
          url: 'https://github.com/Elpmis0518/OpenWrt-Router-Hub/releases/download/tr3000-20261126-040000/immortalwrt-mediatek-filogic-cudy_tr3000-v1-256mb-squashfs-sysupgrade.bin',
          sha256: 'b'.repeat(64),
        },
        releases: [
          {
            tag: 'tr3000-20261126-040000',
            version: '20261126-040000',
            name: 'immortalwrt-mediatek-filogic-cudy_tr3000-v1-256mb-squashfs-sysupgrade.bin',
            url: 'https://github.com/Elpmis0518/OpenWrt-Router-Hub/releases/download/tr3000-20261126-040000/immortalwrt-mediatek-filogic-cudy_tr3000-v1-256mb-squashfs-sysupgrade.bin',
            sha256: 'b'.repeat(64),
          },
          {
            tag: 'tr3000-20261125-074457',
            version: '20261125-074457',
            name: 'immortalwrt-mediatek-filogic-cudy_tr3000-v1-256mb-squashfs-sysupgrade.bin',
            url: 'https://github.com/Elpmis0518/OpenWrt-Router-Hub/releases/download/tr3000-20261125-074457/immortalwrt-mediatek-filogic-cudy_tr3000-v1-256mb-squashfs-sysupgrade.bin',
            sha256: 'c'.repeat(64),
          },
        ],
      },
    },
  };

  it('recognizes TR3000 and AX3000T as Hub-supported boards', () => {
    expect(onlineSupport(tr3000)).toEqual({ ok: true, site: 'https://elpmis0518.github.io/OpenWrt-Router-Hub' });
    expect(onlineSupport({ ...tr3000, board: 'xiaomi,mi-router-ax3000t', distribution: 'OpenWrt', version: '24.10.5' }))
      .toEqual({ ok: true, site: 'https://elpmis0518.github.io/OpenWrt-Router-Hub' });
  });

  it('offers newer Hub firmware and reports up-to-date once matched', async () => {
    const fetchJson = async () => manifest;
    const upgrade = await checkOnline(tr3000, fetchJson);
    expect(upgrade.status).toBe('ok');
    if (upgrade.status === 'ok') {
      expect(upgrade.current.version).toBe('20261125-074457');
      expect(upgrade.upgrades.map((u) => u.version)).toEqual(['20261126-040000']);
    }
    const latest = await checkOnline({ ...tr3000, revision: 'r33412-abcdef0123 / tr3000-20261126-040000' }, fetchJson);
    expect(latest.status).toBe('ok');
    if (latest.status === 'ok') {
      expect(latest.current.version).toBe('20261126-040000');
      expect(latest.upgrades).toEqual([]);
    }
  });
});
"""
        fw_test.write_text(ftext, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <routelink_dir>", file=sys.stderr)
        return 2
    rl_dir = Path(sys.argv[1])
    hub_keys_dir = Path(__file__).resolve().parent.parent / "feed" / "keys"
    patch_feed_keys(rl_dir, hub_keys_dir)
    patch_agent_manifest(rl_dir)
    patch_app_config(rl_dir)
    patch_app_update(rl_dir)
    patch_firmware_online(rl_dir)
    patch_tests(rl_dir)
    print(f"Applied OpenWrt-Router-Hub self-hosted update & feed patches in {rl_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
