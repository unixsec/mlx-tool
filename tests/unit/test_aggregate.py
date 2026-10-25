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
    kw.setdefault("peer_class", "LOOPBACK")             # frames.rewrite 缺省是 REMOTE（fail closed），这里显式要原文
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
r["compat"] = translate.rewrite_frame(b'data: {"model": "x", "choices": []}\n\n', "qwen", "LOOPBACK").decode()
_ef = b'data: {"error": {"message": "open /Users/alex/m/w.bin failed"}}\n\n'
r["compat_err"] = {"remote": "/Users/" in translate.rewrite_frame(_ef, "qwen", "REMOTE").decode(),
                   "loopback": "/Users/" in translate.rewrite_frame(_ef, "qwen", "LOOPBACK").decode(),
                   "default": "/Users/" in FR.rewrite(_ef, "qwen").decode(),
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
    assert got["compat_err"] == {"remote": False, "loopback": True, "default": False, "unknown_class": False}, \
        "流已开始后的错误帧：远端（及未知来源、漏传缺省）净化，本机原文"


PROG2 = r"""
import json
from gateway import frames as FR
from gateway.aggregate import Aggregator
P = "/Users/a/secret/w.bin"
def fr(obj, event=None):
    return (("event: %s\n" % event if event else "") + "data: " + (obj if isinstance(obj, str) else json.dumps(obj)) + "\n\n").encode()
shapes = {
    "response_failed": fr({"type": "response.failed", "response": {"model": "B", "error": {"message": "open " + P}}}, "response.failed"),
    "response_error_only": fr({"type": "response.in_progress", "response": {"model": "B", "error": {"message": "open " + P}}}),
    "error_with_choices": fr({"error": {"message": "open " + P}, "choices": []}),
    "detail": fr({"id": "x", "detail": "open " + P}),
    "top_message": fr({"id": "x", "message": "open " + P}),
    "list_json": fr(["open " + P]),
    "string_json": fr("open " + P),
    "non_json": ("data: open " + P + " failed\n\n").encode(),
}
r = {}
for k, f in shapes.items():
    r[k] = {c: P in FR.rewrite(f, "qwen", c).decode() for c in ("REMOTE", "LOOPBACK")}
ok = fr({"id": "c", "model": "B", "choices": [{"index": 0, "delta": {"content": "see " + P}}]})
ms = fr({"type": "message_start", "message": {"model": "B", "role": "assistant"}}, "message_start")
r["ok_same"] = FR.rewrite(ok, "qwen", "REMOTE") == FR.rewrite(ok, "qwen", "LOOPBACK")
r["ms_model"] = '"model": "qwen"' in FR.rewrite(ms, "qwen", "REMOTE").decode()
# 聚合：arguments 三片、无 index 续帧
def chunk(tc):
    return fr({"id": "c", "model": "B", "choices": [{"index": 0, "delta": {"tool_calls": [tc]}}]})
a = Aggregator("chat")
for f in (chunk({"index": 0, "id": "call_1", "type": "function", "function": {"name": "f", "arguments": '{"ci'}}),
          chunk({"index": 0, "function": {"arguments": 'ty": "北'}}),
          chunk({"index": 0, "function": {"arguments": '京"}'}}),
          fr({"id": "c", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}), fr("[DONE]".join(["", ""]))):
    a.feed(f)
r["args3"] = a.result()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
a = Aggregator("chat")      # 无 index：首片建调用，后续无 index 的续帧并入新条目（回退路径：按到达序号当 index）
a.feed(chunk({"id": "call_1", "function": {"name": "f", "arguments": "{}"}}))
a.feed(chunk({"id": "call_2", "function": {"name": "g", "arguments": "{}"}}))
a.feed(fr({"id": "c", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}))
tcs = a.result()["choices"][0]["message"]["tool_calls"]
r["noindex"] = [(t["id"], t["function"]["name"]) for t in tcs]
a = Aggregator("messages")
def mf(o):
    return fr(o, o["type"])
for o in ({"type": "message_start", "message": {"id": "m", "role": "assistant", "content": []}},
          {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "f", "input": {}}},
          {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"ci'}},
          {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": 'ty": "北'}},
          {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '京"}'}},
          {"type": "content_block_stop", "index": 0},
          {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
          {"type": "message_stop"}):
    a.feed(mf(o))
r["partial3"] = a.result()["content"][0]["input"]
from gateway import translate
try:
    translate.rewrite_frame(ok, "qwen")
    r["peer_class_required"] = False
except TypeError:
    r["peer_class_required"] = True
print(json.dumps(r, ensure_ascii=False))
"""


def test_remote_error_shapes_fail_closed_and_multichunk_aggregation(payload, sys_py):
    got = run_floor(payload, sys_py, PROG2, with_gateway=True)
    for shape in ("response_failed", "response_error_only", "error_with_choices", "detail", "top_message",
                  "list_json", "string_json", "non_json"):
        assert got[shape] == {"REMOTE": False, "LOOPBACK": True}, f"{shape}：远端必须净化、本机原文：{got[shape]}"
    assert got["peer_class_required"] is True, "translate.rewrite_frame 的 peer_class 必填，不得有 fail-open 缺省"
    assert got["ok_same"] is True, "正常内容帧（含正文里的路径）不因来源而改变"
    assert got["ms_model"] is True, "message_start 的 message 是 dict，属正常帧，model 仍改写"
    assert got["args3"] == '{"city": "北京"}', "tool_calls arguments 分片必须累加"
    assert got["noindex"] == [["call_1", "f"], ["call_2", "g"]], "无 index 的调用按到达序各自成条"
    assert got["partial3"] == {"city": "北京"}, "input_json_delta partial_json 分片必须累加"
