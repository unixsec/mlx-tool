"""C4 · `config ls <model-id>` 的「target 为模型 id 时列出该模型 user 层退役键」分支。

`cmd/config.py::run` 的 `ls` 分支里，`inv.target` 是模型 id 且该 id 不在配置域时，
会额外把目标模型 `user` 段里不在配置域（且不是 `repo`/`type`）的键并进 `dropped`——
这条分支此前没有测试覆盖。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

PROG = WORLD + """
from mlx_server.cmd import config
from mlx_server.cmd.svc import Invocation

# `backend` 登记在旧版 schema 里、现已退役（不在当前配置域），
# 与 test_l2_display.py 里验证「全局 settings 退役键」用的是同一个例子。
seed("vis", "org/vis", 8102, user={"backend": "mlx_vlm"})
out = config.run(ctx, None, Invocation("ls", "vis"), {})
print(json.dumps({"rc": out.rc, "dropped": out.payload.get("dropped")}))
"""


def test_config_ls_model_target_lists_retired_user_key(payload, sys_py):
    got = run_floor(payload, sys_py, PROG)
    assert got["rc"] == 0, got
    assert "backend" in got["dropped"], (
        f"模型 user 段里的退役键 backend 应出现在 dropped 里：{got['dropped']}"
    )
