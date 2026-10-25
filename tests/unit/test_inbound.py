"""spec §4.3 入站管线 / §8.2「变换 · 字段」「变换 · 多轮协议」：非媒体 part 与原请求逐字节相同。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROTO = Path(__file__).resolve().parents[1] / "fixtures" / "protocol"

PROG = r"""
import copy, json, tempfile
from gateway import media as M
from gateway import translate as T
PNG = b"\x89PNG" + b"p" * 50
def fake_fetch(url, peer, nets, max_bytes, kinds):
    return {"content_type": "image/png", "data": PNG, "host": "img.test"}
LIMITS = {"media_max_mb": 128, "media_max_parts": 32, "inflight_max_mb": 2048}
store = M.Store(tempfile.mkdtemp())
def media_ctx():
    return {"nets": [], "limits": LIMITS, "ticket": M.Ticket(M.Budget(1 << 30), store),
            "store": store, "fetcher": fake_fetch}
VLM = {"backend": "mlx_vlm", "model_arg": "qwen38", "effort_domain": ["low", "medium", "xhigh"],
       "reads_completion_tokens": False, "native_template_kwargs": False}
LM = dict(VLM, backend="mlx_lm", reads_completion_tokens=True, native_template_kwargs=True)
canon = lambda o: json.dumps(o, sort_keys=True, ensure_ascii=False)
r = {}

cc = json.load(open("@PROTO@/claude_code_messages.json"))
orig = copy.deepcopy(cc)
up, meta = T.build_upstream(cc, "messages", VLM, "REMOTE", "BACKEND-ARG", media_ctx())
m, o = up["messages"], orig["messages"]
r["cc"] = {
    "input_untouched": canon(cc) == canon(orig),
    "top_level_same": all(canon(up[k]) == canon(orig[k]) for k in ("system", "metadata", "thinking", "tools", "max_tokens")),
    "text_parts_same": [canon(m[0]["content"][0]) == canon(o[0]["content"][0]),
                        canon(m[2]["content"][0]["content"][0]) == canon(o[2]["content"][0]["content"][0]),
                        canon(m[2]["content"][1]) == canon(o[2]["content"][1])],
    "assistant_same": canon(m[1]) == canon(o[1]),
    "images": [m[0]["content"][1]["source"]["type"], m[2]["content"][0]["content"][1]["source"]["type"]],
    "tool_result_other_keys": sorted(m[2]["content"][0]) == sorted(o[2]["content"][0]),
    "model": up["model"], "stream": up["stream"], "meta": meta,
    "no_stream_options": "stream_options" not in up}

cx = json.load(open("@PROTO@/codex_responses.json"))
orig = copy.deepcopy(cx)
up, meta = T.build_upstream(cx, "responses", VLM, "REMOTE", "BACKEND-ARG", media_ctx())
r["cx"] = {
    "items_same": [canon(up["input"][i]) == canon(orig["input"][i]) for i in (1, 2, 3, 4, 5)],
    "text_same": canon(up["input"][0]["content"][0]) == canon(orig["input"][0]["content"][0]),
    "image": up["input"][0]["content"][1]["image_url"][:22],
    "top_level_same": all(canon(up[k]) == canon(orig[k]) for k in
                          ("instructions", "store", "parallel_tool_calls", "include", "tools")),
    "reasoning": up["reasoning"], "model": up["model"], "meta": meta}

chat = {"model": "qwen38", "adapters": "x", "adapter_path": "y", "draft_model": "z",
        "messages": [{"role": "user", "content": "hi"}], "thinking": {"type": "disabled"},
        "max_completion_tokens": 4000, "stream_options": {"continuous_usage_stats": True},
        "tools": [{"type": "function", "function": {"name": "f"}}], "top_k": 20}
for name, snap in (("vlm", VLM), ("lm", LM)):
    up, meta = T.build_upstream(chat, "chat", snap, "LOOPBACK", "BACKEND-ARG")
    r["chat_" + name] = {"up": up, "meta": meta}
try:
    T.build_upstream({"model": "m", "messages": [], "thinking_budget": 64}, "chat", LM, "LOOPBACK", "B")
    r["budget"] = None
except T.InboundError as e:
    r["budget"] = list(e.reply())
try:
    T.build_upstream({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "/Users/alex/a.png"}}]}]},
        "chat", VLM, "REMOTE", "B", media_ctx())
    r["media_err"] = None
except M.MediaError as e:
    r["media_err"] = e.reply()[1]["error"]["type"]
r["legacy_inbound"] = T.inbound({"model": "qwen", "x": 1}, "BACKEND-ARG")
print(json.dumps(r, ensure_ascii=False))
"""


def test_build_upstream(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, PROTO=PROTO)
    cc = got["cc"]
    assert cc["input_untouched"] is True, "不得修改客户端请求体"
    assert cc["top_level_same"] is True, "system / metadata / thinking / tools 逐字节不变"
    assert cc["text_parts_same"] == [True, True, True] and cc["assistant_same"] is True, \
        "thinking / tool_use / text / tool_result 文本逐字节不变"
    assert cc["images"] == ["base64", "base64"], "顶层图片与 tool_result 内图片都要代取"
    assert cc["tool_result_other_keys"] is True
    assert (cc["model"], cc["stream"], cc["no_stream_options"]) == ("BACKEND-ARG", True, True)
    assert cc["meta"] == {"client_stream": True, "client_usage": False}
    cx = got["cx"]
    assert cx["items_same"] == [True] * 5 and cx["text_same"] is True and cx["top_level_same"] is True
    assert cx["image"] == "data:image/png;base64,"
    assert cx["reasoning"] == {"summary": "auto", "effort": "xhigh"}, "responses 只映射 effort"
    assert cx["model"] == "BACKEND-ARG"
    vlm, lm = got["chat_vlm"], got["chat_lm"]
    for name, g in (("vlm", vlm), ("lm", lm)):
        up = g["up"]
        assert not any(k in up for k in ("adapters", "adapter_path", "draft_model")), f"{name}：加载类字段未剥离"
        assert up["stream"] is True and up["model"] == "BACKEND-ARG"
        assert up["stream_options"] == {"continuous_usage_stats": True, "include_usage": True}
        assert (up["tools"], up["top_k"]) == ([{"type": "function", "function": {"name": "f"}}], 20)
        assert g["meta"] == {"client_stream": False, "client_usage": False}
    assert vlm["up"]["enable_thinking"] is False and vlm["up"]["max_tokens"] == 4000
    assert lm["up"]["chat_template_kwargs"] == {"enable_thinking": False} and "max_tokens" not in lm["up"]
    assert got["budget"] == [400, {"error": {"type": "thinking_budget_unsupported",
                                             "message": "thinking_budget is not supported by the mlx_lm backend"}}]
    assert got["media_err"] == "media_source_rejected"
    assert got["legacy_inbound"] == {"model": "BACKEND-ARG", "x": 1}
