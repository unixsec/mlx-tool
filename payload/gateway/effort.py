"""T2 · 思考开关、强度与长度归一（spec §4.5、D10）。**纯函数**。

客户端表达「思考」的方式有五六种（OpenAI `reasoning_effort` / `reasoning.effort`、Anthropic
`thinking.type`、Qwen 系 `enable_thinking`、`chat_template_kwargs`），两个后端各认其中一部分：
mlx_vlm 认顶层 `enable_thinking` / `reasoning_effort` / `reasoning`，**不读 `chat_template_kwargs`**；
mlx_lm 只认 `chat_template_kwargs`。强度值还要落在模型模板接受的值域里 —— qwen38 只接受
low / medium / xhigh，传 `high` 模板 `raise_exception` ⇒ 后端 500。

开关与强度**独立解析**：`enable_thinking: true` + `reasoning_effort: "low"` 是「开，低强度」，
不是「以最后一个为准」。
"""

EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max")
DISABLED = frozenset({"none", "off", "disabled", "false", "0"})
LOADING_FIELDS = frozenset({"adapters", "adapter_path", "draft_model"})
_THINKING_TYPE = {"enabled": True, "adaptive": True, "disabled": False, "auto": None}


def _norm(value):
    # type: (object) -> object
    return None if value is None else str(value).strip().lower()


def _truthy(value):
    # type: (object) -> bool
    return _norm(value) not in DISABLED if isinstance(value, str) else bool(value)


def strip_loading(body):
    # type: (dict) -> dict
    """剥离按请求加载权重的字段（D7）：它们会让后端在请求路径上加载另一份权重。"""
    return {k: v for k, v in (body or {}).items() if k not in LOADING_FIELDS}


def parse_switch(body):
    # type: (dict) -> object
    """开关：首个**存在**的来源生效。→ True / False / None（未表达）。"""
    ctk = body.get("chat_template_kwargs")
    if isinstance(ctk, dict) and "enable_thinking" in ctk:
        return _truthy(ctk["enable_thinking"])
    if "enable_thinking" in body:
        return _truthy(body["enable_thinking"])
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and "type" in thinking:
        return _THINKING_TYPE.get(_norm(thinking["type"]))
    reasoning = body.get("reasoning")
    for value in (body.get("reasoning_effort"),
                  reasoning.get("effort") if isinstance(reasoning, dict) else None):
        if value is not None and _norm(value) in DISABLED:
            return False
    return None


def parse_effort(body):
    # type: (dict) -> object
    """强度：按序取**首个存在且不属禁用集合**的值（小写、去空白）。"""
    ctk = body.get("chat_template_kwargs")
    thinking = body.get("thinking")
    reasoning = body.get("reasoning")
    for value in (ctk.get("reasoning_effort") if isinstance(ctk, dict) else None,
                  thinking.get("reasoning_effort") if isinstance(thinking, dict) else None,
                  body.get("reasoning_effort"),
                  reasoning.get("effort") if isinstance(reasoning, dict) else None):
        if value is not None and _norm(value) and _norm(value) not in DISABLED:
            return _norm(value)
    return None


def map_effort(value, domain):
    # type: (object, list) -> object
    """映射到模型值域：在值域里 ⇒ 原样；否则取值域中**不低于它的最小值**，没有则取**最大值**。

    值域为空（模板不认 effort）⇒ None（不写出）。不在顺序表里的值无法比较大小 ⇒ None。
    """
    value = _norm(value)
    domain = [d for d in (domain or []) if d in EFFORT_ORDER]
    if not value or not domain:
        return None
    if value in domain:
        return value
    if value not in EFFORT_ORDER:
        return None
    rank = EFFORT_ORDER.index(value)
    ranked = sorted(domain, key=EFFORT_ORDER.index)
    for d in ranked:
        if EFFORT_ORDER.index(d) >= rank:
            return d
    return ranked[-1]


def _capabilities(snap):
    # type: (dict) -> tuple
    # 没有能力字段的记录是 1.1.0 之前起的 —— 那时只有 mlx_lm
    native = snap.get("native_template_kwargs", True)
    reads = snap.get("reads_completion_tokens", True)
    return bool(native), bool(reads), list(snap.get("effort_domain") or [])


def normalize(body, family, snap):
    # type: (dict, str, dict) -> tuple
    """→ `(新请求体, None | (status, err_type, message))`。不修改入参。"""
    out = dict(body or {})
    native, reads_completion, domain = _capabilities(snap or {})

    if family in ("responses", "messages"):
        # 这两个端点的开关由后端原生处理（mlx_vlm 认 reasoning / thinking），只把强度映射进值域
        reasoning = out.get("reasoning")
        if isinstance(reasoning, dict) and reasoning.get("effort") is not None \
                and _norm(reasoning["effort"]) not in DISABLED:
            reasoning = dict(reasoning)
            mapped = map_effort(reasoning.pop("effort"), domain)
            if mapped:
                reasoning["effort"] = mapped
            out["reasoning"] = reasoning
        if out.get("reasoning_effort") is not None and _norm(out["reasoning_effort"]) not in DISABLED:
            mapped = map_effort(out.pop("reasoning_effort"), domain)
            if mapped:
                out["reasoning_effort"] = mapped
        return out, None

    if "max_completion_tokens" in out and "max_tokens" not in out and not reads_completion:
        out["max_tokens"] = out["max_completion_tokens"]          # mlx_vlm 不读前者

    switch = parse_switch(out)
    mapped = map_effort(parse_effort(out), domain)
    for key in ("enable_thinking", "thinking", "reasoning_effort", "reasoning"):
        out.pop(key, None)
    ctk = out.get("chat_template_kwargs")
    if isinstance(ctk, dict):
        ctk = {k: v for k, v in ctk.items() if k not in ("enable_thinking", "reasoning_effort")}
        out["chat_template_kwargs"] = ctk

    if native:
        if "thinking_budget" in out:
            return out, (400, "thinking_budget_unsupported",
                         "thinking_budget is not supported by the mlx_lm backend")
        if switch is not None or mapped:
            ctk = dict(out.get("chat_template_kwargs") or {})
            if switch is not None:
                ctk["enable_thinking"] = switch
            if mapped:
                ctk["reasoning_effort"] = mapped
            out["chat_template_kwargs"] = ctk
    else:
        if switch is not None:
            out["enable_thinking"] = switch
        if mapped:
            out["reasoning_effort"] = mapped
        if out.get("chat_template_kwargs") == {} and "chat_template_kwargs" not in (body or {}):
            out.pop("chat_template_kwargs")
    return out, None
