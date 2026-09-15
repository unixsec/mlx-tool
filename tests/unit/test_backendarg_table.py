"""G-SSOT-14 落地（spec §3.1）：argv / env 全部来自 backends.tsv 的行。"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

KEY = "K" * 43

PROG = """
import json
from mlx_server import backendarg, route, schema
from mlx_server.tables import TablesView
tv = TablesView("@PAYLOAD@/share")
B = {r["backend"]: r for r in tv.backends()}
v = schema.defaults_from(tv)
v.update({"port": 8123, "backend.api_key": "@KEY@"})
cfg = schema.ServiceConfig("m", v, {}, "/py")
def mk(row, thinking=False):
    return route.Route(row["backend"], row, "R?", thinking, [], "", 1.0,
                       v["max_concurrency"], route.key_fingerprint(v["backend.api_key"]))
FAKE = {"backend": "fake", "module": "fake.server", "host": "10.9.9.9", "offline": "0",
        "concurrency_args": "--slots {n}", "draft_args": "-", "thinking": "template",
        "ready": "bind", "key_env": "FAKE_KEY", "env_clear": "A_ENV,B_ENV"}
out = {}
for name, r in (("text", mk(B["mlx_lm"])), ("vision", mk(B["mlx_vlm"], thinking=True)),
                ("fake", mk(FAKE, thinking=True))):
    s = backendarg.spec("m", cfg, r)
    out[name] = {"argv": list(s.argv), "env": s.env, "secret": list(s.secret_env),
                 "route_rule": s.route.rule}
out["no_route"] = backendarg.spec("m", cfg, None) is None
print(json.dumps(out))
"""


def test_argv_and_env_come_only_from_table_row(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, KEY=KEY)
    assert got["text"]["argv"] == ["/py", "-m", "mlx_lm.server", "--model", "m", "--port", "8123",
                                   "--max-tokens", "81920", "--host", "127.0.0.1",
                                   "--decode-concurrency", "8", "--prompt-concurrency", "8"]
    assert got["text"]["env"] == {"HF_HUB_OFFLINE": "1"}
    assert got["text"]["secret"] == []
    assert got["vision"]["argv"] == ["/py", "-m", "mlx_vlm.server", "--model", "m", "--port", "8123",
                                     "--max-tokens", "81920", "--host", "127.0.0.1",
                                     "--max-num-seqs", "8", "--enable-thinking"]
    assert got["vision"]["env"] == {
        "HF_HUB_OFFLINE": "1", "MLX_VLM_SERVER_API_KEY": KEY,
        "MLX_VLM_DRAFT_MODEL": "", "MLX_VLM_DRAFT_KIND": "", "MLX_VLM_DRAFT_BLOCK_SIZE": "",
        "MLX_TRUST_REMOTE_CODE": "", "MLX_VLM_MAX_NUM_SEQS": ""}
    assert got["vision"]["secret"] == ["MLX_VLM_SERVER_API_KEY"]
    assert got["fake"]["argv"] == ["/py", "-m", "fake.server", "--model", "m", "--port", "8123",
                                   "--max-tokens", "81920", "--host", "10.9.9.9",
                                   "--slots", "8", "--enable-thinking"]
    assert got["fake"]["env"] == {"A_ENV": "", "B_ENV": "", "FAKE_KEY": KEY}
    assert got["fake"]["route_rule"] == "R?"
    assert got["no_route"] is True


def test_backendarg_has_no_backend_name_literals(payload):
    """附加廉价检查：后端名只能来自表。docstring 与注释不算。"""
    tree = ast.parse((payload / "lib" / "mlx_server" / "backendarg.py").read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body \
                and isinstance(node.body[0], ast.Expr) \
                and isinstance(getattr(node.body[0], "value", None), ast.Constant):
            docstrings.add(id(node.body[0].value))
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and id(n) not in docstrings and ("mlx_lm" in n.value or "mlx_vlm" in n.value)]
    assert bad == [], f"backendarg.py 第 {bad} 行出现后端名字面量 —— 它必须来自 backends.tsv"
