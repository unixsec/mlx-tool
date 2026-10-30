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
def on_change(s):
    # 新契约：on_change 返回是否送达；这里总能送达，好保留这条用例原先
    # 对 snaps/calls 计数的断言 —— 待送达重试路径另有专门用例覆盖。
    snaps.append(s)
    return True
v = peers.NetView(loader, allow_cidrs="198.51.100.0/24", clock=lambda: t[0],
                  wall=lambda: 1000.0 + t[0], on_change=on_change)
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
# **两种判定分开两个超时**（账本 A1）：同一个 0.5 s 既要判「rc≠0」又要判「超时」，
# 机器一忙，`exit 1` 的桩也会跟着超时，两条判定的区分就此消失（本会话观测三次，
# 实得 rc 桩也报 TimeoutExpired）。改成：rc 桩给**宽松**超时（只验 rc≠0 这条路径，
# 它立刻返回，宽超时不会让用例变慢），slow 桩用远大于其超时的 sleep 配**紧**超时。
errs = {}
for name, body, timeout in (("rc", "exit 1", 30.0), ("slow", "sleep 30", 0.5)):
    bins = {"networksetup": script(name, body), "ifconfig": script("if2", "echo IF")}
    try:
        peers.command_loader(bins, timeout_sec=timeout)()
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


PROG_PENDING = r"""
import json
from gateway import peers
HW = open("@NET@/networksetup.txt").read()
IF = open("@NET@/ifconfig.txt").read()
t = [0.0]
loader_calls = []
def loader():
    loader_calls.append(t[0])
    return HW, IF
delivered_calls = []
def on_change(snap):
    # 第一次没送达（比如运行记录还没写出来），此后都送达
    delivered_calls.append(snap["nets_source"])
    return len(delivered_calls) > 1
v = peers.NetView(loader, clock=lambda: t[0], wall=lambda: t[0], on_change=on_change)
r = {}
v.refresh()
r["after_refresh"] = [len(delivered_calls), len(loader_calls)]
t[0] = 1.0
r["tick1"] = [v.tick(), len(delivered_calls), len(loader_calls)]
t[0] = 2.0
r["tick2"] = [v.tick(), len(delivered_calls), len(loader_calls)]
print(json.dumps(r))
"""


def test_netview_pending_report_retries_on_tick_until_delivered(payload, sys_py):
    """M13 修复：送达失败不是「记完就算了」——`tick()` 在未到重算周期时
    也要补发上一份快照（不跑 loader），直到送达成功；一旦送达，后续
    未到周期的 tick() 不应再调 on_change。"""
    got = run_floor(payload, sys_py, PROG_PENDING, with_gateway=True, timeout=30, NET=NET)
    assert got["after_refresh"] == [1, 1], "首次 refresh：on_change 调用一次，loader 调用一次"
    assert got["tick1"] == [None, 2, 1], \
        "未到 30 s 周期但有待送达的报告：tick() 应重发 on_change，不跑 loader"
    assert got["tick2"] == [None, 2, 1], "送达成功后，未到周期的 tick() 不应再调 on_change"


PROG_RACE = r"""
import json, threading
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
IF2 = inject(IF, "en3", "inet 192.168.50.10 netmask 0xffffff00 broadcast 192.168.50.255")

ifc_state = {"text": IF}
def loader():
    return HW, ifc_state["text"]

def has_probe_net(snap):
    return "192.168.50.0/24" in [a["net"] for a in snap["allowed_nets"]]

delivered = []
evt = threading.Event()
bg = {}
count = [0]

def on_change(snap):
    count[0] += 1
    n = count[0]
    tag = "S2" if has_probe_net(snap) else "S1"
    if n == 1:
        # 第一次 refresh：模拟「运行记录还没写出来」——没送达，留一份待送达
        delivered.append(tag)
        return False
    if n == 2:
        # tick() 的补发：**在这次调用内部**并发起一次新的 refresh()，制造
        # 「补发旧快照」跟「刚算出的新快照自己送达」之间的竞争。
        ifc_state["text"] = IF2
        t2 = threading.Thread(target=v.refresh)
        bg["t2"] = t2
        t2.start()
        # 有 _deliver_lock 时，t2 送达前必须等这次调用返回、释放锁 —— 超时是
        # **预期**（不是这条用例的失败）；没有那把锁的旧代码里，t2 会在超时
        # 前就送达并置位这个事件。
        # **这一处的超时是判据本身，不是 A2 说的那种**：有 `_deliver_lock` 时
        # t2 在本次调用返回前送不出去，所以**超时到期才是正确行为**；
        # 没有那把锁的旧代码里 t2 会抢在到期前置位这个事件，顺序随之翻转。
        # 去掉它会让**修好的**代码在这里永久阻塞，故保留。
        evt.wait(timeout=2.0)
        delivered.append(tag)
        return True
    # n == 3：并发那次 refresh() 自己的送达
    delivered.append(tag)
    evt.set()
    return True

v = peers.NetView(loader, clock=lambda: 0.0, wall=lambda: 0.0, on_change=on_change)
v.refresh()
v.tick()
t2 = bg.get("t2")
if t2 is not None:
    # **不带超时**（账本 A2）：下面要断言「这个线程跑完了」，而带超时的 join
    # 在机器慢时与真死锁不可区分 —— 截止交给外层 run_floor(timeout=30)，
    # 超时即挂起并留下证据，而不是变成一条关于并发语义的假结论。
    t2.join()
print(json.dumps({"delivered": delivered, "t2_alive": bool(t2 and t2.is_alive())}))
"""


def test_netview_concurrent_pending_resend_never_delivers_stale_after_fresh(payload, sys_py):
    """re-review 修复：`tick()` 补发的是「读到那一刻」的快照；如果没有锁把
    「重读最新 + 调 on_change」串成一步，一次并发 `refresh()`（比如
    `classify()` 触发的）可能先把更新的快照送达成功，而补发那次紧跟着又把
    旧快照送达一次，用旧数据覆盖刚合并进去的新记录。用一个在 `on_change`
    内部起并发线程、经 `threading.Event` 握手的钩子逼出这个交织顺序：
    断言**最后一次送达的快照必须是最新那份**，不能是新的送达之后又追一次
    旧的。"""
    got = run_floor(payload, sys_py, PROG_RACE, with_gateway=True, timeout=30, NET=NET)
    assert not got["t2_alive"], "并发的 refresh() 线程应已经跑完"
    assert "S2" in got["delivered"], got["delivered"]
    assert got["delivered"][-1] == "S2", (
        f"最后一次送达必须是最新快照，不能是旧快照追在新快照后面：{got['delivered']}"
    )


PROG_CLASSIFY_RACE = r"""
import json, threading
from gateway import peers
HW = open("@NET@/networksetup.txt").read()
IF = open("@NET@/ifconfig.txt").read()
calls = []
def loader():
    calls.append(threading.current_thread().name)
    return HW, IF
v = peers.NetView(loader, clock=lambda: 0.0)
a_paused, b_checked = threading.Event(), threading.Event()
class Probe:
    # 包住真锁、计每个线程的放锁次数。classify() 对未知来源的第 2 次放锁就是
    # 「是否到期」那次：A 放完第 2 次后停住，等 B 也放完它的第 2 次再放行 ——
    # 恰好插进「判到期」与「占住 _last_try」之间，逼出两个线程都判到期的交织
    def __init__(self, inner):
        self._inner, self._n = inner, {}
    def __enter__(self):
        return self._inner.__enter__()
    def __exit__(self, *exc):
        out = self._inner.__exit__(*exc)
        name = threading.current_thread().name
        self._n[name] = self._n.get(name, 0) + 1
        if self._n[name] == 2 and name == "A":
            a_paused.set()
            # 不带超时（账本 A2）：B 必定会置位它；置不了就是真挂起，
            # 该让外层 run_floor(timeout=30) 判死并留证据
            b_checked.wait()
        elif self._n[name] == 2 and name == "B":
            b_checked.set()
        return out
v._lock = Probe(v._lock)
res = {}
def run(name):
    res[name] = v.classify("10.14.0.7")
ta = threading.Thread(target=run, args=("A",), name="A")
ta.start()
# 三处握手都不带超时（账本 A2）—— 见上面 b_checked.wait() 的理由
a_paused.wait()
tb = threading.Thread(target=run, args=("B",), name="B")
tb.start()
tb.join()
ta.join()
print(json.dumps({"loader_calls": len(calls), "res": res,
                  "b_checked": b_checked.is_set(), "alive": ta.is_alive() or tb.is_alive()}))
"""


def test_netview_concurrent_unknown_peers_start_only_one_recompute(payload, sys_py):
    """评审发现：`classify()` 的「是否到期」与 `_last_try` 更新曾分两次持锁，
    多个请求线程上同时来的未知来源会各自起一次重算（每次最长数秒）。
    断言：并发的两个未知来源只触发**一次** loader，没抢到的那个直接判不放行。"""
    got = run_floor(payload, sys_py, PROG_CLASSIFY_RACE, with_gateway=True, timeout=30, NET=NET)
    assert not got["alive"] and got["b_checked"], got
    assert got["res"] == {"A": None, "B": None}, got
    assert got["loader_calls"] == 1, f"并发未知来源各自起了重算：{got}"
