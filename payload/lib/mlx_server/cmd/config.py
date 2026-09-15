"""D · `get` / `set` / `unset` / `ls` / `diff` / `validate` / `export` / `import`。"""
from .. import schema, store
from ..result import Aggregate, Done, Refused


def _coerce(key, val, defs):
    # type: (str, object, dict) -> tuple
    """`schema.coerce` 的不抛版本 → `(归一后的值, 错误说明 | None)`。

    `set` 分支据此构造 `Refused`，与值域校验同层：`L0·result-02` 守卫按 AST 遍历序比较行号，
    放在 `except` 里的 `Refused` 会被误判为「在 commit 之后构造」。
    """
    try:
        return schema.coerce(key, val, defs), None
    except schema.ConfigError as exc:
        return val, str(exc)


def _defs(ctx, deps):
    # type: (object, dict) -> dict
    """键定义（十一列）。**来自 `schema.tsv`** —— 表缺失时回落硬编码默认值，
    那让 `config` 在表坏掉时仍能报出「哪个键」而不是整条命令崩。"""
    tables = (deps or {}).get("tables")
    if tables is None:
        from ..tables import open_tables
        try:
            tables = open_tables(ctx)
        except Exception:                              # noqa: BLE001
            return {}
    try:
        return schema.keydefs(tables)
    except Exception:                                  # noqa: BLE001
        return {}


def _domain(ctx, deps):
    """本版认得的配置键集合。表在就用表，否则回落 `schema.DEFAULTS`。"""
    defs = _defs(ctx, deps)
    return set(defs) if defs else set(schema.DEFAULTS)


def _factory(ctx, deps):
    # type: (object, dict) -> dict
    """出厂默认值，**来自 `schema.tsv`**。表读不到时回落硬编码兜底。

    `schema.effective` 是纯函数、不自己读表，所以每个调用点都要注入这个 —— 
    不注入的话它只认硬编码的三个键，表里新加的键会被判「未知配置键」。
    """
    defs = _defs(ctx, deps)
    if not defs:
        return dict(schema.DEFAULTS)
    out = {}
    for k, d in defs.items():
        v = d["default"]
        if v == "-":
            continue                     # machine 段字段没有出厂值
        out[k] = int(v) if d["type"] == "int" else v
    return out


def _layers(ctx, target, deps=None):
    # type: (object, str) -> dict
    """三级链的两个中间层。**读侧此前恒为空** —— `inv.args["layers"]` 这个键
    全项目没有任何地方写过，于是 `schema.effective` 永远只返回出厂默认值，
    `config get` 的「来源」列恒为 `default`，用户设什么都读不回来。

    层序（低→高）：`DEFAULTS < machine < user < cli`。这里产出：
      · `machine` ← `settings.json`（全局设置）
      · `user`    ← 目标模型记录的 user 段（per-model 覆盖全局，`02a:575`）
    只取配置域里有的键 —— 记录的 user 段还装着 `repo` / `type`，
    它们不是配置键，混进去会让 `effective` 抛未知键。
    """
    # **筛键用的是配置域（来自 `schema.tsv`），不是硬编码的 DEFAULTS**。
    # 用后者的话，表里新加的键（`draft_model` / `fit.*` / `api.bind`）会在这里
    # 被静默滤掉 —— `config set` 写进去了，回读却说「来自 default」。
    # 这是「建了表却没让求值器用表」的第二处，与 `effective` 那处同源。
    domain = _domain(ctx, deps)
    out = {}
    glob = {k: v for k, v in store.load_settings(ctx).items() if k in domain}
    if glob:
        out["machine"] = glob
    if target:
        rec = store.load(ctx, store.MODELS).by_id(target)
        if rec is not None:
            per = {k: v for k, v in rec.user.items() if k in domain}
            if per:
                out["user"] = per
    return out


def run(ctx, stack, inv, deps=None):
    layers = (inv.args or {}).get("layers")
    if layers is None:
        # 目标是键名（`config set port ...`）还是模型 id（`config set qwen ...`）——
        # 用**配置域**判，不用硬编码 DEFAULTS（表里新加的键会被误判成模型 id）
        _dom = _domain(ctx, deps)
        layers = _layers(ctx, inv.target if inv.target not in _dom else None, deps)
    if inv.verb == "ls":
        cfg = schema.effective(inv.target or "api", layers, defaults=_factory(ctx, deps))
        return Done({"kind": "config_ls", "values": schema.masked(cfg.values, _defs(ctx, deps)),
                     "sources": cfg.sources})
    if inv.verb == "get":
        key = (inv.args or {}).get("key")
        defs = _defs(ctx, deps)
        if key not in _domain(ctx, deps):
            return Refused(6, "config.unknown_key", key=key)
        # **与 `set` 给同一种说法**。`revision` / `size_mb` 登记在表里但
        # scope=machine —— 它们不在配置域内，`get` 报「未知配置键」而
        # `set` 报「不可写（scope=machine）」的话，同一个字段两条命令
        # 两种说法，用户会以为自己拼错了。
        if defs and not schema.writable(key, defs):
            return Refused(6, "config.not_a_config_key", key=key,
                           scope=(defs.get(key) or {}).get("scope", "?"))
        cfg = schema.effective(inv.target or "api", layers, defaults=_factory(ctx, deps))
        value = cfg.values[key]
        # `--reveal` 是查看密钥原值的**唯一**入口（spec §5.2）
        if not (inv.args or {}).get("reveal"):
            value = schema.masked({key: value}, defs)[key]
        return Done({"kind": "config_get", "key": key,
                     "value": value, "source": cfg.sources[key]})
    if inv.verb == "validate":
        # 附录 A 2827：主产物「每检查项一行 name<TAB>verdict<TAB>detail」，
        # 退出码 `0,2,6,64,65`（与 `check` 共用 64/65 那一对聚合码）。
        # **不短路**：一个键坏了要继续走完剩下的，否则「行数 == 检查项数」
        # 不可能成立（R-OBSERVE-31 的验收）。
        rows, bad = [], []
        try:
            cfg = schema.effective(inv.target or "api", layers, defaults=_factory(ctx, deps))
            err = None
        except schema.ConfigError as e:
            cfg, err = None, f"{e.kind}：{e.key}"
        except Exception as e:                         # noqa: BLE001
            cfg, err = None, str(e)

        for key in sorted(_domain(ctx, deps)):
            if cfg is None:
                # 合成整体失败时，每一项都要有自己的一行（**不短路**），
                # 否则「行数 == 检查项数」不可能成立
                rows.append((key, "failed", err))
                bad.append(key)
                continue
            val, src = cfg.values.get(key), cfg.sources.get(key, "?")
            val = schema.masked({key: val}, _defs(ctx, deps))[key]
            rows.append((key, "ok", f"{val}（来自 {src}）"))
        payload = {"kind": "checks", "rows": rows, "failed": bad,
                   "undeterminable": []}
        if bad:
            return Aggregate(65, payload, "config.validate_failed")
        return Done(payload)
    if inv.verb == "diff":
        # 附录 A：「只列出与默认值不同的键 —— 一眼看出你改过什么」
        _fac = _factory(ctx, deps)
        cfg = schema.effective(inv.target or "api", layers, defaults=_fac)
        defs = _defs(ctx, deps)
        rows = []
        for key in sorted(cfg.values):
            src = cfg.sources.get(key, "default")
            if src == "default":
                continue
            # 出厂值取自表；表里没有该键时回落 —— `_factory` 已经算好了这份
            # 兜底，从它取而不是再碰一次 `schema.DEFAULTS`（那读起来像
            # 「拿硬编码当配置域」，守卫也分不出来）。
            factory = (defs.get(key) or {}).get("default", _fac.get(key))
            rows.append({"key": key, "value": schema.masked({key: cfg.values[key]}, defs)[key],
                         "source": src, "factory": factory})
        return Done({"kind": "config_diff", "rows": rows,
                     "target": inv.target or "（全局）"})

    # `import` 与 `export` 排在 `unset` **之前**：它们的 Refused 全是校验，
    # 而 `unset` 是本模块除 `set` 外唯一做 commit 的分支 —— `L0·result-02`
    # 的判据是 AST 行号（构造点行号 > 资源创建行号即红），顺序反了会当场被抓。
    if inv.verb == "import":
        # **不假装原子**（`02-architecture.draft.md:992`）：预览 + 逐条报告
        # 哪些事务已提交。跨文件事务做不到一次全成，谎称原子比逐条报告更坏。
        src = (inv.args or {}).get("from")
        if not src:
            return Refused(2, "config.import_needs_from")
        try:
            import json as _json
            with open(src, encoding="utf-8") as fh:
                incoming = _json.loads(fh.read())
        except OSError as exc:
            return Refused(6, "config.import_unreadable", path=src, detail=str(exc))
        except ValueError as exc:
            return Refused(6, "config.import_bad_json", path=src, detail=str(exc))
        if not isinstance(incoming, dict):
            return Refused(6, "config.import_bad_json", path=src,
                           detail="顶层必须是对象")
        defs = _defs(ctx, deps)
        domain = _domain(ctx, deps)
        plan, rejected = [], []
        for key in sorted(incoming):
            if key not in domain:
                rejected.append({"key": key, "why": "未知键"})
            elif defs and not schema.writable(key, defs):
                rejected.append({"key": key, "why": "不可写（scope != user）"})
            else:
                try:
                    v = schema.coerce(key, incoming[key], defs)
                except schema.ConfigError as exc:
                    rejected.append({"key": key, "why": str(exc)})
                    continue
                bad = schema.check_domain(key, v, defs) if defs else None
                if bad:
                    rejected.append({"key": key, "why": f"值非法（{bad}）"})
                else:
                    plan.append(key)
        if inv.dry_run:
            return Done({"kind": "config_import", "planned": plan,
                         "rejected": rejected, "applied": [], "dry_run": True})
        applied, failed = [], []
        for key in plan:
            try:
                val = schema.coerce(key, incoming[key], defs)
                store.commit(ctx, store.SETTINGS,
                             store.SettingsTx({key: val}), stack)
                applied.append(key)
            except Exception as exc:                   # noqa: BLE001
                # **逐条报告**：这里不是回滚点，前面已提交的保持提交
                failed.append({"key": key, "why": str(exc)[:80]})
        payload = {"kind": "config_import", "planned": plan, "applied": applied,
                   "rejected": rejected, "failed": failed}
        if failed:
            return Aggregate(65, payload, "config.import_partial")
        return Done(payload)

    if inv.verb == "unset":
        key = (inv.args or {}).get("key")
        defs = _defs(ctx, deps)
        if key not in _domain(ctx, deps):
            return Refused(6, "config.unknown_key", key=key)
        # **可写域 == scope==user**（`P-SCH-15`）。unset 与 set 同一道闸门：
        # 能写才能撤销，否则「撤销一个你本来就写不了的键」是无意义的成功。
        if defs and not schema.writable(key, defs):
            return Refused(6, "config.not_writable", key=key,
                           scope=(defs.get(key) or {}).get("scope", "?"))
        # 目标存在性**前置到任何 commit 之前**（`L0·result-02` 的 AST 行号判据）
        per_model = bool(inv.target) and inv.target != key
        rec = store.load(ctx, store.MODELS).by_id(inv.target) if per_model else None
        if per_model and rec is None:
            return Refused(6, "model.not_found", id=inv.target)
        if per_model:
            if key not in rec.user:
                return Done({"kind": "config_unset", "unset": key,
                             "scope": inv.target, "was_set": False})
            store.commit(ctx, store.MODELS,
                         store.ModelsTx(inv.target, {}, unset_user=(key,)), stack)
            scope = inv.target
        else:
            if key not in store.load_settings(ctx):
                return Done({"kind": "config_unset", "unset": key,
                             "scope": "global", "was_set": False})
            store.commit(ctx, store.SETTINGS, store.SettingsTx({}, unset=(key,)), stack)
            scope = "global"
        eff = schema.effective(
            inv.target, _layers(ctx, None if scope == "global" else inv.target, deps),
            defaults=_factory(ctx, deps))
        return Done({"kind": "config_unset", "unset": key, "scope": scope,
                     "was_set": True, "now": schema.masked({key: eff.values.get(key)}, defs)[key],
                     "source": eff.sources.get(key)})

    if inv.verb == "export":
        # **肯定白名单**（`02:1431`）：`exportable == yes` 且 `secret == no`。
        # 两个条件缺一不可 —— 机密即使标了 exportable 也不导出。
        cfg = schema.effective(inv.target or "api", layers, defaults=_factory(ctx, deps))
        defs = _defs(ctx, deps)
        out, skipped = {}, []
        for key in sorted(cfg.values):
            if defs and not schema.exportable(key, defs):
                skipped.append(key)
                continue
            if cfg.sources.get(key) == "default":
                continue          # 只导出**改过的**：出厂值换台机器仍是出厂值
            out[key] = cfg.values[key]
        return Done({"kind": "config_export", "values": out,
                     "skipped": skipped, "target": inv.target or "（全局）"})

    if inv.verb != "set":
        return Refused(2, "cmd.unknown_verb", verb=inv.verb)

    # `set` 排在最后：它是本模块**唯一**做 commit 的分支，
    # 这样所有 Refused 构造点都在资源创建之前（`L0·result-02` 的 AST 判据）。
    key, val = (inv.args or {}).get("key"), (inv.args or {}).get("value")
    defs = _defs(ctx, deps)
    if key not in _domain(ctx, deps):
        return Refused(6, "config.unknown_key", key=key)
    # **可写域 == scope==user**（`P-SCH-15` 是双向差集）。这道闸门要拦的
    # 正是设计点名的 `mlx config set 27b revision abc` —— `revision` 是磁盘
    # 事实（由 `weights` 产出），不是配置。它**登记在表里但 scope=machine**，
    # 这样报出的原因是「不可写」而不是「未知键」——后者会让用户困惑，
    # 因为他明明在 `model info` 里见过这个字段。
    if defs and not schema.writable(key, defs):
        return Refused(6, "config.not_writable", key=key,
                       scope=(defs.get(key) or {}).get("scope", "?"))
    if val is None:
        # **必填校验**。解析层的缺值检查只管「flag 写了但没给值」，
        # 管不到「flag 完全没写」。此前 `mlx config set backend` 返回 rc 0
        # 并往注册表插了一条 id 为 `backend` 的幽灵模型
        # （`machine.backend = null`），之后 `mlx model gc` 会在
        # `sorted()` 上撞 None 抛 TypeError、rc 1。
        return Refused(2, "config.value_required", key=key)
    # **写到哪**由目标决定，两条路都不再碰 machine 段：
    #   · 目标就是键名（`mlx config set port --value=…`）⇒ 全局层 settings.json
    #   · 目标是模型 id（`mlx config set demo --key=port --value=…`）⇒ 该模型 user 段
    # 此前无论哪种都写 `models.jsonl` 的 machine 段：前者在注册表里造出一个
    # 叫 `port` 的幽灵模型，后者绕过端口唯一性强制（那道检查只看 `tx.port`）。
    val, bad = _coerce(key, val, defs)    # 唯一归一点，见 schema.coerce 的理由
    if bad:
        return Refused(6, "config.invalid", key=key, kind=bad)
    # **值域校验在任何 commit 之前**（spec §2.2 单点；`L0·result-02` 的行号判据）
    bad = schema.check_domain(key, val, defs) if defs else None
    if bad:
        return Refused(6, "config.invalid", key=key, kind=bad)
    # **目标存在性前置到任何 commit 之前**。放在 else 分支里虽然语义上也是
    # 「零落地」，但 `L0·result-02` 的判据是 AST 行号（构造点 > 资源创建点即红），
    # 守卫宁可误红也不能漏 —— 前置之后判据过、语义也对。
    per_model = inv.target != key
    if per_model and store.load(ctx, store.MODELS).by_id(inv.target) is None:
        return Refused(6, "model.not_found", id=inv.target)
    if not per_model:
        store.commit(ctx, store.SETTINGS, store.SettingsTx({key: val}), stack)
        scope = "global"
    else:
        store.commit(ctx, store.MODELS,
                     store.ModelsTx(inv.target, {key: val}, user_keys=(key,)), stack)
        scope = inv.target
    # 效果读路径（INV-25）：用**登记的那个读函数**回读，报的是真生效值
    eff = schema.effective(inv.target, _layers(ctx, None if scope == "global" else inv.target, deps), defaults=_factory(ctx, deps))
    return Done({"kind": "config_set", "set": key, "value": schema.masked({key: eff.values[key]}, defs)[key],
                 "scope": scope, "source": eff.sources[key]})
