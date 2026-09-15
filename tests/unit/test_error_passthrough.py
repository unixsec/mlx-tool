"""spec §4.7 / D21：后端 4xx 对本机来源原文、对远端来源去掉路径与异常片段。"""
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
    "leaky": leaky,
}
print(json.dumps(out))
"""


def test_upstream_error_mapping(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    st, body = got["remote"]
    raw = str(body)
    assert st == 400
    for s in ("/Users", "~/secret", "Traceback", "x.py"):
        assert s not in raw, f"REMOTE 错误体里仍有「{s}」：{raw}"
    assert "<redacted>" in raw
    assert body["error"]["type"] == "invalid_request_error" and body["error"]["code"] == "bad_value"
    assert body["error"]["param"] == "/keep/me", "type / code / param 必须原样保留"
    assert body["repo"] == "mlx-community/Qwen3.8-27B-8bit", "仓库名不是路径，不得被净化"
    assert got["local"] == [400, got["leaky"]], "本机来源应收到原文"
    assert got["auth"] == [502, {"error": {"message": "backend rejected the gateway credentials",
                                           "type": "upstream_auth_failed"}}]
    assert got["5xx"][0] == 502 and got["5xx"][1]["error"]["type"] == "upstream_error"
    assert "/Users" not in str(got["5xx"]), "5xx 体一律不透传"
    assert got["422"] == [422, {"error": {"message": "bad", "type": "t"}}]
    assert got["text_remote"][0] == 400
    assert got["text_remote"][1]["error"]["type"] == "upstream_error"
    assert "/Users" not in str(got["text_remote"])
