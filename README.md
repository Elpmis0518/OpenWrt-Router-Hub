# OpenWrt-Router-Hub

面向 **Cudy TR3000 v1 (256MB)**、**Xiaomi AX3000T** 及 **RouteLink** 手机端/路由端插件的统一自动化每日构建仓库。

## 每日计划与产物

每天 **北京时间 04:00**（`cron: '0 20 * * *'` UTC）自动无条件同步三个上游仓库、应用模块化补丁，并完整编译发布以下产物（每个任务均支持在 Actions 页面独立 `workflow_dispatch` 手动触发）：

| 目标 | 底层源码 | Release Tag 前缀 | 保留策略 | 产物内容 |
| --- | --- | --- | --- | --- |
| **TR3000 (256MB)** | `VIKINGYFY/immortalwrt` (`owrt`) | `tr3000-*` | 独立保留最近 3 版 | 固件镜像 + 独立 `*routelink*.apk` + `.config` + `SHA256SUMS` |
| **AX3000T** | `coolsnowwolf/lede` (`master`) | `ax3000t-*` | 独立保留最近 3 版 | 固件镜像 + 独立 `*routelink*.ipk` + `.config` + `SHA256SUMS` |
| **RouteLink App** | `tsix2019/RouteLink` (`main`) | `routelink-*` | 独立保留最近 3 版 | Android 固定签名 `.apk` + iOS 未签名 `.ipa` + `SHA256SUMS` |

## 目录结构

```text
.
├── .github/workflows/
│   ├── daily-sync-and-build.yml   # 每日 04:00 CST 全量同步与总调度
│   ├── build-tr3000.yml           # TR3000 单机型编译 + 固定私钥签名 RouteLink apk 软件源
│   ├── build-ax3000t.yml          # AX3000T 编译 + 固定私钥 usign 签名 RouteLink opkg 软件源
│   ├── build-routelink.yml        # RouteLink 单测 + Android 签名 APK + iOS 未签名 IPA + 统一发布
│   └── publish-pages.yml          # 基于最新 Releases 原子构建并部署 GitHub Pages（固件目录 + RouteLink 软件源）
├── shared/
│   ├── files/                     # 两台路由器共用 rootfs 覆盖（OpenClash keep.d、auto-whu.sh、权限兜底与 *.whu.edu.cn DNS 白名单）
│   └── scripts/                   # 上游同步、慢速源清理、OpenClash 内核预置、Hub Overlay、Pages 站点生成、隔离清理
├── devices/
│   ├── tr3000-256mb/              # TR3000 上游同步文件、声明式 packages.conf 与单机型过滤脚本
│   └── ax3000t/                   # AX3000T 上游同步文件、声明式 packages.conf、幂等配置写入与 mac80211 自适应补丁
└── routelink/
    ├── feed/keys/                 # 自建 RouteLink 软件源公钥（apk PEM + opkg usign 公钥；私钥仅存 Actions Secrets）
    ├── patches/                   # 模块化幂等补丁（routelinkd block.c + App 兼容 + 自建更新与软件源适配）
    └── upstream/                  # 同步并应用补丁后的 RouteLink 完整源码（App + openwrt/ 插件）
```

## 主要定制说明

- **两台路由器共用**：
  - OpenClash (`vernesong/OpenClash` `dev`) + 预置最新 `master` `linux-arm64` Meta 内核（`/etc/openclash/core/clash_meta`），升级保留配置目录且排除 `core/`。
  - 内置 `/usr/sbin/auto-whu.sh`（适配 BusyBox `sed` 替代 `grep -oP`，固件自带 `777` 权限 + `99-auto-whu-perm` 兜底），并在 `dnsmasq`（`rebind_domain`）、OpenClash Fake-IP 过滤及 `adblock` / `adblock-fast` 中默认放行所有 `*.whu.edu.cn`。
  - 独立设备级编译缓存（`ccache`、`staging_dir/host*`、`staging_dir/tool*`）与滚动清理，移除慢速国内镜像源，下载与编译失败自动回退。
- **TR3000 v1 (256MB)**：基于 `MEDIATEK-WIFI-YES` 单机型构建，沿用 `luci-app-wolultra` 与默认网络/主题设置，新增 OpenClash、RouteLink、WireGuard、OpenVPN (`luci-proto-openvpn`)、DDNS、SQM、Adblock 及对应中文翻译包；保留上游 LLVM/Rust/i2c-tools 编译优化与构建报告收集。
- **AX3000T**：保留上游 `UA3F`、`ddns-go`、`argon` 及默认配置，启用 `CONFIG_CCACHE=y`，通过 `packages.conf` + Python 脚本幂等写入 7 个内核模块与新增插件及对应 `-zh-cn` 翻译包（不启用全局 `CONFIG_LUCI_LANG_zh_Hans`），并内置 `mac80211` `wiphy_namespace` 自适应内核兼容补丁。
- **RouteLink 全栈适配**：
  - **路由端 (`routelinkd`)**：优先 `nft`，无 `nft` 时（AX3000T `firewall3`）自动回退 `iptables`/`ip6tables` 独立链 `ROUTELINK_BLOCK`（防重复跳转、`ip6tables` 缺失容错、`/etc/hotplug.d/firewall/90-routelink-block` 自动恢复）。`luci-app-routelink` 与中文包保持上游原版不变。
  - **手机端 (Android & iOS)**：沿用原包名与 Bundle ID；支持 OpenVPN 双后端（`procd` + `netifd`）、SQM 双机制（`file exec` + `ubus rc init`）、WoL 兼容 `luci-app-wolultra`、`SNAPSHOT` 软件包、`autocore` 温度解析与空 SSH 公钥容错；Android 强制使用仓库 Secrets 中的固定 Release Keystore 签名（缺失即报错，绝不降级为 debug 签名）。

## 自建更新源与 RouteLink 软件源

GitHub Pages 托管地址：<https://elpmis0518.github.io/OpenWrt-Router-Hub/>（通过 GitHub Actions 直接部署，不产生额外分支）：

- **路由器固件在线升级**：`firmware/manifest.json` 收录 TR3000（`cudy,tr3000-v1-256mb`）与 AX3000T（`xiaomi,mi-router-ax3000t`）最新 `sysupgrade` 镜像与 SHA-256，App 内「更多 → 固件升级」自动识别并支持校验下载与刷写（非本仓库机型仍走官方源）。
- **RouteLink 插件一键更新 & 签名软件源**：`agent/manifest.json` 指向本仓库最新 `routelinkd` / `luci-app-routelink` / `luci-i18n-routelink-zh-cn`，固件已预置固定签名公钥与自定义软件源：
  - **TR3000 (`apk`)**：`/etc/apk/keys/routelink.pem` + `https://elpmis0518.github.io/OpenWrt-Router-Hub/agent/SNAPSHOT/aarch64_cortex-a53/packages.adb`
  - **AX3000T (`opkg`)**：`/etc/opkg/keys/d39736bb5c3eb4ef` + `src/gz routelink https://elpmis0518.github.io/OpenWrt-Router-Hub/agent/24.10/aarch64_cortex-a53`
- **手机端 App 自更新**：指向 `Elpmis0518/OpenWrt-Router-Hub` Releases（`routelink-*`），Android 支持应用内检测、SHA-256 校验并调用系统安装器原位覆盖升级，iOS 自动跳转最新 Release 下载未签名 `.ipa`。
- **仓库 Secrets**：`ANDROID_KEYSTORE_BASE64`、`ANDROID_KEYSTORE_PASSWORD`、`ANDROID_KEY_ALIAS`、`OPENWRT_APK_KEY`、`OPENWRT_USIGN_KEY`。
