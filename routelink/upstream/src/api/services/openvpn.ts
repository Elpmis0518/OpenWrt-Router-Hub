import type { RouterConnection } from '../connection/types';
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
