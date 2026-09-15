"""T2 · 放行网段与来源分类（spec §4.1、D14）。

**解析与判定是纯函数**：输入是命令输出文本。跑命令只在 `command_loader` 一处；
周期与限频重算在 `NetView`，时钟与加载器都由调用方注入 —— 每条规则都能拿冻结的
真实抓取对拍，测试不 sleep。

放行：① 回环；② 硬件端口名为 `Wi-Fi` 或以 `Ethernet` 开头的设备的地址段；
③ 成员含 `vmenet` / `vnic` 的网桥的地址段（Parallels / VMware 共享网络）；④ `api.allow_cidrs`。
**其余一律排除**：雷雳端口与雷雳网桥（按前缀匹配 `en*` / `bridge*` 会把它们放进来）、
VPN 隧道（默认路由常在这里，按默认路由判断会放行 VPN 对端）、AirDrop 等。
"""
import contextlib
import ipaddress
import re
import subprocess
import threading
import time

LOOPBACK_NETS = ("127.0.0.0/8", "::1/128")
VM_MEMBER_PREFIXES = ("vmenet", "vnic")

# 排除原因（没有硬件端口名的接口按名字前缀说明）。只用于展示，不参与判定。
_REASONS = (
    ("utun", "隧道接口（VPN 等）"), ("ipsec", "IPsec 隧道"), ("ppp", "拨号 / 点对点"),
    ("awdl", "AirDrop 无线直连"), ("llw", "低延迟 Wi-Fi"), ("anpi", "Apple 内部网络"),
    ("ap", "个人热点接口"), ("gif", "通用隧道"), ("stf", "6to4 隧道"),
    ("vmenet", "虚拟机网卡（由其所在网桥判定）"), ("vnic", "虚拟机网卡（由其所在网桥判定）"),
    ("bridge", "网桥（无虚拟机成员）"),
)

_IFACE = re.compile(r"^([A-Za-z0-9]+): flags=\w+<([^>]*)>")


def parse_hardware_ports(text):
    # type: (str) -> dict
    """`networksetup -listallhardwareports` → `{设备名: 硬件端口名}`。"""
    out, port = {}, None
    for line in (text or "").splitlines():
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and port is not None:
            out[line.split(":", 1)[1].strip()] = port
            port = None
    return out


def parse_ifconfig(text):
    # type: (str) -> dict
    """`ifconfig` → `{接口: {"up", "inet": [(地址, 前缀长)], "inet6": [(地址, 前缀长, scope)], "members"}}`。

    `inet` 的 netmask 是十六进制（`0xfffffff0`）；点对点接口写成 `inet A --> B netmask M`。
    `inet6` 的链路本地地址带 `%接口` 后缀。解析不了的行跳过，不猜。
    """
    out, cur = {}, None
    for line in (text or "").splitlines():
        m = _IFACE.match(line)
        if m:
            cur = {"up": "UP" in m.group(2).split(","), "inet": [], "inet6": [], "members": []}
            out[m.group(1)] = cur
            continue
        if cur is None:
            continue
        tok = line.split()
        if not tok:
            continue
        if tok[0] == "inet" and "netmask" in tok and len(tok) > 1:
            try:
                plen = bin(int(tok[tok.index("netmask") + 1], 16)).count("1")
                ipaddress.IPv4Address(tok[1])
            except (ValueError, IndexError):
                continue
            cur["inet"].append((tok[1], plen))
        elif tok[0] == "inet6" and "prefixlen" in tok and len(tok) > 1:
            addr, _, scope = tok[1].partition("%")
            try:
                ipaddress.IPv6Address(addr)
                plen = int(tok[tok.index("prefixlen") + 1])
            except (ValueError, IndexError):
                continue
            cur["inet6"].append((addr, plen, scope or None))
        elif tok[0] == "member:" and len(tok) > 1:
            cur["members"].append(tok[1])
    return out


def _reason(name, port):
    # type: (str, object) -> str
    if port:
        return port
    for prefix, text in _REASONS:
        if name.startswith(prefix):
            return text
    return "非 Wi-Fi / 以太网 / 虚拟机网桥"


def compute_nets(hw, ifs, allow_cidrs=""):
    # type: (dict, dict, str) -> dict
    """放行网段与排除接口。**同一网段同一 scope 只列一次**（同一 /64 常有多个地址）。"""
    allowed = [{"net": n, "iface": "lo0", "kind": "loopback", "scope": None, "addr": a}
               for n, a in zip(LOOPBACK_NETS, ("127.0.0.1", "::1"))]
    excluded = []
    for name in sorted(ifs):
        if name.startswith("lo"):
            continue                           # 回环网段已固定列出
        info, port = ifs[name], hw.get(name)
        if port == "Wi-Fi":
            kind = "wifi"
        elif port and port.startswith("Ethernet"):
            kind = "ethernet"
        elif name.startswith("bridge") and any(
                m.startswith(VM_MEMBER_PREFIXES) for m in info["members"]):
            kind = "vm"
        else:
            excluded.append({"iface": name, "reason": _reason(name, port)})
            continue
        seen = set()
        for addr, plen in info["inet"]:
            net = str(ipaddress.ip_network(f"{addr}/{plen}", strict=False))
            if (net, None) not in seen:
                seen.add((net, None))
                allowed.append({"net": net, "iface": name, "kind": kind, "scope": None,
                                "addr": addr})
        for addr, plen, _scope in info["inet6"]:
            ip = ipaddress.IPv6Address(addr)
            net = str(ipaddress.ip_network(f"{addr}/{plen}", strict=False))
            # 链路本地网段只在**本接口**上有意义：fe80::/64 在每个接口上都是同一个前缀
            scope = name if ip.is_link_local else None
            if (net, scope) not in seen:
                seen.add((net, scope))
                allowed.append({"net": net, "iface": name, "kind": kind, "scope": scope,
                                "addr": addr})
    for cidr_raw in (allow_cidrs or "").split(","):
        cidr = cidr_raw.strip()
        if cidr:
            allowed.append({"net": str(ipaddress.ip_network(cidr, strict=False)),
                            "iface": None, "kind": "config", "scope": None, "addr": None})
    return {"allowed": allowed, "excluded": excluded}


def normalize_peer(addr):
    # type: (str) -> object
    """socket 对端地址 → ip 对象。双栈监听时 IPv4 对端显示为 `::ffff:a.b.c.d`，还原为 IPv4。"""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def classify_peer(addr, nets):
    # type: (str, dict) -> object
    """→ `"LOOPBACK"` / `"REMOTE"` / `None`（不在放行集合 ⇒ 调用方回 403）。

    **只看 socket 对端地址**，不读任何请求头（`X-Forwarded-For` 可伪造）。
    """
    ip = normalize_peer(addr)
    if ip is None:
        return None
    if ip.is_loopback:
        return "LOOPBACK"
    scope = getattr(ip, "scope_id", None)
    for n in nets.get("allowed") or ():
        if n["kind"] == "loopback":
            continue
        net = ipaddress.ip_network(n["net"])
        if net.version != ip.version or ip not in net:
            continue
        if n.get("scope") and n["scope"] != scope:
            continue                           # fe80::/64%en0 不放行 fe80::x%utun4
        return "REMOTE"
    return None


def command_loader(bins, timeout_sec=3.0):
    # type: (dict, float) -> object
    """→ 无参函数，返回 `(networksetup 输出, ifconfig 输出)`；任一命令失败或超时即抛。

    命令路径来自 `ctx.bins`（绝对路径，不经 PATH）。超时 3 s（spec §4.1）：
    重算发生在请求处理路径上（未知来源限频重算），不能让一次卡住的命令拖住请求。
    """
    def load():
        texts = []
        for name, argv in (("networksetup", [bins["networksetup"], "-listallhardwareports"]),
                           ("ifconfig", [bins["ifconfig"]])):
            r = subprocess.run(argv, capture_output=True, text=True,        # noqa: S603
                               timeout=timeout_sec, check=False)
            if r.returncode != 0:
                raise RuntimeError(f"{name} rc={r.returncode}")
            texts.append(r.stdout)
        return texts[0], texts[1]
    return load


class NetView:
    """放行网段的进程内视图（spec §4.1）。

    - `tick()`：距上次尝试 ≥ `period_sec`（30 s）才重算 —— 网关后台刷新线程调它；
    - `classify(addr)`：来源不在放行集合时，距上次尝试 ≥ `min_gap_sec`（2 s）先重算一次再判 ——
      刚连上 Wi-Fi 的设备不必等满 30 s，扫描器的连串请求也不会把重算打成每请求一次；
    - 加载失败：**保留上一份**并记 `nets_source=fallback`；从未成功过 ⇒ 回环 + `api.allow_cidrs`。
    """

    def __init__(self, loader, allow_cidrs="", clock=None, wall=None,
                 period_sec=30.0, min_gap_sec=2.0, on_change=None):
        # type: (object, str, object, object, float, float, object) -> None
        self._loader = loader
        self._cidrs = allow_cidrs or ""
        self._clock = clock or time.monotonic
        self._wall = wall or time.time
        self._period, self._gap = period_sec, min_gap_sec
        self._on_change = on_change
        self._lock = threading.Lock()
        self._nets = compute_nets({}, {}, self._cidrs)
        self._source, self._error = "fallback", ""
        self._computed_at = None
        self._last_try = None

    def refresh(self):
        # type: () -> dict
        with self._lock:
            self._last_try = self._clock()
        try:
            hw_text, if_text = self._loader()
            ifs = parse_ifconfig(if_text)
            if not ifs:
                raise ValueError("ifconfig 输出里解析不出任何接口")
            nets, source, err = (compute_nets(parse_hardware_ports(hw_text), ifs, self._cidrs),
                                 "parsed", "")
        except Exception as exc:                        # noqa: BLE001
            nets, source, err = None, "fallback", str(exc)[:200]
        with self._lock:
            if nets is not None:
                self._nets = nets
            self._source, self._error = source, err
            self._computed_at = self._wall()
            snap = self._snapshot_locked()
        if self._on_change is not None:
            # 上报失败不影响判定（调用方自己记日志）
            with contextlib.suppress(Exception):        # noqa: BLE001
                self._on_change(snap)
        return snap

    def tick(self):
        # type: () -> object
        with self._lock:
            due = self._last_try is None or self._clock() - self._last_try >= self._period
        return self.refresh() if due else None

    def classify(self, addr):
        # type: (str) -> object
        with self._lock:
            nets = self._nets
        verdict = classify_peer(addr, nets)
        if verdict is not None:
            return verdict
        with self._lock:
            due = self._last_try is None or self._clock() - self._last_try >= self._gap
        if not due:
            return None
        self.refresh()
        with self._lock:
            nets = self._nets
        return classify_peer(addr, nets)

    def snapshot(self):
        # type: () -> dict
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self):
        # type: () -> dict
        return {"allowed_nets": [dict(a) for a in self._nets["allowed"]],
                "excluded_ifaces": [dict(e) for e in self._nets["excluded"]],
                "nets_source": self._source, "nets_computed_at": self._computed_at,
                "nets_error": self._error}
