"""模板采证（spec §2.3）。产品跑在下限解释器上，渲染子进程用 dev venv 的解释器（有 jinja2）。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "models"

PROG = WORLD + """
from mlx_server import templates
def probe(name, template=None, tok=None, python=os.path.join(VENV, "bin", "python3")):
    snap = seed(name, "org/" + name, 8100, config={}, template=template)
    if tok is not None:
        with open(os.path.join(snap, "tokenizer_config.json"), "w") as fh:
            fh.write(json.dumps({"chat_template": tok}))
    r = templates.inspect_template(snap, python)
    return {"unknown": r.kind} if r.is_unknown() else r.value
"""


def _run(payload, sys_py, body, timeout=40):
    return run_floor(payload, sys_py, PROG + body, timeout=timeout,
                     FIXTURES=FIX, RENDER_PY=sys.executable)


def test_real_qwen38_template(payload, sys_py):
    got = _run(payload, sys_py, """
print(json.dumps(probe("q", template=fixture("qwen38/chat_template.jinja"))))
""")
    assert got == {"thinking_default": True, "effort_domain": ["low", "medium", "xhigh"]}, got


def test_semantic_cases(payload, sys_py):
    got = _run(payload, sys_py, """
out = {
  "default_off": probe("off", template="{% if enable_thinking %}<think>{% endif %}{{ messages[0].content }}"),
  "no_switch": probe("plain", template="{{ messages[0].content }}"),
  "tok_only": probe("tok", tok="{% if enable_thinking is undefined or enable_thinking %}T{% endif %}"),
  "no_template": probe("none"),
  "no_python": probe("nopy", template="x", python="/nonexistent/python3"),
}
print(json.dumps(out))
""")
    assert got["default_off"] == {"thinking_default": False, "effort_domain": []}
    assert got["no_switch"] == {"thinking_default": False, "effort_domain": []}
    assert got["tok_only"]["thinking_default"] is True
    assert got["no_template"] == {"unknown": "PARSE_FAILED"}
    assert got["no_python"] == {"unknown": "TOOL_UNAVAILABLE"}


def test_hostile_templates_do_not_execute_or_exhaust(payload, sys_py):
    t0 = time.time()
    got = _run(payload, sys_py, """
out = {
  "mro": probe("mro", template="{{ ().__class__.__mro__ }}"),
  "append": probe("app", template="{% set x = [] %}{{ x.append(1) }}"),
  "repeat": probe("rep", template='{{ "a" * 1000000000 }}'),
  "power": probe("pow", template="{{ 10 ** 100000 }}"),
  "loop": probe("loop", template="{% for i in range(100000) %}{% for j in range(100000) %}{% endfor %}{% endfor %}"),
}
print(json.dumps(out))
""", timeout=60)
    assert got["mro"] == {"unknown": "PARSE_FAILED"}, "沙箱没挡住属性穿越"
    assert got["append"] == {"unknown": "PARSE_FAILED"}, "沙箱没挡住可变操作"
    assert got["repeat"] == {"unknown": "PARSE_FAILED"}, "没拦截超大字符串重复"
    assert got["power"] == {"unknown": "PARSE_FAILED"}, "没拦截超大指数"
    assert got["loop"] == {"unknown": "TIMEOUT"}, "死循环没有被墙钟截断"
    assert time.time() - t0 < 30, "单个恶意模板的判定应在 5 s 墙钟附近结束"


def test_renderer_isolates_environment_from_parent_secrets(payload, sys_py):
    got = _run(payload, sys_py, """
import os, tempfile
os.environ["MLX_GATEWAY_TOKEN"] = "SENTINEL_SECRET_42"
wrapper_dir = tempfile.mkdtemp()
env_dump = os.path.join(wrapper_dir, "env_dump.txt")
wrapper = os.path.join(wrapper_dir, "wrapper.sh")
with open(wrapper, "w") as fh:
    fh.write('#!/bin/sh\\n')
    fh.write('/usr/bin/env > "{}"\\n'.format(env_dump))
    fh.write('exec "{}" "$@"\\n'.format(os.path.join(VENV, "bin", "python3")))
os.chmod(wrapper, 0o755)
snap = seed("env_iso", "org/env_iso", 8100, config={}, template=fixture("qwen38/chat_template.jinja"))
r = templates.inspect_template(snap, wrapper)
result_valid = (not r.is_unknown() and
                r.value.get("thinking_default") is True and
                "xhigh" in r.value.get("effort_domain", []))
dump_exists = os.path.exists(env_dump)
has_sentinel = False
if dump_exists:
    with open(env_dump, "r") as fh:
        has_sentinel = "SENTINEL_SECRET_42" in fh.read()
print(json.dumps({
    "dump_exists": dump_exists,
    "has_sentinel": has_sentinel,
    "result_valid": result_valid,
}))
""")
    assert got["dump_exists"], "Environment dump file should exist"
    assert not got["has_sentinel"], "Renderer subprocess should not inherit parent environment"
    assert got["result_valid"], "Probe result should still be valid after env isolation"


def test_tempfile_errors_map_to_unknown(payload, sys_py):
    got = _run(payload, sys_py, """
import tempfile
tempfile.tempdir = "/nonexistent-mlx-test-dir"
snap = seed("tmpfail", "org/tmpfail", 8100, config={}, template=fixture("qwen38/chat_template.jinja"))
r = templates.inspect_template(snap, os.path.join(VENV, "bin", "python3"))
print(json.dumps({"unknown": r.kind} if r.is_unknown() else {"error": "should be unknown"}))
""")
    assert got.get("unknown") == "TOOL_UNAVAILABLE", f"Expected TOOL_UNAVAILABLE, got {got}"
