"""D · 渲染 —— 配置。

`config` 各命令的渲染器。**随命令走，不留在 `ui`** —— 留在 `ui` 会让它认识
每一个命令的结果类型，那是「一个模块多个变更理由」。

`ui._w` / `ui._pad` / `ui._flat` / `ui._kv` 是 `ui` 的私有名，这里**引用而不复制**：
复制会造出第二份实现，而本项目刚因为「显示宽度三份实现」修过一次（INV-23）。
"""
from .. import ui

# 层名 → 屏幕上的一句话。层序与含义见 `schema.build_layers`。
_LAYER_SAID = {
    "default": "出厂值",
    "machine": "全局设置（mlx config set）",
    "user": "本模型的设置（mlx config set <模型>）",
    "install": "安装时写定（改它要带环境变量重装）",
    "env": "当前 shell 的环境变量",
}


def _config_ls(p):
    # type: (dict) -> str
    """**逐键一行 + 来源标注**（附录 A：「键值 + 逐行来源标注」）。

    此前是两行 `values  a=1、b=2` / `sources  a=default、b=default` ——
    要对照哪个键来自哪一层，得在两行之间来回数位置。
    """
    vals, srcs = p.get("values") or {}, p.get("sources") or {}
    if not vals:
        return "（没有配置键）"
    w = max(ui._w(k) for k in vals)
    lines = [f"  {ui._pad(k, w)}  {ui._pad(ui._flat(v), 12)}{srcs.get(k, '')}"
             for k, v in sorted(vals.items())]
    tail = ["", f"  共 {len(vals)} 个键；右列是取值来源的层"]
    # 层名是 `--json` 的接口，不改；屏幕上给出现过的每个层名一句人话（2026-10-29：`cli` 被读成命令行）
    used = [n for n in _LAYER_SAID if n in set(srcs.values())]
    if used:
        tail.append("  来源：" + " · ".join(f"{n} {_LAYER_SAID[n]}" for n in used))
    if p.get("dropped"):
        tail += ["", "  已退役的键：" + "、".join(p["dropped"]) + "（`mlx model repair` 清除）"]
    return "\n".join(lines + tail)


def _config_get(p):
    # type: (dict) -> str
    return "\n".join(ui._kv([("键", p.get("key")), ("值", ui._flat(p.get("value"))),
                             ("来源", p.get("source"))]))


def _config_diff(p):
    # type: (dict) -> str
    rows = p.get("rows") or []
    if not rows:
        return f"  {p.get('target', '')} 没有改过的键（全是出厂值）"
    w = max(ui._w(r["key"]) for r in rows)
    lines = [f"  {ui._pad('键', w)}   当前值      来自      出厂值"]
    lines.append("  " + "─" * w + "   ──────────  ────────  ────────")
    for r in rows:
        lines.append(f"  {ui._pad(r['key'], w)}   {ui._pad(str(r['value']), 10)}  "
                     f"{ui._pad(r.get('source', ''), 8)}  {r.get('factory', '')}")
    return "\n".join(lines)


def _config_export(p):
    # type: (dict) -> str
    vals = p.get("values") or {}
    if not vals:
        return "  没有改过的键 —— 出厂值换台机器仍是出厂值，不必导出"
    w = max(ui._w(k) for k in vals)
    lines = [f"  {ui._pad(k, w)}   {v}" for k, v in sorted(vals.items())]
    sk = p.get("skipped") or []
    if sk:
        # **机密与不可导出的要说出来**，否则用户以为导全了
        lines += ["", f"  未导出：{'、'.join(sk)}（机密或标了 exportable=no）"]
    return "\n".join(lines)


def _config_unset(p):
    # type: (dict) -> str
    if not p.get("was_set"):
        return f"  {p.get('unset')} 本来就没设过（{p.get('scope')}）—— 无需撤销"
    return (f"  ✓ 已撤销  {p.get('unset')}（{p.get('scope')}）\n"
            f"    现在是  {p.get('now')}（来自 {p.get('source')}）")


def _config_import(p):
    # type: (dict) -> str
    lines = []
    if p.get("dry_run"):
        lines.append(f"  将导入 {len(p.get('planned') or [])} 个键（这是预览，没有写入）")
    else:
        lines.append(f"  ✓ 已导入  {'、'.join(p.get('applied') or []) or '（无）'}")
    for r in p.get("rejected") or []:
        lines.append(f"  ✗ 拒绝    {r['key']}：{r['why']}")
    for r in p.get("failed") or []:
        # **逐条报告**：不假装原子，前面成功的保持生效
        lines.append(f"  ✗ 失败    {r['key']}：{r['why']}（前面成功的保持生效）")
    return "\n".join(lines)


ui.register({
    "config_ls": _config_ls,
    "config_get": _config_get,
    "config_diff": _config_diff,
    "config_export": _config_export,
    "config_unset": _config_unset,
    "config_import": _config_import,
})
