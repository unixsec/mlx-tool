"""spec §4.5 / D10：开关与强度独立解析；effort 按模型值域映射；写出位置按后端；长度字段映射。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json
from gateway import effort as E
VLM = {"backend": "mlx_vlm", "effort_domain": ["low", "medium", "xhigh"],
       "reads_completion_tokens": False, "native_template_kwargs": False}
LM = {"backend": "mlx_lm", "effort_domain": ["low", "medium", "xhigh"],
      "reads_completion_tokens": True, "native_template_kwargs": True}
NODOMAIN = dict(VLM, effort_domain=[])
LEGACY = {"model_arg": "x"}
def run(body, family, snap):
    out, err = E.normalize(body, family, snap)
    return {"body": out, "err": list(err) if err else None}
r = {}
OFF = {"ctk": {"chat_template_kwargs": {"enable_thinking": False}},
       "top": {"enable_thinking": False},
       "thinking": {"thinking": {"type": "disabled"}},
       "effort_none": {"reasoning_effort": "none"},
       "reasoning_off": {"reasoning": {"effort": "off"}}}
for name, extra in OFF.items():
    for bname, snap in (("vlm", VLM), ("lm", LM)):
        r["off_%s_%s" % (name, bname)] = run(dict({"model": "m", "messages": []}, **extra), "chat", snap)["body"]
r["independent_vlm"] = run({"enable_thinking": True, "reasoning_effort": "low"}, "chat", VLM)["body"]
r["independent_lm"] = run({"enable_thinking": True, "reasoning_effort": "low"}, "chat", LM)["body"]
r["auto"] = E.parse_switch({"thinking": {"type": "auto"}})
r["adaptive"] = E.parse_switch({"thinking": {"type": "adaptive"}})
r["first_wins"] = E.parse_switch({"chat_template_kwargs": {"enable_thinking": True}, "enable_thinking": False})
r["effort_order"] = E.parse_effort({"chat_template_kwargs": {"reasoning_effort": "off"},
                                    "thinking": {"reasoning_effort": "HIGH "}, "reasoning_effort": "low"})
dom = ["low", "medium", "xhigh"]
r["map"] = {v: E.map_effort(v, dom) for v in ("minimal", "low", "medium", "high", "xhigh", "max", "turbo")}
r["map_empty"] = E.map_effort("high", [])
r["chat_high_vlm"] = run({"reasoning_effort": "high"}, "chat", VLM)["body"]
r["chat_high_nodomain"] = run({"reasoning_effort": "high"}, "chat", NODOMAIN)["body"]
r["responses"] = run({"reasoning": {"effort": "high", "summary": "auto"}}, "responses", VLM)["body"]
r["responses_nodomain"] = run({"reasoning": {"effort": "high", "summary": "auto"}}, "responses", NODOMAIN)["body"]
r["messages"] = run({"reasoning_effort": "max", "thinking": {"type": "enabled", "budget_tokens": 512}},
                    "messages", VLM)["body"]
r["budget_lm"] = run({"thinking_budget": 512}, "chat", LM)
r["budget_vlm"] = run({"thinking_budget": 512}, "chat", VLM)
r["len_vlm"] = run({"max_completion_tokens": 4000}, "chat", VLM)["body"]
r["len_lm"] = run({"max_completion_tokens": 4000}, "chat", LM)["body"]
r["len_both"] = run({"max_completion_tokens": 4000, "max_tokens": 10}, "chat", VLM)["body"]
r["len_legacy"] = run({"max_completion_tokens": 4000}, "chat", LEGACY)["body"]
keep = {"tools": [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}],
        "top_k": 20, "parallel_tool_calls": False, "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"preserve_thinking": True, "enable_thinking": True}}
kept = run(dict(keep), "chat", VLM)["body"]
r["kept"] = {k: kept.get(k) for k in keep}
r["stripped"] = sorted(E.strip_loading({"model": "m", "adapters": "a", "adapter_path": "b",
                                        "draft_model": "c", "messages": []}))
r["legacy_lm_style"] = run({"enable_thinking": False}, "chat", LEGACY)["body"]
import copy
# completions 目前与 chat 共用同一分支（family 只区分 responses/messages）——
# 一旦有人为 completions 单开分支，这条必须先变红
r["completions_high_vlm"] = run({"reasoning_effort": "high"}, "completions", VLM)["body"]
# DISABLED 三个字符串成员：disabled / false / "0"，_truthy 与 reasoning_effort 判定都直接查它
r["switch_disabled_variants"] = {
    "top_false_str": E.parse_switch({"enable_thinking": "false"}),
    "top_zero_str": E.parse_switch({"enable_thinking": "0"}),
    "top_disabled_str": E.parse_switch({"enable_thinking": "disabled"}),
    "reasoning_effort_false": E.parse_switch({"reasoning_effort": "false"}),
    "reasoning_effort_zero": E.parse_switch({"reasoning_effort": "0"}),
    "reasoning_effort_disabled": E.parse_switch({"reasoning_effort": "disabled"}),
    "nested_reasoning_effort_zero": E.parse_switch({"reasoning": {"effort": "0"}}),
}
# _THINKING_TYPE["enabled"] 独立断言（此前只经 auto/adaptive 命中，enabled 从未被直接调用）
r["thinking_enabled"] = E.parse_switch({"thinking": {"type": "enabled"}})
# 完全未表达开关 ⇒ None，直接调用，不经 normalize 间接验证
r["switch_absent"] = E.parse_switch({"foo": "bar"})
# responses/messages 分支：effort 为禁用值时原样放行（开关由后端原生处理），此前双向都未断言
r["responses_effort_off"] = run({"reasoning": {"effort": "off", "summary": "s"}}, "responses", VLM)["body"]
r["messages_effort_off"] = run({"reasoning_effort": "off"}, "messages", VLM)["body"]
# normalize / strip_loading 不得修改入参（含嵌套结构）——T5/T6 复用同一约定
mutate_body = {"chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "low", "keep": 1},
               "reasoning": {"effort": "high", "summary": "x"}, "enable_thinking": True,
               "reasoning_effort": "low", "thinking": {"type": "enabled"},
               "adapters": "a", "model": "m"}
before_normalize = copy.deepcopy(mutate_body)
E.normalize(mutate_body, "chat", VLM)
r["no_mutate_normalize"] = mutate_body == before_normalize
mutate_resp = {"reasoning": {"effort": "high", "summary": "x"}}
before_resp = copy.deepcopy(mutate_resp)
E.normalize(mutate_resp, "responses", VLM)
r["no_mutate_responses"] = mutate_resp == before_resp
mutate_strip = {"model": "m", "adapters": "a", "adapter_path": "b", "draft_model": "c", "messages": []}
before_strip = copy.deepcopy(mutate_strip)
E.strip_loading(mutate_strip)
r["no_mutate_strip"] = mutate_strip == before_strip
print(json.dumps(r, ensure_ascii=False, sort_keys=True))
"""


def test_effort_normalization(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    normalized = ("enable_thinking", "thinking", "reasoning_effort", "reasoning")
    for name in ("ctk", "top", "thinking", "effort_none", "reasoning_off"):
        vlm, lm = got[f"off_{name}_vlm"], got[f"off_{name}_lm"]
        assert vlm["enable_thinking"] is False, f"{name}：mlx_vlm 应写顶层 enable_thinking=false"
        assert "enable_thinking" not in (vlm.get("chat_template_kwargs") or {})
        assert not any(k in vlm for k in ("thinking", "reasoning_effort", "reasoning")), vlm
        assert lm["chat_template_kwargs"]["enable_thinking"] is False, f"{name}：mlx_lm 应写进 chat_template_kwargs"
        assert not any(k in lm for k in normalized), lm
        assert vlm["model"] == "m" and vlm["messages"] == []
    assert got["independent_vlm"] == {"enable_thinking": True, "reasoning_effort": "low"}
    assert got["independent_lm"] == {"chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "low"}}
    assert (got["auto"], got["adaptive"], got["first_wins"]) == (None, True, True)
    assert got["effort_order"] == "high", "禁用值跳过，取下一个；大小写与空白归一"
    assert got["map"] == {"minimal": "low", "low": "low", "medium": "medium", "high": "xhigh",
                          "xhigh": "xhigh", "max": "xhigh", "turbo": None}
    assert got["map_empty"] is None
    assert got["chat_high_vlm"] == {"reasoning_effort": "xhigh"}
    assert got["chat_high_nodomain"] == {}, "值域为空 ⇒ 不写出 effort"
    assert got["responses"] == {"reasoning": {"effort": "xhigh", "summary": "auto"}}
    assert got["responses_nodomain"] == {"reasoning": {"summary": "auto"}}
    assert got["messages"] == {"reasoning_effort": "xhigh",
                               "thinking": {"type": "enabled", "budget_tokens": 512}}, \
        "messages 只做 effort 映射，thinking 由后端原生处理"
    assert got["budget_lm"]["err"] == [400, "thinking_budget_unsupported",
                                       "thinking_budget is not supported by the mlx_lm backend"]
    assert got["budget_vlm"] == {"body": {"thinking_budget": 512}, "err": None}
    assert got["len_vlm"] == {"max_completion_tokens": 4000, "max_tokens": 4000}
    assert got["len_lm"] == {"max_completion_tokens": 4000}, "mlx_lm 自己读 max_completion_tokens"
    assert got["len_both"] == {"max_completion_tokens": 4000, "max_tokens": 10}
    assert got["len_legacy"] == {"max_completion_tokens": 4000}, "旧记录按 mlx_lm 处理"
    kept = got["kept"]
    assert kept["tools"] == [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    assert (kept["top_k"], kept["parallel_tool_calls"]) == (20, False)
    assert kept["response_format"] == {"type": "json_object"}
    assert kept["chat_template_kwargs"] == {"preserve_thinking": True}, \
        "只移除已归一的键，chat_template_kwargs 的其他键原样保留"
    assert got["stripped"] == ["messages", "model"]
    assert got["legacy_lm_style"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert got["completions_high_vlm"] == {"reasoning_effort": "xhigh"}, \
        "completions 目前与 chat 同分支，effort 映射同样生效；未来分叉必须先经这条变红"
    assert got["switch_disabled_variants"] == {
        "top_false_str": False, "top_zero_str": False, "top_disabled_str": False,
        "reasoning_effort_false": False, "reasoning_effort_zero": False,
        "reasoning_effort_disabled": False, "nested_reasoning_effort_zero": False,
    }, "DISABLED 的 disabled / false / \"0\" 三个字符串成员必须都能关闭思考"
    assert got["thinking_enabled"] is True, "_THINKING_TYPE['enabled'] 必须映射为 True"
    assert got["switch_absent"] is None, "完全未表达开关 ⇒ None，不得与显式 False 混淆"
    assert got["responses_effort_off"] == {"reasoning": {"effort": "off", "summary": "s"}}, \
        "responses 分支对禁用值原样放行，开关留给后端原生处理"
    assert got["messages_effort_off"] == {"reasoning_effort": "off"}, \
        "messages 分支对禁用值原样放行，开关留给后端原生处理"
    assert got["no_mutate_normalize"] is True, "normalize 不得修改入参（含嵌套 dict）"
    assert got["no_mutate_responses"] is True, "responses/messages 分支同样不得修改入参"
    assert got["no_mutate_strip"] is True, "strip_loading 不得修改入参"
