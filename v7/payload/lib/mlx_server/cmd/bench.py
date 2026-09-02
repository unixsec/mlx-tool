"""D · 一次基准。提示词指纹随主产物输出，**不落盘**（creates_resource == no）。"""
import hashlib

from ..result import Done, Refused, is_unknown


def run(ctx, stack, inv, deps=None):
    deps = deps or {}
    # **要的是 `bench`（bench_once）不是 `send`（send_chat）** ——
    # 两者签名不同：`send_chat(port, messages, ...)` 的第一个参数是端口、
    # 第二个是消息**列表**，而这里此前传的是 `(模型id, 提示词字符串)` ⇒
    # URL 变成 `http://127.0.0.1:demo1/…` ⇒ ValueError 直接崩到用户脸上。
    # 签名对不上却没人发现，说明这条命令从未被真正跑过。
    bench = deps.get("bench")
    if bench is None:
        return Refused(3, "bench.no_transport")
    prompt = (inv.args or {}).get("prompt") or ""
    fp = hashlib.sha256(prompt.encode()).hexdigest()[:16]

    if inv.dry_run:
        # **dry-run 不得发出任何外部请求。** 此前这里直行到 `send`，于是
        # `mlx bench <不存在的目标> --dry-run` 崩在 http.client.InvalidURL ——
        # 用户用 `--dry-run` 正是为了「先看看会发生什么」，那里给他 traceback
        # 是最坏的时机，而且它已经**做了**一次网络尝试，与 dry-run 的语义相反。
        return Done({"service": inv.target, "prompt_fingerprint": fp,
                     "would_send": True})

    # 端口来自注册表 —— 用户给的是模型 id，不是端口。
    # 解析走 `supervisor.effective_port`（唯一实现点），不再各抄一份。
    import os

    from .. import store, supervisor
    # 存在性校验先行（`R-SAFETY-11`），同 `chat` 的那条
    reg = store.load(ctx, store.MODELS)
    rec = reg.by_id(inv.target)
    if rec is None:
        known = [r.id for r in reg.records]
        return Refused(6, "bench.model_not_found", id=inv.target,
                       known="、".join(known) if known else "（还没登记任何模型）",
                       known_first=known[0] if known else "<id>")
    # 「在不在跑」问 `running`，不拿端口当代理（同 `chat` 的那条注释）
    alive = supervisor.running(ctx, inv.target)
    if not is_unknown(alive) and alive.value is False:
        return Refused(6, "bench.not_running", service=inv.target)
    port = supervisor.effective_port(
        ctx, inv.target, store.model_port(rec))
    if port is None:
        return Refused(6, "bench.not_running", service=inv.target)

    # INV-27：运行侧读**快照**里的 model_arg，不用 CLI 给的 id
    out = bench(int(port), prompt,
                model=supervisor.effective_model_arg(ctx, inv.target))
    if out is None:
        return Refused(3, "bench.backend_unreachable", service=inv.target,
                       port=port,
                       log=os.path.join(ctx.root_data, "logs",
                                        f"{inv.target}.log"))
    return Done({"kind": "bench", "service": inv.target,
                 "prompt_fingerprint": fp, "port": int(port), **out})
