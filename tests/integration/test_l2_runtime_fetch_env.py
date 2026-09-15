"""L2 · `runtime.fetch` 起第三方 `hf` CLI 时同样不得让子进程继承两把密钥（C10）。

坏样本：`fetch` 起 `hf` 时没有显式传 `env=`，子进程会拿到完整父进程环境，
继承 shell 里的 `MLX_GATEWAY_TOKEN` / `MLX_BACKEND_API_KEY`——与 `spawn`
此前的缺陷（I-4，见 `test_l2_runtime_spawn_env.py`）同源。

剔除逻辑复用 `spawn` 共用的模块内辅助函数 `runtime._stripped_env`，
不在 `fetch` 里再抄一份。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r'''
import json, os, stat, tempfile
from mlx_server import runtime

d = tempfile.mkdtemp()
dump_path = os.path.join(d, "dump.json")

# 假 hf 可执行脚本：忽略自己的 argv（download/repo/--local-dir/dest_dir），
# 把自身环境里的两把密钥名字写入一个字面写死的路径（生成脚本时确定，
# 不经 argv 传递 —— 满足 fetch 对 hf 的最小调用约定即可）。
fake_hf = os.path.join(d, "fake_hf")
with open(fake_hf, "w", encoding="utf-8") as fh:
    fh.write(
        "#!/usr/bin/python3\n"
        "import json, os\n"
        "with open(" + repr(dump_path) + ", 'w') as out:\n"
        "    out.write(json.dumps({k: os.environ.get(k) for k in "
        "('MLX_GATEWAY_TOKEN', 'MLX_BACKEND_API_KEY')}))\n"
    )
os.chmod(fake_hf, stat.S_IRWXU)

# 父进程（本脚本，即 fetch 的调用方）的 os.environ 设两个哨兵 ——
# 模拟「开发者 shell 里导出了这两把密钥」。
os.environ["MLX_GATEWAY_TOKEN"] = "SENTINEL_GW_F"
os.environ["MLX_BACKEND_API_KEY"] = "SENTINEL_BK_F"

result = runtime.fetch("org/repo", None, os.path.join(d, "dest"), hf_bin=fake_hf)
assert result.get("ok"), f"fetch 未成功：{result}"
with open(dump_path, encoding="utf-8") as fh:
    dumped = json.load(fh)
print(json.dumps(dumped))
'''


def test_fetch_strips_inherited_gateway_and_backend_secrets(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, timeout=20)
    assert got == {"MLX_GATEWAY_TOKEN": None, "MLX_BACKEND_API_KEY": None}, (
        f"hf 子进程继承了 shell 里的哨兵密钥：{got}"
    )
