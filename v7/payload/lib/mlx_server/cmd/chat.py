"""D · REPL。一轮生成期间向 `entry.on_interrupt` 压「取消本轮」闭包。"""
import os

from .. import sessions, store, supervisor
from ..result import Done, Refused, is_unknown


def _log(ctx, service):
    # type: (object, str) -> str
    """后端日志路径。**错误文案要指向它，不要指向 `mlx status`** ——
    `mlx_lm.server` 先 bind 端口、首次请求才加载模型，所以「端口在监听」
    与「模型能用」是两件事。此前文案让用户去跑 `mlx status`，而它会回答
    「在跑」：两条命令对同一事实给出相反答案，用户困在中间。
    真话在日志里（2026-09-02 实测那次是
    `ValueError: Model type qwen3_5_mtp not supported`）。
    """
    return os.path.join(ctx.root_data, "logs", f"{service}.log")


def run(ctx, stack, inv, deps=None):
    deps = deps or {}
    send = deps.get("send")
    if send is None:
        return Refused(3, "chat.no_transport")

    # ── 存在性校验必须在**任何东西被创建之前**（`R-SAFETY-11`）───────────
    # 来源栏点名的 v6 症状是「详情命令对未注册短名崩溃」；这里的症状更隐蔽：
    # `chat` 的第一个位置参数是**模型 id**，而用户很自然会把问题写在那儿
    # （`mlx chat "如何查询手机sn号"`）。校验缺席时，那句话被当成服务名，
    # 一路走到「连不上后端 如何查询手机sn号」，还报出一个
    # `logs/如何查询手机sn号.log` 的荒谬路径 —— 2026-09-02 Alex 实测撞到。
    #
    # **位置在 `sessions.new` 之前**：那一行会在盘上建一个会话文件，
    # 放在校验之后等于每敲错一次就留一份垃圾，且违反 BCR-R-01
    # （`Refused` 的前提是零资源已创建）。
    reg = store.load(ctx, store.MODELS)
    rec = reg.by_id(inv.target)
    if rec is None:
        known = [r.id for r in reg.records]
        return Refused(6, "chat.model_not_found", id=inv.target,
                       known="、".join(known) if known else "（还没登记任何模型）",
                       example=known[0] if known else "<id>")

    sid = (inv.args or {}).get("session") or sessions.new(ctx, inv.target)
    sess = sessions.load(ctx, sid) or {"sid": sid, "turns": []}
    turns = list(sess.get("turns", []))

    # **第一个参数是端口，不是模型 id。** 此前这里传 `inv.target`，
    # URL 成了 `http://127.0.0.1:qwen05b/v1/chat/completions` ——
    # 服务在跑也永远「连不上后端」。`bench` 早修过同型的一处，chat 漏了。
    # **「在不在跑」要问 `running`，不能拿「有没有端口」当代理**。
    # `effective_port` 在没有运行记录时**回落到注册表登记的端口**
    # （`supervisor.py:213`），所以服务停着它照样返回 8100 ——
    # 我第一版按 `port is None` 判，实测毫无作用，报的仍是
    # 「进程在跑但连不上」。**能拿到端口不等于有人在监听它。**
    alive = supervisor.running(ctx, inv.target)
    if not is_unknown(alive) and alive.value is False:
        return Refused(6, "chat.not_running", service=inv.target)
    port = supervisor.effective_port(
        ctx, inv.target, store.model_port(rec))
    if port is None:
        return Refused(6, "chat.not_running", service=inv.target)

    def _one(text):
        """发一轮。返回回复正文；失败返回 None。**取消闭包压清理栈**（INV-06 的
        意图栈还没有所有者，见 KNOWN-ISSUES 第 7 条；这里先用清理栈占位）。"""
        cancelled = {"v": False}
        h = stack.push(lambda: cancelled.__setitem__("v", True)) if stack else None
        try:
            return send(port, turns + [{"role": "user", "content": text}],
                        model=supervisor.effective_model_arg(ctx, inv.target))
        finally:
            if h is not None and stack:
                stack.drop(h)      # 正常结束 ⇒ 撤销登记，不执行取消

    prompt = (inv.args or {}).get("prompt")
    if prompt is not None:
        # 单轮：给了 `--prompt` 就发一次、存一次、返回
        reply = _one(prompt)
        if reply is None:
            return Refused(3, "chat.backend_unreachable", service=inv.target,
                           port=port, log=_log(ctx, inv.target))
        turns += [{"role": "user", "content": prompt},
                  {"role": "assistant", "content": reply}]
        sessions.save(ctx, sid, turns)         # **显式保存**
        return Done({"kind": "chat", "session": sid, "reply": reply,
                     "turns": len(turns)})

    # ── 多轮 REPL ──────────────────────────────────────────────
    # 模块 docstring 一直写着「REPL」，而读取循环**从来不存在** ——
    # 不给 `--prompt` 时它只开一个空会话就返回 `turns 0` 并且 rc 0。
    # 台账给 `chat` 的空结果契约逐字是「空回复 → 零字节，且**必须 rc≠0**」，
    # 而「一轮都没聊」恰好是零字节 + rc 0，两边正好相反。
    read_line = deps.get("read_line")
    if read_line is None:
        return Refused(3, "chat.no_reader",
                       detail="多轮模式需要一个输入源 —— 用 `--prompt=<问题>` 发单轮")
    said = 0
    while True:
        line = read_line("你 > ")
        if line is None:                       # EOF / Ctrl-D ⇒ 正常结束
            break
        text = line.strip()
        if not text:
            continue
        if text in ("/exit", "/quit", "/q"):
            break
        reply = _one(text)
        if reply is None:
            return Refused(3, "chat.backend_unreachable", service=inv.target,
                           port=port, log=_log(ctx, inv.target))
        turns += [{"role": "user", "content": text},
                  {"role": "assistant", "content": reply}]
        sessions.save(ctx, sid, turns)         # **每轮都存** —— 中途断了不丢
        said += 1
        # 主产物是**回复正文**（台账：逐字节只含回复正文），走 stdout
        deps.get("emit", lambda _t: None)(reply)
    if said == 0:
        # 一轮都没聊成 ⇒ 不是成功。台账的空结果契约要求 rc≠0
        return Refused(5, "chat.nothing_said", session=sid)
    return Done({"kind": "chat", "session": sid, "turns": len(turns),
                 "rounds": said})
