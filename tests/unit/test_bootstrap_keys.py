"""`mlx bootstrap keys`（spec §5.2）：只补空值、输出不含值、settings.json 0600。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PRE = """
import json, os, re, stat, tempfile
os.umask(0o022)
from mlx_server import store
from mlx_server.cmd import bootstrap
from mlx_server.cmd.svc import Invocation
from mlx_server.ctx import Ctx
from mlx_server.tables import TablesView
d = tempfile.mkdtemp()
ctx = Ctx(d, d, d, os.path.join(d, "venv"), 8340, (8100, 8199), None,
          share_root="@PAYLOAD@/share")
deps = {"tables": TablesView("@PAYLOAD@/share")}
"""


def test_keys_generates_missing_only_and_never_prints_values(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
p = store.settings_path(ctx)
open(p, "w").write(json.dumps({"api.token": "K" * 40}))
os.chmod(p, 0o644)
first = bootstrap.run(ctx, None, Invocation("keys"), deps)
s1 = store.load_settings(ctx)
second = bootstrap.run(ctx, None, Invocation("keys"), deps)
s2 = store.load_settings(ctx)
print(json.dumps({
    "rc": [first.rc, second.rc],
    "first": first.payload, "second": second.payload,
    "key_ok": bool(re.fullmatch(r"[A-Za-z0-9_-]{32,128}", s1.get("backend.api_key", ""))),
    "token_kept": s1.get("api.token") == "K" * 40,
    "stable": s1 == s2,
    "leak": s1.get("backend.api_key", "") in json.dumps([first.payload, second.payload]),
    "mode": oct(stat.S_IMODE(os.stat(p).st_mode)),
}))
""")
    assert got["rc"] == [0, 0]
    assert got["first"]["generated"] == ["backend.api_key"]
    assert got["first"]["kept"] == ["api.token"]
    assert got["second"]["generated"] == [] and sorted(got["second"]["kept"]) == \
        ["api.token", "backend.api_key"]
    assert got["key_ok"] and got["token_kept"] and got["stable"]
    assert not got["leak"], "密钥值出现在命令输出里"
    assert got["mode"] == "0o600"
