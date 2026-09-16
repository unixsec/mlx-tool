"""`up` / `restart` 的生命周期契约：先校验后停、汇总结论的形态、flag 透传。

三条都来自整分支收尾评审，共同点是**只有驱动真的 `cmd.svc.run()` 才看得见**：
它们各自的坏结局都发生在「命令已经动过手，结论却说它拒绝了」这条缝上。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

TOK = "T" * 43


RESTART_VALIDATE_FIRST = WORLD + r"""
import subprocess
from mlx_server import backendarg, supervisor
from mlx_server.cmd.svc import Invocation, run
from mlx_server.ctx import Ctx, DEFAULT_BINS

# `lsof` 打桩：本用例与端口上有没有别人无关，不该对真机端口发探测。
# rc≠0 且 stderr 空 ⇒ `probe.listener_pids` 判「确知无人监听」。
_lsof = os.path.join(DATA, "lsof_stub")
with open(_lsof, "w") as fh:
    fh.write("#!/bin/sh\nexit 1\n")
os.chmod(_lsof, 0o755)
c = Ctx(_ROOT, DATA, WEIGHTS, VENV, 8340, (8100, 8199), None,
        bins=dict(DEFAULT_BINS, lsof=_lsof), share_root=SHARE)

# **一个真的已经死掉的 pid**：起一个立刻退出的子进程，记下它的 pid 再回收它。
# 编一个 999999 这样的号是不行的 —— `ps` 会把「超出范围」报到 stderr，
# 判定于是是 `PS_FAILED`（不可判定）而不是 `DEAD`，`down` 会在删记录**之前**
# 就先行拒绝，这条用例测的就不再是它要测的东西了（实测踩过这一版）。
_dead = subprocess.Popen(["/bin/echo"], stdout=subprocess.DEVNULL)
DEAD = _dead.pid
_dead.wait()

put_settings({})                       # **没有 api.token** ⇒ 校验段必拒
os.makedirs(os.path.join(DATA, "run"), exist_ok=True)
REC = os.path.join(DATA, "run", "api.json")
with open(REC, "w") as fh:
    fh.write(json.dumps({"pid": DEAD, "pgid": DEAD, "port": 8340, "started_at": "t",
                         "model_arg": None, "type": "api", "schema_ver": 1, "nonce": "n"}))
out = run(c, None, Invocation("restart", "api"), {"backendarg_spec": backendarg.spec})
print(json.dumps({"rc": out.rc, "key": getattr(out, "msg_key", None),
                  "record_survived": os.path.exists(REC)}))
"""


def test_restart_refusal_does_not_destroy_the_running_service_first(payload, sys_py):
    """收尾评审 Critical 5：`restart` 先 `supervisor.down()` 再校验。

    后果是「服务已经被停掉了，命令却报拒绝」—— 而拒绝文案读起来像是什么都
    没发生。本计划给 api 路径新增了五条校验段拒绝（`api.token_missing` /
    `api.tls_incomplete` / `api.tls_unreadable` / `api.tls_key_permissive` /
    `api.tls_unusable`），每一条都是一条把用户撂在死网关上的新路径；
    `api.token_missing` 的文案还写着「没有它网关不启动」。

    判据取**可观测的破坏**：`run/api.json` 在拒绝之后还在不在。
    修复前它被删掉（`down` 的最后一步就是 `os.unlink` 运行记录），
    修复后校验段整段排在 `down` 之前，拒绝时一个字节都没动。
    """
    got = run_floor(payload, sys_py, RESTART_VALIDATE_FIRST, timeout=60)
    assert (got["rc"], got["key"]) == (6, "api.token_missing"), got
    assert got["record_survived"], (
        "restart 在校验拒绝之前就把运行记录删了 —— 用户拿到一条「拒绝」，"
        "而服务已经没了")


MULTI_TARGET_DRY_RUN = WORLD + r"""
from mlx_server import backendarg
from mlx_server.cmd.svc import Invocation, run
out = run(ctx, None, Invocation("up", "nosuch1", targets=("nosuch1", "nosuch2"),
                                dry_run=True), {"backendarg_spec": backendarg.spec})
rd = os.path.join(DATA, "run")
print(json.dumps({"type": type(out).__name__, "rc": out.rc,
                  "key": getattr(out, "msg_key", None),
                  "committed": list(getattr(out, "committed", None) or []),
                  "run_dir": sorted(os.listdir(rd)) if os.path.isdir(rd) else []}))
"""


def test_multi_target_refusals_do_not_aggregate_into_a_lying_failed(payload, sys_py):
    """收尾评审 Critical 6 —— 同三行里的三个缺陷，一起钉住。

    `mlx up nosuch1 nosuch2 --dry-run` 修复前返回
    `Failed(1, committed=["run/nosuch1.json", "run/nosuch2.json"])`：

    ① `committed` 是**凭空拼出来的**（`[f"run/{t}.json" for t in inv.targets]`），
       让用户去收敛两个从来没存在过的文件 —— `run/` 是空的，而 `--dry-run`
       更不可能落地任何东西；
    ② 每个子结论都是 `Refused`（零资源已创建），汇总却包成了 `Failed`，
       与 `BCR-R-01` 直接冲突；
    ③ `worst if worst in (1,3,4,10) else 1` 把 rc 6 塌成 rc 1，
       「模型不存在」这个最有用的结论被丢掉。
    """
    got = run_floor(payload, sys_py, MULTI_TARGET_DRY_RUN, timeout=60)
    assert got["run_dir"] == [], f"夹具前提坏了：dry-run 竟然落了盘：{got['run_dir']}"
    assert got["type"] == "Refused", (
        f"子结论全是 Refused（零资源）⇒ 汇总也必须是 Refused，实得 {got['type']}")
    assert got["rc"] == 6, f"rc 6（model.not_found）被塌成了 {got['rc']}"
    assert got["committed"] == [], (
        f"committed 指名了从未落地的资源：{got['committed']}")


RESTART_FLAGS = WORLD + r"""
from mlx_server import backendarg
from mlx_server.cmd.svc import Invocation, run
from mlx_server.result import Ok
put_settings({})
put_caps(["mlx_lm", "mlx_vlm", "mlx"])
seed("big", "org/big", 8100, config="{}", machine={"size_mb": 900000})
deps = {"backendarg_spec": backendarg.spec, "avail_mem_mb": lambda: Ok(1024)}
def key(args):
    out = run(ctx, None, Invocation("restart", "big", args=args), dict(deps))
    return getattr(out, "msg_key", None)
print(json.dumps({"without": key(None), "with": key({"ignore_fitness": True})}))
"""


def test_restart_forwards_its_flags_to_the_up_phase(payload, sys_py):
    """收尾评审 Important 8：`restart` 重造 Invocation 时把 `inv.args` 整个丢掉。

    `cli.tsv:32` 把 `--ignore-fitness` 登记给了 `up,restart`，于是它在 `restart`
    上被接受、被解析、然后被静默忽略 —— 一个装不下的模型 `mlx restart <id>
    --ignore-fitness` 照样撞 `fit.over`，而 `fit.over` 的文案让用户去加
    他已经加过的这个 flag。与 Critical 5 叠加时更糟：模型先被停掉，再被拒。

    判据是**同一个模型、只差这一个 flag，结论必须不同**：
    不带 ⇒ 仍然 `fit.over`（闸门还在）；带上 ⇒ 越过闸门走到执行段
    （本用例没注 `spawn`，所以停在 `svc.no_spawner`，那正说明它走过去了）。
    """
    got = run_floor(payload, sys_py, RESTART_FLAGS, timeout=60)
    assert got["without"] == "fit.over", f"容量闸门本身失效了：{got}"
    assert got["with"] == "svc.no_spawner", (
        f"--ignore-fitness 在 restart 上是死的（args 没透传）：{got}")
