"""D 层 · 服务编排。零自有资源。

格 1（`BCR-4-08`）与格 2（`BCR-R-27`）在这里一起被定死：
  `BCR-R-27` 把 venv 漂移（`caps.drift`）排在**执行段**，
  而 `DE-T-018` 断言「`stage_split` 之后外部命令计数 == 0」。
  两条同真，唯一解是：**`--dry-run` 在 `enter_execute()` 之前返回**，
  于是执行段整段不跑 —— 外部命令计数自然为 0，而 `caps.drift` 仍留在执行段。
  这就是 `BCR-4-08`「`--dry-run` 要不要跑 venv 探测」的答案：**不跑，因为它根本到不了那一段**。
"""
from .. import fitness, store, weights
from ..result import Done, Failed, Ok, Refused, is_unknown

# `BCR-R-27` 钉死的求值顺序。写成数据而不是散在代码里的 if：
# 不钉死的后果是「端口冲突 + 容量 OVER」时 rc 在 6/7 之间随实现顺序漂移。
VALIDATE_STEPS = ("目标域", "记录", "权重解析", "生效配置", "后端判定",
                  "ports.conflict_check", "backendarg.spec")
EXECUTE_STEPS = ("已在跑", "端口即时可监听性", "容量判档", "venv 漂移", "资源创建")


class Invocation:
    """一次命令调用的解析结果。"""

    __slots__ = ("verb", "target", "targets", "args", "dry_run")

    def __init__(self, verb, target=None, args=None, dry_run=False, targets=None):
        # type: (str, object, dict, bool, object) -> None
        self.verb = verb
        self.target = target
        # **多目标**：附录 A 2807/2808 的动词形态是 `up <service>...`，
        # 主产物是「**每个**目标一行结论」。`target` 保留为第一个，
        # 使既有单目标调用点不变；`targets` 是全部。
        self.targets = tuple(targets) if targets else ((target,) if target else ())
        self.args = dict(args or {})
        self.dry_run = dry_run


def enter_execute():
    # type: () -> None
    """`stage_split`（`BCR-R-01`）。

    **两个口径**（格 3）：
      · 静态口径：AST 上，本函数的调用点把 `run()` 的函数体分成校验段与执行段。
        `L0·result-02` 等守卫按这个口径求值。
      · 运行期口径：它是一次真实调用，`DE-T-018` 数的是「本次调用**之后**」
        发生的外部命令次数。
    两个口径必须指同一个点，否则守卫求值面与被测行为错位。
    """
    return None


def _fit_verdict(ctx, target, deps, cfg):
    # type: (object, str, dict, object) -> object
    """把三个探针值喂给 `fitness.classify`。**探针在这里取，判定在那里做**
    （`fitness` 是纯函数，不调探针 —— 那是它能被逐样例对拍的前提）。"""
    from .. import fitness, store, supervisor
    avail = deps["avail_mem_mb"]()
    recs = store.load(ctx, store.MODELS)
    rec = recs.by_id(target)
    need = int((rec.machine.get("size_mb") or 0) if rec is not None else 0)
    # **drafter 的体积也要算进去**：投机解码时它与主模型**同时驻留内存**。
    # 不算的话，一个刚好卡在 TIGHT 边界的主模型加上 drafter 就越界了，
    # 而判档会说「宽裕」—— 那正是这道闸门要防的情形。
    draft_id = (rec.user.get("draft_model") if rec is not None else None) or ""
    if draft_id:
        drec = recs.by_id(draft_id)
        if drec is not None:
            need += int(drec.machine.get("size_mb") or 0)
    size = Ok(need)
    # 已在跑的模型占用之和：判档要看的是**剩余**预算
    rss_of, total = deps.get("rss_mb_of"), 0
    if rss_of is not None:
        for sid in supervisor.services_with_record(ctx):
            if sid == target:
                continue
            snap = supervisor.snapshot(ctx, sid) or {}
            if not snap.get("pid"):
                continue
            m = rss_of(snap["pid"])
            if not is_unknown(m) and m.value:
                total += int(m.value) // 1024
    th = fitness.FitThresholds(cfg.values["fit.reserve_pct"],
                               cfg.values["fit.tight_pct"],
                               cfg.values["fit.over_pct"])
    return fitness.classify(avail, size, Ok(total), th)


def _collect(ctx, stack, deps, verb, targets, args=None, dry_run=False):
    # type: (object, object, dict, str, object, dict, bool) -> tuple
    """逐目标跑一遍，收集**带文案参数**的结论。
    返回 `(results, worst_rc, committed, failed_codes)`。

    两条批量路径（显式多目标 与 `--all`）共用它。此前各写一份几乎相同的循环，
    于是给结论补 `args` 时只补了一处 —— `mlx down --all` 的失败行仍旧渲染成
    笼统的「失败」，而显式多目标那条已经能打出「进程所有权不可证（PS_FAILED）」。
    同一逻辑两份实现必然漂移，这是本轮第三次遇到同一形态。
    """
    results, worst, committed, failed_codes = {}, 0, [], []
    for t in targets:
        one = run(ctx, stack, Invocation(verb, t, args=args, dry_run=dry_run), deps)
        results[t] = getattr(one, "payload", None) or {
            "rc": one.rc, "key": getattr(one, "msg_key", None),
            "args": dict(getattr(one, "args", {}) or {})}
        worst = max(worst, one.rc)
        # **`committed` 只有 `Failed` 有**（`Done` / `Refused` 都没有这个属性），
        # 所以它同时是「这个目标到底有没有落地东西」的判据 —— 汇总层据此决定
        # 自己该是 `Refused`（零资源）还是 `Failed`（有资源要收敛），
        # 而不是无条件包成 `Failed` 再凭空编一份 committed。
        c = getattr(one, "committed", None)
        if c is not None:
            failed_codes.append(one.rc)
            committed += list(c)
    return results, worst, committed, failed_codes


def _v_target(state, inv, deps):
    """校验段步 1 · 目标域。"""
    return None if inv.target else Refused(2, "svc.target_required", cmd=inv.verb)


def _v_record(state, inv, deps):
    """校验段步 2 · 记录。

    这一步曾是**空桩**（注释写着「片 2 接 store.load」而片 2 没做）。
    后果不是「少一道校验」而是**端口取了默认值**：`backendarg.spec` 的
    `cfg` 里没有注册表那一层，`DEFAULTS["port"] == 8100` 于是生效 ——
    登记在 8399 的模型被起在 8100，`status` 和 `up` 各说各的端口。
    """
    ctx = state["ctx"]
    if inv.target == "api":            # 网关没有注册表记录，端口来自 ctx
        return None
    rec = store.load(ctx, store.MODELS).by_id(inv.target)
    if rec is None:
        return Refused(6, "model.not_found", id=inv.target)
    state["record"] = rec
    return None


def _v_weights(state, inv, deps):
    """校验段步 3 · 权重解析。

    同样曾是空桩。后果是**别名目录没有指向目标** —— `materialize` 建出
    一个空目录当 cwd，mlx_lm 在里面找不到 `./<短名>`，就把短名当成
    HuggingFace 仓库名去网上查，401 之后进程立刻退出（rc 10）。
    snapshot 路径**由调用方注入**（`03b` §5.3 N5），所以在这里解析、
    存进 state，执行段再递给 `supervisor.materialize`。
    """
    if inv.target == "api":
        return None
    rec = state.get("record")
    if rec is None:
        return None
    # per-model 的 `draft_model` 落在记录的 user 段（`config set <id> --key=draft_model`）
    state["cfg_draft"] = rec.user.get("draft_model") or ""
    r = weights.resolve(state["ctx"], rec.user.get("repo", ""),
                        rec.machine.get("revision"))
    if is_unknown(r) or not r.value:
        # **零资源已创建** ⇒ Refused。权重不在盘上起不来，这是确定的结论，
        # 不是探测不确定 —— 不报 9。
        return Refused(6, "svc.weights_missing", id=inv.target,
                       repo=rec.user.get("repo", ""))
    state["weights_path"] = r.value["path"]

    # **投机解码的 drafter**：值是另一条记录的短名（`schema.tsv#draft_model`）。
    # 它与主模型走同一道闸门 —— 权重不在盘上就拒且零资源创建，
    # 而不是「起来之后 mlx_lm 自己去下载」（那会在启动路径上引入一次网络等待，
    # 而 `up` 的就绪窗口是有界的）。
    draft_id = (state.get("cfg_draft") or "").strip()
    if not draft_id:
        return None
    if draft_id == inv.target:
        return Refused(6, "svc.draft_is_self", id=inv.target)
    drec = store.load(state["ctx"], store.MODELS).by_id(draft_id)
    if drec is None:
        return Refused(6, "svc.draft_not_found", id=inv.target, draft=draft_id)
    dr = weights.resolve(state["ctx"], drec.user.get("repo", ""),
                         drec.machine.get("revision"))
    if is_unknown(dr) or not dr.value:
        return Refused(6, "svc.draft_weights_missing", id=inv.target,
                       draft=draft_id, repo=drec.user.get("repo", ""))
    state["draft_path"] = dr.value["path"]
    state["draft_record"] = drec
    return None


def _v_config(state, inv, deps):
    """校验段步 4 · 生效配置 —— **cfg 的唯一产出点**（spec §2.2）。

    此前 cfg 在 `entry._spec` 里现造：user 层按硬编码 `DEFAULTS` 过滤（per-model
    `modality` 被静默丢掉），也没有 settings 层（`config set fit.*` 从不生效）。
    层序（低→高）：出厂默认 < machine（settings ∪ 注册表端口）< user（记录 user 段）< cli。
    """
    import os

    from .. import schema
    from ..tables import open_tables
    ctx = state["ctx"]
    tv = open_tables(ctx)
    defs = schema.keydefs(tv)
    settings = store.load_settings(ctx)
    rec = None if inv.target == "api" else state["record"]
    # 层构造的唯一实现点在 `schema.build_layers`（与 `config get/ls` 共用）
    layers, dropped = schema.build_layers(
        defs, settings, is_model=rec is not None,
        rec_user=None if rec is None else rec.user,
        registered_port=None if rec is None else store.model_port(rec),
        gateway_port=ctx.gateway_port,
        gateway_token_env=getattr(ctx, "gateway_token_env", ""))
    venv_py = os.path.join(ctx.venv, "bin", "python3") if getattr(ctx, "venv", "") else ""
    if venv_py and not os.path.exists(venv_py):
        venv_py = ""                           # venv 没建成 ⇒ 回落（§0.4.2）
    try:
        cfg = schema.effective(inv.target, layers, venv_python=venv_py,
                               defaults=schema.defaults_from(tv))
    except schema.ConfigError as exc:
        return Refused(6, "config.invalid", key=exc.key, kind=str(exc))
    # 值域判定与 `config validate` 同一份实现（schema.domain_errors）
    for k, bad in schema.domain_errors(cfg, defs).items():
        return Refused(6, "config.invalid", key=k, kind=bad)
    state.update(cfg=cfg, tables=tv, dropped=dropped)
    return None


def _size_mb(rec, path):
    # type: (object, str) -> object
    """登记的体积优先；缺失（register 从不写、flat 布局 repair 填不上）就现场量（spec P3）。"""
    v = rec.machine.get("size_mb") if rec is not None else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return float(v)
    s = weights.size(path)
    return None if is_unknown(s) else round(s.value / (1024 * 1024), 1)


def _api_checks(cfg):
    # type: (object) -> object
    """api 目标的校验（spec §4.1）。**只读文件系统 + 一次本地 TLS 加载**，
    所以留在校验段（dry-run 同样会拒；零资源已创建）。

    判据与 `payload/gateway/server.py::tls_context` 逐条对齐（可读、私钥权限、
    `load_cert_chain` 真的加载得动），但**不 import 网关代码** —— 复检 Important 1：
    只查前两条会漏掉「文件读得到、权限也对，但内容不是合法证书/私钥，或证书与
    私钥不配对」这一类——那种输入会一路混到执行段，网关子进程起来后自己发现
    TLS 加载失败再退出（rc 6），用户看到的是 `Failed(10, svc.died_before_ready)`
    加一段日志尾，而这本是**零资源创建**时就能问出的问题。
    """
    import os
    import ssl
    import stat
    v = cfg.values
    if not v.get("api.token"):
        return Refused(6, "api.token_missing")
    cert, key = v.get("api.tls_cert") or "", v.get("api.tls_key") or ""
    if bool(cert) != bool(key):
        return Refused(6, "api.tls_incomplete")
    if cert:
        for p in (cert, key):
            if not os.access(p, os.R_OK):
                return Refused(6, "api.tls_unreadable", path=p)
        mode = stat.S_IMODE(os.stat(key).st_mode)
        if mode & 0o077:
            return Refused(6, "api.tls_key_permissive", path=key, mode=format(mode & 0o777, "03o"))
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
        except (ssl.SSLError, OSError) as exc:
            return Refused(6, "api.tls_unusable", cert=cert, key=key, detail=str(exc))
    return None


def _v_backend(state, inv, deps):
    """校验段步 5 · 后端判定（spec §2.3–§2.4）。**采证在这里，裁决在 `route`**。"""
    if inv.target == "api":
        return _api_checks(state["cfg"])
    from .. import caps, route, templates
    ctx, cfg, tv = state["ctx"], state["cfg"], state["tables"]
    attach = [r["model_type"] for r in tv.drafters()]
    facts = weights.inspect_config(state["weights_path"], attach)
    if is_unknown(facts):
        return Refused(9, "svc.inspect_undeterminable", id=inv.target,
                       detail=facts.reason or "")
    draft, size = None, _size_mb(state["record"], state["weights_path"])
    if state.get("draft_path"):
        d = weights.inspect_config(state["draft_path"], attach)
        draft = (state["cfg_draft"], None if is_unknown(d) else d.value)
        dsize = _size_mb(state.get("draft_record"), state["draft_path"])
        size = None if (size is None or dsize is None) else size + dsize
    # 模板采证对 R2/R3/R4 一视同仁（spec §2.3 不按后端限定；§4.5 mlx_lm 同样要把
    # reasoning_effort 转发进 chat_template_kwargs）。此前只在「无 drafter 且
    # vision」时才取证，R2（drafter）/R4（纯文本）两条路线因此恒记
    # effort_domain == []，qwen38 被 modality=text 强制成文本后 reasoning
    # effort 也跟着丢。判不出（没有可用 venv 解释器 / 没有 jinja2 / 模型没
    # 模板）按 Unknown 处理，`route.decide` 已经把它消化成空证据——不对
    # mlx_lm 引入新的拒绝，`--enable-thinking` 仍然只认 `backends.tsv` 的
    # `thinking` 列（mlx_lm 该列是 `-`，恒不受此影响）。
    template = templates.inspect_template(
        state["weights_path"], cfg.venv_python,
        ps_path=(getattr(ctx, "bins", None) or {}).get("ps", "/bin/ps"))
    out = route.decide(inv.target, facts.value, template, draft, cfg.values,
                       (caps.get(ctx) or {}).get("packages") or [],
                       {r["backend"]: r for r in tv.backends()}, size)
    if not isinstance(out, route.Route):
        return out
    state["route"] = out
    return None


def ready_timeout(rt):
    # type: (object) -> int
    """就绪超时（spec §3.3）：先监听后加载的后端 20 s；加载完才监听的按体积算。"""
    import math
    if rt is None or rt.row.get("ready") != "load":
        return 20
    return 20 + 3 * int(math.ceil((rt.size_mb or 0) / 1024.0))


def _v_ports(state, inv, deps):
    """校验段步 4 · ports.conflict_check（登记面互斥，纯表读）。

    **必须排在 spec 之前**：`BCR-R-27` 原文的后果是
    「端口冲突 + 容量 OVER」同时成立时 rc 在 6/7 之间随实现顺序漂移。
    """
    conflict = deps.get("ports_conflict")
    if conflict is None:
        return None
    out = conflict(inv.target)
    # **原样返回**：`conflict_check` 已经构造好带 `detail` 的 `Refused(7)`，
    # 在这里重造一个不带参数的会让用户看到「端口冲突：」后面空着。
    return out if out is not None and getattr(out, "rc", 0) != 0 else None


def _v_spec(state, inv, deps):
    """校验段步 7 · backendarg.spec。cfg 与判定结论都来自前两步，这里不再造第二份 cfg。"""
    spec = deps["backendarg_spec"](inv.target, state["cfg"], state.get("route"))
    if spec is None:
        return Refused(5, "svc.spec_unavailable", id=inv.target)
    state["spec"] = spec
    return None


# **执行顺序的唯一真源**：`run()` 按本表 dispatch，元组不再只是文档。
# 守卫 `test_gate2` 断言 set(VALIDATORS) == set(VALIDATE_STEPS)（相等，不是子集）。
VALIDATORS = {
    "目标域": _v_target,
    "记录": _v_record,
    "权重解析": _v_weights,
    "生效配置": _v_config,
    "后端判定": _v_backend,
    "ports.conflict_check": _v_ports,
    "backendarg.spec": _v_spec,
}


def _tool_status(ctx):
    # type: (object) -> object
    """`mlx status`（不带目标）—— **工具自身**的状态。

    判据（`verbs.tsv` 第 4 行）：必须输出三根路径与 PATH 生效性 ——
    否则「装完了但 mlx 找不到」这个最常见的首次使用故障，
    没有任何命令诊断得出来。
    """
    import os

    from .. import supervisor

    roots = {"code": ctx.root_code, "data": ctx.root_data,
             "weights": ctx.root_weights}
    bin_dir = os.path.join(ctx.root_code, "bin")

    try:
        from ..tables import open_tables
        rows = open_tables(ctx).manifest()
        missing = [r["path"] for r in rows
                   if not r["path"].endswith("/")
                   and not os.path.exists(os.path.join(ctx.root_code, r["path"]))]
        manifest = {"declared": len(rows), "missing": missing}
    except Exception as exc:                      # noqa: BLE001
        manifest = {"declared": None, "missing": None, "error": str(exc)}

    # **模型行与网关行**：设计（附录 A）明写「工具状态 + 模型行 + 网关一行」。
    # 少了它们，用户问「我注册了几个模型、都什么状态」时无处可看。
    # **存活判定走唯一实现点** `supervisor.running`（INV-04/INV-20）。
    # 此前这里用 `services_with_record`（run/ 下有没有文件），把文件存在
    # 当成进程活着 —— 后端崩掉后 `mlx status` 一直报绿勾，而同一秒的
    # `mlx status <id>` 说未运行。三份独立审查各自撞上了这一条。
    # `Unknown` **不塌成 False**：不可判定要显示成不可判定。
    def _alive(sid):
        st = supervisor.running(ctx, sid)
        return None if is_unknown(st) else bool(st.value)

    # ── 密钥轮换检测（spec §5.2）：运行记录只存指纹，与**同一来源**的当前值比 ──
    from .. import route as _route
    from .. import store as _store
    try:
        _cur = _store.load_settings(ctx)
    except Exception:                             # noqa: BLE001
        _cur = None                               # 读不了设置 ⇒ 不比较，不误报「已变更」
    cur_key_fp = _route.key_fingerprint((_cur or {}).get("backend.api_key"))

    models = []
    try:
        from .. import store
        for rec in store.load(ctx).records:
            mid = rec.get("id") if isinstance(rec, dict) else getattr(rec, "id", None)
            if not mid:
                continue
            snap = supervisor.snapshot(ctx, mid) or {}
            user = (rec.get("user") if isinstance(rec, dict)
                    else getattr(rec, "user", None)) or {}
            machine = (rec.get("machine") if isinstance(rec, dict)
                       else getattr(rec, "machine", None)) or {}
            row = {"id": mid, "running": _alive(mid),
                   # 端口取自**注册表**，snapshot 只在服务跑起来后才有
                   "port": snap.get("port") or store.model_port(rec),
                   "state": store.model_state(rec),
                   # 用户视角要看到的：这个 id 到底是哪个模型、装了没、多大
                   "repo": user.get("repo"), "modality": user.get("modality") or "auto",
                   # 上一次启动的判定结论（运行记录快照，spec §3.4）；没起过就是 None
                   "backend": snap.get("backend"),
                   "max_concurrency": snap.get("max_concurrency"),
                   "rule": snap.get("rule"),
                   "revision": machine.get("revision"),
                   "size_mb": machine.get("size_mb"),
                   "pid": snap.get("pid")}
            row["key_changed"] = (_cur is not None and row["running"] is True
                                  and bool(snap.get("backend_auth"))
                                  and snap.get("key_fp") != cur_key_fp)
            if row["running"] is True and row["pid"]:
                # `health.model_memory`（02:1833「运行中模型的实际占用」）
                from .. import probe
                from ..result import is_unknown as _unk
                m = probe.rss_kb(row["pid"])
                row["rss_mb"] = (round(m.value / 1024, 1)
                                 if not _unk(m) and m.value else None)
            models.append(row)
    except Exception as exc:                      # noqa: BLE001
        models = None                             # 读不到注册表时不假装为空
        models_error = str(exc)
    else:
        models_error = None

    # **一份写坏的 `run/api.json` 不得让整屏消失**（收尾评审 Important 7）。
    # `snapshot` 对「存在但解析不了」是**抛**的（与「没在跑」处置相反），
    # 而这里和 `_alive("api")`（内部同样调 snapshot）都是裸调 ⇒ 异常一路冒到
    # `entry` 的边界映射变成 `Refused(6)`，`mlx status` 一个字都不打印，
    # 而 `verbs.tsv` 第 4 行给 status 的契约是 rc ∈ {0,2,3} 且「不带目标恒非空」。
    # 上面的模型循环早就这样降级了，`checks.gateway_nets` / `gateway_port` 也是；
    # 本计划新增的三个网关读点只有那两个接了这个模式。
    gw_error = None
    try:
        gw_snap = supervisor.snapshot(ctx, "api") or {}
        gw_running = _alive("api")
    except supervisor.RunRecordUnreadable as exc:
        gw_snap, gw_running, gw_error = {}, None, str(exc)
    gateway = {"running": gw_running, "record_error": gw_error,
               "port": gw_snap.get("port"), "pid": gw_snap.get("pid")}

    ks = _route.gateway_key_state(gw_snap, _cur, getattr(ctx, "gateway_token_env", ""))
    token_state, backend_key_changed = ks["token_state"], ks["backend_key_changed"]
    gateway.update(token_state=token_state, backend_key_changed=backend_key_changed,
                   bind=gw_snap.get("bind"), tls=gw_snap.get("tls"),
                   allowed_count=len(gw_snap.get("allowed_nets") or []) or None)
    restart_needed = [m["id"] for m in (models or []) if m.get("key_changed")]
    if gateway["running"] and (token_state == "changed" or backend_key_changed):
        restart_needed.append("api")

    from .meta import VERSION  # **同一个常量**，不在这里写第二遍版本串

    return Done({
        # 机读消费者据此分派：两种语义形状不同而 `--json` 只有一个（评审 P1-4）
        "kind": "tool",
        # 附录 A 2810 / §4.1.2 1071 / 台账 182 三处都写「三根 · **版本** ·
        # PATH 生效性 · 清单完整性」，且台账把版本写进了空结果契约的最小字段集
        # （「恒非空：三根 + 版本 + PATH 生效性至少四个字段」）
        "version": VERSION,
        "models": models,
        "models_error": models_error,
        "gateway": gateway,
        "roots": roots,
        "roots_exist": {k: os.path.isdir(v) for k, v in roots.items()},
        "path_effective": bin_dir in os.environ.get("PATH", "").split(os.pathsep),
        "bin_dir": bin_dir,
        "manifest": manifest,
        "services_running": supervisor.services_with_record(ctx),
        "restart_needed": restart_needed,
    })


def run(ctx, stack, inv, deps):
    # type: (object, object, Invocation, dict) -> object
    """服务动词的编排。**先按 verb 分派** —— 上一版把 down/status 也走了 up 的路径。"""
    from .. import supervisor

    # **多目标**：附录 A「每个目标一行结论」。与 `--all` 共用同一种汇总形状 ——
    # 两者的差别只在目标域从哪来，结论的形态必须一致。
    if len(getattr(inv, "targets", ()) or ()) > 1 and inv.verb in ("up", "down", "restart"):
        results, worst, committed, failed_codes = _collect(
            ctx, stack, deps, inv.verb, inv.targets,
            args=inv.args, dry_run=inv.dry_run)
        if worst == 0:
            return Done({"kind": "svc", "targets": list(inv.targets), "results": results})
        if not failed_codes:
            # 每个非零子结论都是 `Refused` ⇒ **零资源已创建** ⇒ 汇总也必须是
            # `Refused`（收尾评审 Critical 6）。此前无条件包成 `Failed`，并把
            # committed 写成 `[f"run/{t}.json" for t in inv.targets]` —— 于是
            # `mlx up nosuch1 nosuch2 --dry-run` 让用户去收敛两个从来没存在过的
            # 文件，而 `run/` 是空的；`--dry-run` 更不可能落地任何东西。
            # 同时保住了 `worst`：旧写法的 `else 1` 把 rc 6（model.not_found）
            # 塌成 rc 1，「模型不存在」这个最有用的结论被丢掉。
            # 各子结论都由 `Refused` 构造 ⇒ `worst` 必 ∈ Refused 的码集。
            return Refused(worst, "svc.some_targets_failed", results=results)
        # 有目标真的落地了东西 ⇒ `Failed`，`committed` **逐字取自子结论**。
        # `worst` 可能是某个 `Refused` 的码（如 6），那不在 Failed 的码集里 ——
        # 这时退到真正失败过的那些目标里的最差码，不凭空造一个。
        return Failed(worst if worst in (1, 3, 4, 10) else max(failed_codes),
                      "svc.some_targets_failed", committed, results=results)

    if (inv.args or {}).get("all") and inv.verb in ("status", "down"):
        # `--all` 的目标域 = **run/ 里有记录的全部服务**，不是注册表里的全部模型
        # （没起过的服务没有运行记录，也就无从停止）
        services = supervisor.services_with_record(ctx)
        results, worst, _committed, _failed_codes = _collect(
            ctx, stack, deps, inv.verb, services, dry_run=inv.dry_run)
        if worst == 0:
            return Done({"kind": "svc", "targets": services, "results": results})
        # 有服务没停下来 ⇒ Failed 并**逐个指名**，调用方（卸载器）据此拒绝动盘
        return Failed(4, "svc.some_not_stopped",
                      [f"run/{s}.json" for s in services],
                      results=results)

    if inv.verb == "status" and not inv.target:
        # **不带目标 = 工具自身状态。** 判据（`verbs.tsv` 第 4 行）：必须输出
        # 三根路径与 PATH 生效性 —— 否则「装完了但 mlx 找不到」这个最常见的
        # 首次使用故障，没有任何命令诊断得出来。
        # 包络带 `kind` 供机读消费者分派：两种语义的形状不同，而 `--json` 只有一个。
        return _tool_status(ctx)

    if inv.verb == "status":
        snap = supervisor.snapshot(ctx, inv.target)
        st = supervisor.running(ctx, inv.target)
        return Done({"kind": "service",
                     "service": inv.target,
                     "running": None if is_unknown(st) else st.value,
                     "undeterminable": st.kind if is_unknown(st) else None,
                     "pid": (snap or {}).get("pid"),
                     "port": (snap or {}).get("port"),
                     "backend": (snap or {}).get("backend"),
                     "max_concurrency": (snap or {}).get("max_concurrency"),
                     "rule": (snap or {}).get("rule")})

    # ── `down` / `restart` 的 `--dry-run`〔2026-09-02〕──────────────────
    # 此前这两条**在门之前就动手了**：`down` 在 375 行直接 `supervisor.down()`,
    # `restart` 在 379 行同样。而 398 行那道门在它们之后 —— 顺序上够不到。
    # 实测后果：`mlx down api --dry-run` 真杀掉 pid 31919、释放端口、
    # 删掉 `run/api.json`；`restart --dry-run` 同样。
    # **一个用来「安全预览」的 flag，执行了它声称不会做的事。**
    #
    # 预览的内容就是「会停掉谁」—— 这恰恰是用户敲 `down --all --dry-run`
    # 时想知道的（尤其 `--all`：他要确认不会误停别的服务）。
    if inv.verb in ("down", "restart") and inv.dry_run:
        snap = supervisor.snapshot(ctx, inv.target)
        st = supervisor.running(ctx, inv.target)
        return Done({"kind": "svc_down_dry_run", "verb": inv.verb,
                     "service": inv.target,
                     # `Unknown` 不塌成 False —— 「所有权不可证」时用户更需要
                     # 知道这一点，而不是被告知「没在跑」然后放心执行
                     "running": None if is_unknown(st) else st.value,
                     "undeterminable": st.kind if is_unknown(st) else None,
                     "pid": (snap or {}).get("pid"),
                     "port": (snap or {}).get("port"),
                     "restarts": inv.verb == "restart"})

    if inv.verb == "down":
        return supervisor.down(ctx, inv.target)

    if inv.verb not in ("up", "restart"):
        return Refused(2, "cmd.unknown_verb", verb=inv.verb)

    state = {"ctx": ctx}
    # ── 校验段：**按 VALIDATE_STEPS 逐项 dispatch**，顺序即数据（BCR-R-27）──
    #
    # **`restart` 的停止动作排在整个校验段之后**（收尾评审 Critical 5）。
    # 此前是「先 `supervisor.down()` 再校验」，于是校验段的每一条拒绝都把用户
    # 留在「服务已经被停了、命令却报拒绝」的状态里，而拒绝文案读起来像是
    # 什么都没发生。本计划给 api 路径新增的五条拒绝（`api.token_missing` /
    # `api.tls_incomplete` / `api.tls_unreadable` / `api.tls_key_permissive` /
    # `api.tls_unusable`）每一条都是这个形态 —— 实测：没配 `api.token` 时
    # `mlx restart api` 回 rc 6、网关已死、`run/api.json` 也没了，
    # 而那条文案写的是「没有它网关不启动」。
    # 挪动是安全的：校验段整段**只读**（`_api_checks` 的 docstring 逐字说明
    # 它只读文件系统 + 一次本地 TLS 加载，dry-run 同样会拒、零资源创建），
    # 所以先校验不改变任何判定结果，只改变「拒绝时服务还在不在跑」。
    # 传的是**原始 `inv`**（verb 仍是 restart）—— `_v_target` 的文案参数
    # `cmd=inv.verb` 要说真话。
    for step in VALIDATE_STEPS:
        refusal = VALIDATORS[step](state, inv, deps)
        if refusal is not None:
            return refusal

    if inv.verb == "restart":
        # 校验全过了才动手停。到这一行为止零资源创建、零外部效应。
        out = supervisor.down(ctx, inv.target)
        if out.rc not in (0,):
            return out
        # **`args` 必须带过去**（收尾评审 Important 8）：此前这里重造
        # `Invocation("up", inv.target, dry_run=inv.dry_run)`，把 `inv.args`
        # 整个丢掉，而 `cli.tsv:32` 把 `--ignore-fitness` 登记给了 `up,restart`
        # ⇒ 那个 flag 在 restart 上是死的，执行段的容量闸门照样 `fit.over`，
        # 而 `fit.over` 的文案还让用户去加他已经加过的这个 flag。
        inv = Invocation("up", inv.target, args=inv.args, dry_run=inv.dry_run)
    spec = state["spec"]

    # `--dry-run` 在 stage_split **之前**返回 —— 这是 BCR-4-08 的答案（格 1）：
    # 执行段整段不跑，外部命令计数自然 == 0（DE-T-018），
    # 而 caps.drift 仍留在执行段（BCR-R-27），两条同真。
    if inv.dry_run:
        from .. import schema as _schema
        pay = {"argv": spec.argv, "sources": spec.sources,
               "env": {k: (_schema.MASK if k in spec.secret_env and val else val)
                       for k, val in sorted(spec.env.items())},
               "dropped": state.get("dropped") or []}
        if state.get("route") is not None:
            pay["route"] = state["route"].to_dict()
            pay["ready_timeout_sec"] = ready_timeout(state["route"])
        return Done(pay)

    enter_execute()                                  # ── stage_split ──

    # ── 执行段（这里才允许碰外部命令；此后只能 Failed 且带 committed）──
    from .. import supervisor

    already = supervisor.running(ctx, inv.target)
    if is_unknown(already):
        return Failed(4, "owns.undeterminable", [], kind=already.kind)
    if already.value is True:
        return Done({"already_running": inv.target})

    drift = deps.get("caps_drift")
    # **网关不查 venv 漂移**：它跑在下限解释器上、零依赖（标准库 HTTP 服务器），
    # venv 里有什么与它能不能起来无关。查了反而让「venv 没装」挡住网关。
    if drift is not None and inv.target != "api":
        d = drift()
        if hasattr(d, "is_unknown") and d.is_unknown():
            if d.kind == "NOT_COLLECTED":
                # 可操作：**零资源已创建**，用 Refused 并指名下一步命令
                return Refused(6, "caps.not_collected")
            # **`Refused(9)` 而不是 `Failed(4)`**。契约逐字：「`is_unknown(caps.drift())`
            # ⇒ `Refused(9, caps.drift.undeterminable)`，**零资源**」。
            # 两处都错：① 此刻确实零资源已创建，而 `Failed` 要求带 `committed`，
            # 传空列表是自相矛盾的形态；② rc 4 在词汇表里是「有界等待走完仍未达成
            # （就绪/终止确认）」，用它承载「判定不确定」会让脚本走错分支 ——
            # 用户看到的是「停止未达成」，而实际什么都没启动也没停止。
            return Refused(9, "caps.drift.undeterminable", kind=d.kind)
        if (d.value if hasattr(d, "value") else d):
            # 同理：漂移是**确定的**结论且零资源 ⇒ `Refused(3)`。
            return Refused(3, "caps.drift.detected")

    # ── 容量判档 ────────────────────────────────────────────────
    # `EXECUTE_STEPS` 里一直写着这一步，而它**没有任何读者** ——
    # `run()` 只按 `VALIDATE_STEPS` dispatch。后果：`mlx up <28GB 模型>`
    # 在只剩 8 GB 的机器上照起不误，起完开始换页、机器卡死。
    # 被保护的对象逐字是「不要在容量不明时启动一个装不下的模型」，
    # 所以 `Unknown` 也拒绝（rc 9），并指名是哪个输入不确定。
    avail = deps.get("avail_mem_mb")
    if avail is not None and inv.target != "api":
        verdict = _fit_verdict(ctx, inv.target, deps, state["cfg"])
        if is_unknown(verdict):
            return Refused(9, "fit.undeterminable", detail=verdict.reason)
        if verdict.value == fitness.OVER and not (inv.args or {}).get("ignore_fitness"):
            return Refused(6, "fit.over", id=inv.target)

    api_extra = None
    if inv.target == "api":
        # ── 端口上的外来监听者（spec D24）──
        # 此刻网关确知没在跑（上面 `already` 已判），端口上的任何监听者都是别人的。
        # 放在执行段而非校验段：它要调 lsof，而 dry-run 的外部命令计数必须为 0（DE-T-018）。
        foreign = deps.get("foreign_listeners")
        if foreign is not None:
            fl = foreign(spec_port(spec))
            if is_unknown(fl):
                return Refused(9, "ports.probe_undeterminable", port=spec_port(spec), kind=fl.kind)
            if fl.value:
                return Refused(7, "ports.foreign_listener", port=spec_port(spec),
                               pids=",".join(str(p) for p in fl.value))
        # ── 网关解释器（spec D20）──
        pick = deps.get("gateway_interpreter")
        if pick is not None:
            from .. import backendarg
            interp = pick()
            spec = backendarg.with_interpreter(spec, interp["path"])
            v = state["cfg"].values
            api_extra = {"bind": v.get("api.bind") or "0.0.0.0",
                         "tls": bool(v.get("api.tls_cert")),
                         "interpreter": interp["path"],
                         "interpreter_source": interp["source"],
                         "interpreter_version": interp["version"],
                         "tls_max": interp["tls_max"]}

    spawn = deps.get("spawn")
    if spawn is None:
        return Failed(1, "svc.no_spawner", [],
                      detail="执行段需要注入 spawn —— 产品不自带进程启动器实现")

    nonce = deps.get("nonce", lambda: "n0")()
    alias = supervisor.materialize(ctx, spec, nonce,
                                   target=state.get("weights_path"),
                                   draft_target=state.get("draft_path"))
    h = stack.push(alias.close) if stack else None
    started = spawn(spec, alias.path)
    if started is None:
        if h is not None and stack:
            stack.run(h)
        return Failed(1, "svc.spawn_failed", [f"run/alias/{spec.service}/{nonce}"],
                      service=spec.service)
    port = spec_port(spec)
    supervisor.write_record(ctx, spec, started["pid"], started["pgid"],
                            port, started["started_at"], nonce, extra=api_extra)

    # **就绪等待**（T3 第 1 行）。没有这一步时，spawn 成功就报 Done ——
    # 而进程可能在三秒后因端口被占而退出，用户拿到 rc=0 和「started」，
    # 下一条 `mlx status` 却说「未运行」。报成功但没产生效果。
    wait = deps.get("wait_ready")
    if wait is not None:
        rt = state.get("route")
        timeout = ready_timeout(rt)
        if rt is not None and rt.row.get("ready") == "load":
            # 加载完才监听的后端：用户会等上一两分钟，先说清楚在等什么
            notice = deps.get("notice")
            if notice is not None:
                from ..tables import open_tables
                tvn = open_tables(ctx)
                notice(tvn.text(tvn.msg("svc.loading_notice"), backend=rt.backend,
                                gb=round((rt.size_mb or 0) / 1024.0, 1), sec=timeout))
        verdict = wait(spec, port, timeout)
        if verdict is not None:
            reason, tail = verdict
            rc, key = (10, "svc.died_before_ready") if reason == "dead" \
                else (4, "svc.ready_timeout")
            detail = tail or "（日志为空）"
            if reason != "dead" and rt is not None and rt.row.get("ready") == "load":
                # spec §3.3：加载完才监听的后端，超时多半是还没加载完 —— 要说出来，
                # 否则用户看到「就绪超时」会以为起失败了而去重复启动
                from ..tables import open_tables
                tvh = open_tables(ctx)
                detail += "\n" + tvh.text(tvh.msg("svc.still_loading_hint"),
                                          sec=timeout, service=spec.service)
            return Failed(rc, key, [f"run/{spec.service}.json"],
                          service=spec.service, port=port, detail=detail)
    rt = state.get("route")
    extra_pay = {}
    if rt is not None:
        # **`rule` 保留**：brief 给的替换代码在这里漏了它 —— `render_svc._svc_change`
        # 的 R2「串行」判据读的正是这个字段（`p.get("rule") == "R2"`），漏掉它会让
        # 真实（非 --dry-run）`mlx up <R2 模型>` 的成功行显示「并发 N」而不是「串行」，
        # 与 dry-run 路径（`pay["route"] = state["route"].to_dict()`）不一致。
        extra_pay = {"backend": rt.backend, "max_concurrency": rt.max_concurrency,
                    "rule": rt.rule}
    elif api_extra:
        extra_pay = {k: api_extra[k] for k in ("bind", "tls", "interpreter")}
    return Done(dict({"kind": "svc", "started": inv.target,
                      "pid": started["pid"], "port": port}, **extra_pay))


def spec_port(spec):
    """取服务端口。argv 是唯一产出点，端口不另存一份。

    两种形态：模型服务经 `--port <n>` 传，网关经 `env["MLX_GATEWAY_PORT"]` 传
    （它是 `-m gateway` 的入口，没有 argv 参数面）。**都从 spec 读，不另存。**
    """
    argv = list(spec.argv)
    if "--port" in argv:
        return int(argv[argv.index("--port") + 1])
    env_port = (spec.env or {}).get("MLX_GATEWAY_PORT")
    return int(env_port) if env_port else None
