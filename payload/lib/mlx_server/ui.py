"""B 层 · 输出与交互原语。**只留原语**，每命令的渲染器随 `cmd.*` 走。

两条流（INV-17）：**stdout 只含主产物，stderr 只含过程说明**。
文案参数只接受 `MessageKey`（INV-24），传裸 str 是类型错误。
"""
import unicodedata


def display_width(text):
    # type: (str) -> int
    """东亚显示宽度。**全项目唯一读东亚宽度表的地方**（INV-23）。

    判据是这张表的读取点数，不是函数名 —— 换个名字再抄一份同样会被守卫抓住。
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def pad(text, width):
    # type: (str, int) -> str
    """右侧补空格到指定显示宽度，**唯一实现**（INV-23：裸 `%-Ns` 在含 CJK 的
    字段上出现点 == 0）。宽度计算委托 `display_width`，不自己再算一遍。"""
    return text + " " * max(0, width - display_width(text))


# ── 人类可读渲染 ────────────────────────────────────────────────
# **默认输出给人看，`--json` 才给机器看**（附录 A 的「机读」列写的是 `--json`）。
# 渲染按 payload 的**形状**分派（`kind` 字段），不按动词 —— 按动词分派会在这里
# 长出第二份动词表，而消灭「两份真实命令集合」正是 `verbs.tsv` 存在的理由。

_MARK = {"ok": "✓", "failed": "✗", "undeterminable": "?", True: "✓", False: "✗"}
# 「未运行」是**正常状态**不是故障 —— 用中性点，不用 ✗。
# 一屏全是红叉会让用户以为哪里坏了，而实际只是服务没起。
_RUN = {True: "✓", False: "·", None: "?"}
# `store.model_state` 的值是**数据**（进 `--json`，不改）；屏幕上的那一列说的是权重在不在盘上。
# 屏幕不写「就绪」：这个词在 `up` 里指「服务已在监听」，`status` 又把这一列放在运行标记旁边 ——
# 没在跑的模型、甚至不能单独起的 drafter 都显示「就绪」（2026-10-29 真机；D4 选 A）。
WEIGHTS_SAID = {"就绪": "已下载", "已登记": "未下载", "已登记未下载": "未下载"}


def weights_said(state):
    # type: (object) -> str
    return WEIGHTS_SAID.get(state, state or "—")


def _mark(v):
    # type: (object) -> str
    return _MARK.get(v, "·")


def _kv(pairs, indent="  ", width=None):
    # type: (list, str, object) -> list
    if width is None:
        width = max((_w(k) for k, _ in pairs), default=0)
    return [indent + k + " " * max(1, width - _w(k) + 2) + str(v) for k, v in pairs]


# `_w` / `_pad` 曾是这里的**第二份**实现，与 `pad` / `display_width` 逐字相同 ——
# 于是 INV-23 声称的「唯一 `ui.pad()`」是死代码（零调用点），而真正在用的是
# 两个私有副本。「唯一」写在不变式里而实现有三份，守卫就只是装饰。
def _w(s):
    # type: (str) -> int
    return display_width(str(s))


def _pad(s, n):
    # type: (str, int) -> str
    return pad(str(s), n)


_TABLES = {}        # human() 的表句柄。**显式传入，不依赖 ui.bind 的装配顺序**


def _text(key, fallback=None, **args):
    # type: (str, object, object) -> str
    """文案键 → 文案。**渲染层不打印裸 key**（L1·ui-10）。

    表由 `human(payload, tables=...)` 显式给入 —— 渲染是纯函数，
    不该因为「谁先调了 bind」而时灵时不灵（实测过：entry 从未调 bind，
    于是 `mlx api info` 一直在打印裸 key `api.honest_no_boundary`）。
    """
    t = _TABLES.get("t")
    if t is None:
        return fallback if fallback is not None else key
    try:
        return t.text(t.msg(key), **args)
    except Exception:                              # noqa: BLE001
        return fallback if fallback is not None else key


def human(payload, tables=None):
    # type: (object, object) -> str
    """结构化 payload → 人类可读文本。未知形状回落到对齐的键值对。

    `tables` 用于把文案键渲染成文案；不传时键原样保留（可测，但不美）。
    """
    if tables is not None:
        _TABLES["t"] = tables
    if not isinstance(payload, dict):
        return str(payload)
    kind = payload.get("kind")
    fn = _renderers().get(kind)
    return fn(payload) if fn else _generic(payload)


def _renderers():
    # type: () -> dict
    """kind → 渲染器。

    **`ui` 不去找 `cmd`**：那是 B 层 import D 层，层序反向边（守卫当场抓到）。
    这条边说明「渲染器随 cmd 走 + ui 负责分派」这个组合结构上不成立 ——
    **分派方必须认识被分派方**。

    所以方向反过来：各 `cmd/render_*.py` 在被 import 时调 `ui.register()`
    把自己登记进来，而**谁去 import 它们由装配层（`entry`）决定** ——
    装配点认识所有层，那正是它的职责。
    """
    return dict(_BUILTIN, **_EXTRA)


_EXTRA = {}


def register(renderers):
    # type: (dict) -> None
    """登记一组渲染器（`kind` → 函数）。由各 `cmd/render_*.py` 在装配期调用。"""
    _EXTRA.update(renderers)


def _generic(p):
    # type: (dict) -> str
    rows = [(k, _flat(v)) for k, v in sorted(p.items()) if k != "kind"]
    return "\n".join(_kv(rows)) if rows else "（无内容）"


def _flat(v):
    # type: (object) -> str
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, list):
        return "（空）" if not v else "、".join(str(x) for x in v)
    if isinstance(v, dict):
        return "、".join(f"{k}={_flat(x)}" for k, x in sorted(v.items())) or "（空）"
    return "（未设置）" if v is None else str(v)


def _checks(p):
    # type: (dict) -> str
    rows = p.get("rows") or []
    if not rows:
        return "（无检查项）"
    w = max(_w(r[0]) for r in rows)
    lines = []
    for name, verdict, detail in rows:
        line = f"  {_mark(verdict)} {_pad(name, w)}  {verdict}"
        if detail:
            line += f"   {detail}"
        lines.append(line)
    bad = [r[0] for r in rows if r[1] != "ok"]
    lines += ["", f"  {len(rows)} 项，{len(rows) - len(bad)} 项通过"
              + (f"，{len(bad)} 项未通过：{'、'.join(bad)}" if bad else "")]
    return "\n".join(lines)


def _trash_restore(p):
    # type: (dict) -> str
    return (f"  ✓ 已恢复  {p.get('restored')}\n"
            f"    恢复到  {p.get('origin')}")


def _repair(p):
    # type: (dict) -> str
    ch = p.get("changes") or []
    lines = ([f"  重建了 {len(ch)} 个字段："] +
             [f"    {c['id']}.{c['field']}  {_flat(c.get('before'))} → {_flat(c.get('after'))}"
              for c in ch]) if ch else ["  没有需要重建的字段（machine 段都已填好）"]
    lines.append(f"  扫描到磁盘上 {p.get('scanned', 0)} 个仓库，注册表 {p.get('records', 0)} 条")
    cl = p.get("cleared_fields") or []
    lines += ["", f"  清除了 {len(cl)} 个 1.0.0 旧字段：" if cl else "  没有 1.0.0 旧字段"]
    lines += [f"    {c['id']}.{c['segment']}.{c['field']}" for c in cl]
    rt = p.get("retired_keys") or []
    lines += ["", f"  清除了 {len(rt)} 个退役配置键：" if rt else "  没有退役配置键"]
    lines += [f"    {'全局' if r['scope'] == 'global' else r['scope']}  {r['key']}" for r in rt]
    if p.get("registered_but_missing"):
        lines += ["", "  已登记但磁盘上没有：" + "、".join(p["registered_but_missing"]),
                  "    → 权重未下载或已被移走；`mlx model rm <id>` 清理登记"]
    if p.get("on_disk_but_unregistered"):
        lines += ["", "  磁盘上有但未登记：" + "、".join(p["on_disk_but_unregistered"]),
                  "    → `mlx model register <id> --repo=<repo>` 登记，或 `mlx model gc` 回收"]
    bad = p.get("bad_lines")
    if bad:
        lines += ["", f"  注册表有 {len(bad)} 行解析失败（已跳过）："]
        lines += [f"    第 {b['line']} 行  {b['why']}" for b in bad]
    return "\n".join(lines)


# 留在 `ui` 的渲染器：它们的 payload 由 **C 层**构造
# （`store.rebuild_machine` 产 `repair`、`vault.restore` 产 `trash_restore`），
# 或被多个 D 模块共用（`checks` 由 `cmd.check` 与 `cmd.config` 共同产出）。
# 「随 cmd 走」在这三处落不下去 —— 硬搬会让 C 层的产物去依赖某个 D 模块。
_BUILTIN = {
    # 只剩三个：它们的 payload 由 **C 层**构造（`store.rebuild_machine` 产
    # `repair`、`vault.restore` 产 `trash_restore`），或被多个 D 模块共用
    # （`checks` 由 `cmd.check` 与 `cmd.config` 共同产出）。
    # 「渲染器随 cmd 走」在这三处落不下去 —— 硬搬会让 C 层的产物去依赖
    # 某个 D 模块，那比现在的耦合更糟。
    "repair": _repair,
    "trash_restore": _trash_restore,
    "checks": _checks,
}
