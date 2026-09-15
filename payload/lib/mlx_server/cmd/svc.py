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
    """逐目标跑一遍，收集**带文案参数**的结论。返回 `(results, worst_rc)`。

    两条批量路径（显式多目标 与 `--all`）共用它。此前各写一份几乎相同的循环，
    于是给结论补 `args` 时只补了一处 —— `mlx down --all` 的失败行仍旧渲染成
    笼统的「失败」，而显式多目标那条已经能打出「进程所有权不可证（PS_FAILED）」。
    同一逻辑两份实现必然漂移，这是本轮第三次遇到同一形态。
    """
    results, worst = {}, 0
    for t in targets:
        one = run(ctx, stack, Invocation(verb, t, args=args, dry_run=dry_run), deps)
        results[t] = getattr(one, "payload", None) or {
            "rc": one.rc, "key": getattr(one, "msg_key", None),
            "args": dict(getattr(one, "args", {}) or {})}
        worst = max(worst, one.rc)
    return results, worst


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
    dropped = sorted(k for k in settings if k not in defs)
    machine = {k: val for k, val in settings.items()
               if k in defs and defs[k]["scope"] == "user"
               and defs[k]["level"] in ("global", "both")
               and not (inv.target != "api" and k == "port")}
    layers = {}
    if inv.target == "api":
        layers["cli"] = {"port": ctx.gateway_port}
    else:
        rec = state["record"]
        port = store.model_port(rec)
        if port is not None:
            machine["port"] = int(port)
        user = {}
        for k in list(rec.user):
            d = defs.get(k)
            if d is None:
                if k not in ("repo", "type"):       # 非配置字段（repo）与旧字段（type）
                    dropped.append(k)
                continue
            if d["scope"] == "user" and d["level"] in ("model", "both"):
                user[k] = dict.get(rec.user, k)
        if user:
            layers["user"] = user
    if machine:
        layers["machine"] = machine
    venv_py = os.path.join(ctx.venv, "bin", "python3") if getattr(ctx, "venv", "") else ""
    if venv_py and not os.path.exists(venv_py):
        venv_py = ""                           # venv 没建成 ⇒ 回落（§0.4.2）
    try:
        cfg = schema.effective(inv.target, layers, venv_python=venv_py,
                               defaults=schema.defaults_from(tv))
    except schema.ConfigError as exc:
        return Refused(6, "config.invalid", key=exc.key, kind=str(exc))
    for k, val in cfg.values.items():
        if cfg.sources.get(k) == "default":
            continue
        bad = schema.check_domain(k, val, defs)
        if bad:
            return Refused(6, "config.invalid", key=k, kind=bad)
    state.update(cfg=cfg, tables=tv, dropped=sorted(set(dropped)))
    return None


def _size_mb(rec, path):
    # type: (object, str) -> object
    """登记的体积优先；缺失（register 从不写、flat 布局 repair 填不上）就现场量（spec P3）。"""
    v = rec.machine.get("size_mb") if rec is not None else None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return float(v)
    s = weights.size(path)
    return None if is_unknown(s) else round(s.value / (1024 * 1024), 1)


def _v_backend(state, inv, deps):
    """校验段步 5 · 后端判定（spec §2.3–§2.4）。**采证在这里，裁决在 `route`**。"""
    if inv.target == "api":
        return None
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
    template = None
    # 模板只影响走模板开关的后端（mlx_vlm）；纯文本不必付 5 s 上限的子进程代价
    if draft is None and route.effective_vision(facts.value.get("vision"),
                                                cfg.values.get("modality")):
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
                   "repo": user.get("repo"), "type": user.get("type"),
                   "revision": machine.get("revision"),
                   "size_mb": machine.get("size_mb"),
                   "pid": snap.get("pid")}
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

    gw_snap = supervisor.snapshot(ctx, "api") or {}
    gateway = {"running": _alive("api"),
               "port": gw_snap.get("port"), "pid": gw_snap.get("pid")}

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
    })


def run(ctx, stack, inv, deps):
    # type: (object, object, Invocation, dict) -> object
    """服务动词的编排。**先按 verb 分派** —— 上一版把 down/status 也走了 up 的路径。"""
    from .. import supervisor

    # **多目标**：附录 A「每个目标一行结论」。与 `--all` 共用同一种汇总形状 ——
    # 两者的差别只在目标域从哪来，结论的形态必须一致。
    if len(getattr(inv, "targets", ()) or ()) > 1 and inv.verb in ("up", "down", "restart"):
        results, worst = _collect(ctx, stack, deps, inv.verb, inv.targets,
                                  args=inv.args, dry_run=inv.dry_run)
        if worst == 0:
            return Done({"kind": "svc", "targets": list(inv.targets), "results": results})
        return Failed(worst if worst in (1, 3, 4, 10) else 1,
                      "svc.some_targets_failed",
                      [f"run/{t}.json" for t in inv.targets], results=results)

    if (inv.args or {}).get("all") and inv.verb in ("status", "down"):
        # `--all` 的目标域 = **run/ 里有记录的全部服务**，不是注册表里的全部模型
        # （没起过的服务没有运行记录，也就无从停止）
        services = supervisor.services_with_record(ctx)
        results, worst = _collect(ctx, stack, deps, inv.verb, services)
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
                     "port": (snap or {}).get("port")})

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

    if inv.verb == "restart":
        out = supervisor.down(ctx, inv.target)
        if out.rc not in (0,):
            return out
        inv = Invocation("up", inv.target, dry_run=inv.dry_run)

    if inv.verb not in ("up", "restart"):
        return Refused(2, "cmd.unknown_verb", verb=inv.verb)

    state = {"ctx": ctx}
    # ── 校验段：**按 VALIDATE_STEPS 逐项 dispatch**，顺序即数据（BCR-R-27）──
    for step in VALIDATE_STEPS:
        refusal = VALIDATORS[step](state, inv, deps)
        if refusal is not None:
            return refusal
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
                            port, started["started_at"], nonce)

    # **就绪等待**（T3 第 1 行）。没有这一步时，spawn 成功就报 Done ——
    # 而进程可能在三秒后因端口被占而退出，用户拿到 rc=0 和「started」，
    # 下一条 `mlx status` 却说「未运行」。报成功但没产生效果。
    wait = deps.get("wait_ready")
    if wait is not None:
        rt = state.get("route")
        timeout = ready_timeout(rt)
        if rt is not None and rt.row.get("ready") == "load":
            # 加载完才监听的后端：用户会等上一两分钟，先说清楚在等什么
            from ..tables import open_tables
            tvn = open_tables(ctx)
            deps["notice"](tvn.text(tvn.msg("svc.loading_notice"), backend=rt.backend,
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
    return Done(dict({"kind": "svc", "started": inv.target,
                      "pid": started["pid"], "port": port},
                     **({"backend": rt.backend, "max_concurrency": rt.max_concurrency}
                        if rt is not None else {})))


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
