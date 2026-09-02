"""D · `install` / `register` / … —— **两段式提交**的事务形状。

`stage_split == 提交点①`（`BCR-R-01`）：之前构造 `Refused`（零资源），
之后只能构造 `Failed` 并带 `committed`。
"""
from .. import store, weights
from ..result import Done, Failed, Refused, is_unknown

TYPE_EVIDENCE = ("CLI_EXPLICIT", "CONFIG_ARCH", "FILE_PATTERN", "ASKED")


def decide_type(meta, files, cli_type, ask):
    # type: (dict, list, object, object) -> tuple
    """`user.type` 的四级判定链（裁决 R2-2）。**按序短路，不设默认值第五级**。

    猜错的 type 会让 backendarg 产出错误 argv ⇒ 后端起得来但推理全错且 rc 0，
    比起不来更难发现。
    """
    if cli_type:
        return cli_type, "CLI_EXPLICIT"
    arch = (meta or {}).get("architectures") or []
    if any("vision" in str(a).lower() for a in arch):
        return "vision", "CONFIG_ARCH"
    if arch:
        return "text", "CONFIG_ARCH"
    names = {f.lower() for f in (files or [])}
    if any(n.endswith(".safetensors") for n in names):
        if any("vision" in n or "image" in n for n in names):
            return "vision", "FILE_PATTERN"
        return "text", "FILE_PATTERN"
    if ask is None:
        return None, None
    answer = ask("model.ask_type")
    return (answer, "ASKED") if answer else (None, None)


def run(ctx, stack, inv, deps=None):
    deps = deps or {}
    if inv.verb not in ("install", "register"):
        return Refused(2, "cmd.unknown_verb", verb=inv.verb)

    # ── 校验段（零资源，可 Refused）──
    repo = (inv.args or {}).get("repo") or inv.target
    # `register` 是「登记一个模型」，**不要求权重已在本地** ——
    # 它的用途正是先登记、后下载。解析失败只记进 machine 段，不拒绝。
    r = weights.resolve(ctx, repo, (inv.args or {}).get("revision"))
    resolved = r.value if not is_unknown(r) else None

    t, evidence = decide_type((inv.args or {}).get("meta"),
                              (inv.args or {}).get("files"),
                              (inv.args or {}).get("type"),
                              deps.get("ask"))
    if t is None:
        return Refused(9, "model.type_undeterminable",
                       detail="四级判定链全不确定且未取得回答 —— 不落默认值")

    # **端口归一成 int**。CLI 给的是字符串，落盘后
    # `ports.conflict_check` 的 `machine["port"] == ctx.gateway_port` 恒为假，
    # 网关端口互斥这道检查从不命中（`mlx check` 的 ports 项也因此恒绿）。
    from .. import schema as _schema
    port = _schema.coerce("port", (inv.args or {}).get("port"))

    # 唯一性与 type 域校验**前置到校验段**（纯读，零资源）——
    # 放在 commit 的 except 里虽然语义上也是「零落地」，但那让
    # `L0·result-02` 的 AST 判据（构造点行号 > 资源创建行号）无法区分，
    # 而守卫宁可误红也不能漏。前置之后两者都成立：判据过、语义也对。
    types = deps.get("types")
    if callable(types):
        types = types()          # 惰性：只有真要校验类型时才读表
    if types and t not in set(types):
        return Refused(6, "model.type_domain", type=t, allowed=sorted(types))
    existing = store.load(ctx, store.MODELS)
    # **网关端口也在登记面上**（它是 §6 登记的单例槽位）。此前唯一性只比模型之间，
    # 于是登记在网关端口上的模型要等到 `mlx up` 才被拦下 —— 而那时用户已经
    # 忘了自己填过什么。文案要指名「这是网关的端口」：复用 `model.uniqueness`
    # 会说被某个模型占了，用户去 `model ls` 里怎么也找不到那个占用者。
    if port is not None and int(port) == int(ctx.gateway_port):
        return Refused(7, "ports.gateway_conflict", port=port)
    if port is not None:
        for r in existing.records:
            if r.id != inv.target and r.machine.get("port") == port:
                return Refused(7, "model.uniqueness", port=port, holder=r.id)

    # **覆盖要说出来**。`upsert` 是设计行为，但此前重复登记同一个 id
    # 输出与首次登记逐字节相同 —— 旧的 repo 与端口无声消失，
    # 用户以为自己新增了一个模型，实际是改掉了一个。
    prev = existing.by_id(inv.target)
    replaced = None
    if prev is not None:
        old_repo, old_port = prev.user.get("repo"), store.model_port(prev)
        if old_repo != repo or (port is not None and old_port != port):
            replaced = {"repo": old_repo, "port": old_port}

    patch = {"repo": repo, "type": t, "type_evidence": evidence}
    if resolved:
        patch["revision"] = resolved["revision"]
        patch["layout"] = resolved["layout"]
    tx = store.ModelsTx(inv.target, patch, port=port)

    # ── `--dry-run` 在**提交点①之前**返回（`DE-T-035`，`04-interfaces.md:3386`）──
    # 判据逐字：「`install --dry-run`：`commit_user_placeholder()` **未被调用**
    # （计数 0）∧ 全轮沙箱 diff == ∅」，坏样本逐字：「dry-run 只跳过下载
    # 不跳过提交点① ⇒ 注册表里多出一条幽灵记录」。
    #
    # **实现此前一个字都没读过 `inv.dry_run`**（该文件里出现 0 次），
    # 于是坏样本就是实际行为：2026-09-02 实测 `model install --dry-run`
    # 在 `models.jsonl` 里留下 `{"id": "qwen38", ...}` 后返回 rc 3。
    # 判据写在设计里、坏样本连症状都点名了，代码零实现、守卫零实现 ——
    # 三者同时缺席，所以没有任何东西会红。
    #
    # 位置必须在 commit **之前**：`BCR-R-01` 要求 `Refused` 的前提是零资源
    # 已创建，放在 commit 之后会被 L1 的行号守卫当场抓住。
    if inv.dry_run:
        # `register` 与 `install` 共用本 run()，但**代价差着数量级**：
        # 前者只写一行登记，后者还要下几十 GB。措辞跟着动词走。
        return Done({"kind": "model_install_dry_run", "verb": inv.verb,
                     "id": inv.target,
                     "repo": repo, "type": t, "port": port,
                     "revision": (resolved or {}).get("revision"),
                     "would_commit": dict(patch),
                     "replaces": replaced})

    # ── stage_split（提交点①）·此后只能 Failed 且带 committed ──
    store.commit(ctx, store.MODELS, tx, stack, types=types)

    # ── 执行段（已有资源落地，只能 Failed 且带 committed）──
    if inv.verb == "register":
        return Done({"kind": "model_register", "registered": inv.target,
                     "type": t, "evidence": evidence,
                     **({"replaced": replaced} if replaced else {})})

    fetch = deps.get("fetch")
    if fetch is None:
        return Failed(1, "weights.no_fetcher", [f"models.jsonl:{inv.target}"])
    out = weights.download(ctx, repo, (inv.args or {}).get("revision"), fetch,
                           announce=deps.get("announce"))
    if out.rc != 0:
        # 记录已 commit（stage_split 之后），所以是 Failed 且带 committed；
        # **rc 取 download 给的码**（3 = 外部依赖不可用），不塌成 1 ——
        # 用户要能区分「没装下载器」与「下载中途断了」。
        return Failed(out.rc if out.rc in (1, 3, 4, 10) else 1,
                      getattr(out, "msg_key", "weights.download_failed"),
                      [f"models.jsonl:{inv.target}"],
                      repo=repo, id=inv.target,
                      detail=(getattr(out, "args", {}) or {}).get("detail", ""))
    # ── 提交点②：两段式的**第二段** ──
    # `stage_split == commit_user_placeholder`（`02:1048`）的含义是第一次 commit
    # 只落 user 占位，machine 段要在权重到位之后回填。此前这里直接 Done ——
    # 于是刚装完的模型状态停在「已登记」（`store.model_state` 判的是
    # `machine.revision`），用户必须再敲一次 `mlx model repair` 才变「就绪」。
    # 两段式只实现了第一段。
    #
    # **不调 `rebuild_machine`**：它的「谁调用它」只列了 `reclaim` 与 `repair`
    # 两条路径（`04-iface-C.md:273`），从这里调是扩边。走 INV-03 授权的
    # 另一个来源 —— 边 **#16a**（`cmd.model.acquire → weights`），即校验段
    # 第 46 行已经在用的那条；那次 resolve 跑在**下载之前**，权重还不在，
    # 所以拿不到 revision，这里补跑一次。
    placed = out.payload or {}
    patch2 = {}
    if placed.get("revision"):
        patch2["revision"] = placed["revision"]
    if placed.get("path"):
        sz = weights.size(placed["path"])
        if not is_unknown(sz):
            patch2["size_mb"] = round(sz.value / (1024 * 1024), 1)
    stale = ""
    if patch2:
        try:
            store.commit(ctx, store.MODELS, store.ModelsTx(inv.target, patch2), stack)
        except OSError as exc:
            # 权重**已经落地**了，回填失败不该把它判成安装失败；但也不静默 ——
            # 明说状态没跟上、以及怎么补。
            stale = f"machine 段未回填（{exc}）—— 跑 `mlx model repair` 补上"
    return Done({"kind": "model_install", "installed": inv.target,
                 "type": t, "evidence": evidence,
                 "path": placed.get("path"),
                 "state": store.model_state(
                     store.load(ctx, store.MODELS).by_id(inv.target)),
                 **({"warning": stale} if stale else {})})
