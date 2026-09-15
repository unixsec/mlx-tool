# 网络夹具

`/usr/sbin/networksetup -listallhardwareports` 与 `/sbin/ifconfig` 的真实输出，2026-09-15 抓取（当时 `en0` 连手机热点），**冻结**：
`SHA256SUMS` 由 `tests/unit/test_net_fixtures.py` 核对，改动夹具必须同时改期望集合并说明理由。

脱敏规则（两个文件共用一张映射表）：MAC → `02:00:5e:00:00:NN`；公网 IPv6 前缀 → `2001:db8:0:N::/64`；
全部非回环 IPv6 接口标识 → 序号；IPv4、ULA 与链路本地前缀保留。未脱敏的原始抓取不进仓库。

形态覆盖：Wi-Fi（`en0`，IPv4 /28 + 链路本地 + 公网 IPv6）、三个无地址以太网（`en3`–`en5`）、
雷雳网桥 `bridge0`（UP、无地址、成员为雷雳端口）、Parallels 共享网络网桥 `bridge100`（成员 `vmenet0`）、
VPN `utun4`（点对点 IPv4）、AirDrop / 低延迟 Wi-Fi / Apple 内部接口。
