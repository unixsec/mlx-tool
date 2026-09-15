"""L2 · D 层编排的关键行为。全部经 3.9.6 下限解释器。"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _floor(payload, sys_py, body):
    prog = "\n".join(textwrap.dedent(p) for p in body.split("\n@@\n"))
    r = subprocess.run([sys_py, "-c", prog], capture_output=True, text=True,
                       timeout=25, check=False,
                       env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(payload / "lib")})
    assert r.returncode == 0, f"下限解释器执行失败：{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


PRE = '''
import json, os, tempfile
from mlx_server.result import Ok
from mlx_server.cleanup import CleanupStack
class Inv:
    def __init__(self, verb, target=None, args=None, dry_run=False):
        self.verb, self.target, self.args, self.dry_run = verb, target, args or {}, dry_run
d = tempfile.mkdtemp()
class C:
    root_code = d; root_data = d; root_weights = d
    venv = os.path.join(d, "venv"); gateway_port = 8080
    port_range = (8100, 8103); self_started_at = Ok("t")
ctx = C()
'''


def test_l2_acquire_refuses_before_commit(payload, sys_py):
    """`BCR-R-01`：唯一性冲突在**校验段**被拒 ⇒ `Refused` 且**零落地**。

    ruling L3：本用例传 `--type`，`model_acquire.run` 因此要读表做模态域校验 ——
    ctx 需要一个真实的 `share_root`（指到产品的 `payload/share`），否则读表就崩。
    """
    pre = PRE.replace(
        "ctx = C()",
        "C.share_root = " + repr(str(payload / "share")) + "\nctx = C()",
    )
    got = _floor(payload, sys_py, pre + "\n@@\n" + '''
        from mlx_server import store
        from mlx_server.cmd import model_acquire
        store.commit(ctx, store.MODELS, store.ModelsTx("a", {"repo": "x/y", "type": "text"}, port=8100), CleanupStack())
        before = open(os.path.join(d, "models.jsonl")).read()
        # 用 install：register 会因「权重未下载」先返回 Refused(6)，
        # 那是另一条正确路径，测不到端口唯一性这一条。
        out = model_acquire.run(ctx, CleanupStack(),
                                Inv("install", "b", {"repo": "p/q", "type": "text", "port": 8100}))
        after = open(os.path.join(d, "models.jsonl")).read()
        print(json.dumps({"rc": out.rc, "unchanged": before == after,
                          "is_refused": type(out).__name__}))
    ''')
    assert got["rc"] == 7 and got["is_refused"] == "Refused"
    assert got["unchanged"], "拒绝时动了盘 —— Refused 的前提是零资源已创建"


def test_l2_check_never_collapses_unknown_to_ok(payload, sys_py):
    """`R-OBSERVE-24`：`Unknown` 报 undeterminable 并计入补集，**不塌成 ok**。

    坏样本：塌成 ok ⇒ 健康报告说谎，用户以为一切正常。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        from mlx_server.result import Ok, Unknown
        from mlx_server.cmd import check
        checks = {n: (lambda c: Ok(True)) for n in check.ITEMS}
        checks["venv"] = lambda c: Unknown("TOOL_UNAVAILABLE")
        out = check.run(ctx, None, Inv("check"), {"checks": checks})
        rows = dict((r[0], r[1]) for r in out.payload["rows"])
        print(json.dumps({"rc": out.rc, "venv": rows["venv"],
                          "undeterminable": out.payload["undeterminable"]}))
    ''')
    assert got["venv"] == "undeterminable", "Unknown 被塌成了 ok ⇒ 健康报告说谎"
    # rc 64 = 聚合含告警（`Aggregate` 而非 `Failed`：**它携带主产物** ——
    # check 的契约是「九项各一行」，用 Failed 表达会把那九行丢掉）
    assert got["undeterminable"] == ["venv"] and got["rc"] == 64


def test_l2_reclaim_rm_declined_deletes_nothing(payload, sys_py):
    """确认被拒 ⇒ **零删除**（rc 5），记录与权重都不动。"""
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        from mlx_server import store
        from mlx_server.cmd import model_reclaim
        store.commit(ctx, store.MODELS, store.ModelsTx("a", {"repo": "x/y", "type": "text"}), CleanupStack())
        before = open(os.path.join(d, "models.jsonl")).read()
        # **带 `--weights` 才有确认**（分档：不带的那档可完全恢复，不问）
        out = model_reclaim.run(ctx, CleanupStack(), Inv("rm", "a", {"weights": True}),
                                {"confirm": lambda *a, **k: False})
        after = open(os.path.join(d, "models.jsonl")).read()
        print(json.dumps({"rc": out.rc, "unchanged": before == after}))
    ''')
    assert got["rc"] == 5 and got["unchanged"], f"确认被拒仍删了东西：{got}"


def test_l2_caps_not_collected_is_actionable(payload, sys_py):
    """**「从未采集」不是「探测失败」** —— 前者可操作，后者不可。

    坏样本（本次真实发生）：两者都塌成 `Unknown` ⇒ 执行段一律 fail-closed
    ⇒ **首次使用必然被锁死**，因为 caps 缓存必然缺失，
    而用户拿到的是「不可判定」，不知道该做什么。
    现在：从未采集 ⇒ `Refused(6, caps.not_collected)`，文案指名 `mlx caps refresh`。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        from mlx_server.cmd import caps
        from mlx_server.result import Ok, Unknown
        never = caps.drift(ctx, lambda: Ok("fp"))          # 缓存缺失
        probe_fail = None
        import os, json as _j
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "caps.json"), "w").write(_j.dumps({"fingerprint": "x"}))
        probe_fail = caps.drift(ctx, lambda: Unknown("TOOL_UNAVAILABLE"))
        print(json.dumps({"never": never.kind, "probe_fail": probe_fail.kind}))
    ''')
    assert got["never"] == "NOT_COLLECTED", f"从未采集报成了 {got['never']}"
    assert got["probe_fail"] == "TOOL_UNAVAILABLE"


def test_l2_gateway_skips_venv_drift(payload, sys_py):
    """**网关不查 venv 漂移** —— 它跑在下限解释器上、零依赖。

    坏样本：查了 ⇒ 「venv 没装」把网关也挡住，而网关根本不用 venv。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        C.share_root = "@SHARE@"
        from mlx_server.cmd.svc import run
        from mlx_server.result import Unknown
        calls = []
        deps = {
            "backendarg_spec": lambda t, cfg, route: type("S", (), {
                "service": t, "argv": ("/usr/bin/python3", "-m", "gateway"),
                "env": {"MLX_GATEWAY_PORT": "8099"}, "cwd_rel": "",
                "model_arg": None, "sources": {}})(),
            "caps_drift": lambda: (calls.append("drift"), Unknown("NOT_COLLECTED"))[1],
            "spawn": lambda spec, cwd: None,      # 起不来，但已经过了 caps 那一关
            "nonce": lambda: "n0",
        }
        out = run(ctx, CleanupStack(), Inv("up", "api"), deps)
        print(json.dumps({"drift_called": calls, "key": getattr(out, "msg_key", None)}))
    '''.replace("@SHARE@", str(payload / "share")))
    assert got["drift_called"] == [], "网关启动查了 venv 漂移"
    assert got["key"] == "svc.spawn_failed", f"没走到 spawn 就被挡了：{got['key']}"
