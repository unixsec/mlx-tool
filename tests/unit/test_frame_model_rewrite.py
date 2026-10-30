"""回程把 `model` 改回短 id：不只顶层与已知的两处，任何深度的 `model` 字符串都改，
用户数据所在的子树（`input`、`metadata`、`parameters`、`schema`、`tools`）不碰。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json
from gateway import frames as F
def fr(obj, event=None):
    head = ("event: %s\n" % event) if event else ""
    return (head + "data: " + json.dumps(obj) + "\n\n").encode()
def back(b):
    return json.loads(F.parse(b)[1])
out = {
    "item": back(F.rewrite(fr({"type": "response.output_item.added", "item": {"model": "/w/Qwen-dir"}}),
                           "qwen", "LOOPBACK")),
    "delta": back(F.rewrite(fr({"choices": [{"delta": {"model": "Qwen-dir"}}]}), "qwen", "LOOPBACK")),
    "top": back(F.rewrite(fr({"model": "Qwen-dir", "choices": []}), "qwen", "LOOPBACK")),
    "tool_input": F.patch_body({"content": [{"type": "tool_use", "input": {"model": "keep"}}]}, "qwen"),
    "metadata": F.patch_body({"model": "x", "metadata": {"model": "keep"}}, "qwen"),
    "schema": F.patch_body({"tools": [{"parameters": {"properties": {"model": "keep"}}}]}, "qwen"),
}
print(json.dumps(out))
"""


def test_model_rewritten_at_any_depth_but_not_inside_user_data(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True)
    assert got["item"]["item"]["model"] == "qwen", got
    assert got["delta"]["choices"][0]["delta"]["model"] == "qwen", got
    assert got["top"]["model"] == "qwen", got
    assert got["tool_input"]["content"][0]["input"] == {"model": "keep"}, got
    assert got["metadata"] == {"model": "qwen", "metadata": {"model": "keep"}}, got
    assert got["schema"]["tools"][0]["parameters"]["properties"]["model"] == "keep", got
