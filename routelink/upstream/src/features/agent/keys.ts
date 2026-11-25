/**
 * Public key of the plugin's apk packages (openwrt/feed/keys/routelink-apk.pem; a test keeps the two equal).
 * apk on OpenWrt 25.12 only installs local packages signed by a key in /etc/apk/keys.
 */
export const ROUTELINK_APK_PEM = `-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE3T5gtUvJbxyeSzvStcjLdt3BQ7JR
Yato8TcaLzwO4oexB/2F8XMmJEY9RpyWtZ6zuMJIZbd/J6Cl14H/PrZ/Aw==
-----END PUBLIC KEY-----
`;
