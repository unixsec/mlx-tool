"""值域校验单点（spec §2.2）与新配置键（spec §5.1、§6.1）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402
from floor import run_floor  # noqa: E402

PRE = """
import json, os, tempfile
from mlx_server import schema, store
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation
from mlx_server.ctx import Ctx
from mlx_server.tables import TablesView
d = tempfile.mkdtemp()
ctx = Ctx(d, d, d, os.path.join(d, "venv"), 8340, (8100, 8199), None,
          share_root="@PAYLOAD@/share")
tv = TablesView("@PAYLOAD@/share")
defs = schema.keydefs(tv)
deps = {"tables": tv}
def run(verb, target=None, **args):
    out = config.run(ctx, None, Invocation(verb, target, args=args), deps)
    return {"rc": out.rc, "key": getattr(out, "msg_key", None),
            "payload": getattr(out, "payload", None)}
"""


def test_new_keys_are_declared_with_spec_values(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
want = ("max_tokens", "modality", "max_concurrency", "backend.api_key", "api.token")
print(json.dumps({k: defs.get(k) for k in want}))
""")
    assert got["max_tokens"]["default"] == "81920"
    assert (got["modality"]["domain"], got["modality"]["default"]) == ("auto|text|vision", "auto")
    assert (got["max_concurrency"]["domain"], got["max_concurrency"]["default"]) == ("1-256", "8")
    for k in ("backend.api_key", "api.token"):
        d = got[k]
        assert d["secret"] == "yes" and d["exportable"] == "no", k
        assert d["domain"] == "^[A-Za-z0-9_-]{32,128}$", k
        assert d["level"] == "global" and d["scope"] == "user", k
    assert got["backend.api_key"]["restart"] == "model+gateway"


def test_check_domain_matrix(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
cases = [
    ("modality", "text"), ("modality", "vison"), ("modality", ""),
    ("max_concurrency", 8), ("max_concurrency", 0), ("max_concurrency", 257),
    ("max_concurrency", "8"), ("port", 8100), ("port", 70000),
    ("api.token", "a" * 43), ("api.token", "short"), ("api.token", ""),
    ("api.bind", "0.0.0.0"), ("revision", "main"), ("revision", "zzz"),
]
print(json.dumps([schema.check_domain(k, v, defs) is None for k, v in cases]))
""")
    # 与上面 cases 逐项对应的期望：True = 合法
    expected = [True, False, True,
                True, False, False,
                False, True, False,
                True, False, True,
                True, True, False]
    Cardinality.require_nonempty("值域样例", got, "样例为空 ⇒ 空真")
    wrong = [i for i, (g, e) in enumerate(zip(got, expected)) if g != e]
    assert len(got) == len(expected), f"样例数 {len(got)} ≠ 期望数 {len(expected)}"
    Cardinality.check("值域判定与期望不符的样例下标", wrong, rel="empty")


def test_config_set_rejects_out_of_domain_and_coerces_table_ints(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
store.commit(ctx, store.MODELS, store.ModelsTx("qwen", {"repo": "o/q"}, port=8100))
r = {}
r["vison"] = run("set", "qwen", key="modality", value="vison")
r["text"] = run("set", "qwen", key="modality", value="text")
r["zero"] = run("set", "max_concurrency", key="max_concurrency", value="0")
r["four"] = run("set", "max_concurrency", key="max_concurrency", value="4")
r["draft_tokens"] = run("set", "qwen", key="num_draft_tokens", value="5")
rec = store.load(ctx, store.MODELS).by_id("qwen")
r["stored"] = {"modality": rec.user.get("modality"),
               "num_draft_tokens": rec.user.get("num_draft_tokens"),
               "max_concurrency": store.load_settings(ctx).get("max_concurrency")}
print(json.dumps(r))
""")
    assert (got["vison"]["rc"], got["vison"]["key"]) == (6, "config.invalid")
    assert got["text"]["rc"] == 0
    assert (got["zero"]["rc"], got["zero"]["key"]) == (6, "config.invalid")
    assert got["four"]["rc"] == 0
    assert got["stored"] == {"modality": "text", "num_draft_tokens": 5, "max_concurrency": 4}, \
        f"表里的 int 键没按整数落盘：{got['stored']}"


def test_config_set_rejects_nonnumeric_string_for_int_key(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
store.commit(ctx, store.MODELS, store.ModelsTx("qwen", {"repo": "o/q"}, port=8100))
r = {}
r["max_concurrency_abc"] = run("set", "qwen", key="max_concurrency", value="abc")
r["port_xyz"] = run("set", key="port", value="xyz")
r["max_tokens_bad"] = run("set", key="max_tokens", value="not_a_number")
print(json.dumps(r))
""")
    for case in ("max_concurrency_abc", "port_xyz", "max_tokens_bad"):
        result = got[case]
        assert result["rc"] == 6, f"{case}: rc should be 6, got {result['rc']}"
        assert result["key"] == "config.invalid", f"{case}: msg_key should be config.invalid, got {result['key']}"
