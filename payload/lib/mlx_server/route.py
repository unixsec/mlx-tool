"""P 层 · 后端判定。**纯函数、零 I/O**（spec §2.4）。

输入全部由调用方采好递进来（`weights.inspect_config` / `templates.inspect_template` /
`caps.get` / 校验段「生效配置」），本模块只裁决 —— 与 `fitness` 同一种分工，
每条规则因此能不碰盘逐样例对拍。
"""
import hashlib

from .result import Refused


class Route:
    """一次判定的结论。`row` 是 `backends.tsv` 的那一行，`backendarg` 只读它。"""

    __slots__ = ("backend", "row", "rule", "thinking", "effort_domain", "vision_src",
                 "size_mb", "max_concurrency", "key_fp", "template_note")

    def __init__(self, backend, row, rule, thinking, effort_domain, vision_src, size_mb,
                 max_concurrency, key_fp, template_note=None):
        # type: (str, dict, str, bool, list, str, object, object, object, object) -> None
        self.backend = backend
        self.row = dict(row)
        self.rule = rule
        self.thinking = thinking
        self.effort_domain = list(effort_domain)
        self.vision_src = vision_src
        self.size_mb = size_mb
        self.max_concurrency = max_concurrency
        self.key_fp = key_fp
        # 模板判不出时的原因（spec §2.3：按空值域处理，dry-run 要标注出来）
        self.template_note = template_note

    def to_dict(self):
        # type: () -> dict
        """给 `--dry-run` 与运行记录用。**不含密钥**，只含指纹。"""
        return {"backend": self.backend, "rule": self.rule, "thinking": self.thinking,
                "effort_domain": list(self.effort_domain), "vision_src": self.vision_src,
                "size_mb": self.size_mb, "ready": self.row.get("ready"),
                "max_concurrency": self.max_concurrency, "template_note": self.template_note}


def effective_vision(facts_vision, modality):
    # type: (object, object) -> object
    """模态覆盖的**唯一实现**：`model ls` 与 `up` 都调它（spec §2.4）。

    `text` ⇒ False · `vision` ⇒ True · `auto` / 未设 ⇒ 原事实（None = 判不出）。
    """
    if modality == "text":
        return False
    if modality == "vision":
        return True
    return facts_vision


def key_fingerprint(value):
    # type: (object) -> object
    """密钥指纹：`sha256` 前 12 hex。运行记录只存它，用来判「密钥已变更，需 restart」。"""
    if not value:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def decide(model_id, facts, template, draft, cfg_values, packages, backends, size_mb):
    # type: (str, dict, object, object, dict, object, dict, object) -> object
    """→ `Route` 或 `Refused`。**按 S1 U1 U2 R0 R1 R2 R3 R4 P1 P2 P3 顺序短路。**"""
    if not facts.get("standalone", True):
        return Refused(6, "svc.not_standalone_model", id=model_id,
                       model_type=facts.get("model_type") or "?")
    vision = effective_vision(facts.get("vision"), cfg_values.get("modality"))
    if vision is None:
        return Refused(9, "svc.inspect_undeterminable", id=model_id,
                       detail=facts.get("vision_src") or "")
    if draft is not None:
        draft_id, dfacts = draft
        if dfacts is None or dfacts.get("vision") is None:
            return Refused(9, "svc.draft_inspect_undeterminable", id=model_id, draft=draft_id)
        if dfacts.get("attachment"):
            return Refused(6, "svc.draft_unsupported", id=model_id, draft=draft_id,
                           model_type=dfacts.get("model_type") or "?")
        if vision:
            return Refused(6, "svc.draft_backend_conflict", id=model_id, draft=draft_id)
        backend, rule = "mlx_lm", "R2"
    else:
        backend, rule = ("mlx_vlm", "R3") if vision else ("mlx_lm", "R4")
    row = backends.get(backend)
    if row is None:
        return Refused(3, "tables.unreadable", detail=f"backends.tsv 里没有 {backend}")
    needs_key = row.get("key_env") not in ("", "-", None)
    if row.get("ready") == "load" or needs_key:
        if backend not in set(packages or ()):
            return Refused(6, "svc.backend_missing", id=model_id, pkg=backend)
        if needs_key and not cfg_values.get("backend.api_key"):
            return Refused(6, "svc.backend_key_missing", id=model_id)
        if row.get("ready") == "load" and size_mb is None:
            return Refused(9, "svc.size_undeterminable", id=model_id)
    ok = template is not None and not template.is_unknown()
    tv = template.value if ok else {}
    return Route(backend, row, rule,
                 thinking=bool(tv.get("thinking_default")) and row.get("thinking") == "template",
                 effort_domain=tv.get("effort_domain") or [],
                 vision_src=facts.get("vision_src") or "",
                 size_mb=size_mb,
                 max_concurrency=cfg_values.get("max_concurrency"),
                 key_fp=key_fingerprint(cfg_values.get("backend.api_key")) if needs_key else None,
                 template_note=((template.reason or template.kind)
                                if template is not None and template.is_unknown() else None))
