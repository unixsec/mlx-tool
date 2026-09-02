"""D · `ls` / `info` / `rename` —— **零 commit**（只读）。"""
import os

from .. import store
from ..result import Done, Refused, is_unknown


def run(ctx, stack, inv, deps=None):
    if inv.verb == "ls":
        res = store.load(ctx, store.MODELS)
        return Done({"kind": "models",
                     "models": [dict({"id": r.id}, **dict(r.user, **r.machine),
                                   state=store.model_state(
                                       {"user": r.user, "machine": r.machine}))
                                for r in res.records],
                     "bad_lines": [(b.lineno, b.reason_key) for b in res.bad_lines]})
    if inv.verb == "info":
        r = store.load(ctx, store.MODELS).by_id(inv.target)
        if r is None:
            return Refused(6, "model.not_found", id=inv.target)
        # **期望的权重路径**。手册 §3.5 把 `model info` 指定为「路径算错了」
        # 这个最主要的静默故障的唯一对照手段（「它会打印期望的权重路径，
        # 跟你 hf/modelscope 实际写的目录逐字比」）—— 而它此前不打印路径。
        from .. import weights
        exp = os.path.join(
            ctx.root_weights, "hub",
            "models--" + str(r.user.get("repo", "")).replace("/", "--"),
            "snapshots", str(r.machine.get("revision") or "main"))
        got = weights.resolve(ctx, r.user.get("repo", ""), r.machine.get("revision"))
        found = None if is_unknown(got) else (got.value or {}).get("path")
        return Done({"kind": "model_info", "id": r.id,
                     "user": r.user, "machine": r.machine,
                     "weights_expected": exp,
                     "weights_found": found or "（没找到 —— 权重不在盘上，或目录名不对）"})
    return Refused(2, "cmd.unknown_verb", verb=inv.verb)
