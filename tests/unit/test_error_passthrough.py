"""spec §4.7 / D21：后端 4xx 对本机来源原文；对远端来源按白名单重建错误（1.2.0 起），其余部分再去掉路径。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json
from gateway import translate as T
leaky = {"error": {"message": 'Traceback (most recent call last):\n  File "/Users/alex/lab/x.py", line 3\nValueError: bad',
                   "type": "invalid_request_error", "param": "/keep/me", "code": "bad_value"},
         "detail": "cannot open /Users/alex/lab/models/mlx/hub/models--a/config.json and ~/secret.txt",
         "repo": "mlx-community/Qwen3.8-27B-8bit"}
out = {
    "remote": T.upstream_error(400, leaky, "REMOTE"),
    "local": T.upstream_error(400, leaky, "LOOPBACK"),
    "auth": T.upstream_error(401, {"detail": "/Users/alex/x"}, "LOOPBACK"),
    "5xx": T.upstream_error(503, {"detail": "/Users/alex/x"}, "LOOPBACK"),
    "422": T.upstream_error(422, {"error": {"message": "bad", "type": "t"}}, "REMOTE"),
    "text_remote": T.upstream_error(400, "oops at /Users/alex/a/b.py", "REMOTE"),
    "key_path": T.sanitize({"/Users/alex/lab/models/x/config.json": "not found"}),
    "param_list": T.upstream_error(
        400, {"error": {"type": "invalid_request_error",
                        "param": ["/Users/alex/img1.png", "/Users/alex/img2.png"]}}, "REMOTE"),
    "tilde_user": T.sanitize("open ~alex/secret.txt failed"),
    "leaky": leaky,
    "exc_text": T.upstream_error(400, {"error": {"message": "KeyError: 'vision_tower' in mlx_vlm.models.qwen",
                                                 "type": "invalid_request_error"}}, "REMOTE"),
    "ctx_len": T.upstream_error(400, {"error": {"message": "This model's maximum context length is 32768 tokens",
                                                "type": "invalid_request_error", "code": "context_length_exceeded"}},
                                "REMOTE"),
    "pydantic": T.upstream_error(422, {"detail": [{"loc": ["body", "messages", 0, "content"], "msg": "Field required",
                                                   "type": "missing", "input": {"api_key": "SECRET_IN"}}]}, "REMOTE"),
    "frame_remote": T.scrub_error({"type": "error", "error": {"type": "api_error",
                                                              "message": "RuntimeError: Metal OOM at 0x7f00"}}),
}
from gateway import frames as F
from gateway.app import aggregate_error_reply
from gateway.aggregate import AggregateError
fr = b'data: {"error": {"message": "IndexError: list index out of range", "type": "x"}}\n\n'
out["frame_rewrite"] = F.rewrite(fr, "qwen", "REMOTE").decode()
out["frame_local"] = F.rewrite(fr, "qwen", "LOOPBACK").decode()
out["agg"] = aggregate_error_reply(AggregateError(json.dumps({"message": "ValueError: shape mismatch", "code": "x1"})),
                                   "REMOTE")
print(json.dumps(out))
"""


def test_upstream_error_mapping(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    st, body = got["remote"]
    raw = str(body)
    assert st == 400
    for s in ("/Users", "~/secret", "Traceback", "x.py"):
        assert s not in raw, f"REMOTE 错误体里仍有「{s}」：{raw}"
    assert body["error"]["message"] == "backend rejected the request (HTTP 400)", raw
    assert body["error"]["type"] == "invalid_request_error" and body["error"]["code"] == "bad_value"
    assert "param" not in body["error"], "param 只在是短标识符时保留，路径形态的一律去掉"
    assert "repo" not in body, "远端错误体按严格白名单重建，不认识的键一律丢弃"
    assert got["local"] == [400, got["leaky"]], "本机来源应收到原文"
    assert got["auth"] == [502, {"error": {"message": "backend rejected the gateway credentials",
                                           "type": "upstream_auth_failed"}}]
    assert got["5xx"][0] == 502 and got["5xx"][1]["error"]["type"] == "upstream_error"
    assert "/Users" not in str(got["5xx"]), "5xx 体一律不透传"
    assert got["422"] == [422, {"error": {"message": "backend rejected the request (HTTP 422)", "type": "t"}}], \
        "不在安全句式里的 message 换成通用文案，状态码与 type 保留"
    gen = "backend rejected the request (HTTP 400)"
    assert got["exc_text"][1]["error"]["message"] == gen, f"异常原文不得回给远端：{got['exc_text']}"
    assert got["ctx_len"][1]["error"] == {"message": "This model's maximum context length is 32768 tokens",
                                          "type": "invalid_request_error", "code": "context_length_exceeded"}
    det = got["pydantic"][1]["detail"]
    assert det == [{"loc": ["body", "messages", 0, "content"], "msg": "Field required", "type": "missing"}], \
        f"422 detail 只留 loc / msg / type，input 一律丢：{det}"
    assert got["frame_remote"]["error"]["message"] == "backend rejected the request", got["frame_remote"]
    assert got["frame_remote"]["type"] == "error" and got["frame_remote"]["error"]["type"] == "api_error"
    assert "IndexError" not in got["frame_rewrite"] and "backend rejected the request" in got["frame_rewrite"]
    assert "IndexError" in got["frame_local"], "本机来源的流内错误帧看原文"
    agg = got["agg"][1]["error"]
    assert "ValueError" not in agg["message"] and agg.get("code") == "x1", f"聚合失败也按白名单：{agg}"
    assert got["text_remote"][0] == 400
    assert got["text_remote"][1]["error"]["type"] == "upstream_error"
    assert "/Users" not in str(got["text_remote"])
    assert got["key_path"] == {"<redacted>": "not found"}, \
        f"错误体的 key 本身是路径也得净化：{got['key_path']}"
    param_body = got["param_list"][1]
    assert "param" not in param_body["error"], (
        f"param 不是短标识符（这里是一串图片路径）时整个去掉：{param_body}"
    )
    assert got["tilde_user"] == "open <redacted> failed", \
        f"~user/ 形式的路径也要净化：{got['tilde_user']}"
