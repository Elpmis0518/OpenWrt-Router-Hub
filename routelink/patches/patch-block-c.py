#!/usr/bin/env python3
"""Patch routelinkd sys/block.c (nftables-first with iptables/ip6tables fallback) and install firewall hotplug hook."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

PATCH_MARKER = "ROUTELINK_BLOCK"

BLOCK_C_CONTENT = r'''#define _GNU_SOURCE
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>

#include "sys/block.h"
#include "sys/proc.h"

#define NFT_TIMEOUT_MS 5000
#define RL_IPT_CHAIN "ROUTELINK_BLOCK"
#define RL_IPT_MAX_UNLINK 64

struct rl_block {
	rl_mac *macs; /* as loaded */
	size_t n, cap;
	rl_nft_net local[RL_BLOCK_MAX_NETS];
	size_t n_local;
	bool loaded;       /* the table/chain matches macs/local */
	bool dirty;        /* a table/chain of ours may exist */
	bool use_iptables; /* true when nft is unavailable (firewall3 / LEDE) */
	char error[200];
};

static int cmp_mac(const void *a, const void *b)
{
	return memcmp(a, b, sizeof(rl_mac));
}

static int ipt_unlink_forward(const char *cmd)
{
	char out[256];
	const char *del[] = { cmd, "-w", "5", "-D", "FORWARD", "-j", RL_IPT_CHAIN, NULL };
	int rc = 0;
	for (int i = 0; i < RL_IPT_MAX_UNLINK; i++) {
		rc = rl_proc_run(del, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS);
		if (rc == 127)
			return 127;
		if (rc != 0)
			return 0;
	}
	return 0;
}

static int ipt_clear_table(const char *cmd, bool optional)
{
	char out[256];
	int rc = ipt_unlink_forward(cmd);
	if (rc == 127)
		return optional ? 0 : 127;

	const char *flush[] = { cmd, "-w", "5", "-F", RL_IPT_CHAIN, NULL };
	(void)rl_proc_run(flush, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS);

	const char *del_chain[] = { cmd, "-w", "5", "-X", RL_IPT_CHAIN, NULL };
	(void)rl_proc_run(del_chain, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS);
	return 0;
}

static int ipt_apply_table(const rl_block *b, const char *cmd, uint8_t family, bool optional, char *errbuf,
			   size_t errsize)
{
	char out[512];
	if (errsize)
		errbuf[0] = '\0';

	if (!b->n) {
		int rc = ipt_clear_table(cmd, optional);
		if (rc == 127 && !optional) {
			snprintf(errbuf, errsize, "%s is not installed", cmd);
			return -1;
		}
		return 0;
	}

	/* 1. Remove any stale/duplicate FORWARD -> ROUTELINK_BLOCK jumps first. */
	int rc = ipt_unlink_forward(cmd);
	if (rc == 127) {
		if (optional)
			return 0;
		snprintf(errbuf, errsize, "%s is not installed", cmd);
		return -1;
	}

	/* 2. Ensure chain exists, then flush it cleanly. */
	const char *create[] = { cmd, "-w", "5", "-N", RL_IPT_CHAIN, NULL };
	rc = rl_proc_run(create, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS);
	if (rc == 127) {
		if (optional)
			return 0;
		snprintf(errbuf, errsize, "%s is not installed", cmd);
		return -1;
	}

	const char *flush[] = { cmd, "-w", "5", "-F", RL_IPT_CHAIN, NULL };
	rc = rl_proc_run(flush, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS);
	if (rc != 0) {
		if (optional)
			return 0;
		rl_proc_first_line(out, errbuf, errsize);
		if (!errbuf[0])
			snprintf(errbuf, errsize, "%s -F %s failed", cmd, RL_IPT_CHAIN);
		return -1;
	}

	/* 3. Allow traffic destined to local LAN subnets first (-j RETURN). */
	for (size_t i = 0; i < b->n_local; i++) {
		if (b->local[i].family != family)
			continue;
		char ip[INET6_ADDRSTRLEN], cidr[INET6_ADDRSTRLEN + 8];
		if (!inet_ntop(family == 6 ? AF_INET6 : AF_INET, b->local[i].addr, ip, sizeof(ip)))
			continue;
		snprintf(cidr, sizeof(cidr), "%s/%u", ip, b->local[i].prefix);
		const char *allow[] = { cmd, "-w", "5", "-A", RL_IPT_CHAIN, "-d", cidr, "-j", "RETURN", NULL };
		rc = rl_proc_run(allow, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS);
		if (rc != 0) {
			if (optional)
				return 0;
			rl_proc_first_line(out, errbuf, errsize);
			if (!errbuf[0])
				snprintf(errbuf, errsize, "%s RETURN rule failed", cmd);
			return -1;
		}
	}

	/* 4. Drop forwarded packets from blocked MAC addresses. */
	for (size_t i = 0; i < b->n; i++) {
		char mac[18];
		snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x", b->macs[i].b[0], b->macs[i].b[1],
			 b->macs[i].b[2], b->macs[i].b[3], b->macs[i].b[4], b->macs[i].b[5]);
		const char *drop[] = { cmd, "-w", "5", "-A", RL_IPT_CHAIN, "-m", "mac", "--mac-source", mac, "-j",
				       "DROP", NULL };
		rc = rl_proc_run(drop, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS);
		if (rc != 0) {
			if (optional)
				return 0;
			rl_proc_first_line(out, errbuf, errsize);
			if (!errbuf[0])
				snprintf(errbuf, errsize, "%s mac DROP rule failed", cmd);
			return -1;
		}
	}

	/* 5. Mount ROUTELINK_BLOCK at the top of FORWARD. */
	const char *jump[] = { cmd, "-w", "5", "-I", "FORWARD", "1", "-j", RL_IPT_CHAIN, NULL };
	rc = rl_proc_run(jump, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS);
	if (rc != 0) {
		if (optional)
			return 0;
		rl_proc_first_line(out, errbuf, errsize);
		if (!errbuf[0])
			snprintf(errbuf, errsize, "%s FORWARD jump failed", cmd);
		return -1;
	}

	return 0;
}

static int load_iptables(rl_block *b)
{
	char line[160], line6[160];
	b->dirty = true;
	if (ipt_apply_table(b, "iptables", 4, false, line, sizeof(line)) != 0) {
		b->loaded = false;
		snprintf(b->error, sizeof(b->error), "blocking: %s", line[0] ? line : "iptables failed");
		syslog(LOG_ERR, "loading iptables chain " RL_IPT_CHAIN " failed: %s",
		       line[0] ? line : "iptables failed");
		return -1;
	}
	/* ip6tables is optional: tolerate missing binary or IPv6 filter support. */
	(void)ipt_apply_table(b, "ip6tables", 6, true, line6, sizeof(line6));

	b->loaded = true;
	b->error[0] = '\0';
	if (!b->n)
		b->dirty = false;
	return 0;
}

static int load_nft(rl_block *b)
{
	size_t size = 1024 + b->n * 24 + b->n_local * 64;
	char *script = malloc(size), out[512], line[160];
	uint8_t(*macs)[6] = malloc((b->n ? b->n : 1) * 6);
	if (!script || !macs)
		abort();
	for (size_t i = 0; i < b->n; i++)
		memcpy(macs[i], b->macs[i].b, 6);
	size_t len = rl_nft_script(script, size, (const uint8_t(*)[6])macs, b->n, b->local, b->n_local);
	free(macs);
	if (len == (size_t)-1) {
		free(script);
		snprintf(b->error, sizeof(b->error), "nft script too long");
		return -1;
	}
	const char *argv[] = { "nft", "-f", "-", NULL };
	int rc = rl_proc_run(argv, script, len, out, sizeof(out), NFT_TIMEOUT_MS);
	free(script);
	if (rc == 127) {
		/* nft is not installed: transparently fall back to iptables/ip6tables. */
		b->use_iptables = true;
		syslog(LOG_INFO, "nft not found; falling back to iptables chain " RL_IPT_CHAIN);
		return load_iptables(b);
	}
	b->dirty = true;
	if (rc == 0) {
		b->loaded = true;
		b->error[0] = '\0';
		if (!b->n)
			b->dirty = false; /* the script only deleted the table */
		return 0;
	}
	b->loaded = false;
	if (rc < 0)
		snprintf(line, sizeof(line), "nft did not finish");
	else
		rl_proc_first_line(out, line, sizeof(line));
	snprintf(b->error, sizeof(b->error), "blocking: %s", line[0] ? line : "nft failed");
	syslog(LOG_ERR, "loading the nftables table inet " RL_NFT_TABLE " failed: %s", line[0] ? line : "nft failed");
	return -1;
}

static int load(rl_block *b)
{
	if (b->use_iptables)
		return load_iptables(b);
	return load_nft(b);
}

rl_block *rl_block_new(void)
{
	rl_block *b = calloc(1, sizeof(*b));
	if (!b)
		abort();
	b->dirty = true; /* left behind by a daemon that crashed: the first set replaces it */
	return b;
}

void rl_block_free(rl_block *b, bool clear)
{
	if (!b)
		return;
	if (clear && b->dirty) {
		b->n = 0;
		load(b);
	}
	free(b->macs);
	free(b);
}

int rl_block_set(rl_block *b, const rl_mac *macs, size_t n, const rl_nft_net *local, size_t n_local)
{
	rl_mac *sorted = malloc((n ? n : 1) * sizeof(rl_mac));
	if (!sorted)
		abort();
	if (n) {
		memcpy(sorted, macs, n * sizeof(rl_mac));
		qsort(sorted, n, sizeof(rl_mac), cmp_mac);
	}
	if (n_local > RL_BLOCK_MAX_NETS)
		n_local = RL_BLOCK_MAX_NETS;
	bool same = n == b->n && (!n || !memcmp(sorted, b->macs, n * sizeof(rl_mac))) &&
		    (!n || (n_local == b->n_local &&
			    (!n_local || !memcmp(local, b->local, n_local * sizeof(rl_nft_net)))));
	/* unchanged: loaded, or failed (not retried every minute) */
	if ((same && (b->loaded || b->error[0])) || (!n && !b->n && !b->dirty)) {
		free(sorted);
		return b->error[0] ? -1 : 0;
	}
	free(b->macs);
	b->macs = sorted;
	b->n = b->cap = n;
	if (n_local)
		memcpy(b->local, local, n_local * sizeof(rl_nft_net));
	b->n_local = n_local;
	return load(b);
}

bool rl_block_has(const rl_block *b, const rl_mac *mac)
{
	return b->loaded && bsearch(mac, b->macs, b->n, sizeof(rl_mac), cmp_mac) != NULL;
}

void rl_block_retry(rl_block *b)
{
	b->error[0] = '\0';
	b->loaded = false;
}

void rl_block_check(rl_block *b)
{
	if (!b->n || b->error[0])
		return;
	if (b->loaded) {
		char out[256];
		if (b->use_iptables) {
			char mac[18];
			snprintf(mac, sizeof(mac), "%02x:%02x:%02x:%02x:%02x:%02x", b->macs[0].b[0], b->macs[0].b[1],
				 b->macs[0].b[2], b->macs[0].b[3], b->macs[0].b[4], b->macs[0].b[5]);
			const char *chk_jump[] = { "iptables", "-w", "5", "-C", "FORWARD", "-j", RL_IPT_CHAIN, NULL };
			const char *chk_rule[] = { "iptables", "-w", "5", "-C", RL_IPT_CHAIN, "-m", "mac",
						   "--mac-source", mac, "-j", "DROP", NULL };
			if (rl_proc_run(chk_jump, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS) == 0 &&
			    rl_proc_run(chk_rule, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS) == 0)
				return;
			syslog(LOG_NOTICE, "iptables chain " RL_IPT_CHAIN " or FORWARD jump missing, loading again");
		} else {
			const char *argv[] = { "nft", "list", "table", "inet", RL_NFT_TABLE, NULL };
			if (rl_proc_run(argv, NULL, 0, out, sizeof(out), NFT_TIMEOUT_MS) == 0)
				return;
			syslog(LOG_NOTICE, "the nftables table inet " RL_NFT_TABLE " went missing, loading it again");
		}
	}
	load(b);
}

const char *rl_block_error(const rl_block *b)
{
	return b->error;
}
'''


def patch_routelinkd(routelinkd_dir: Path) -> None:
    block_c = routelinkd_dir / "src/sys/block.c"
    makefile = routelinkd_dir / "Makefile"
    files_dir = routelinkd_dir / "files"

    if not block_c.is_file():
        raise FileNotFoundError(f"block.c not found: {block_c}")
    if not makefile.is_file():
        raise FileNotFoundError(f"routelinkd Makefile not found: {makefile}")

    # 1. Write patched block.c
    block_c.write_text(BLOCK_C_CONTENT, encoding="utf-8")

    # 2. Install firewall restore script + fw3 uci-defaults hook into routelinkd/files/
    patch_files_dir = Path(__file__).resolve().parent / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(patch_files_dir / "routelink.firewall", files_dir / "routelink.firewall")
    shutil.copy2(
        patch_files_dir / "routelink.firewall-uci-defaults",
        files_dir / "routelink.firewall-uci-defaults",
    )

    # 3. Idempotently wire /usr/share/routelink/firewall-restore.sh,
    #    /etc/hotplug.d/firewall/90-routelink-block, and /etc/uci-defaults/90-routelink-firewall
    #    (plus fw3 include registration in postinst for standalone .ipk installs).
    mk = makefile.read_text(encoding="utf-8")
    if "90-routelink-firewall" not in mk:
        # Handle both fresh upstream Makefile and previously patched Makefile
        if "$(1)/etc/hotplug.d/firewall" not in mk:
            target_dir_line = "$(1)/etc/hotplug.d/ntp $(1)/usr/share/rpcd/acl.d"
            if target_dir_line not in mk:
                raise RuntimeError(f"Unexpected install dir line in {makefile}")
            mk = mk.replace(
                target_dir_line,
                "$(1)/etc/hotplug.d/ntp $(1)/etc/hotplug.d/firewall $(1)/etc/uci-defaults $(1)/usr/share/routelink $(1)/usr/share/rpcd/acl.d",
                1,
            )
        elif "$(1)/usr/share/routelink" not in mk:
            mk = mk.replace(
                "$(1)/etc/hotplug.d/firewall $(1)/usr/share/rpcd/acl.d",
                "$(1)/etc/hotplug.d/firewall $(1)/etc/uci-defaults $(1)/usr/share/routelink $(1)/usr/share/rpcd/acl.d",
                1,
            )

        install_lines = (
            "\t$(INSTALL_BIN) ./files/routelink.firewall $(1)/usr/share/routelink/firewall-restore.sh\n"
            "\t$(INSTALL_BIN) ./files/routelink.firewall $(1)/etc/hotplug.d/firewall/90-routelink-block\n"
            "\t$(INSTALL_BIN) ./files/routelink.firewall-uci-defaults $(1)/etc/uci-defaults/90-routelink-firewall\n"
        )
        old_fw_line = "\t$(INSTALL_BIN) ./files/routelink.firewall $(1)/etc/hotplug.d/firewall/90-routelink-block\n"
        ntp_install = "\t$(INSTALL_DATA) ./files/routelink.ntp $(1)/etc/hotplug.d/ntp/90-routelink\n"
        if old_fw_line in mk:
            mk = mk.replace(old_fw_line, install_lines, 1)
        elif ntp_install in mk:
            mk = mk.replace(ntp_install, ntp_install + install_lines, 1)
        else:
            raise RuntimeError(f"Unexpected ntp install line in {makefile}")

        postinst_old = '[ -n "$${IPKG_INSTROOT}" ] || /etc/init.d/rpcd reload\n'
        postinst_new = (
            '[ -n "$${IPKG_INSTROOT}" ] || /etc/init.d/rpcd reload\n'
            '[ -n "$${IPKG_INSTROOT}" ] || [ ! -x /etc/uci-defaults/90-routelink-firewall ] || sh /etc/uci-defaults/90-routelink-firewall\n'
        )
        if postinst_old in mk and "90-routelink-firewall" not in mk.split("postinst", 1)[-1]:
            mk = mk.replace(postinst_old, postinst_new, 1)

        makefile.write_text(mk, encoding="utf-8")

    print(f"Patched routelinkd block.c and firewall3/hotplug hooks in {routelinkd_dir}.")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <routelinkd_dir>", file=sys.stderr)
        return 2
    patch_routelinkd(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
