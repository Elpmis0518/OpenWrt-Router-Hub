import { fail, FixtureConnection, ok } from '../../../test/fixture-connection';
import { NativeError } from '../http/errors';
import type { UbusCall } from '../ubus/types';
import {
  checkOnline,
  findOfficialImage,
  firmwareInfoOf,
  flashFirmware,
  onlineSupport,
  pickImage,
  sysupgradeArgs,
  upgradeVersions,
  uploadFirmware,
  validateFirmware,
  type FirmwareInfo,
  type ProfilesJson,
  type VersionsJson,
} from './firmware';

const VERSIONS: VersionsJson = {
  stable_version: '25.12.5',
  oldstable_version: '24.10.8',
  versions_list: [
    '25.12.5',
    '25.12.4',
    '25.12.0-rc1',
    '25.12.0',
    '24.10.8',
    '24.10.7',
    '24.10.0',
    '23.05.6',
    '23.05.5',
  ],
};

/** Trimmed from downloads.openwrt.org (24.10.8). */
const X86: ProfilesJson = {
  target: 'x86/64',
  version_number: '24.10.8',
  version_code: 'r29233-443ec4032a',
  profiles: {
    generic: {
      supported_devices: [],
      titles: [{ vendor: 'Generic', model: 'x86/64' }],
      images: [
        {
          type: 'rootfs',
          filesystem: 'squashfs',
          name: 'openwrt-24.10.8-x86-64-generic-squashfs-rootfs.img.gz',
          sha256: 'a1',
        },
        {
          type: 'combined',
          filesystem: 'squashfs',
          name: 'openwrt-24.10.8-x86-64-generic-squashfs-combined.img.gz',
          sha256: 'a2',
        },
        {
          type: 'combined-efi',
          filesystem: 'ext4',
          name: 'openwrt-24.10.8-x86-64-generic-ext4-combined-efi.img.gz',
          sha256: 'a3',
        },
        {
          type: 'combined',
          filesystem: 'ext4',
          name: 'openwrt-24.10.8-x86-64-generic-ext4-combined.img.gz',
          sha256: 'a4',
        },
        {
          type: 'combined-efi',
          filesystem: 'squashfs',
          name: 'openwrt-24.10.8-x86-64-generic-squashfs-combined-efi.img.gz',
          sha256: 'a5',
        },
      ],
    },
  },
};
const FILOGIC: ProfilesJson = {
  target: 'mediatek/filogic',
  version_number: '24.10.8',
  version_code: 'r29233-443ec4032a',
  profiles: {
    openwrt_one: {
      supported_devices: ['openwrt,one'],
      titles: [{ vendor: 'OpenWrt', model: 'One' }],
      images: [
        { type: 'factory.ubi', name: 'openwrt-24.10.8-mediatek-filogic-openwrt_one-factory.ubi', sha256: 'b1' },
        {
          type: 'kernel',
          filesystem: 'initramfs',
          name: 'openwrt-24.10.8-mediatek-filogic-openwrt_one-initramfs.itb',
          sha256: 'b2',
        },
        {
          type: 'sysupgrade',
          filesystem: 'squashfs',
          name: 'openwrt-24.10.8-mediatek-filogic-openwrt_one-squashfs-sysupgrade.itb',
          sha256: 'b3',
        },
      ],
    },
    glinet_gl_mt6000: {
      supported_devices: ['glinet,gl-mt6000'],
      titles: [{ vendor: 'GL.iNet', model: 'GL-MT6000' }],
      images: [{ type: 'sysupgrade', filesystem: 'squashfs', name: 'mt6000-sysupgrade.bin', sha256: 'c1' }],
    },
  },
};

const one: FirmwareInfo = {
  distribution: 'OpenWrt',
  version: '24.10.8',
  revision: 'r29233-443ec4032a',
  target: 'mediatek/filogic',
  board: 'openwrt,one',
  model: 'OpenWrt One',
  rootfs: 'squashfs',
  efi: false,
};
const qemu: FirmwareInfo = {
  ...one,
  target: 'x86/64',
  board: 'qemu-standard-pc-i440fx-piix-1996',
  model: 'QEMU',
  efi: false,
};

describe('firmwareInfoOf', () => {
  it('reads system board and spots an EFI boot partition', () => {
    const board = {
      model: 'QEMU Standard PC',
      board_name: 'qemu-standard-pc-i440fx-piix-1996',
      rootfs_type: 'squashfs',
      release: { distribution: 'OpenWrt', version: '24.10.8', revision: 'r29233-443ec4032a', target: 'x86/64' },
    };
    expect(firmwareInfoOf(board, '/dev/root /rom squashfs ro 0 0\n/dev/sda1 /boot ext4 rw 0 0\n')).toEqual({
      ...qemu,
      model: 'QEMU Standard PC',
    });
    expect(firmwareInfoOf(board, '/dev/sda1 /boot vfat rw 0 0\n').efi).toBe(true);
  });
});

describe('onlineSupport', () => {
  it.each([
    [one, { ok: true, site: 'https://downloads.openwrt.org' }],
    [
      { ...one, distribution: 'ImmortalWrt' },
      { ok: true, site: 'https://downloads.immortalwrt.org' },
    ],
    [
      { ...one, distribution: 'Kwrt' },
      { ok: false, reason: 'distribution' },
    ],
    [
      { ...one, version: 'SNAPSHOT' },
      { ok: false, reason: 'version' },
    ],
    [
      { ...one, version: '25.12.0-rc1' },
      { ok: false, reason: 'version' },
    ],
  ])('%#', (info, expected) => {
    expect(onlineSupport(info as FirmwareInfo)).toEqual(expected);
  });
});

describe('upgradeVersions', () => {
  it('offers the newest of the same series and the newest stable release', () => {
    expect(upgradeVersions('24.10.7', VERSIONS)).toEqual(['24.10.8', '25.12.5']);
    expect(upgradeVersions('24.10.8', VERSIONS)).toEqual(['25.12.5']);
    expect(upgradeVersions('25.12.5', VERSIONS)).toEqual([]);
    expect(upgradeVersions('23.05.5', VERSIONS)).toEqual(['23.05.6', '25.12.5']);
  });
});

describe('pickImage', () => {
  it('finds the device by board name and takes its sysupgrade image', () => {
    expect(pickImage(FILOGIC, one)?.name).toBe('openwrt-24.10.8-mediatek-filogic-openwrt_one-squashfs-sysupgrade.itb');
    expect(pickImage(FILOGIC, { ...one, board: 'glinet,gl-mt6000' })?.sha256).toBe('c1');
    expect(pickImage(FILOGIC, { ...one, board: 'unknown,router' })).toBeNull();
  });

  it('x86: the generic combined image with the same file system and boot method', () => {
    expect(pickImage(X86, qemu)?.name).toBe('openwrt-24.10.8-x86-64-generic-squashfs-combined.img.gz');
    expect(pickImage(X86, { ...qemu, efi: true })?.name).toBe(
      'openwrt-24.10.8-x86-64-generic-squashfs-combined-efi.img.gz',
    );
    expect(pickImage(X86, { ...qemu, rootfs: 'ext4' })?.name).toBe(
      'openwrt-24.10.8-x86-64-generic-ext4-combined.img.gz',
    );
  });
});

describe('findOfficialImage and checkOnline', () => {
  const site = 'https://downloads.openwrt.org';
  const fetchJson = async (url: string) => {
    if (url === `${site}/.versions.json`) return VERSIONS;
    if (url === `${site}/releases/24.10.8/targets/x86/64/profiles.json`) return X86;
    if (url === `${site}/releases/25.12.5/targets/x86/64/profiles.json`) {
      return {
        ...X86,
        version_number: '25.12.5',
        version_code: 'r30000-aaaaaaaaaa',
        profiles: {
          generic: {
            ...X86.profiles.generic,
            images: X86.profiles.generic.images.map((i) => ({ ...i, name: i.name.replace('24.10.8', '25.12.5') })),
          },
        },
      };
    }
    throw new Error(`404 ${url}`);
  };

  it('builds the download URL', async () => {
    expect(await findOfficialImage(qemu, '24.10.8', fetchJson)).toEqual({
      version: '24.10.8',
      name: 'openwrt-24.10.8-x86-64-generic-squashfs-combined.img.gz',
      url: `${site}/releases/24.10.8/targets/x86/64/openwrt-24.10.8-x86-64-generic-squashfs-combined.img.gz`,
      sha256: 'a2',
    });
  });

  it('lists the upgrades for an official build', async () => {
    const r = await checkOnline(qemu, fetchJson);
    expect(r).toMatchObject({ status: 'ok', current: { version: '24.10.8' } });
    expect(r.status === 'ok' && r.upgrades.map((u) => u.version)).toEqual(['25.12.5']);
  });

  it('turns down a build whose revision is not the official one', async () => {
    expect(await checkOnline({ ...qemu, revision: 'r29233-deadbeef00' }, fetchJson)).toEqual({
      status: 'custom-build',
    });
    expect(await checkOnline({ ...qemu, distribution: 'Kwrt' }, fetchJson)).toEqual({
      status: 'unsupported',
      reason: 'distribution',
    });
  });

  it('says so when the device is not in the official build', async () => {
    const r = await checkOnline({ ...one, target: 'x86/64', board: 'x', model: 'x' }, async (url) =>
      url.endsWith('.versions.json') ? VERSIONS : { ...X86, profiles: {} },
    );
    expect(r).toEqual({ status: 'no-image' });
  });
});

describe('on the router', () => {
  const execOf = (c: UbusCall) =>
    [String((c.params as { command: string }).command), ...((c.params as { params?: string[] }).params ?? [])].join(
      ' ',
    );

  it('uses the exact sysupgrade command lines from LuCI’s ACL', () => {
    expect(sysupgradeArgs(true, false)).toEqual(['/tmp/firmware.bin']);
    expect(sysupgradeArgs(false, false)).toEqual(['-n', '/tmp/firmware.bin']);
    expect(sysupgradeArgs(true, true)).toEqual(['--force', '/tmp/firmware.bin']);
    expect(sysupgradeArgs(false, true)).toEqual(['-n', '--force', '/tmp/firmware.bin']);
  });

  it('refuses an image larger than the free RAM in /tmp, and uploads otherwise', async () => {
    const conn = new FixtureConnection().override('file.write', ok({}));
    await expect(uploadFirmware(conn, new Uint8Array(3_000_000), 2_000)).rejects.toMatchObject({ code: 'no-space' });
    await uploadFirmware(conn, new Uint8Array(70_000), 200_000);
    expect(new Set(conn.calls.map((c) => (c.params as { path: string }).path))).toEqual(new Set(['/tmp/firmware.bin']));
  });

  it('reads the image check', async () => {
    const conn = new FixtureConnection().override(
      'system.validate_firmware_image',
      ok({
        tests: { fwtool_signature: true, fwtool_device_match: false },
        valid: false,
        forceable: true,
        allow_backup: true,
      }),
    );
    expect(await validateFirmware(conn)).toEqual({
      valid: false,
      forceable: true,
      allowBackup: true,
      failed: ['fwtool_device_match'],
    });
    expect(conn.calls[0].params).toEqual({ path: '/tmp/firmware.bin' });
  });

  it('starts the upgrade; the router may stop answering right away', async () => {
    const conn = new FixtureConnection().override('file.exec', () => {
      throw new NativeError('ERR_TIMEOUT', 'gone');
    });
    await flashFirmware(conn, true, false);
    expect(execOf(conn.calls[0])).toBe('/sbin/sysupgrade /tmp/firmware.bin');
    const refused = new FixtureConnection().override('file.exec', ok({ code: 1, stderr: 'Image check failed.' }));
    await expect(flashFirmware(refused, false, false)).rejects.toMatchObject({ code: 'flash-failed' });
    // 25.12 in QEMU: rpcd is gone before it answers, and uhttpd reports a ubus timeout.
    const stopped = new FixtureConnection().override('file.exec', fail('TIMEOUT'));
    await expect(flashFirmware(stopped, true, false)).resolves.toBeUndefined();
    const denied = new FixtureConnection().override('file.exec', fail('PERMISSION_DENIED'));
    await expect(flashFirmware(denied, true, false)).rejects.toMatchObject({ code: 'PERMISSION_DENIED' });
  });
});

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
