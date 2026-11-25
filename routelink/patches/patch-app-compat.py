#!/usr/bin/env python3
"""Apply modular, idempotent RouteLink mobile app compatibility patches:
  1. Dual OpenVPN backend (procd /etc/config/openvpn + netifd /etc/config/network proto 'openvpn')
  2. Dual SQM init mechanism (/etc/init.d/sqm file exec + ubus rc init fallback)
  3. WoL compatibility with luci-app-wolultra (capabilities + luci.wolultra wake fallback)
  4. Unit test coverage for all three additions
"""

from __future__ import annotations

import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"[{label}] expected 1 occurrence, found {count}")
    return text.replace(old, new, 1)


def patch_capabilities(rl_dir: Path) -> None:
    path = rl_dir / "src/api/capabilities.ts"
    text = path.read_text(encoding="utf-8")

    # 1. clients.wol.router: add luci.wolultra wake
    if "ubus('luci.wolultra', 'wake')" not in text:
        text = replace_once(
            text,
            "    orAccess: [[ubus('luci.wol', 'exec')]],",
            "    orAccess: [[ubus('luci.wol', 'exec')], [ubus('luci.wolultra', 'wake')]],",
            "capabilities.wol",
        )

    # 2. network.openvpn: add netifd (luci-proto-openvpn) alternative access
    if "ubus('luci.openvpn', 'generateKey')" not in text:
        text = replace_once(
            text,
            "  'network.openvpn': {\n"
            "    access: [uciConfig('openvpn', 'write'), file('/etc/openvpn/routelink.ovpn', 'write')],\n"
            "    packages: ['openvpn-openssl', 'luci-app-openvpn'],\n"
            "  },",
            "  'network.openvpn': {\n"
            "    access: [uciConfig('openvpn', 'write'), file('/etc/openvpn/routelink.ovpn', 'write')],\n"
            "    orAccess: [\n"
            "      [\n"
            "        uciConfig('network', 'write'),\n"
            "        file('/etc/openvpn/routelink/profile.ovpn', 'write'),\n"
            "        ubus('luci.openvpn', 'generateKey'),\n"
            "      ],\n"
            "    ],\n"
            "    packages: ['openvpn-openssl', 'luci-app-openvpn'],\n"
            "  },",
            "capabilities.openvpn",
        )

    # 3. network.sqm: support ubus rc init alongside file exec /etc/init.d/sqm
    if "[uciConfig('sqm', 'write'), ubus('rc', 'init')]" not in text:
        text = replace_once(
            text,
            "  'network.sqm': {\n"
            "    access: [uciConfig('sqm', 'write'), file('/etc/init.d/sqm enable')],\n"
            "    packages: ['sqm-scripts', 'luci-app-sqm'],\n"
            "  },",
            "  'network.sqm': {\n"
            "    access: [uciConfig('sqm', 'write'), file('/etc/init.d/sqm enable')],\n"
            "    orAccess: [[uciConfig('sqm', 'write'), ubus('rc', 'init')]],\n"
            "    packages: ['sqm-scripts', 'luci-app-sqm'],\n"
            "  },",
            "capabilities.sqm",
        )

    path.write_text(text, encoding="utf-8")


def patch_sqm(rl_dir: Path) -> None:
    path = rl_dir / "src/api/services/sqm.ts"
    text = path.read_text(encoding="utf-8")
    if "conn.call('rc', 'init', { name: 'sqm', action })" in text:
        return

    old = (
        "export async function saveSqm(conn: RouterConnection, changes: UbusCall[]): Promise<void> {\n"
        "  await stageAndApply(conn, changes, { mode: 'direct' });\n"
        "  for (const action of ['enable', 'start']) {\n"
        "    await conn.call('file', 'exec', { command: '/etc/init.d/sqm', params: [action] });\n"
        "  }\n"
        "}"
    )
    new = (
        "const SQM_INIT_FALLBACK_CODES = new Set(['PERMISSION_DENIED', 'ACCESS_DENIED', 'NOT_FOUND']);\n"
        "\n"
        "async function runSqmInit(conn: RouterConnection, action: string): Promise<void> {\n"
        "  try {\n"
        "    await conn.call('file', 'exec', { command: '/etc/init.d/sqm', params: [action] });\n"
        "  } catch (error) {\n"
        "    if (!(error instanceof UbusError && SQM_INIT_FALLBACK_CODES.has(error.code))) {\n"
        "      throw error;\n"
        "    }\n"
        "    await conn.call('rc', 'init', { name: 'sqm', action });\n"
        "  }\n"
        "}\n"
        "\n"
        "export async function saveSqm(conn: RouterConnection, changes: UbusCall[]): Promise<void> {\n"
        "  await stageAndApply(conn, changes, { mode: 'direct' });\n"
        "  for (const action of ['enable', 'start']) {\n"
        "    await runSqmInit(conn, action);\n"
        "  }\n"
        "}"
    )
    text = replace_once(text, old, new, "sqm.saveSqm")
    path.write_text(text, encoding="utf-8")


def patch_client_actions(rl_dir: Path) -> None:
    path = rl_dir / "src/api/services/client-actions.ts"
    text = path.read_text(encoding="utf-8")
    if "luci.wolultra" in text:
        return

    old = (
        "async function etherwake(conn: RouterConnection, args: string[]): Promise<ExecResult> {\n"
        "  try {\n"
        "    return await conn.call<ExecResult>('file', 'exec', { command: '/usr/bin/etherwake', params: args });\n"
        "  } catch (error) {\n"
        "    if (!(error instanceof UbusError && ['PERMISSION_DENIED', 'ACCESS_DENIED', 'NOT_FOUND'].includes(error.code))) {\n"
        "      throw error;\n"
        "    }\n"
        "    return conn.call<ExecResult>('luci.wol', 'exec', { name: '/usr/bin/etherwake', args });\n"
        "  }\n"
        "}\n"
        "\n"
        "export async function wakeOnLan(conn: RouterConnection, mac: string, o: WolOptions): Promise<'router' | 'phone'> {\n"
        "  if (o.routerSide) {\n"
        "    try {\n"
        "      const r = await etherwake(conn, ['-D', '-i', o.lanDevice ?? 'br-lan', mac]);"
    )
    new = (
        "const WOL_FALLBACK_CODES = new Set(['PERMISSION_DENIED', 'ACCESS_DENIED', 'NOT_FOUND']);\n"
        "\n"
        "async function etherwake(conn: RouterConnection, iface: string, mac: string): Promise<ExecResult> {\n"
        "  const args = ['-D', '-i', iface, mac];\n"
        "  try {\n"
        "    return await conn.call<ExecResult>('file', 'exec', { command: '/usr/bin/etherwake', params: args });\n"
        "  } catch (error) {\n"
        "    if (!(error instanceof UbusError && WOL_FALLBACK_CODES.has(error.code))) {\n"
        "      throw error;\n"
        "    }\n"
        "    try {\n"
        "      return await conn.call<ExecResult>('luci.wol', 'exec', { name: '/usr/bin/etherwake', args });\n"
        "    } catch (wolError) {\n"
        "      if (!(wolError instanceof UbusError && WOL_FALLBACK_CODES.has(wolError.code))) {\n"
        "        throw wolError;\n"
        "      }\n"
        "      return conn.call<ExecResult>('luci.wolultra', 'wake', { iface, mac });\n"
        "    }\n"
        "  }\n"
        "}\n"
        "\n"
        "export async function wakeOnLan(conn: RouterConnection, mac: string, o: WolOptions): Promise<'router' | 'phone'> {\n"
        "  if (o.routerSide) {\n"
        "    try {\n"
        "      const r = await etherwake(conn, o.lanDevice ?? 'br-lan', mac);"
    )
    text = replace_once(text, old, new, "client-actions.wolultra")
    path.write_text(text, encoding="utf-8")


OPENVPN_TS_CONTENT = r'''import type { RouterConnection } from '../connection/types';
import { UbusError } from '../ubus/errors';
import { stageAndApply, uci, type ApplyOptions, type ApplyOutcome, type UciSection, type UciValues } from '../uci';
import type { RawInterface } from './network';

/**
 * NW-8: OpenVPN client profiles with dual backends:
 *  - procd (/etc/config/openvpn + /etc/openvpn/<name>.ovpn, e.g. AX3000T / luci-app-openvpn)
 *  - netifd (/etc/config/network proto 'openvpn' + /etc/openvpn/<name>/profile.ovpn, e.g. TR3000 / luci-proto-openvpn)
 */

const DIR = '/etc/openvpn';
/** Disabled examples from the package's default config. */
const EXAMPLES = new Set(['custom_config', 'sample_server', 'sample_client']);
const FALLBACK_CODES = new Set(['NOT_FOUND', 'PERMISSION_DENIED', 'ACCESS_DENIED']);

export type OvpnBackend = 'procd' | 'netifd';

export interface OvpnInstance {
  name: string;
  enabled: boolean;
  configFile?: string;
  hasLogin: boolean;
  /** null when the router does not let the session see procd's services (23.05). */
  running: boolean | null;
  /** Backend that owns this instance; omitted defaults to 'procd'. */
  backend?: OvpnBackend;
}

export interface OvpnProfile {
  client: boolean;
  remote?: string;
  port?: string;
  proto?: string;
  /** `auth-user-pass`: the server wants a user name and password. */
  needsLogin: boolean;
  /** Certificates or keys referenced as separate files, which the import cannot bring along. */
  missingFiles: string[];
}

const FILE_DIRECTIVES = new Set([
  'ca',
  'cert',
  'key',
  'tls-auth',
  'tls-crypt',
  'tls-crypt-v2',
  'pkcs12',
  'secret',
  'dh',
]);

export function netifdProfilePath(name: string): string {
  return `${DIR}/${name}/profile.ovpn`;
}

export function inspectOvpn(text: string): OvpnProfile {
  const profile: OvpnProfile = { client: false, needsLogin: false, missingFiles: [] };
  let inline: string | null = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (inline) {
      if (line === `</${inline}>`) inline = null;
      continue;
    }
    const block = /^<([\w-]+)>$/.exec(line);
    if (block) {
      inline = block[1];
      continue;
    }
    if (!line || line.startsWith('#') || line.startsWith(';')) continue;
    const [directive, ...args] = line.split(/\s+/);
    if (directive === 'client' || directive === 'tls-client') profile.client = true;
    else if (directive === 'remote' && !profile.remote) {
      profile.remote = args[0];
      if (args[1]) profile.port = args[1];
      if (args[2]) profile.proto = args[2];
    } else if (directive === 'proto' && !profile.proto) profile.proto = args[0];
    else if (directive === 'port' && !profile.port) profile.port = args[0];
    else if (directive === 'auth-user-pass') profile.needsLogin = true;
    else if (FILE_DIRECTIVES.has(directive) && args[0] && args[0] !== '[inline]') profile.missingFiles.push(args[0]);
  }
  return profile;
}

export interface OvpnLogin {
  username: string;
  password: string;
}

export type ImportError = 'name-invalid' | 'name-taken' | 'empty' | 'not-client' | 'missing-files' | 'needs-login';

export function validateImport(
  name: string,
  text: string,
  instances: OvpnInstance[],
  login?: OvpnLogin,
  takenNames: string[] = [...EXAMPLES],
): ImportError | null {
  if (!/^[A-Za-z0-9_]{1,32}$/.test(name)) return 'name-invalid';
  if (instances.some((i) => i.name === name) || takenNames.includes(name)) return 'name-taken';
  if (!text.trim()) return 'empty';
  const profile = inspectOvpn(text);
  if (!profile.client) return 'not-client';
  if (profile.missingFiles.length) return 'missing-files';
  if (profile.needsLogin && !login?.username) return 'needs-login';
  return null;
}

type Services = Record<string, { instances?: Record<string, { running?: boolean }> }> | null;

export function parseOpenvpn(values: Record<string, UciSection>, services: Services): OvpnInstance[] {
  const live = services?.openvpn?.instances ?? {};
  return Object.values(values)
    .filter((s) => s['.type'] === 'openvpn')
    .sort((a, b) => Number(a['.index'] ?? 0) - Number(b['.index'] ?? 0))
    .filter((s) => !(EXAMPLES.has(s['.name']) && s.enabled !== '1'))
    .map((s) => {
      const i: OvpnInstance = {
        name: s['.name'],
        enabled: s.enabled === '1',
        hasLogin: typeof s.username === 'string' && s.username !== '',
        running: services ? live[s['.name']]?.running === true : null,
      };
      if (typeof s.config === 'string') i.configFile = s.config;
      return i;
    });
}

export function parseNetifdOpenvpn(
  networkValues: Record<string, UciSection>,
  interfaces: RawInterface[] | null,
): OvpnInstance[] {
  const live = new Map<string, boolean>();
  if (interfaces) {
    for (const iface of interfaces) live.set(iface.interface, iface.up === true);
  }
  return Object.values(networkValues)
    .filter((s) => s['.type'] === 'interface' && s.proto === 'openvpn')
    .sort((a, b) => Number(a['.index'] ?? 0) - Number(b['.index'] ?? 0))
    .map((s) => {
      const i: OvpnInstance = {
        name: s['.name'],
        enabled: s.disabled !== '1' && s.auto !== '0',
        hasLogin:
          (typeof s.username === 'string' && s.username !== '') ||
          (typeof s.auth_user_pass === 'string' && s.auth_user_pass !== ''),
        running: interfaces ? live.get(s['.name']) === true : null,
        backend: 'netifd',
      };
      if (typeof s.config === 'string') i.configFile = s.config;
      return i;
    });
}

const detectedBackend = new WeakMap<RouterConnection, OvpnBackend>();

export async function getOpenvpn(conn: RouterConnection): Promise<{ installed: boolean; instances: OvpnInstance[] }> {
  const [config, services, protoAccess, netConfig, netDump] = await conn.batch([
    { object: 'uci', method: 'get', params: { config: 'openvpn' } },
    { object: 'service', method: 'list', params: { name: 'openvpn' } },
    {
      object: 'session',
      method: 'access',
      params: { scope: 'ubus', object: 'luci.openvpn', function: 'generateKey' },
    },
    { object: 'uci', method: 'get', params: { config: 'network' } },
    { object: 'network.interface', method: 'dump' },
  ]);

  const netValues = netConfig.ok ? ((netConfig.data as { values?: Record<string, UciSection> }).values ?? {}) : {};
  const ifaces = netDump.ok ? ((netDump.data as { interface?: RawInterface[] }).interface ?? []) : null;
  const netifdInstances = parseNetifdOpenvpn(netValues, ifaces);
  const hasNetifd = (protoAccess.ok && (protoAccess.data as { access?: boolean }).access === true) || netifdInstances.length > 0;

  if (config.ok) {
    const values = (config.data as { values?: Record<string, UciSection> }).values ?? {};
    const procdInstances = parseOpenvpn(values, services.ok ? (services.data as Services) : null);
    if (!hasNetifd || (procdInstances.length > 0 && netifdInstances.length === 0)) {
      detectedBackend.set(conn, 'procd');
      return { installed: true, instances: procdInstances };
    }
    // No active procd instances (or netifd interfaces already exist) alongside luci-proto-openvpn (TR3000):
    // prefer netifd for new imports and surface instances from both backends when present.
    detectedBackend.set(conn, 'netifd');
    return { installed: true, instances: [...procdInstances, ...netifdInstances] };
  }

  if (!(config.error instanceof UbusError && FALLBACK_CODES.has(config.error.code))) {
    throw config.error;
  }

  if (!hasNetifd) {
    return { installed: false, instances: [] };
  }
  detectedBackend.set(conn, 'netifd');
  return { installed: true, instances: netifdInstances };
}

type Tuning = Omit<ApplyOptions, 'mode'> & { backend?: OvpnBackend };

async function importNetifdOvpn(
  conn: RouterConnection,
  name: string,
  text: string,
  login?: OvpnLogin,
  t?: Tuning,
): Promise<ApplyOutcome> {
  const dir = `${DIR}/${name}`;
  const path = netifdProfilePath(name);
  await conn.call('file', 'exec', { command: '/bin/mkdir', params: ['-p', dir] }).catch(() => undefined);
  await conn.call('file', 'write', { path, data: text });
  const values: UciValues = { proto: 'openvpn', config: path, disabled: '0' };
  if (login?.username) {
    values.username = login.username;
    values.password = login.password;
  }
  const { backend: _backend, ...applyOpts } = t ?? {};
  return stageAndApply(conn, [uci.add('network', 'interface', values, name)], { mode: 'rollback', ...applyOpts });
}

/**
 * A tunnel can take over the default route; rollback keeps a profile that cuts the phone off from
 * sticking. The profile file is written first and stays if the instance is rolled back (harmless).
 */
export async function importOvpn(
  conn: RouterConnection,
  name: string,
  text: string,
  login?: OvpnLogin,
  t?: Tuning,
): Promise<ApplyOutcome> {
  const backend = t?.backend ?? detectedBackend.get(conn);
  if (backend === 'netifd') {
    return importNetifdOvpn(conn, name, text, login, t);
  }
  const path = `${DIR}/${name}.ovpn`;
  await conn.call('file', 'write', { path, data: text });
  const values: UciValues = { enabled: '1', config: path };
  if (login?.username) {
    values.username = login.username;
    values.password = login.password;
  }
  const { backend: _backend, ...applyOpts } = t ?? {};
  try {
    return await stageAndApply(conn, [uci.add('openvpn', 'openvpn', values, name)], {
      mode: 'rollback',
      ...applyOpts,
    });
  } catch (error) {
    if (t?.backend === 'procd' || !(error instanceof UbusError && FALLBACK_CODES.has(error.code))) {
      throw error;
    }
    await conn.call('file', 'remove', { path }).catch(() => undefined);
    detectedBackend.set(conn, 'netifd');
    return importNetifdOvpn(conn, name, text, login, t);
  }
}

export const setInstanceEnabled = (conn: RouterConnection, instance: OvpnInstance, enabled: boolean, t?: Tuning) => {
  const { backend: _backend, ...applyOpts } = t ?? {};
  if (instance.backend === 'netifd') {
    return stageAndApply(conn, [uci.set('network', instance.name, { disabled: enabled ? '0' : '1' })], {
      mode: 'rollback',
      ...applyOpts,
    });
  }
  return stageAndApply(conn, [uci.set('openvpn', instance.name, { enabled: enabled ? '1' : '0' })], {
    mode: 'rollback',
    ...applyOpts,
  });
};

/** Removes the instance; the uploaded profile goes too when it is one of ours. */
export async function deleteInstance(
  conn: RouterConnection,
  instance: OvpnInstance,
  t?: Tuning,
): Promise<ApplyOutcome> {
  const { backend: _backend, ...applyOpts } = t ?? {};
  if (instance.backend === 'netifd') {
    const outcome = await stageAndApply(conn, [uci.del('network', instance.name)], {
      mode: 'rollback',
      ...applyOpts,
    });
    if (outcome.status !== 'rolled-back' && instance.configFile === netifdProfilePath(instance.name)) {
      await conn.call('file', 'remove', { path: instance.configFile }).catch(() => undefined);
    }
    return outcome;
  }
  const outcome = await stageAndApply(conn, [uci.del('openvpn', instance.name)], { mode: 'rollback', ...applyOpts });
  if (outcome.status !== 'rolled-back' && instance.configFile === `${DIR}/${instance.name}.ovpn`) {
    await conn.call('file', 'remove', { path: instance.configFile }).catch(() => undefined);
  }
  return outcome;
}
'''


def patch_openvpn(rl_dir: Path) -> None:
    path = rl_dir / "src/api/services/openvpn.ts"
    path.write_text(OPENVPN_TS_CONTENT, encoding="utf-8")


def patch_packages(rl_dir: Path) -> None:
    path = rl_dir / "src/api/services/packages.ts"
    text = path.read_text(encoding="utf-8")
    if "version.trim() || undefined" not in text:
        text = replace_once(
            text,
            "  const release = /^\\d+\\.\\d+/.exec(version)?.[0];\n",
            "  const release = /^\\d+\\.\\d+/.exec(version)?.[0] ?? (version.trim() || undefined);\n",
            "packages.release.snapshot",
        )
    if "r[4].ok || r[6].ok" not in text:
        text = replace_once(
            text,
            "  const manager = r[4].ok ? 'apk' : 'opkg';\n",
            "  const manager = r[4].ok || r[6].ok ? 'apk' : 'opkg';\n",
            "packages.manager.apk",
        )
    path.write_text(text, encoding="utf-8")


def patch_tests(rl_dir: Path) -> None:
    # 1. capabilities.test.ts
    cap_test = rl_dir / "src/api/capabilities.test.ts"
    text = cap_test.read_text(encoding="utf-8")
    if "OpenWrt-Router-Hub compatibility capabilities" not in text:
        text += '''
describe('OpenWrt-Router-Hub compatibility capabilities', () => {
  it('accepts luci.wolultra for router-side Wake-on-LAN', async () => {
    const conn = new FixtureConnection()
      .override('file.stat.usr-bin-etherwake', ok({ type: 'file' }))
      .override('session.access.file-usr-bin-etherwake-exec', ok({ access: false }))
      .override('session.access.ubus-luci-wol-exec', ok({ access: false }))
      .override('session.access.ubus-luci-wolultra-wake', ok({ access: true }));
    const caps = await detectCapabilities(conn);
    expect(caps['clients.wol.router']).toEqual({ status: 'ok' });
  });

  it('accepts ubus rc init as an alternative for SQM', async () => {
    const conn = new FixtureConnection('openwrt-24.10.8')
      .override('session.access.file-etc-init-d-sqm-enable-exec', ok({ access: false }))
      .override('session.access.uci-sqm-write', ok({ access: true }))
      .override('session.access.ubus-rc-init', ok({ access: true }));
    const caps = await detectCapabilities(conn);
    expect(caps['network.sqm']).toEqual({ status: 'ok' });
  });

  it('accepts netifd luci-proto-openvpn for OpenVPN when procd config is absent', async () => {
    const conn = new FixtureConnection()
      .override('session.access.uci-openvpn-write', ok({ access: false }))
      .override('session.access.uci-network-write', ok({ access: true }))
      .override('session.access.file-etc-openvpn-routelink-profile-ovpn-write', ok({ access: true }))
      .override('session.access.ubus-luci-openvpn-generatekey', ok({ access: true }));
    const caps = await detectCapabilities(conn);
    expect(caps['network.openvpn']).toEqual({ status: 'ok' });
  });
});
'''
        cap_test.write_text(text, encoding="utf-8")

    # 2. sqm.test.ts
    sqm_test = rl_dir / "src/api/services/sqm.test.ts"
    text = sqm_test.read_text(encoding="utf-8")
    if "falls back to ubus rc init when file exec is denied" not in text:
        text += '''
describe('saveSqm rc init fallback', () => {
  it('falls back to ubus rc init when file exec is denied', async () => {
    const conn = new FixtureConnection()
      .override('uci.set', ok({}))
      .override('uci.apply', ok({}))
      .override('file.exec', fail('PERMISSION_DENIED', 'file.exec'))
      .override('rc.init', ok({}));
    const [queue] = parseSqm(recorded(), []);
    await saveSqm(conn, queueChanges(input, queue));
    expect(conn.calls.filter((c) => c.object === 'rc' && c.method === 'init').map((c) => c.params)).toEqual([
      { name: 'sqm', action: 'enable' },
      { name: 'sqm', action: 'start' },
    ]);
  });
});
'''
        sqm_test.write_text(text, encoding="utf-8")

    # 3. client-actions.test.ts
    ca_test = rl_dir / "src/api/services/client-actions.test.ts"
    text = ca_test.read_text(encoding="utf-8")
    if "falls back to luci.wolultra wake" not in text:
        text += '''
describe('wakeOnLan luci.wolultra fallback', () => {
  it('falls back to luci.wolultra wake when file exec and luci.wol are unavailable', async () => {
    const conn = new FixtureConnection()
      .override('file.exec.usr-bin-etherwake-d-i-br-lan-aa-bb-cc-00-11-22', fail('PERMISSION_DENIED', 'file.exec'))
      .override('luci.wol.exec', fail('NOT_FOUND', 'luci.wol.exec'))
      .override('luci.wolultra.wake', ok({ code: 0 }));
    await expect(wakeOnLan(conn, 'AA:BB:CC:00:11:22', { routerSide: true })).resolves.toBe('router');
    expect(conn.calls.at(-1)).toEqual({
      object: 'luci.wolultra',
      method: 'wake',
      params: { iface: 'br-lan', mac: 'AA:BB:CC:00:11:22' },
    });
  });

  it('reports failure when luci.wolultra returns non-zero code', async () => {
    const conn = new FixtureConnection()
      .override('file.exec.usr-bin-etherwake-d-i-br-lan-aa-bb-cc-00-11-22', fail('ACCESS_DENIED', 'file.exec'))
      .override('luci.wol.exec', fail('ACCESS_DENIED', 'luci.wol.exec'))
      .override('luci.wolultra.wake', ok({ code: 1, stderr: 'wake failed' }));
    await expect(wakeOnLan(conn, 'AA:BB:CC:00:11:22', { routerSide: true })).rejects.toThrow('wake failed');
  });
});
'''
        ca_test.write_text(text, encoding="utf-8")

    # 4. openvpn.test.ts
    ovpn_test = rl_dir / "src/api/services/openvpn.test.ts"
    text = ovpn_test.read_text(encoding="utf-8")
    if "netifd OpenVPN backend" not in text:
        text = replace_once(
            text,
            "  parseOpenvpn,\n",
            "  netifdProfilePath,\n  parseNetifdOpenvpn,\n  parseOpenvpn,\n",
            "openvpn.test.imports",
        )
        text += '''
describe('netifd OpenVPN backend (luci-proto-openvpn)', () => {
  const apply = { sleep: async () => {} };

  it('parses proto openvpn interfaces from /etc/config/network with live up state', () => {
    const network: Record<string, UciSection> = {
      lan: { '.name': 'lan', '.type': 'interface', proto: 'static' },
      tr_vpn: {
        '.name': 'tr_vpn',
        '.type': 'interface',
        proto: 'openvpn',
        config: '/etc/openvpn/tr_vpn/profile.ovpn',
        username: 'alice',
        disabled: '0',
      },
      off_vpn: {
        '.name': 'off_vpn',
        '.type': 'interface',
        proto: 'openvpn',
        config: '/etc/openvpn/off_vpn/profile.ovpn',
        disabled: '1',
      },
    };
    expect(
      parseNetifdOpenvpn(network, [
        { interface: 'tr_vpn', up: true },
        { interface: 'off_vpn', up: false },
      ]),
    ).toEqual<OvpnInstance[]>([
      {
        name: 'tr_vpn',
        enabled: true,
        configFile: '/etc/openvpn/tr_vpn/profile.ovpn',
        hasLogin: true,
        running: true,
        backend: 'netifd',
      },
      {
        name: 'off_vpn',
        enabled: false,
        configFile: '/etc/openvpn/off_vpn/profile.ovpn',
        hasLogin: false,
        running: false,
        backend: 'netifd',
      },
    ]);
  });

  it('reads, imports, toggles, and deletes netifd OpenVPN interfaces', async () => {
    const conn = new FixtureConnection('openwrt-24.10.8')
      .override('uci.get.openvpn', fail('NOT_FOUND', 'uci.get'))
      .override('session.access.ubus-luci-openvpn-generatekey', ok({ access: true }))
      .override(
        'uci.get.network',
        ok({
          values: {
            homevpn: {
              '.name': 'homevpn',
              '.type': 'interface',
              proto: 'openvpn',
              config: netifdProfilePath('homevpn'),
              disabled: '0',
            },
          },
        }),
      )
      .override('network.interface.dump', ok({ interface: [{ interface: 'homevpn', up: true }] }))
      .override('file.exec', ok({ code: 0 }))
      .override('file.write', ok({}))
      .override('file.remove', ok({}))
      .override('uci.add', ok({ section: 'office' }))
      .override('uci.set', ok({}))
      .override('uci.delete', ok({}))
      .override('uci.apply', ok({}))
      .override('uci.confirm', ok({}));

    const state = await getOpenvpn(conn);
    expect(state).toEqual({
      installed: true,
      instances: [
        {
          name: 'homevpn',
          enabled: true,
          configFile: '/etc/openvpn/homevpn/profile.ovpn',
          hasLogin: false,
          running: true,
          backend: 'netifd',
        },
      ],
    });

    const outcome = await importOvpn(conn, 'office', CLIENT, { username: 'me', password: 'secret' }, apply);
    expect(outcome.status).toBe('confirmed');
    expect(conn.calls).toContainEqual({
      object: 'file',
      method: 'write',
      params: { path: '/etc/openvpn/office/profile.ovpn', data: CLIENT },
    });
    expect(conn.calls).toContainEqual({
      object: 'uci',
      method: 'add',
      params: {
        config: 'network',
        type: 'interface',
        name: 'office',
        values: {
          proto: 'openvpn',
          config: '/etc/openvpn/office/profile.ovpn',
          disabled: '0',
          username: 'me',
          password: 'secret',
        },
      },
    });

    await setInstanceEnabled(conn, state.instances[0], false, apply);
    expect(conn.calls).toContainEqual({
      object: 'uci',
      method: 'set',
      params: { config: 'network', section: 'homevpn', values: { disabled: '1' } },
    });

    await deleteInstance(conn, state.instances[0], apply);
    expect(conn.calls).toContainEqual({
      object: 'uci',
      method: 'delete',
      params: { config: 'network', section: 'homevpn' },
    });
    expect(conn.calls).toContainEqual({
      object: 'file',
      method: 'remove',
      params: { path: '/etc/openvpn/homevpn/profile.ovpn' },
    });
  });

  it('prefers netifd when /etc/config/openvpn is readable with default examples alongside luci-proto-openvpn', async () => {
    const conn = new FixtureConnection('openwrt-24.10.8')
      .override('session.access.ubus-luci-openvpn-generatekey', ok({ access: true }))
      .override(
        'uci.get.network',
        ok({
          values: {
            tr_vpn: {
              '.name': 'tr_vpn',
              '.type': 'interface',
              proto: 'openvpn',
              config: netifdProfilePath('tr_vpn'),
              disabled: '0',
            },
          },
        }),
      )
      .override('network.interface.dump', ok({ interface: [{ interface: 'tr_vpn', up: true }] }))
      .override('file.exec', ok({ code: 0 }))
      .override('file.write', ok({}))
      .override('uci.add', ok({ section: 'work' }))
      .override('uci.apply', ok({}))
      .override('uci.confirm', ok({}));

    const state = await getOpenvpn(conn);
    expect(state.installed).toBe(true);
    expect(state.instances).toEqual([
      {
        name: 'tr_vpn',
        enabled: true,
        configFile: '/etc/openvpn/tr_vpn/profile.ovpn',
        hasLogin: false,
        running: true,
        backend: 'netifd',
      },
    ]);

    await importOvpn(conn, 'work', CLIENT, { username: 'u', password: 'p' }, apply);
    expect(conn.calls).toContainEqual({
      object: 'uci',
      method: 'add',
      params: {
        config: 'network',
        type: 'interface',
        name: 'work',
        values: {
          proto: 'openvpn',
          config: '/etc/openvpn/work/profile.ovpn',
          disabled: '0',
          username: 'u',
          password: 'p',
        },
      },
    });
  });
});
'''
        ovpn_test.write_text(text, encoding="utf-8")


    # 5. diagnostics/screens.test.tsx: stabilize upstream 'Today' outage test during 00:00-04:59 CST
    diag_test = rl_dir / "src/features/diagnostics/screens.test.tsx"
    if diag_test.is_file():
        dtext = diag_test.read_text(encoding="utf-8")
        if "2026-11-25T14:00:00+08:00" not in dtext:
            dtext = replace_once(
                dtext,
                "describe('outages and latency', () => {\n"
                "  it('shows availability, the curve of a custom target and the demo outage, and shares a summary', async () => {",
                "describe('outages and latency', () => {\n"
                "  afterEach(() => jest.useRealTimers());\n"
                "\n"
                "  it('shows availability, the curve of a custom target and the demo outage, and shares a summary', async () => {\n"
                "    jest.useFakeTimers({\n"
                "      now: new Date('2026-11-25T14:00:00+08:00'),\n"
                "      doNotFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'setImmediate', 'clearImmediate', 'nextTick', 'queueMicrotask', 'performance'],\n"
                "    });\n"
                "    resetDemoConnection();",
                "diagnostics.screens.test.clock",
            )
            diag_test.write_text(dtext, encoding="utf-8")


    # 6. packages.test.ts: verify SNAPSHOT (ImmortalWrt owrt / TR3000) apk support
    pkg_test = rl_dir / "src/api/services/packages.test.ts"
    ptext = pkg_test.read_text(encoding="utf-8")
    if "reads ImmortalWrt SNAPSHOT with apk" not in ptext:
        ptext = replace_once(
            ptext,
            "  it('needs a release version', async () => {",
            "  it('reads ImmortalWrt SNAPSHOT with apk (TR3000 owrt)', async () => {\n"
            "    await expect(\n"
            "      detectPackageEnv(router({ release: 'SNAPSHOT', apk: true, arch: 'aarch64_cortex-a53' })),\n"
            "    ).resolves.toMatchObject({\n"
            "      release: 'SNAPSHOT',\n"
            "      arch: 'aarch64_cortex-a53',\n"
            "      manager: 'apk',\n"
            "      uploadPath: '/tmp/upload.apk',\n"
            "      hasLists: true,\n"
            "    });\n"
            "  });\n\n"
            "  it('needs a release version', async () => {",
            "packages.test.snapshot",
        )
        pkg_test.write_text(ptext, encoding="utf-8")

    # 7. system.test.ts: verify autocore string tempinfo parsing (TR3000 & AX3000T)
    sys_test = rl_dir / "src/api/services/system.test.ts"
    stext = sys_test.read_text(encoding="utf-8")
    if "parses autocore tempinfo string" not in stext:
        stext = replace_once(
            stext,
            "  it('reads the hottest sensor and converts millidegrees', async () => {\n"
            "    const conn = new FixtureConnection().override('luci.getTempInfo', ok({ cpu: 48500, wifi: { value: 51000 } }));\n"
            "    await expect(getTemperature(conn)).resolves.toBe(51);\n"
            "  });",
            "  it('reads the hottest sensor and converts millidegrees', async () => {\n"
            "    const conn = new FixtureConnection().override('luci.getTempInfo', ok({ cpu: 48500, wifi: { value: 51000 } }));\n"
            "    await expect(getTemperature(conn)).resolves.toBe(51);\n"
            "  });\n\n"
            "  it('parses autocore tempinfo string preferring CPU temperature', async () => {\n"
            "    const conn = new FixtureConnection().override(\n"
            "      'luci.getTempInfo',\n"
            "      ok({ tempinfo: 'CPU: 48.2°C WiFi: 51.0°C 46.0°C ' }),\n"
            "    );\n"
            "    await expect(getTemperature(conn)).resolves.toBe(48.2);\n"
            "  });\n\n"
            "  it('parses autocore tempinfo string without CPU label and ignores unavailable text', async () => {\n"
            "    const wifiOnly = new FixtureConnection().override('luci.getTempInfo', ok({ tempinfo: 'WiFi: 46.5°C 49.0°C ' }));\n"
            "    await expect(getTemperature(wifiOnly)).resolves.toBe(49);\n"
            "    const none = new FixtureConnection().override('luci.getTempInfo', ok({ tempinfo: 'No temperature info' }));\n"
            "    await expect(getTemperature(none)).resolves.toBeNull();\n"
            "  });",
            "system.test.tempinfo",
        )
        sys_test.write_text(stext, encoding="utf-8")

    # 8. ssh-keys.test.ts: verify NO_DATA (empty authorized_keys) handling
    ssh_test = rl_dir / "src/api/services/ssh-keys.test.ts"
    ktext = ssh_test.read_text(encoding="utf-8")
    if "treats empty authorized_keys (NO_DATA) as empty" not in ktext:
        ktext = replace_once(
            ktext,
            "  it('does not write over a file it could not read', async () => {",
            "  it('treats empty authorized_keys (NO_DATA) as empty', async () => {\n"
            "    const conn = new FixtureConnection().override('file.read', fail('NO_DATA')).override('file.write', ok({}));\n"
            "    expect(await hasPublicKey(conn, APP)).toBe(false);\n"
            "    expect(await installPublicKey(conn, APP)).toBe('installed');\n"
            "    expect(writes(conn)).toEqual([{ path: AUTHORIZED_KEYS, data: `${APP}\\n`, mode: 0o600 }]);\n"
            "  });\n\n"
            "  it('does not write over a file it could not read', async () => {",
            "ssh-keys.test.nodata",
        )
        ssh_test.write_text(ktext, encoding="utf-8")


def patch_temperature(rl_dir: Path) -> None:
    path = rl_dir / "src/api/services/system.ts"
    text = path.read_text(encoding="utf-8")
    if "CPU:\\s*(-?\\d+(?:\\.\\d+)?)\\s*°?\\s*C" in text:
        return

    old = (
        "export async function getTemperature(conn: RouterConnection): Promise<number | null> {\n"
        "  try {\n"
        "    const data = await conn.call<unknown>('luci', 'getTempInfo');\n"
        "    const numbers: number[] = [];\n"
        "    const walk = (v: unknown) => {\n"
        "      if (typeof v === 'number' && Number.isFinite(v)) numbers.push(v);\n"
        "      else if (v && typeof v === 'object') Object.values(v).forEach(walk);\n"
        "    };\n"
        "    walk(data);\n"
        "    if (numbers.length === 0) return null;\n"
        "    const max = Math.max(...numbers);\n"
        "    return max > 1000 ? max / 1000 : max; // millidegrees from sysfs\n"
        "  } catch {\n"
        "    return null;\n"
        "  }\n"
        "}"
    )
    new = (
        "export async function getTemperature(conn: RouterConnection): Promise<number | null> {\n"
        "  try {\n"
        "    const data = await conn.call<unknown>('luci', 'getTempInfo');\n"
        "    const numbers: number[] = [];\n"
        "    let cpuTemp: number | null = null;\n"
        "    const walk = (v: unknown) => {\n"
        "      if (typeof v === 'number' && Number.isFinite(v)) {\n"
        "        numbers.push(v);\n"
        "      } else if (typeof v === 'string') {\n"
        "        const cpu = /CPU:\\s*(-?\\d+(?:\\.\\d+)?)\\s*°?\\s*C/i.exec(v);\n"
        "        if (cpu) {\n"
        "          const n = Number(cpu[1]);\n"
        "          if (Number.isFinite(n)) cpuTemp = n;\n"
        "        }\n"
        "        for (const m of v.matchAll(/(-?\\d+(?:\\.\\d+)?)\\s*°\\s*C/gi)) {\n"
        "          const n = Number(m[1]);\n"
        "          if (Number.isFinite(n)) numbers.push(n);\n"
        "        }\n"
        "      } else if (v && typeof v === 'object') {\n"
        "        Object.values(v).forEach(walk);\n"
        "      }\n"
        "    };\n"
        "    walk(data);\n"
        "    if (cpuTemp !== null) return cpuTemp;\n"
        "    if (numbers.length === 0) return null;\n"
        "    const max = Math.max(...numbers);\n"
        "    return max > 1000 ? max / 1000 : max; // millidegrees from sysfs\n"
        "  } catch {\n"
        "    return null;\n"
        "  }\n"
        "}"
    )
    text = replace_once(text, old, new, "system.getTemperature")
    path.write_text(text, encoding="utf-8")


def patch_ssh_keys(rl_dir: Path) -> None:
    path = rl_dir / "src/api/services/ssh-keys.ts"
    text = path.read_text(encoding="utf-8")
    if "error.code === 'NO_DATA'" in text:
        return

    text = replace_once(
        text,
        "    if (error instanceof UbusError && error.code === 'NOT_FOUND') return '';",
        "    if (error instanceof UbusError && (error.code === 'NOT_FOUND' || error.code === 'NO_DATA')) return '';",
        "ssh-keys.nodata",
    )
    path.write_text(text, encoding="utf-8")


def patch_logs(rl_dir: Path) -> None:
    path = rl_dir / "src/api/services/logs.ts"
    text = path.read_text(encoding="utf-8")
    if "'ACCESS_DENIED'" in text:
        return

    text = replace_once(
        text,
        "    if (!(error instanceof UbusError && ['NOT_FOUND', 'PERMISSION_DENIED'].includes(error.code))) throw error;",
        "    if (!(error instanceof UbusError && ['NOT_FOUND', 'PERMISSION_DENIED', 'ACCESS_DENIED'].includes(error.code))) throw error;",
        "logs.access_denied",
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <routelink_dir>", file=sys.stderr)
        return 2
    rl_dir = Path(sys.argv[1])
    patch_capabilities(rl_dir)
    patch_sqm(rl_dir)
    patch_client_actions(rl_dir)
    patch_openvpn(rl_dir)
    patch_packages(rl_dir)
    patch_temperature(rl_dir)
    patch_ssh_keys(rl_dir)
    patch_logs(rl_dir)
    patch_tests(rl_dir)
    print(f"Applied RouteLink app compatibility patches and unit tests in {rl_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
