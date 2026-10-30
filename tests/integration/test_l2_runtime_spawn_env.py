"""L2 · `runtime.spawn` 不得让子进程继承 shell 里的两把密钥（I-4）。

坏样本（评审）：`spawn` 以 `dict(os.environ, **spec.env)` 造子进程环境 ——
`backend.api_key` 配置为空、shell 却导出了 `MLX_BACKEND_API_KEY` 时，网关
（以及它拉起的 mlx_lm / mlx_vlm 后端）仍会拿到这把密钥，而运行记录写
`backend_key_fp=None`，轮换检测据此误报「没变」。`MLX_GATEWAY_TOKEN` 同理。

`spawn` 构造子进程环境时，先复制 `os.environ`，**移除**这两个
名字，再叠加 `spec.env`——`spec.env` 显式给出的值不受影响。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r'''
import json, os, tempfile
from mlx_server import runtime
from mlx_server.backendarg import LaunchSpec

d = tempfile.mkdtemp()

def dump_code(path):
    return (
        "import json,os;"
        "open(" + repr(path) + ", 'w').write(json.dumps("
        "{k: os.environ.get(k) for k in "
        "('MLX_GATEWAY_TOKEN','MLX_BACKEND_API_KEY')}))"
    )

def run_and_wait(env, dump_path):
    spec = LaunchSpec(
        service="t", argv=("/usr/bin/python3", "-c", dump_code(dump_path)),
        env=env, cwd_rel="", model_arg=None, self_id_expected=None, sources={},
    )
    h = runtime.spawn(spec, d, log_path=os.path.join(d, "log", "t.log"))
    assert h is not None, "spawn 失败"
    os.waitpid(h["pid"], 0)
    with open(dump_path, encoding="utf-8") as fh:
        return json.load(fh)

# 父进程（本脚本，即 spawn 的调用方）的 os.environ 设两个哨兵 ——
# 模拟「开发者 shell 里导出了这两把密钥」。
os.environ["MLX_GATEWAY_TOKEN"] = "SENTINEL_GW_1"
os.environ["MLX_BACKEND_API_KEY"] = "SENTINEL_BK_1"

# 用例 1：spec.env 不含这两个名字 —— 子进程必须两个都拿不到（None）。
r1 = run_and_wait({}, os.path.join(d, "dump1.json"))

# 用例 2：spec.env 显式给 MLX_GATEWAY_TOKEN —— 子进程拿到 spec 给的值，
# MLX_BACKEND_API_KEY（spec 未给）仍为 None，不从 shell 漏进来。
r2 = run_and_wait({"MLX_GATEWAY_TOKEN": "FROM_SPEC_2"}, os.path.join(d, "dump2.json"))

print(json.dumps({"r1": r1, "r2": r2}))
'''


def test_spawn_strips_inherited_gateway_and_backend_secrets(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=20)
    assert got["r1"] == {"MLX_GATEWAY_TOKEN": None, "MLX_BACKEND_API_KEY": None}, (
        f"子进程继承了 shell 里的哨兵密钥：{got['r1']}"
    )
    assert got["r2"] == {"MLX_GATEWAY_TOKEN": "FROM_SPEC_2", "MLX_BACKEND_API_KEY": None}, (
        f"spec.env 显式给出的值应生效，shell 里的另一把密钥不应漏进来：{got['r2']}"
    )
