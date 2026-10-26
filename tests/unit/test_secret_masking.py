"""secret 键打码（spec §5.2）：ls / get / diff / set / unset / validate 回显一律 `****`，
`config get --reveal` 给原值；空值不打码（「未设置」本身不是秘密）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

TOKEN = "T" * 43

PRE = """
import json, os, tempfile
from mlx_server import store
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation
from mlx_server.ctx import Ctx
from mlx_server.tables import TablesView
d = tempfile.mkdtemp()
ctx = Ctx(d, d, d, os.path.join(d, "venv"), 8340, (8100, 8199), None,
          share_root="@PAYLOAD@/share")
deps = {"tables": TablesView("@PAYLOAD@/share")}
def run(verb, target=None, **args):
    out = config.run(ctx, None, Invocation(verb, target, args=args), deps)
    return getattr(out, "payload", None) or {"rc": out.rc, "key": out.msg_key}
"""


def test_secret_values_are_masked_everywhere_but_reveal(payload, sys_py):
    got = run_floor(payload, sys_py, PRE + """
r = {}
r["set"] = run("set", "api.token", key="api.token", value="@TOKEN@")
r["ls"] = run("ls")["values"]
r["get"] = run("get", None, key="api.token")["value"]
r["reveal"] = run("get", None, key="api.token", reveal=True)["value"]
r["diff"] = [x["value"] for x in run("diff")["rows"] if x["key"] == "api.token"]
r["validate"] = [x[2] for x in run("validate")["rows"] if x[0] == "api.token"]
r["export"] = run("export")
r["empty_key"] = run("get", None, key="backend.api_key")["value"]
print(json.dumps(r))
""", TOKEN=TOKEN)
    blob = str({k: v for k, v in got.items() if k != "reveal"})
    assert TOKEN not in blob, f"密钥原值出现在非 --reveal 输出里：{blob[:300]}"
    assert got["set"]["value"] == "****"
    assert got["ls"]["api.token"] == "****"
    assert got["get"] == "****"
    assert got["reveal"] == TOKEN
    assert got["diff"] == ["****"]
    assert got["validate"] and "****" in got["validate"][0]
    assert "api.token" in got["export"]["skipped"]
    assert got["empty_key"] == "", "未设置的密钥键不该被打成 ****"


def test_unset_masks_secret_values(payload, sys_py):
    """unset 分支在 `now` 字段回显秘密值时需打码（spec §8.3）。

    在全局层和模型层都设置秘密值，从模型层 unset 后 now 字段应回落到全局层的值（被打码）。"""
    got = run_floor(payload, sys_py, PRE + """
import json
r = {}
# 设置全局层 api.token
r["set_global"] = run("set", "api.token", key="api.token", value="@TOKEN@G")
# 创建模型并在模型层设置 api.token
from mlx_server import store
stack = []
store.commit(ctx, store.MODELS,
             store.ModelsTx("test_model", {"repo": "o/m"}, port=8100), stack)
# 模型层的 api.token：`config set` 现在按 level 拒绝（全局级键写不进模型层），
# 所以这里直接落一条**旧版遗留的**死值 —— unset 仍须能清掉它，且回显不泄密
store.commit(ctx, store.MODELS,
             store.ModelsTx("test_model", {"api.token": "@TOKEN@M"},
                            user_keys=("api.token",)), stack)
# 从模型层 unset（应该保留全局层的值，回显为 ****）
r["unset"] = run("unset", "test_model", key="api.token")
print(json.dumps(r))
""", TOKEN=TOKEN)
    # 检查 unset 输出中都没有原始 token
    blob = str(got["unset"])
    assert TOKEN not in blob, f"密钥原值出现在 unset 输出里：{blob[:300]}"
    # 确认 was_set 为 True（之前确实在模型层设过）
    assert got["unset"]["was_set"] is True
    # unset 后回落到全局层的值，应被打码为 ****
    assert got["unset"]["now"] == "****", f"unset 后仍有值时应被打码，实得 {got['unset']['now']!r}"
