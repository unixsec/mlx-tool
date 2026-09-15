"""spec §4.1 重算：周期 30 s；未知来源限频（≥ 2 s）重算后再判；失败保留上一份。时钟注入，不 sleep。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

NET = Path(__file__).resolve().parents[1] / "fixtures" / "net"

PROG = r"""
import json, os, stat, tempfile
from gateway import peers
HW = open("@NET@/networksetup.txt").read()
IF = open("@NET@/ifconfig.txt").read()
def inject(ifc, iface, line):
    out = []
    for ln in ifc.splitlines(True):
        out.append(ln)
        if ln.startswith(iface + ": flags="):
            out.append("\t" + line + "\n")
    return "".join(out)
t, calls, snaps = [0.0], [], []
state = {"ifc": IF, "fail": False}
def loader():
    calls.append(t[0])
    if state["fail"]:
        raise RuntimeError("networksetup rc=1")
    return HW, state["ifc"]
v = peers.NetView(loader, allow_cidrs="198.51.100.0/24", clock=lambda: t[0],
                  wall=lambda: 1000.0 + t[0], on_change=snaps.append)
nets = lambda s: [a["net"] for a in s["allowed_nets"]]
r = {"before": [v.snapshot()["nets_source"], nets(v.snapshot())]}
v.tick()
r["first"] = [v.snapshot()["nets_source"], v.snapshot()["nets_computed_at"], len(calls)]
state["ifc"] = inject(IF, "en3", "inet 192.168.50.10 netmask 0xffffff00 broadcast 192.168.50.255")
t[0] = 5.0
r["new_peer"] = [v.classify("192.168.50.7"), len(calls)]
t[0] = 6.0
r["second_unknown"] = [v.classify("10.14.0.7"), len(calls)]
t[0] = 6.5
r["known_peer_no_refresh"] = [v.classify("172.20.10.5"), len(calls)]
t[0] = 20.0
r["tick_early"] = [v.tick() is None, len(calls)]
t[0] = 36.0
state["fail"] = True
v.tick()
s = v.snapshot()
r["failed"] = [s["nets_source"], "192.168.50.0/24" in nets(s), s["nets_error"], len(calls)]
r["snaps"] = len(snaps)
def boom():
    raise RuntimeError("ifconfig 不可用")
v2 = peers.NetView(boom, allow_cidrs="198.51.100.0/24", clock=lambda: 0.0)
v2.tick()
r["never_ok"] = [v2.snapshot()["nets_source"], nets(v2.snapshot())]
d = tempfile.mkdtemp()
def script(name, body):
    p = os.path.join(d, name)
    with open(p, "w") as fh:
        fh.write("#!/bin/sh\n" + body + "\n")
    os.chmod(p, 0o755)
    return p
ok = peers.command_loader({"networksetup": script("ns", "echo HW"), "ifconfig": script("if", "echo IF")})()
errs = {}
for name, body in (("rc", "exit 1"), ("slow", "sleep 5")):
    bins = {"networksetup": script(name, body), "ifconfig": script("if2", "echo IF")}
    try:
        peers.command_loader(bins, timeout_sec=0.5)()
        errs[name] = "no error"
    except Exception as exc:
        errs[name] = type(exc).__name__
r["loader"] = [ok[0].strip(), ok[1].strip(), errs]
print(json.dumps(r))
"""


def test_netview_recompute_rules(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=30, NET=NET)
    assert got["before"] == ["fallback", ["127.0.0.0/8", "::1/128", "198.51.100.0/24"]]
    assert got["first"] == ["parsed", 1000.0, 1]
    assert got["new_peer"] == ["REMOTE", 2], "新接入的网段应在限频重算后放行"
    assert got["second_unknown"] == [None, 2], "2 s 内第二个未知来源不得再触发重算"
    assert got["known_peer_no_refresh"] == ["REMOTE", 2], "已放行来源不触发重算"
    assert got["tick_early"] == [True, 2]
    assert got["failed"] == ["fallback", True, "networksetup rc=1", 3], "解析失败应保留上一份"
    assert got["snaps"] == 3
    assert got["never_ok"] == ["fallback", ["127.0.0.0/8", "::1/128", "198.51.100.0/24"]]
    assert got["loader"] == ["HW", "IF", {"rc": "RuntimeError", "slow": "TimeoutExpired"}]
