"""spec §4.6 聚合 / §4.7 回程改写 / §8.2「流式与聚合」。帧夹具由上游构造函数生成：
聚合结果必须等于上游**自己的**非流式响应（只允许去掉 null 字段与 timings；mlx_vlm 非流式 tool_calls 多带的
index 按 OpenAI 非流式格式去掉；mlx_lm 多出回程补上的 reasoning_content）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

FR = Path(__file__).resolve().parents[1] / "fixtures" / "frames" / "frames.json"

PROG = r"""
import copy, json
from gateway import frames as FR
from gateway.aggregate import AggregateError, Aggregator
from mlx_server.wire import SseDecoder
F = json.load(open("@FRAMES@"))
def frames_of(text, step=7):
    dec, out, raw = SseDecoder(), [], text.encode("utf-8")
    for i in range(0, len(raw), step):
        out += dec.feed(raw[i:i + step])
    assert dec.pending() == b"", "夹具流不应有未终结的帧"
    return out
def agg(family, key, cut=None):
    a = Aggregator(family)
    fr = frames_of(F[key])
    for f in (fr if cut is None else fr[:cut]):
        a.feed(f)
    try:
        return {"ok": a.result()}
    except AggregateError as e:
        return {"err": str(e)}
def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if v is not None and k != "timings"}
    if isinstance(o, list):
        return [strip(v) for v in o]
    return o
r = {}
lm = agg("chat", "mlx_lm_chat_stream")["ok"]
want_lm = copy.deepcopy(F["mlx_lm_chat_nonstream"])
want_lm["choices"][0]["message"]["reasoning_content"] = "先想一想。"
r["lm_chat_equal"] = strip(lm) == strip(want_lm)
r["lm_chat"] = lm
vlm = agg("chat", "mlx_vlm_chat_stream_tools")["ok"]
want_vlm = copy.deepcopy(F["mlx_vlm_chat_nonstream"])
for tc in want_vlm["choices"][0]["message"]["tool_calls"]:
    tc.pop("index")
r["vlm_chat_equal"] = strip(vlm) == strip(want_vlm)
r["vlm_chat"] = vlm
r["vlm_length"] = agg("chat", "mlx_vlm_chat_stream_length")["ok"]["choices"][0]
r["vlm_error"] = agg("chat", "mlx_vlm_chat_stream_error")
r["lm_truncated"] = agg("chat", "mlx_lm_chat_stream", cut=4)
r["completion"] = agg("completions", "mlx_lm_completion_stream")["ok"]
r["responses_equal"] = agg("responses", "mlx_vlm_responses_stream")["ok"] == F["mlx_vlm_responses_completed"]
r["responses_truncated"] = agg("responses", "mlx_vlm_responses_stream", cut=5)
msg = agg("messages", "mlx_vlm_messages_stream")["ok"]
r["messages_equal"] = strip(msg) == strip(F["mlx_vlm_messages_nonstream"])
r["messages"] = msg
r["messages_error"] = agg("messages", "mlx_vlm_messages_stream_error")

def rewrite_all(key, **kw):
    return b"".join(FR.rewrite(f, "qwen", **kw) for f in frames_of(F[key])).decode("utf-8")
lm_local = rewrite_all("mlx_lm_chat_stream")
lm_nousage = rewrite_all("mlx_lm_chat_stream", drop_usage=True)
r["rw_lm"] = {"backend_arg_left": "BACKEND-ARG" in lm_local, "keepalive_kept": lm_local.startswith(": keepalive"),
              "reasoning_content": lm_local.count('"reasoning_content": "先想"'),
              "usage_kept": '"usage"' in lm_local, "usage_dropped": '"usage"' not in lm_nousage,
              "done": lm_nousage.rstrip().endswith("data: [DONE]")}
rs = rewrite_all("mlx_vlm_responses_stream")
ms = rewrite_all("mlx_vlm_messages_stream")
r["rw_nested"] = {"responses_left": "BACKEND-ARG" in rs, "messages_left": "BACKEND-ARG" in ms,
                  "events_kept": rs.count("event: response.") == F["mlx_vlm_responses_stream"].count("event: response.")}
r["rw_error"] = {"chat_remote": "/Users/" in rewrite_all("mlx_vlm_chat_stream_error", peer_class="REMOTE"),
                 "chat_local": "/Users/" in rewrite_all("mlx_vlm_chat_stream_error"),
                 "anthropic_remote": rewrite_all("mlx_vlm_messages_stream_error", peer_class="REMOTE"),
                 "vlm_ok_frames_remote_same": rewrite_all("mlx_vlm_chat_stream_tools", peer_class="REMOTE")
                     == rewrite_all("mlx_vlm_chat_stream_tools")}
body = F["mlx_lm_chat_nonstream"]
patched = FR.patch_body(body, "qwen")
r["patch"] = {"model": patched["model"], "rc": patched["choices"][0]["message"].get("reasoning_content"),
              "input_untouched": "reasoning_content" not in body["choices"][0]["message"] and body["model"] == "BACKEND-ARG",
              "responses_model": FR.patch_body(F["mlx_vlm_responses_completed"], "qwen")["model"],
              "messages_model": FR.patch_body(F["mlx_vlm_messages_nonstream"], "qwen")["model"]}
from gateway import translate
r["compat"] = translate.rewrite_frame(b'data: {"model": "x", "choices": []}\n\n', "qwen").decode()
_ef = b'data: {"error": {"message": "open /Users/alex/m/w.bin failed"}}\n\n'
r["compat_err"] = {"remote": "/Users/" in translate.rewrite_frame(_ef, "qwen", "REMOTE").decode(),
                   "loopback": "/Users/" in translate.rewrite_frame(_ef, "qwen", "LOOPBACK").decode(),
                   "default": "/Users/" in translate.rewrite_frame(_ef, "qwen").decode(),
                   "unknown_class": "/Users/" in translate.rewrite_frame(_ef, "qwen", None).decode()}
print(json.dumps(r, ensure_ascii=False))
"""


def test_aggregate_and_rewrite(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, FRAMES=FR)
    assert got["lm_chat_equal"], f"mlx_lm 流式聚合 ≠ 上游非流式：{got['lm_chat']}"
    assert got["vlm_chat_equal"], f"mlx_vlm 流式聚合 ≠ 上游非流式：{got['vlm_chat']}"
    assert got["lm_chat"]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"city": "北京"}'
    assert got["vlm_length"]["finish_reason"] == "length"
    assert got["vlm_length"]["message"]["content"] == "很长的回答"
    assert got["vlm_error"]["err"].startswith("open /Users/"), "中途错误帧必须判为上游错误"
    assert "ended before" in got["lm_truncated"]["err"], "没有结束标志就断了的流不得当作正常结束"
    assert got["completion"] == {"id": "cmpl-lm", "object": "text_completion", "created": 1757900000,
                                 "model": "BACKEND-ARG", "system_fingerprint": "fp_lm",
                                 "choices": [{"index": 0, "finish_reason": "length", "text": "Once upon a time"}],
                                 "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}
    assert got["responses_equal"] is True
    assert "response.completed" in got["responses_truncated"]["err"]
    assert got["messages_equal"], f"messages 聚合 ≠ 上游非流式：{got['messages']}"
    assert got["messages_error"]["err"].startswith("open /Users/")
    assert got["rw_lm"] == {"backend_arg_left": False, "keepalive_kept": True, "reasoning_content": 1,
                            "usage_kept": True, "usage_dropped": True, "done": True}
    assert got["rw_nested"] == {"responses_left": False, "messages_left": False, "events_kept": True}
    err = got["rw_error"]
    assert (err["chat_remote"], err["chat_local"]) == (False, True), "错误帧对远端净化、对本机原文"
    assert "/Users/" not in err["anthropic_remote"] and '"type": "api_error"' in err["anthropic_remote"]
    assert err["vlm_ok_frames_remote_same"] is True, "正常帧不因来源不同而改变"
    assert got["patch"] == {"model": "qwen", "rc": "先想一想。", "input_untouched": True,
                            "responses_model": "qwen", "messages_model": "qwen"}
    assert got["compat"] == 'data: {"model": "qwen", "choices": []}\n\n'
    assert got["compat_err"] == {"remote": False, "loopback": True, "default": True, "unknown_class": False}, \
        "流已开始后的错误帧：远端（及未知来源）净化，本机原文"
