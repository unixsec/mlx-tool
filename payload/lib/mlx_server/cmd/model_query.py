"""D · `ls` / `info` / `rename` —— **零 commit**（只读）。"""
import os

from .. import store
from ..result import Done, Refused, is_unknown


def _attachment_types(ctx):
    # type: (object) -> list
    from ..tables import open_tables
    return [r["model_type"] for r in open_tables(ctx).drafters()]


def _modality_display(ctx, rec, attach):
    # type: (object, object, list) -> str
    """「模态」列：`vision` / `text` / `drafter` / `—`（权重不在盘上）/ `?`（判不出）。

    **只读 `config.json`，不渲染模板**（spec §6.5）：模板渲染是带 5 s 上限的子进程，
    列表有几个模型就要付几次，而这一列不需要 thinking 与 effort。
    覆盖规则走 `route.effective_vision` —— 与 `up` 同一个实现，两处不会说法不一。
    """
    from .. import route, weights
    got = weights.resolve(ctx, rec.user.get("repo", ""), rec.machine.get("revision"))
    if is_unknown(got) or not got.value:
        return "—"
    facts = weights.inspect_config(got.value["path"], attach)
    if is_unknown(facts):
        return "?"
    if not facts.value.get("standalone", True):
        return "drafter"
    v = route.effective_vision(facts.value.get("vision"), rec.user.get("modality"))
    return "?" if v is None else ("vision" if v else "text")


def _merged(r):
    # type: (object) -> dict
    """`model ls` 一行的字段：user 与 machine 段合并；端口列是**生效端口**
    （user 段优先，与 up / status / config get 同一个读函数，终审 C I4）。"""
    out = dict(r.user, **r.machine)
    port = store.model_port(r)
    if port is not None:
        out["port"] = port
    return out


def run(ctx, stack, inv, deps=None):
    if inv.verb == "ls":
        res = store.load(ctx, store.MODELS)
        # `drafters.tsv` 坏掉不该让这条只读列表命令整体不可用（C6）——
        # 降级为「模态列全占位 `?`」，其余信息（id/端口/状态）照常给。
        # `model info` 不走这条降级路径，仍保持原行为（不在本项范围）。
        from ..tables import TableError
        try:
            attach = _attachment_types(ctx)
            drafters_ok = True
        except (TableError, OSError):
            attach = []
            drafters_ok = False

        def _modality(r):
            return "?" if not drafters_ok else _modality_display(ctx, r, attach)

        return Done({"kind": "models",
                     "models": [dict({"id": r.id}, **_merged(r),
                                     state=store.model_state(
                                         {"user": r.user, "machine": r.machine}),
                                     modality_display=_modality(r))
                                for r in res.records],
                     "bad_lines": [(b.lineno, b.reason_key) for b in res.bad_lines],
                     "drafters_unreadable": not drafters_ok})
    if inv.verb == "info":
        r = store.load(ctx, store.MODELS).by_id(inv.target)
        if r is None:
            return Refused(6, "model.not_found", id=inv.target)
        # **期望的权重路径**。手册 §3.5 把 `model info` 指定为「路径算错了」
        # 这个最主要的静默故障的唯一对照手段（「它会打印期望的权重路径，
        # 跟你 hf/modelscope 实际写的目录逐字比」）—— 而它此前不打印路径。
        from .. import schema, templates, weights
        from ..tables import open_tables
        exp = os.path.join(
            ctx.root_weights, "hub",
            "models--" + str(r.user.get("repo", "")).replace("/", "--"),
            "snapshots", str(r.machine.get("revision") or "main"))
        got = weights.resolve(ctx, r.user.get("repo", ""), r.machine.get("revision"))
        found = None if is_unknown(got) else (got.value or {}).get("path")

        attach = _attachment_types(ctx)
        modality = {"display": _modality_display(ctx, r, attach),
                    "configured": r.user.get("modality") or "auto"}
        template = {"undeterminable": "权重不在盘上"}
        if found:
            facts = weights.inspect_config(found, attach)
            modality["basis"] = (facts.reason if is_unknown(facts)
                                 else facts.value.get("vision_src"))
            py = os.path.join(ctx.venv, "bin", "python3") if getattr(ctx, "venv", "") else ""
            if py and os.path.exists(py):
                t = templates.inspect_template(
                    found, py, ps_path=(getattr(ctx, "bins", None) or {}).get("ps", "/bin/ps"))
                template = ({"undeterminable": t.reason or t.kind} if is_unknown(t) else
                            {"thinking_default": t.value.get("thinking_default"),
                             "effort_domain": list(t.value.get("effort_domain") or [])})
            else:
                template = {"undeterminable": "venv 不可用，渲染不了模板"}
        defs = schema.keydefs(open_tables(ctx))
        old = ([f"user.{k}" for k in ("type",) if k in r.user]
               + [f"machine.{k}" for k in ("type_evidence",) if k in r.machine])
        retired = sorted(k for k in r.user if k not in defs and k not in ("repo", "type"))
        return Done({"kind": "model_info", "id": r.id,
                     "user": r.user, "machine": r.machine,
                     "weights_expected": exp,
                     "weights_found": found or "（没找到 —— 权重不在盘上，或目录名不对）",
                     "modality": modality, "template": template,
                     "old_fields": old, "retired_keys": retired})
    return Refused(2, "cmd.unknown_verb", verb=inv.verb)
