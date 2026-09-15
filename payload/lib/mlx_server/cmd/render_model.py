"""D · 渲染 —— 模型与回收站。

模块级调 `ui.register()` 自我登记（由 `entry._load_renderers()` 在装配期 import）。
`cmd` 是 D 层、`ui` 是 B 层，`from .. import ui` 是合法的向下边。

排版辅助（`_w` / `_pad` / `_flat`）**引用 `ui` 的那一份，不在这里再抄一遍** ——
显示宽度已经因为「三份实现」修过一次（INV-23）。
"""
from .. import ui


def _models(p):
    # type: (dict) -> str
    ms = p.get("models") or []
    if not ms:
        return "（尚未登记任何模型。`mlx model register <id>` 登记一个）"
    cols = ["id", "modality_display", "port", "state"]
    hdr = {"id": "ID", "modality_display": "模态", "port": "端口", "state": "状态"}
    w = {c: max(ui._w(hdr[c]), max((ui._w(m.get(c, "")) for m in ms), default=0)) for c in cols}
    lines = ["  " + "  ".join(ui._pad(hdr[c], w[c]) for c in cols),
             "  " + "  ".join("─" * w[c] for c in cols)]
    for m in ms:
        lines.append("  " + "  ".join(ui._pad(ui._flat(m.get(c)), w[c]) for c in cols))
    lines += ["", f"  共 {len(ms)} 个"]
    return "\n".join(lines)


def _model_info(p):
    # type: (dict) -> str
    """`model info`：模态与依据、模板默认的思考开关与 effort 值域、路径对照、待清理的旧字段。"""
    mod, tpl = p.get("modality") or {}, p.get("template") or {}
    basis = f"；依据 {mod['basis']}" if mod.get("basis") else ""
    rows = [("ID", p.get("id")), ("仓库", (p.get("user") or {}).get("repo")),
            ("模态", f"{mod.get('display')}（配置 {mod.get('configured')}{basis}）")]
    if "undeterminable" in tpl:
        rows.append(("思考默认", f"判不出（{tpl['undeterminable']}）"))
    else:
        rows += [("思考默认", "开" if tpl.get("thinking_default") else "关"),
                 ("effort 值域", "、".join(tpl.get("effort_domain") or [])
                  or "（模板不支持，请求里不传）")]
    rows += [("期望路径", p.get("weights_expected")), ("实际找到", p.get("weights_found"))]
    lines = ui._kv([(k, ui._flat(v)) for k, v in rows])
    extra = (p.get("old_fields") or []) + (p.get("retired_keys") or [])
    if extra:
        lines += ["", "  旧字段 / 退役键：" + "、".join(extra) + "（`mlx model repair` 清除）"]
    return "\n".join(lines)


def _model_register(p):
    # type: (dict) -> str
    lines = [f"  ✓ 已登记  {p.get('registered')}",
             f"    模态    {p.get('modality')}（auto = 每次 up 按权重判定）"]
    rep = p.get("replaced")
    if rep:
        # **覆盖必须显式说出来**：同一个 id 再登记一次是 upsert，
        # 输出若与首次登记一模一样，用户不会意识到自己改掉了一条记录。
        lines.append(f"    覆盖了  原 repo={rep.get('repo')} 端口={rep.get('port')}"
                     f"（旧值已不可恢复）")
    return "\n".join(lines)


def _model_rm(p):
    # type: (dict) -> str
    lines = [f"  ✓ {p.get('removed')}   已删除登记"]
    if p.get("trashed"):
        lines.append(f"    权重已移入回收站  {p['trashed']}")
    elif p.get("weights_kept"):
        lines.append(f"    {p['weights_kept']}")
    return "\n".join(lines)


def _gc(p):
    # type: (dict) -> str
    can, did = p.get("reclaimable_mb", 0), p.get("reclaimed_mb", 0)
    if not p.get("targets"):
        # 附录 A：「无可回收 → 合法空对象且**不打印矛盾文案**」
        return f"  没有可回收的权重（已登记的 {len(p.get('retained') or [])} 个都在用）"
    # 预览与真回收**必须用不同的动词**：dry-run 下打「回收了」会让用户
    # 以为东西已经没了（而它们还在），真回收时打「将回收」则相反。
    # 两个量同源（都从同一份 orphans 算），`did == 0` 即预览。
    preview = did == 0 and can > 0
    lines = [f"  可回收 {can} MB，实际回收 {did} MB",
             ("  将回收：" if preview else "  回收了：") + "、".join(p["targets"])]
    if preview:
        lines.append("  这是预览（--dry-run），一个字节都没动")
    if p.get("retained"):
        lines.append("  保留：" + "、".join(p["retained"]) + "（仍被登记引用）")
    return "\n".join(lines)


def _trash_ls(p):
    # type: (dict) -> str
    items = p.get("items") or []
    if not items:
        return "  回收站是空的"
    w = max(ui._w(i["id"]) for i in items)
    lines = [f"  {ui._pad('ID', w)}   体积      原路径"]
    lines.append("  " + "─" * w + "   ────────  " + "─" * 40)
    for i in items:
        mb = f"{i.get('size_mb', 0)} MB"
        lines.append(f"  {ui._pad(i['id'], w)}   {ui._pad(mb, 8)}  {i.get('origin', '')}")
    lines.append("")
    lines.append(f"  共 {len(items)} 项，{p.get('total_mb', 0)} MB"
                 f"（`mlx model trash restore <ID>` 恢复到原路径）")
    return "\n".join(lines)


def _trash_empty(p):
    # type: (dict) -> str
    n = len(p.get("purged") or [])
    if not n:
        return "  回收站本来就是空的"
    return f"  ✓ 已清空  {n} 项，释放 {p.get('freed_mb', 0)} MB（不可恢复）"


def _install_dry_run(p):
    # type: (dict) -> str
    """`model install --dry-run` 的预览。

    **动词必须是「将」而不是「已」**（同 `_gc` 的理由）：这条路径此前
    根本不存在 —— dry-run 直接走完 commit，用户看到的是真安装的成功文案，
    而注册表里真多了一条记录。措辞与事实同时错，两者互相印证，
    没有任何地方露出破绽。
    """
    verb = p.get("verb") or "install"
    lines = ["  这是预览（--dry-run），一个字节都没动",
             f"  将登记  {p.get('id')}" + ("（并下载权重）" if verb == "install" else ""),
             f"    repo  {p.get('repo')}",
             f"    模态  {p.get('modality')}   端口  {p.get('port')}"]
    if p.get("revision"):
        lines.append(f"    版本  {p['revision']}")
    if p.get("replaces"):
        r = p["replaces"]
        # **覆盖是最值得预览的那件事** —— 用户以为在新增，实际会改掉一条
        lines.append(f"  ⚠ 会覆盖现有登记：{r.get('repo')}（端口 {r.get('port')}）")
    lines.append("  去掉 --dry-run 执行")
    return "\n".join(lines)


ui.register({
    "models": _models,
    "model_install_dry_run": _install_dry_run,
    "model_info": _model_info,
    "model_register": _model_register,
    "model_rm": _model_rm,
    "gc": _gc,
    "trash_ls": _trash_ls,
    "trash_empty": _trash_empty,
})
