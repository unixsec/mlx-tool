"""D · `rm` / `gc` / `trash {ls,restore,empty}` / `repair`。递归删除**全部委托 vault**。"""
import contextlib
import os

from .. import store, vault, weights
from ..result import Done, Failed, Refused, is_unknown


def run(ctx, stack, inv, deps=None):
    deps = deps or {}
    # **R-SAFETY-11 的第三层**：「任何按外部输入索引内部映射之前必须有存在性
    # 校验」，来源栏点名的 v6 症状正是「详情命令对未注册短名崩溃」。
    # 此前 gc/repair 分支**全程一次都没读过 inv.target** —— 不是校验写错，
    # 是根本没有；对不存在的 id 静默返回 rc 0，用户以为做了什么。
    # **位置必须在任何 commit 之前**：BCR-R-01 要求 `Refused` 的前提是
    # 零资源已创建，放在 rm 分支的 commit 之后会被 L1 守卫当场抓住。
    if (inv.verb in ("gc", "repair") and inv.target
            and store.load(ctx, store.MODELS).by_id(inv.target) is None):
        return Refused(6, "model.not_found", id=inv.target)

    # **磁盘事实也在这里取**，理由同上：`Unknown` 要在任何 commit 之前
    # 变成 `Refused(9)`。契约逐字是「扫描不完整 ⇒ 上层拒绝重建，
    # **不用部分事实覆盖记录**」—— 用部分事实的后果是 gc 把扫不到的模型
    # 当成孤儿删掉、repair 把 `size_mb: 0.0` 写进记录（而容量判档读的就是它）。
    facts = None
    if inv.verb in ("gc", "repair"):
        facts = weights.scan_installed(ctx)
        if is_unknown(facts):
            return Refused(9, "weights.scan_undeterminable", detail=facts.reason)

    # ── 回收站三件套（`03b-ledgers.md:196-198`）──────────────────
    # `model rm --weights` 与 `model gc` 都说「移入回收站（可恢复）」，
    # 而在这三条实现之前**恢复的手段不存在** —— 用户只能手工 mv，
    # 且得先自己发现台账里记着原路径。
    if inv.verb == "trash ls":
        items = vault.entries(ctx)
        return Done({"kind": "trash_ls", "items": items,
                     "total_mb": round(sum(i.get("size_mb") or 0 for i in items), 1)})

    if inv.verb == "trash restore":
        if not inv.target:
            return Refused(2, "trash.target_required")
        return vault.restore(ctx, inv.target)

    if inv.verb == "trash empty":
        items = vault.entries(ctx)
        if not items:
            return Done({"kind": "trash_empty", "purged": [], "freed_mb": 0})
        confirm = deps.get("confirm")
        freed = round(sum(i.get("size_mb") or 0 for i in items), 1)
        # **不可恢复**，所以要确认；键入 `empty`（比 yes 更防手滑）
        if confirm is not None and not confirm(
                "empty", f"将永久删除回收站里的 {len(items)} 项（{freed} MB），"
                         f"**不可恢复**。键入 empty 确认"):
            return Refused(5, "trash.confirm_declined")
        out = vault.purge(ctx, vault.trash_dir(ctx))
        if out.rc != 0:
            return out
        return Done({"kind": "trash_empty",
                     "purged": [i["id"] for i in items], "freed_mb": freed})

    if inv.verb == "rm":
        rec = store.load(ctx, store.MODELS).by_id(inv.target)
        if rec is None:
            return Refused(6, "model.not_found", id=inv.target)
        # ── 确认强度**按可恢复性分档** ───────────────────────────
        # 三个破坏性动作的真实代价差着数量级，而此前它们受同样的约束：
        #   · `model rm`            登记少一行，`model register` 一条命令就回来
        #   · `model rm --weights`  权重进回收站，`model trash restore` 能恢复
        #   · `model trash empty`   **真正不可恢复**
        # 第一行几乎没有代价，却和第三行一样要求 tty —— 而 CI 里被卡住的
        # 主要就是第一行。现在：不带 `--weights` 不问；带 `--weights` 问但认
        # `--yes`；`trash empty` 问且**三种绕过全拒**（唯一不可逆的动作，
        # 保持最严的约束）。
        take_weights = bool((inv.args or {}).get("weights"))
        confirm = deps.get("confirm")
        if take_weights and confirm is not None and not (inv.args or {}).get("yes"):
            # 附录 A 的过程流：**逐行列出目标 + 确认串**。
            # 要键入的是**目标 id**（比 yes 更防手滑），提示语由文案表渲染 ——
            # 此前这里把文案键 `model.confirm_rm` 当成了要键入的串，
            # 用户看到的是「键入 model.confirm_rm 确认:」。
            tables = deps.get("tables")
            prompt = f"将删除模型 {inv.target} 及其权重（移入回收站，可恢复）"
            if tables is not None:
                # 文案渲染失败不该挡住删除流程 —— 上面已有可用的兜底提示
                with contextlib.suppress(Exception):
                    prompt = tables.text(tables.msg("model.confirm_rm"), id=inv.target)
            if not confirm(inv.target, prompt):
                return Refused(5, "model.confirm_declined")     # 零删除
        # **权重只在 `--weights` 时回收**（附录 A：`model rm <id> [--weights][--unbacked]`，
        # 主产物「每目标一行结论（`removed`/`trashed`/`refused`）」——
        # `trashed` 正是 `--weights` 的结果）。
        # 此前这里**无条件**把权重移进回收站：用户只想删登记，几十 GB 的权重
        # 却一起没了。默认删更多是最坏的一类默认值。
        trashed = None
        if take_weights:
            # **`revision` 在 machine 段**（`schema.tsv#revision(scope=machine)`）——
            # 它是磁盘事实，由 `weights` 产出，不是用户填的。
            # 这里此前读 `rec.user`，永远取到 `None` ⇒ `resolve` 退化成
            # 「取最新的那个 revision」。同一个 repo 存了两个版本时，
            # `model rm --weights` 删的可能不是这条记录登记的那一个。
            r = weights.resolve(ctx, rec.user.get("repo", ""),
                                rec.machine.get("revision"))
            if not is_unknown(r) and r.value:
                vault.trash(ctx, r.value["path"], stack)    # 委托唯一删除点
                trashed = r.value["path"]
        store.commit(ctx, store.MODELS, store.ModelsTx(inv.target, {}, mode="delete"), stack)
        return Done({"kind": "model_rm", "removed": inv.target,
                     "trashed": trashed,
                     "weights_kept": None if take_weights else "未加 --weights，权重原样保留"})

    if inv.verb == "gc":
        facts = weights.scan_installed(ctx)
        # **过滤掉空 repo**：注册表里出现过无 repo 的记录时（历史上
        # `config set` 的幽灵记录就是这样），`sorted(known)` 会在
        # `None < str` 上抛 TypeError，rc 1 —— 一条只读的 gc 变成崩溃。
        known = {r.user.get("repo") for r in store.load(ctx, store.MODELS).records
                 if r.user.get("repo")}
        orphans = [i for i in facts.installed if i["repo"] not in known]
        # 附录 A 2819：主产物是**两个量**（可回收量 + 实际回收量，**同源**），
        # 空结果是「合法空对象且**不打印矛盾文案**」，stderr 还欠一条保留集说明。
        # 两个量同源 = 都从同一份 orphans 算，不各算各的。
        def _mb(items):
            return round(sum(i.get("size_mb") or 0 for i in items), 1)

        reclaimable_mb = _mb(orphans)
        # **`dry_run` 只有 `inv.dry_run` 一个读取面**（`cli.py:279` 明确不把
        # `dry-run` 放进 `flags`）。此前这里读 `inv.args.get("dry_run")` ——
        # 恒假，于是预览分支**不可达**：用户跑 `mlx model gc --dry-run`
        # 想先看看会回收什么，实际把全部孤儿权重移进了回收站，
        # 还打印「实际回收 3.0 MB」。这是最坏的一类：预览动作真的删数据。
        if inv.dry_run:
            return Done({"kind": "gc", "reclaimable_mb": reclaimable_mb,
                         "reclaimed_mb": 0, "targets": [o["repo"] for o in orphans],
                         "retained": sorted(known)})
        done = []
        for o in orphans:
            vault.trash(ctx, os.path.join(ctx.root_weights, "hub", o["dir"]), stack)
            done.append(o)
        return Done({"kind": "gc",
                     "reclaimable_mb": reclaimable_mb,
                     "reclaimed_mb": _mb(done),
                     "targets": [o["repo"] for o in done],
                     "retained": sorted(known)})

    if inv.verb == "repair":
        # **边 #17**：磁盘事实由本模块递交 `store.rebuild_machine`
        # （`weights → store` 的边故意不存在，§3.4 N3）。
        # 附录 A 2821：主产物是「重建前后的差异（逐字段）」，
        # 空结果「无差异 → 合法空数组」，stderr 说「从磁盘读到了什么」。
        # 此前这里**根本没在重建** —— 只报三个桶，而 `store.rebuild_machine`
        # 全项目零实现，`registered_but_missing` 这类桶在设计里 0 命中。
        out = store.rebuild_machine(ctx, facts, stack)
        if out.rc != 0:
            return out

        # ── B / C（spec §6.3）：清除 1.0.0 旧字段与退役配置键 ──
        # **只删列出的键**：machine 段其余字段、settings.json 其余键逐字节不变。
        # 判据读 `schema.tsv`（与校验段「生效配置」记入 dropped 的判据同一个），
        # 不在这里写死 `backend` —— 下一次退役一个键时这里不用改。
        from .. import schema
        from ..tables import open_tables
        defs = schema.keydefs(open_tables(ctx))
        cleared, retired = [], []
        # `Failed.committed` 是调用方唯一能看的收敛线索（`result.py:89`）——
        # 必须只列**失败点之前已经落地**的资源，绝不能列刚失败的那一个：
        # `atomic.replace` 全有全无，抛异常时临时文件已被清掉，`os.replace`
        # 根本没跑，那次写**什么都没落地**。A 动作此刻已经 rc==0（上面判过），
        # 它改过的记录也算已落地，一并计入。
        landed = {f"models.jsonl:{c['id']}" for c in (out.payload.get("changes") or [])}
        for r in store.load(ctx, store.MODELS).records:
            old_u = [k for k in ("type",) if k in r.user]
            old_m = [k for k in ("type_evidence",) if k in r.machine]
            ret_u = sorted(k for k in r.user if k not in defs and k not in ("repo", "type"))
            if not (old_u or old_m or ret_u):
                continue
            try:
                store.commit(ctx, store.MODELS, store.ModelsTx(
                    r.id, {}, unset_user=tuple(old_u + ret_u),
                    unset_machine=tuple(old_m)), stack)
            except Exception as exc:              # noqa: BLE001
                return Failed(1, "repair.commit_failed", sorted(landed),
                              id=r.id, detail=str(exc))
            cleared += [{"id": r.id, "segment": "user", "field": k} for k in old_u]
            cleared += [{"id": r.id, "segment": "machine", "field": k} for k in old_m]
            retired += [{"scope": r.id, "key": k} for k in ret_u]
            landed.add(f"models.jsonl:{r.id}")
        stale = sorted(k for k in store.load_settings(ctx) if k not in defs)
        if stale:
            try:
                store.commit(ctx, store.SETTINGS, store.SettingsTx(unset=stale), stack)
            except Exception as exc:              # noqa: BLE001
                return Failed(1, "repair.commit_failed", sorted(landed),
                              id="settings.json", detail=str(exc))
            retired += [{"scope": "global", "key": k} for k in stale]

        res = store.load(ctx, store.MODELS)
        on_disk = {i["repo"] for i in facts.installed}
        pay = dict(out.payload)
        pay["registered_but_missing"] = [r.id for r in res.records
                                         if r.user.get("repo") not in on_disk]
        pay["on_disk_but_unregistered"] = sorted(
            on_disk - {r.user.get("repo") for r in res.records})
        pay["bad_lines"] = [{"line": b.lineno, "why": b.reason_key}
                            for b in res.bad_lines]
        pay["cleared_fields"] = cleared
        pay["retired_keys"] = retired
        return Done(pay)
    return Refused(2, "cmd.unknown_verb", verb=inv.verb)
