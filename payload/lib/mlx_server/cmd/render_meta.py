"""D · 渲染 —— 版本 · 能力 · 部署 · 网关 · 对话。

渲染器随命令走：这几个 `kind` 由 `cmd.meta` / `cmd.caps` / `cmd.self_deploy` /
`cmd.api` / `cmd.chat` 产出，它们的显示形状就落在这里，`ui` 不必认识它们。
`from .. import ui` 是 D → B 的向下边（合法）；反过来才是守卫要抓的反向边。

复用 `ui` 的私有原语（`ui._kv` / `ui._flat` / `ui._text` / `ui._RUN`）而**不复制** ——
显示宽度刚因为三份实现修过一次，再抄一份等于把同一个坑重挖。
"""
from .. import ui


def _version(p):
    # type: (dict) -> str
    order = ["version", "python", "venv_python", "packages", "caps_collected_at"]
    label = {"version": "工具版本", "python": "当前解释器",
             "venv_python": "venv 解释器", "packages": "后端包",
             "caps_collected_at": "能力采集于"}
    rows = [(label.get(k, k), ui._flat(p[k])) for k in order if k in p]
    rows += [(k, ui._flat(v)) for k, v in sorted(p.items())
             if k not in order and k != "kind"]
    return "\n".join(ui._kv(rows))


def _caps(p):
    # type: (dict) -> str
    pkgs = p.get("packages")
    rows = [("后端包", "、".join(pkgs) if pkgs else "venv 未建，后端包未装"),
            ("指纹", p.get("fingerprint") or "（无）"),
            ("采集于", p.get("collected_at") or "（未记录）")]
    return "\n".join(ui._kv(rows))


def _caps_diff(p):
    # type: (dict) -> str
    ch = p.get("changes")
    if not ch:
        return f"  能力无变化（采集于 {p.get('collected_at', '')}）"
    lines = [f"  {len(ch)} 项变化："]
    for c in ch:
        lines.append(f"    {c['item']}  {ui._flat(c.get('before'))} → {ui._flat(c.get('after'))}")
    return "\n".join(lines)


def _self_deploy(p):
    # type: (dict) -> str
    roots = p.get("roots") or {}
    label = {"code": "代码根", "data": "数据根", "weights": "权重根"}
    act = {"install": "安装完成", "uninstall": "卸载完成"}.get(p.get("action"), "")
    lines = [f"  {act}" if act else "  完成"]
    if p.get("action") == "uninstall":
        lines.append("  已删除  " + roots.get("code", ""))
        lines += ["  保留    " + roots.get(k, "") + f"（{label[k]}）"
                  for k in ("data", "weights") if roots.get(k)]
    else:
        lines += ["  " + label[k] + "  " + roots.get(k, "")
                  for k in ("code", "data", "weights") if roots.get(k)]
    if p.get("would_run"):
        lines = ["  将执行  " + p["would_run"]] + lines[1:]
    return "\n".join(lines)


def _api(p):
    # type: (dict) -> str
    run = p.get("running")
    lines = [f"  {ui._RUN.get(run, '·')} 网关   " +
             ("在跑" if run else "未运行   `mlx up api` 起它")]
    if p.get("base_url"):
        lines.append(f"    地址   {p['base_url']}")
    if p.get("port"):
        lines.append(f"    端口   {p['port']}" +
                     (f"   pid {p['pid']}" if p.get("pid") else ""))
    ids = p.get("models")
    if ids is not None:
        lines.append(f"    可用   {'、'.join(ids) if ids else '（无 —— 先 `mlx up <id>` 起模型）'}")
    other = p.get("registered_only")
    if other:
        # **分两行**：已登记 ≠ 网关转发得到。合成一行会让用户抄到一个
        # 填进客户端却连不上的 id，而这一屏所有别的指标都是绿的。
        lines.append(f"    未起   {'、'.join(other)}（已登记，但网关转发不到）")
    key = p.get("honest_note_key")
    if key:
        lines += ["", "  " + ui._text(key)]
    return "\n".join(lines)


def _chat(p):
    # type: (dict) -> str
    """`chat` 的主产物是**回复正文**（台账：逐字节只含回复正文）。

    单轮：正文就是 payload 里的 `reply`。
    多轮：每轮的正文在生成时就已经写到 stdout 了（边聊边出，不能攒到最后），
    所以这里返回**空串** —— 再渲染一次汇总会让 `mlx chat x > out.txt`
    存下来的文件末尾多出 `rounds/session/turns` 三行。汇总走 stderr。
    """
    if p.get("rounds") is not None:
        return ""
    return p.get("reply") or ""


def _bootstrap_keys(p):
    # type: (dict) -> str
    """`bootstrap keys` 的结论。**只列键名** —— payload 里本来就没有值。"""
    lines = [f"  ✓ 已生成  {k}" for k in p.get("generated") or []]
    lines += [f"  · 已存在  {k}" for k in p.get("kept") or []]
    if not lines:
        return "  没有需要生成的密钥"
    lines.append("  查看原值：mlx config get --key=<键名> --reveal")
    return "\n".join(lines)


ui.register({
    "version": _version,
    "caps": _caps,
    "caps_diff": _caps_diff,
    "self": _self_deploy,
    "api": _api,
    "chat": _chat,
    "keys": _bootstrap_keys,
})
