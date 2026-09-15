"""finding I-1 · `schema.tsv` 读不到时，`config` 全体动词 fail-closed。

坏样本（修前）：`_defs` 读表失败时回落 `{}`，`_domain`/`_factory` 跟着回落
`schema.DEFAULTS`（现在也含 `api.token`/`backend.api_key`），而 `is_secret`
靠 `defs` 判「这个键是不是机密」——`defs` 一旦是空字典，`is_secret` 恒假，
`ls`/`get`/`diff`/`validate`/`set`/`unset` 全都把密钥原值当普通值回显，
`export` 的白名单闸门 `if defs and not schema.exportable(...)` 在 `defs` 为
空时同样恒真（不跳过），密钥被写进导出文件。

修后：`schema.tsv` 读不到 ⇒ 这八个动词一律 `Refused(3, "tables.unreadable")`，
在任何 commit 之前，宁可命令整体不可用也不让密钥漏出去。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

TOKEN = "T" * 43
VERBS = ("ls", "get", "diff", "validate", "export", "import", "set", "unset")

PROG = WORLD + """
import json, os
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation

seed("qwen", "org/qwen", 8101)
put_settings({"api.token": "@TOKEN@"})

# 复现「安装缺陷」：schema.tsv 读不到（表被删/装坏）。
os.remove(os.path.join(SHARE, "schema.tsv"))

EXPORT_PATH = os.path.join(_ROOT, "exported.json")

def call(verb, target=None, **kw):
    out = config.run(ctx, None, Invocation(verb, target, args=kw), {})
    return {"rc": out.rc, "key": getattr(out, "msg_key", None),
            "payload": getattr(out, "payload", None)}

results = {}
results["ls"] = call("ls")
results["get"] = call("get", None, key="api.token")
results["diff"] = call("diff")
results["validate"] = call("validate")
results["export"] = call("export")
if results["export"]["rc"] == 0:
    # 产品自己的 `export` 分支不落盘 —— 真实用法是 `mlx config export > 文件`。
    # 拒绝之前它能 Done，就把 payload 落到这个文件，复现「密钥进了导出文件」
    # 这条症状；拒绝之后 rc != 0，这一步压根不会跑，文件也就不存在。
    with open(EXPORT_PATH, "w") as fh:
        json.dump(results["export"]["payload"], fh)
results["import"] = call("import")
results["set"] = call("set", "api.token", key="api.token", value="@TOKEN@")
results["unset"] = call("unset", None, key="api.token")
results["export_file_exists"] = os.path.exists(EXPORT_PATH)
print(json.dumps(results))
"""


def test_config_fails_closed_when_schema_table_unreadable(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, TOKEN=TOKEN)

    for v in VERBS:
        assert got[v]["rc"] == 3, f"{v}：rc 应为 3，实得 {got[v]}"
        assert got[v]["key"] == "tables.unreadable", \
            f"{v}：msg_key 应为 tables.unreadable，实得 {got[v]}"

    blob = json.dumps(got)
    assert TOKEN not in blob, f"密钥原值出现在输出里：{blob[:300]}"
    assert TOKEN[:8] not in blob, f"密钥前 8 位出现在输出里：{blob[:300]}"
    assert got["export_file_exists"] is False, "export 在表坏掉时不该产出任何文件"
