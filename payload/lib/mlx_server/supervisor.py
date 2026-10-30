"""C 层 · 服务进程生命周期。模型与网关**同构**，**不拼任何 argv**。

拥有：后端进程树 · `run/` 命名空间（**唯一写者**）· 日志文件。
五个判定的唯一实现：`running` / `port_ready` / `service_ready` / `port_owner` / `api_ready`。
"""
import json
import os
import signal
import time

from . import probe, proc
from .result import Done, Failed, Ok, Unknown, is_unknown


class AliasDir:
    """别名目录句柄。**它就是后端的 cwd** —— 删掉它会让正在服务的后端解析失败。"""

    __slots__ = ("path", "_closed")

    def __init__(self, path):
        self.path = path
        self._closed = False

    def close(self):
        self._closed = True


def _run_dir(ctx):
    return os.path.join(ctx.root_data, "run")


def _record_path(ctx, service):
    return os.path.join(_run_dir(ctx), f"{service}.json")


class RunRecordUnreadable(Exception):
    """运行记录存在但读不了/解析不了。**不是「没在跑」** —— 两者处置相反。"""


def _bin(ctx, name, override=None):
    # type: (object, str, object) -> str
    """外部命令的路径。**从 ctx 取**（`MLX_BIN_<NAME>` 可覆盖），
    显式入参优先 —— 那是给单元测试直接注桩用的。

    此前这些路径是十处函数默认参数里的字面量，产品从不读注入面，
    于是 L2 里注了 `MLX_BIN_PS` 的 ps 桩就是摆设。
    """
    if override:
        return override
    from .ctx import DEFAULT_BINS
    return (getattr(ctx, "bins", None) or DEFAULT_BINS).get(name, DEFAULT_BINS[name])


def snapshot(ctx, service):
    # type: (object, str) -> object
    """运行记录快照。

    **缺失 ⇒ `None`**（「没在跑」是正常态）；
    **存在但不可解析 ⇒ 抛**（契约：调用方构造 `Refused(6)`）。

    此前不可解析也返回 `None`，于是一份被写坏的 `run/<svc>.json` 被读成
    「没在跑」—— `mlx down` 报「已停止」而什么都不做，28 GB 的后端继续跑着，
    而删记录的唯一线索也没了。「读不到」与「不存在」在这里的处置完全相反。
    """
    p = _record_path(ctx, service)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            return json.loads(fh.read())
    except (ValueError, OSError) as exc:
        raise RunRecordUnreadable(f"{p} 读不出来：{exc}") from exc


def services_with_record(ctx):
    # type: (object) -> list
    """`run/` 里有记录的全部服务 —— **这是目标域枚举，不是存活判定**。

    名字曾经叫 `list_running`，于是 `cmd.svc` 的 status 概览拿它当
    「在跑」用了（`mid in list_running(ctx)`），把**文件存在**当成**进程活着**。
    症状：后端崩掉之后 `mlx status` 一直显示绿勾说它在跑，而同一秒的
    `mlx status <id>` 说未运行 —— 同一个工具对同一个事实给出相反答案。
    裁决 G1 点名过这个形态：「v6 的第二份 `running` 恰恰不叫 running」。
    改名是为了让它读起来不再像判定词；判定唯一实现点是下面的 `running()`。

    没起过的服务没有运行记录，`down --all` 无从停止它，也不该报错。
    """
    d = _run_dir(ctx)
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def running(ctx, service, ps_path=None):
    # type: (object, str, str) -> object
    """判定「在跑」。三态 —— **`Unknown` 不得塌成 `Ok(False)`**。

    塌了会让 `up` 在一个已经在跑的服务上再起一份 28GB 后端。
    """
    ps_path = _bin(ctx, "ps", ps_path)
    snap = snapshot(ctx, service)
    if snap is None:
        return Ok(False)                      # 确知没有记录 ⇒ 没在跑
    st = proc.started_at_raw(snap["pid"], ps_path)
    if is_unknown(st):
        return Ok(False) if st.is_dead() else Unknown(st.kind, st.reason)
    return Ok(st.value.strip() == str(snap.get("started_at", "")).strip())


def port_owner(ctx, service, lsof_path=None, ps_path=None):
    # type: (object, str, str, str) -> object
    """端口上的服务确实是它。**pgid 归属判据**（裁决 R1-5）：

    监听 pid == 记录 pid **或** `pgid_of(监听 pid) == 记录 pgid`。
    **不是**对二元组 `(pid, pgid)` 做集合成员测试 —— launcher/worker 型后端上
    监听的是 worker，其 pid 既 != 记录 pid 也 != 记录 pgid。

    端口上有多个监听 pid 时**每一个**都要满足（spec D24）。
    """
    lsof_path, ps_path = _bin(ctx, "lsof", lsof_path), _bin(ctx, "ps", ps_path)
    snap = snapshot(ctx, service)
    if snap is None:
        return Unknown("NO_LISTENER")
    lp = probe.listener_pids(snap["port"], lsof_path)
    if is_unknown(lp):
        return Unknown(lp.kind, lp.reason)
    # **全部**监听者都得是本服务（spec D24）：有一个不是，端口就不归它独占
    for pid in lp.value:
        if pid == snap["pid"]:
            continue
        pg = probe.pgid_of(pid, ps_path)
        if is_unknown(pg):
            return Unknown(pg.kind, pg.reason)
        if pg.value != snap.get("pgid"):
            return Ok(False)
    return Ok(True)


def foreign_listeners(ctx, port, lsof_path=None):
    # type: (object, int, str) -> object
    """端口上现有的监听 pid。无人监听 ⇒ `Ok([])`；探测失败 ⇒ `Unknown`（**不当作无人**）。

    `up api` 在 spawn 之前调它（此刻网关尚未启动，任何监听者都是外来的）。
    """
    lp = probe.listener_pids(port, _bin(ctx, "lsof", lsof_path))
    if is_unknown(lp):
        return Ok([]) if lp.kind == "NO_LISTENER" else lp
    return lp


def may_kill(ctx, service, ps_path=None, lsof_path=None):
    # type: (object, str, str, str) -> bool
    """`proc.owns == Ok(ALIVE) ∧ port_owner != Ok(False)`。

    **第二项是否决票不是合取项**：写成 `== Ok(True)` 会让「端口从未 bind」
    这一最常见的就绪超时形态永不终止 —— E-7 逐字复现。
    """
    ps_path, lsof_path = _bin(ctx, "ps", ps_path), _bin(ctx, "lsof", lsof_path)
    alive = running(ctx, service, ps_path)
    if is_unknown(alive) or alive.value is not True:
        return False
    po = port_owner(ctx, service, lsof_path, ps_path)
    return not (not is_unknown(po) and po.value is False)     # != Ok(False)


def materialize(ctx, spec, nonce, target=None, draft_target=None):
    # type: (object, object, str, str, str) -> AliasDir
    """建别名目录（**后端的 cwd**）。`nonce` 由调用方先产出。

    `cwd_rel` 为空的服务（网关）不需要别名目录 —— 它不按 cwd 解析模型路径。
    这时 cwd 取代码根，因为 `-m gateway` 要在那里找到包。

    **目录里要有那一个条目**：裁决 C4 逐字给了形状
    `run/alias/<id>/<nonce>/<id>` —— 最后一层是指向 snapshot 的符号链接。
    独占父目录（带 nonce）是 C4 的理由：VLM 每请求按 cwd 重解析，
    别名若与兄弟同级，`../<其它 id>/<其它 id>` 会解析到别的模型。

    此前这里**只建空目录**：后端在空 cwd 下找不到 `./<短名>`，
    于是把短名当成 HuggingFace 仓库名去网上查 —— 401、进程立刻退出、rc 10。
    别名是 INV-01 态 A（「snapshot 绝对路径不出现在 argv 里」）成立的**前提**，
    不是可选优化：没有它，态 A 的 argv 根本解析不到本地权重。

    `target` 缺席时仍建目录（网关、以及注入了桩的测试），只是不建链接。

    **`draft_target`（投机解码）走同一套机制**：在同一个别名目录里再建一条
    `<id>-draft` 链接，`backendarg` 拼 `--draft-model <id>-draft`。
    放同一个目录而不是各自独立目录的理由：`--draft-model` 是相对 cwd 解析的，
    而 cwd 只有一个。C4 的「独占父目录」约束仍然满足 —— 那条约束防的是
    `../<别的 id>/<别的 id>` 解析到别人的模型，而这两条链接同属一个服务。
    """
    if not getattr(spec, "cwd_rel", ""):
        return AliasDir(ctx.root_code)
    d = os.path.join(_run_dir(ctx), "alias", spec.service, nonce)
    os.makedirs(d, exist_ok=True)
    for name, dest in ((spec.service, target),
                       (f"{spec.service}-draft", draft_target)):
        if not dest:
            continue
        link = os.path.join(d, name)
        # 幂等：同一个 nonce 重进时不因 FileExistsError 挂掉
        if not os.path.lexists(link):
            os.symlink(dest, link)
    return AliasDir(d)


def write_record(ctx, spec, pid, pgid, port, started_at, nonce, extra=None):
    # type: (object, object, int, int, int, str, str, object) -> None
    """`run/<service>.json` 的**唯一写点**。ServiceId 只从 `spec.service` 取（裁决 R4-1）。"""
    os.makedirs(_run_dir(ctx), exist_ok=True)
    rec = {"pid": pid, "pgid": pgid, "port": port, "started_at": started_at,
           "model_arg": spec.model_arg, "type": "model" if spec.model_arg else "api",
           "schema_ver": 1, "nonce": nonce}
    rt = getattr(spec, "route", None)
    if rt is not None:
        # 运行侧读这些**快照**，不在查看时重新判定（spec §3.4）
        rec.update({"backend": rt.backend,
                    "backend_auth": rt.row.get("key_env") not in ("", "-", None),
                    "max_concurrency": rt.max_concurrency,
                    "rule": rt.rule,
                    "effort_domain": list(rt.effort_domain),
                    "key_fp": rt.key_fp,
                    # `-`（这张表「未给」的既有哨兵，`key_env`/`env_clear` 等列同一套写法）
                    # 与空 cell 都规范成空列表 —— 网关侧 `endpoints_of` 把空列表当「未给」回退
                    "endpoints": [e for e in (rt.row.get("endpoints") or "").split(",")
                                 if e and e != "-"],
                    # 网关据此做兼容映射（spec §4.5）：记录快照，不在请求时查表
                    "reads_completion_tokens":
                        rt.row.get("completion_tokens") == "max_completion_tokens",
                    "native_template_kwargs": rt.row.get("thinking_params") == "template_kwargs"})
    if not spec.model_arg:
        # 网关：密钥只存指纹（spec §3.4），`status` 用它判「已变更，需重启」
        from .route import key_fingerprint
        env = spec.env or {}
        rec.update({"token_fp": key_fingerprint(env.get("MLX_GATEWAY_TOKEN")),
                    "backend_key_fp": key_fingerprint(env.get("MLX_BACKEND_API_KEY")),
                    "token_source": ("env" if (spec.sources or {}).get("api.token") == "env"
                                     else "config")})
    if extra:
        rec.update(extra)          # 调用方判定的启动事实（api：绑定、TLS、解释器）
    from . import atomic
    # **0600**：运行记录含密钥指纹与后端信息（spec §3.4）
    with atomic.replace(_record_path(ctx, spec.service), mode=0o600) as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True))


# 网关经门面能并进 api 运行记录的键（spec §3.4）。**白名单**：门面给网关的是
# 「报告自己算出的放行网段」的能力，不是改 pid / 端口 / 指纹的能力。
MERGEABLE = frozenset({"allowed_nets", "excluded_ifaces", "nets_source",
                       "nets_computed_at", "nets_error"})


def merge_record(ctx, service, fields, pid=None):
    # type: (object, str, dict, object) -> bool
    """把 `fields` 并进已有运行记录。**运行记录的写者仍只有本模块**（`write_record` 与这里）。

    记录不存在 ⇒ False（不新建：网关不是经 `mlx up api` 起的就没有记录可并）；
    给了 `pid` 而记录的 pid 不同 ⇒ False（陈旧记录属于上一个进程，不替它写）。
    """
    bad = sorted(set(fields) - MERGEABLE)
    if bad:
        raise ValueError(f"merge_record 不接受这些键：{bad}")
    snap = snapshot(ctx, service)
    if snap is None or (pid is not None and snap.get("pid") != pid):
        return False
    snap.update(fields)
    from . import atomic
    with atomic.replace(_record_path(ctx, service), mode=0o600) as fh:
        fh.write(json.dumps(snap, ensure_ascii=False, sort_keys=True))
    return True


def effective_port(ctx, service, registered=None):
    # type: (object, str, object) -> object
    """服务**实际在用**的端口。run 记录优先（那是真在跑的那个），回落登记值。

    `registered` 由调用方从注册表算好传进来 —— supervisor 不 import `store`
    （两者同层，反向边会让层序守卫恒红）。

    `chat` 与 `bench` 此前各写各的：`bench` 抄了一份十行的回落逻辑，
    而 `chat` **把模型 id 当端口传给了 `send_chat`** —— URL 成了
    `http://127.0.0.1:qwen05b/…`，每次都失败。同一处逻辑两份实现必然漂移。
    """
    snap = snapshot(ctx, service) or {}
    port = snap.get("port") or registered
    return int(port) if port is not None else None


def effective_model_arg(ctx, service, fallback=None):
    # type: (object, str, object) -> object
    """服务**实际启动时用的** model 名。INV-27：对正在跑的服务，
    一切请求只读运行记录里的快照，不调 `spec()`。

    `upgrade` / `rollback` / `rename` 落地那天，启动侧与运行侧会分叉 ——
    那时用 CLI 给的 id 去请求一个已经换了名字的后端就会全部失败。
    现在没有那三个动词，所以这条目前无用户可见后果；但形态先摆对，
    免得那天再回来找。
    """
    snap = snapshot(ctx, service) or {}
    return snap.get("model_arg") or fallback or service


def kill_tree(ctx, pid, pgid, term_wait_sec=2.0, poll=0.1, children=None):
    # type: (object, int, int, float, float, object) -> object
    """先子后父、TERM → 等 → KILL。

    **主路径 `killpg`**（`BCR-R-13`）；`pgrep -P` 的返回值**只用于报告未死 pid**，
    其结果 ∩ 终止目标集合 == ∅（裁决 R5-3 —— 报告段仍要枚举，否则逃逸态不可见）。
    """
    escapees = ()
    if children is not None:
        c = children(pid)
        if not is_unknown(c):
            escapees = c.value

    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as e:
        return Failed(4, "kill.killpg_failed", [], pid=pid, detail=str(e))

    deadline = time.time() + term_wait_sec
    while time.time() < deadline:
        st = proc.started_at_raw(pid)
        if is_unknown(st) and st.is_dead():
            return Done({"terminated": (pid,), "survivors": (), "degraded": False})
        time.sleep(poll)

    with_kill = True
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        with_kill = False
    time.sleep(poll)

    st = proc.started_at_raw(pid)
    survivors = () if (is_unknown(st) and st.is_dead()) else (pid,)
    # 逃逸后代：pgrep 枚举到但 killpg 覆盖不到的（setsid 自建会话）
    survivors = tuple(survivors) + tuple(e for e in escapees if e != pid)
    if survivors:
        return Failed(4, "kill.escalation_exhausted", [], survivors=list(survivors))
    return Done({"terminated": (pid,), "survivors": (), "degraded": not with_kill})


def down(ctx, service, ps_path=None, lsof_path=None):
    # type: (object, str, str, str) -> object
    """停止服务。四支分流（`BCR-R-04`，求值顺序 ④ → ①b → ② → ①a）。"""
    ps_path, lsof_path = _bin(ctx, "ps", ps_path), _bin(ctx, "lsof", lsof_path)
    snap = snapshot(ctx, service)
    if snap is None:
        return Done({"kind": "svc", "already_stopped": service, "stopped": service})

    alive = running(ctx, service, ps_path)
    if is_unknown(alive):                                   # ④ 所有权不可证
        # 用 `Failed` 不是 `Refused`：rc 4 ∈ Failed 的码集，且此时运行记录已落地
        # （`Refused` 的唯一前提是零资源已创建，BCR-R-01）。committed 是收敛线索。
        return Failed(4, "owns.undeterminable", [f"run/{service}.json"], kind=alive.kind)

    po = port_owner(ctx, service, lsof_path, ps_path)
    if alive.value is True and not is_unknown(po) and po.value is False:
        # ①b 端口上是别人而进程还活着 ⇒ **不终止、保留别名目录与运行记录**
        # （裁决 R-安全-重1：别名目录是后端的 cwd，删掉会让正在服务的后端解析失败）
        return Failed(4, "port_owner.foreign", [f"run/{service}.json"],
                      pid=snap["pid"], port=snap["port"])

    # ── 屏蔽窗口（`G-RES-09(b)` 的三个调用点之一）─────────────────
    # 覆盖「终止 → 删运行记录」这一段。中间被 TERM 打断的后果是
    # **进程已经死了而记录还在** —— 下一条 `status` 说它在跑、`down` 说它
    # 已停止，两个命令对同一事实给出相反答案（这个形态本轮修过一次）。
    # 窗口内允许的可阻塞调用：`kill_tree` 的 TERM→等→KILL 阶梯，
    # 它的等待有 `down.term_grace_sec` 封顶（J-2：不得存在没有上界的停留）。
    with proc.masked():
        if alive.value is True:
            out = kill_tree(ctx, snap["pid"], snap.get("pgid", snap["pid"]),
                            children=probe.children_of)
            if out.rc != 0:
                return out
        os.unlink(_record_path(ctx, service))
    return Done({"kind": "svc", "stopped": service})
