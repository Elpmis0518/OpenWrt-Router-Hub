import { FixtureConnection, fail, loadFixture, ok } from '../../../test/fixture-connection';
import type { UciSection } from '../uci';
import {
  deleteInstance,
  getOpenvpn,
  importOvpn,
  inspectOvpn,
  netifdProfilePath,
  parseNetifdOpenvpn,
  parseOpenvpn,
  setInstanceEnabled,
  validateImport,
  type OvpnInstance,
} from './openvpn';

const CLIENT = `# exported by the provider
client
dev tun
proto udp
remote vpn.example.com 1194
resolv-retry infinite
nobind
auth-user-pass
remote-cert-tls server
<ca>
-----BEGIN CERTIFICATE-----
MIIB...
-----END CERTIFICATE-----
</ca>
verb 3
`;

const recordedUci = () =>
  (loadFixture('openwrt-24.10.8', 'uci.get.openvpn') as { data: { values: Record<string, UciSection> } }).data.values;

describe('inspectOvpn', () => {
  it('reads a client profile with inline certificates and a login', () => {
    expect(inspectOvpn(CLIENT)).toEqual({
      client: true,
      remote: 'vpn.example.com',
      port: '1194',
      proto: 'udp',
      needsLogin: true,
      missingFiles: [],
    });
  });

  it('notices certificates and keys kept in separate files, and server profiles', () => {
    const text =
      'tls-client\nremote 198.51.100.1 443 tcp\nca ca.crt\ncert client.crt\nkey client.key\ntls-auth ta.key 1\n';
    expect(inspectOvpn(text)).toMatchObject({
      client: true,
      port: '443',
      proto: 'tcp',
      needsLogin: false,
      missingFiles: ['ca.crt', 'client.crt', 'client.key', 'ta.key'],
    });
    expect(inspectOvpn('port 1194\nproto udp\ndev tun\nserver 10.8.0.0 255.255.255.0\n').client).toBe(false);
  });

  it('ignores comments, and Windows line ends', () => {
    expect(inspectOvpn('# client\r\n; client\r\nremote a 1\r\n').client).toBe(false);
    expect(inspectOvpn('client\r\nremote a 1\r\n').remote).toBe('a');
  });
});

describe('validateImport', () => {
  const instances = parseOpenvpn(recordedUci(), null);
  it.each([
    ['a client profile', 'office', CLIENT, true, null],
    ['no name', '', CLIENT, true, 'name-invalid'],
    ['a space in the name', 'my vpn', CLIENT, true, 'name-invalid'],
    ['the name of an example', 'sample_client', CLIENT, true, 'name-taken'],
    ['a server profile', 'office', 'dev tun\nserver 10.8.0.0 255.255.255.0\n', true, 'not-client'],
    ['a separate CA file', 'office', 'client\nremote a 1\nca ca.crt\n', true, 'missing-files'],
    ['no login', 'office', CLIENT, false, 'needs-login'],
  ] as const)('%s', (_label, name, text, login, error) => {
    expect(validateImport(name, text, instances, login ? { username: 'u', password: 'p' } : undefined)).toBe(error);
  });
});

describe('parseOpenvpn', () => {
  it('hides the default examples while they are off', () => {
    expect(parseOpenvpn(recordedUci(), null)).toEqual([]);
  });

  it('reads imported instances with their running state', () => {
    const values: Record<string, UciSection> = {
      ...recordedUci(),
      office: {
        '.name': 'office',
        '.type': 'openvpn',
        enabled: '1',
        config: '/etc/openvpn/office.ovpn',
        username: 'me',
        password: 'secret',
      },
      backup: { '.name': 'backup', '.type': 'openvpn', enabled: '0', config: '/etc/openvpn/backup.ovpn' },
    };
    const services = { openvpn: { instances: { office: { running: true, pid: 1234 } } } };
    expect(parseOpenvpn(values, services)).toEqual<OvpnInstance[]>([
      { name: 'office', enabled: true, configFile: '/etc/openvpn/office.ovpn', hasLogin: true, running: true },
      { name: 'backup', enabled: false, configFile: '/etc/openvpn/backup.ovpn', hasLogin: false, running: false },
    ]);
    // 23.05 does not let LuCI's sessions read procd's service list.
    expect(parseOpenvpn(values, null)[0].running).toBeNull();
  });
});

describe('router calls', () => {
  const apply = { sleep: async () => {} };

  it('reads uci and the service list, and copes without the service list', async () => {
    const conn = new FixtureConnection('openwrt-24.10.8');
    expect(await getOpenvpn(conn)).toEqual({ installed: true, instances: [] });
    const old = new FixtureConnection('openwrt-23.05.6').override('service.list', fail('PERMISSION_DENIED'));
    expect((await getOpenvpn(old)).installed).toBe(true);
    const none = new FixtureConnection('openwrt-24.10.8').override('uci.get.openvpn', fail('NOT_FOUND'));
    expect(await getOpenvpn(none)).toEqual({ installed: false, instances: [] });
  });

  it('uploads the profile, then adds the instance with the login', async () => {
    const conn = new FixtureConnection()
      .override('file.write', ok({}))
      .override('uci.add', ok({ section: 'office' }))
      .override('uci.apply', ok({}))
      .override('uci.confirm', ok({}));
    const outcome = await importOvpn(conn, 'office', CLIENT, { username: 'me', password: 'secret' }, apply);
    expect(outcome.status).toBe('confirmed');
    expect(conn.calls[0]).toEqual({
      object: 'file',
      method: 'write',
      params: { path: '/etc/openvpn/office.ovpn', data: CLIENT },
    });
    expect(conn.calls[1]).toEqual({
      object: 'uci',
      method: 'add',
      params: {
        config: 'openvpn',
        type: 'openvpn',
        name: 'office',
        values: { enabled: '1', config: '/etc/openvpn/office.ovpn', username: 'me', password: 'secret' },
      },
    });
  });

  it('switches an instance and deletes it with its profile', async () => {
    const office: OvpnInstance = {
      name: 'office',
      enabled: true,
      configFile: '/etc/openvpn/office.ovpn',
      hasLogin: false,
      running: true,
    };
    const conn = new FixtureConnection()
      .override('uci.set', ok({}))
      .override('uci.delete', ok({}))
      .override('uci.apply', ok({}))
      .override('uci.confirm', ok({}))
      .override('file.remove', ok({}));
    await setInstanceEnabled(conn, office, false, apply);
    expect(conn.calls[0].params).toEqual({ config: 'openvpn', section: 'office', values: { enabled: '0' } });
    await deleteInstance(conn, office, apply);
    expect(conn.calls.map((c) => `${c.object}.${c.method}`)).toContain('file.remove');
  });
});

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
