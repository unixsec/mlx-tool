"""spec §4.1 / D14：按硬件端口类型与 VM 网桥放行。期望集合对着冻结夹具写成字面量。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402
from floor import run_floor  # noqa: E402

NET = Path(__file__).resolve().parents[1] / "fixtures" / "net"

PRE = """
import json
from gateway import peers
HW = open("@NET@/networksetup.txt").read()
IF = open("@NET@/ifconfig.txt").read()
def nets_of(hw=HW, ifc=IF, cidrs=""):
    return peers.compute_nets(peers.parse_hardware_ports(hw), peers.parse_ifconfig(ifc), cidrs)
def inject(ifc, iface, line):
    out = []
    for ln in ifc.splitlines(True):
        out.append(ln)
        if ln.startswith(iface + ": flags="):
            out.append("\\t" + line + "\\n")
    return "".join(out)
"""

WANT_ALLOWED = sorted([
    ["127.0.0.0/8", None], ["::1/128", None],
    ["172.20.10.0/28", None], ["fe80::/64", "en0"], ["2001:db8:0:1::/64", None],
    ["10.211.55.0/24", None], ["fe80::/64", "bridge100"], ["fda8:69e7:f901:187b::/64", None],
], key=str)
WANT_EXCLUDED = sorted(["anpi0", "anpi1", "anpi2", "ap1", "awdl0", "bridge0", "en1", "en2", "en6",
                        "gif0", "llw0", "stf0", "utun0", "utun1", "utun2", "utun3", "utun4",
                        "utun5", "utun6", "vmenet0"])


def _run(payload, sys_py, body):
    return run_floor(payload, sys_py, PRE + body, with_gateway=True, NET=NET)


def test_allowed_and_excluded_sets_from_real_capture(payload, sys_py):
    got = _run(payload, sys_py, """
n = nets_of()
print(json.dumps({"allowed": sorted([[a["net"], a["scope"]] for a in n["allowed"]], key=str),
                  "addrs": sorted({a["iface"] + " " + a["addr"] for a in n["allowed"] if a["addr"]}),
                  "kinds": sorted({a["iface"]: a["kind"] for a in n["allowed"]}.items()),
                  "excluded": {e["iface"]: e["reason"] for e in n["excluded"]}}))
""")
    Cardinality.check("放行网段", got["allowed"], WANT_ALLOWED, rel="equal")
    assert got["kinds"] == [["bridge100", "vm"], ["en0", "wifi"], ["lo0", "loopback"]]
    assert got["addrs"] == sorted(["lo0 127.0.0.1", "lo0 ::1", "en0 172.20.10.3", "en0 fe80::103",
                                   "en0 2001:db8:0:1::104", "bridge100 10.211.55.2",
                                   "bridge100 fe80::10b", "bridge100 fda8:69e7:f901:187b::10c"])
    Cardinality.check("排除接口", sorted(got["excluded"]), WANT_EXCLUDED, rel="equal")
    assert got["excluded"]["bridge0"] == "Thunderbolt Bridge"
    assert got["excluded"]["en1"] == "Thunderbolt 1"
    assert "VPN" in got["excluded"]["utun4"]


def test_injected_addresses(payload, sys_py):
    """雷雳网桥即使有地址也排除（D14 否决前缀匹配的理由）；以太网口一旦有地址即放行。"""
    got = _run(payload, sys_py, """
tb = nets_of(ifc=inject(IF, "bridge0", "inet 169.254.10.1 netmask 0xffff0000 broadcast 169.254.255.255"))
eth = nets_of(ifc=inject(IF, "en3", "inet 192.168.50.10 netmask 0xffffff00 broadcast 192.168.50.255"))
cfg = nets_of(cidrs="192.168.2.0/24, fd00:1::/64")
print(json.dumps({
    "tb_allowed": [a["net"] for a in tb["allowed"] if a["iface"] == "bridge0"],
    "tb_excluded": any(e["iface"] == "bridge0" for e in tb["excluded"]),
    "eth": [[a["net"], a["kind"]] for a in eth["allowed"] if a["iface"] == "en3"],
    "cfg": [[a["net"], a["kind"]] for a in cfg["allowed"] if a["kind"] == "config"],
    "empty": nets_of(hw="", ifc="")["allowed"]}))
""")
    assert got["tb_allowed"] == [] and got["tb_excluded"] is True
    assert got["eth"] == [["192.168.50.0/24", "ethernet"]]
    assert got["cfg"] == [["192.168.2.0/24", "config"], ["fd00:1::/64", "config"]]
    assert [a["net"] for a in got["empty"]] == ["127.0.0.0/8", "::1/128"], \
        "解析不出任何接口时只剩回环"


def test_classify_matrix(payload, sys_py):
    got = _run(payload, sys_py, """
n = nets_of()
n_cfg = nets_of(cidrs="192.168.2.0/24")
cases = ["127.0.0.1", "::1", "::ffff:127.0.0.1", "172.20.10.5", "::ffff:172.20.10.5",
         "172.20.10.20", "10.211.55.3", "fda8:69e7:f901:187b::5", "2001:db8:0:1::9",
         "fe80::5%en0", "fe80::5%bridge100", "fe80::5%utun4", "fe80::5",
         "10.14.0.7", "10.200.0.1", "100.64.1.1", "192.168.2.50", "8.8.8.8", "garbage", ""]
print(json.dumps({"base": {c: peers.classify_peer(c, n) for c in cases},
                  "cfg": peers.classify_peer("192.168.2.50", n_cfg)}))
""")
    L, R = "LOOPBACK", "REMOTE"
    assert got["base"] == {
        "127.0.0.1": L, "::1": L, "::ffff:127.0.0.1": L,
        "172.20.10.5": R, "::ffff:172.20.10.5": R, "172.20.10.20": None,
        "10.211.55.3": R, "fda8:69e7:f901:187b::5": R, "2001:db8:0:1::9": R,
        "fe80::5%en0": R, "fe80::5%bridge100": R, "fe80::5%utun4": None, "fe80::5": None,
        "10.14.0.7": None, "10.200.0.1": None, "100.64.1.1": None, "192.168.2.50": None,
        "8.8.8.8": None, "garbage": None, "": None,
    }
    assert got["cfg"] == R
