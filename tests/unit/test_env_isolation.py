"""开发者 shell 导出的变量不得进入测试 —— 结果不能随机器变化。

坏样本：shell 里导出的 `MLX_GATEWAY_TOKEN` / `MLX_BACKEND_API_KEY` 会被
子进程环境构造（`dict(os.environ, …)`）继承，覆盖安装生成的密钥；
`MLX_ROOT_*` 可能把安装指向真实目录。由 `conftest.py` 的 autouse fixture
统一清除，而不是靠每个用例记得清。
"""
import os

INHERITED = ("MLX_GATEWAY_TOKEN", "MLX_BACKEND_API_KEY",
             "MLX_ROOT_CODE", "MLX_ROOT_DATA", "MLX_ROOT_WEIGHTS")


def test_developer_shell_vars_cleared():
    leaked = sorted(n for n in INHERITED if n in os.environ)
    assert leaked == [], f"开发者 shell 的变量漏进了测试环境：{leaked}"
