"""D · 渲染 —— 服务与工具状态。

`ui` 只留原语；`kind` → 函数的对应关系随命令走，模块级 `ui.register()` 自我登记
（由 `entry._load_renderers()` 在装配期 import 触发）。

`_w` / `_pad` / `_mark` / `_kv` / `_flat` / `_text` / `_RUN` 都**引用 `ui` 的那一份**，
不在这里再抄一遍 —— 显示宽度长出三份实现的教训（INV-23）适用于所有这类原语。
"""
from .. import ui


def _tool_status(p):
    # type: (dict) -> str
    roots, exist = p.get("roots", {}), p.get("roots_exist", {})
    label = {"code": "代码根", "data": "数据根", "weights": "权重根"}
    lines = ["工具状态", ""]
    for k in ("code", "data", "weights"):
        if k in roots:
            lines.append(f"  {ui._mark(exist.get(k))} {label[k]}   {roots[k]}")

    if p.get("version"):
        # 附录 A 2810 / §4.1.2 1071 / 台账 182：不带目标的四项之一是**版本**
        lines.append(f"  · 工具版本 {p['version']}")

    lines += ["", f"  {ui._mark(p.get('path_effective'))} PATH 生效  {p.get('bin_dir', '')}"]
    if not p.get("path_effective"):
        lines.append("     └ 新开一个终端，或 source ~/.zshrc")

    man = p.get("manifest") or {}
    miss = man.get("missing")
    lines.append(f"  {ui._mark(not miss if miss is not None else None)} 分发清单  "
                 f"{man.get('declared', '?')} 项" +
                 (f"，缺 {len(miss)} 项：{'、'.join(miss[:3])}" if miss else "，完整"))

    models = p.get("models")
    if models is not None:
        lines += ["", f"模型（{len(models)}）"]
        if not models:
            lines.append("  （尚未登记任何模型。`mlx model register <id>` 登记一个）")
        else:
            lines += _model_table(models)

    gw = p.get("gateway")
    if gw is not None:
        run = gw.get("running")
        lines += ["", "网关",
                  f"  {ui._RUN.get(run, '·')} api        " +
                  (f"在跑  :{gw.get('port')}  pid {gw.get('pid')}"
                   + (f" · 绑定 {gw['bind']}" if gw.get("bind") else "")
                   + (" · TLS " + ("开" if gw.get("tls") else "关") if gw.get("bind") else "")
                   + (f" · 放行网段 {gw['allowed_count']}" if gw.get("allowed_count") else "")
                   if run
                   else ui._text("status.gateway.not_running"))]
        if gw.get("record_error"):
            # 记录读不出来时 `running` 是 None（不可判定），上面那行会落到
            # 「未运行」的中性点上 —— 必须补一句说清楚这不是「没在跑」
            lines.append("     └ " + ui._text("status.gateway.record_unreadable",
                                              detail=gw["record_error"]))
        # 只在网关真的在跑时才提示令牌轮换检测的局限——记录还在但进程已停时
        # `token_state` 可能仍是 `env_unknown`，这时打印它是误导（进程都没在跑，
        # 「无法比较是否已变更」这句话没有意义）。
        if run and gw.get("token_state") == "env_unknown":
            lines.append("     └ " + ui._text("status.gateway.token_env_unknown"))

    rs = p.get("restart_needed") or []
    if rs:
        lines += ["", "  ⚠ 密钥已变更，需重启：`mlx restart " + " ".join(rs) + "`"]
    return "\n".join(lines)


def _concurrency_display(rule, max_concurrency):
    # type: (object, object) -> str
    """R2（挂了独立完整 drafter 的 mlx_lm）实际逐请求**串行**处理 ——
    `max_concurrency` 这个配置值在这条路由下不生效，显示「并发 N」会夸大
    真实的并发能力（spec §2.4 R2 行）。三处人读显示（`up` 成功行、
    `status` 的并发列/单服务行）统一改显示「串行」，argv 不受影响
    （`--decode-concurrency` 等仍按表生成，C1）。"""
    if rule == "R2":
        return "串行"
    return str(max_concurrency) if max_concurrency else "—"


def _model_table(models):
    # type: (list) -> list
    """模型段的对齐表。**从用户视角看得懂**：这个 id 是哪个模型、装了没、
    在不在跑、占多少内存、端口是几号。

    列取自 v5.3.1 的形态（状态 / 短名 / 模型 / 端口 / PID / 内存），
    并补上本版有的两项：**状态**（就绪 / 已登记未下载）与**体积**。
    v5.3.1 的表是本项目在这件事上唯一的既有参照，Alex 点名要它。
    """
    cols = [("", lambda m: ui._RUN.get(m.get("running"), "·")),
            ("ID", lambda m: m.get("id") or ""),
            ("模型", lambda m: m.get("repo") or "—"),
            ("状态", lambda m: m.get("state") or "—"),
            ("端口", lambda m: f":{m['port']}" if m.get("port") else "—"),
            ("后端", lambda m: m.get("backend") or "—"),
            ("并发", lambda m: _concurrency_display(m.get("rule"), m.get("max_concurrency"))),
            ("PID", lambda m: str(m["pid"]) if m.get("running") and m.get("pid") else "—"),
            ("内存", lambda m: f"{m['rss_mb']:.0f} MB" if m.get("rss_mb") else "—"),
            ("体积", lambda m: f"{m['size_mb']:.0f} MB" if m.get("size_mb") else "—")]
    cells = [[fn(m) for _, fn in cols] for m in models]
    w = [max(ui._w(h), max((ui._w(r[i]) for r in cells), default=0))
         for i, (h, _) in enumerate(cols)]
    out = ["  " + "  ".join(ui._pad(h, w[i]) for i, (h, _) in enumerate(cols)).rstrip(),
           "  " + "  ".join("─" * w[i] for i in range(len(cols)))]
    for row in cells:
        out.append("  " + "  ".join(ui._pad(c, w[i]) for i, c in enumerate(row)).rstrip())

    running = sum(1 for m in models if m.get("running"))
    mem = sum(m.get("rss_mb") or 0 for m in models)
    tail = f"  {len(models)} 个已登记，{running} 个在跑"
    if mem:
        tail += f"，共占 {mem:.0f} MB 内存"
    out += ["", tail]
    return out


def _service_status(p):
    # type: (dict) -> str
    run = p.get("running")
    head = f"{ui._mark(run)} {p.get('service')}   " + (
        "在跑" if run else ("状态不可判定" if run is None else "未运行"))
    vals = dict(p, max_concurrency=_concurrency_display(p.get("rule"), p.get("max_concurrency")))
    rows = [(k, ui._flat(vals.get(k))) for k in ("pid", "port", "backend",
                                                 "max_concurrency", "undeterminable")
            if p.get(k) is not None]
    return "\n".join([head] + (ui._kv(rows, indent="    ") if rows else []))


def _svc_change(p):
    # type: (dict) -> str
    """`up` / `down` / `restart` 的结论。批量与单个共用一种形状。"""
    res = p.get("results")
    if isinstance(res, dict) and "targets" in p:
        tgts = p.get("targets") or []
        if not tgts:
            return "（没有正在运行的服务）"
        w = max(ui._w(t) for t in tgts)
        lines = []
        for t in tgts:
            r = res.get(t) or {}
            rc = r.get("rc", 0)
            mark = "✓" if not rc else "✗"
            if rc:
                # **渲染成文案，不打裸 key**（L1·ui-10）。此前这里直接用
                # `r["key"]`，于是 `mlx down --all` 的失败行甩给用户一个
                # `owns.undeterminable` —— 批量操作里最需要读懂的那一行，
                # 而文案表里明明有中文。
                why = (ui._text(r["key"], "失败", **(r.get("args") or {}))
                       if r.get("key") else "失败")
                lines.append(f"  {mark} {ui._pad(t, w)}   {why}（rc {rc}）")
            else:
                bits = [f"{k} {r[k]}" for k in ("port", "pid") if r.get(k)]
                verb = ("已启动" if r.get("started") else
                        "已停止" if r.get("stopped") else "完成")
                lines.append(f"  {mark} {ui._pad(t, w)}   {verb}"
                             + ("   " + "  ".join(bits) if bits else ""))
        return "\n".join(lines)
    act = p.get("started") or p.get("stopped") or p.get("service") or ""
    if p.get("already_stopped"):
        return f"  · {act}   本来就没在跑"
    if p.get("started") and p.get("backend"):
        # spec §6.5：`✓ qwen38 已启动 · mlx_vlm · 并发 8 · port 8100` —— 判定结论要看得见
        # （R2 挂 drafter 时改显示「串行」，不夸大并发能力，C1）
        conc = _concurrency_display(p.get("rule"), p.get("max_concurrency"))
        return (f"  ✓ {act} 已启动 · {p['backend']} · "
                f"{conc if p.get('rule') == 'R2' else '并发 ' + conc}"
                f" · port {p.get('port')}")
    if p.get("started") and p.get("interpreter"):
        return (f"  ✓ {act} 已启动 · 绑定 {p.get('bind')} · TLS {'开' if p.get('tls') else '关'}"
                f" · port {p.get('port')}")
    verb = "已启动" if p.get("started") else ("已停止" if p.get("stopped") else "")
    tail = "".join(f"   {k} {p[k]}" for k in ("port", "pid") if p.get(k))
    return f"  ✓ {act}   {verb}{tail}"


def _down_dry_run(p):
    # type: (dict) -> str
    """`down` / `restart --dry-run` 的预览：**会停掉谁**。

    此前这条路径不存在 —— 门在 `supervisor.down()` **之后**，于是
    `mlx down api --dry-run` 真杀进程、真删运行记录。用户得到的
    「预览」是一个已经发生的事实。
    """
    svc = p.get("service")
    if p.get("undeterminable"):
        return (f"  这是预览（--dry-run），一个字节都没动\n"
                f"  ⚠ {svc} 的所有权不可证（{p['undeterminable']}）—— "
                f"真执行时会拒绝，先跑 `mlx status`")
    if not p.get("running"):
        return (f"  这是预览（--dry-run），一个字节都没动\n"
                f"  {svc} 没在跑，执行后也是「已停止」")
    what = "停掉再起回来" if p.get("restarts") else "停掉"
    return (f"  这是预览（--dry-run），一个字节都没动\n"
            f"  将{what}  {svc}（pid {p.get('pid')}，端口 {p.get('port')}）\n"
            f"  去掉 --dry-run 执行")


ui.register({
    "svc_down_dry_run": _down_dry_run,
    "svc": _svc_change,
    "tool": _tool_status,
    "service": _service_status,
})
