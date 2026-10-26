"""开发者 shell 导出的变量不得进入测试 —— 结果不能随机器变化。

坏样本：shell 里导出的 `MLX_GATEWAY_TOKEN` 会经 `ctx` 折入 cli 层的 cfg；
`MLX_ROOT_*` 可能把安装指向真实目录。由 `conftest.py` 的 autouse fixture
统一清除，而不是靠每个用例记得清。
"""
import os

INHERITED = ("MLX_GATEWAY_TOKEN", "MLX_BACKEND_API_KEY",
             "MLX_ROOT_CODE", "MLX_ROOT_DATA", "MLX_ROOT_WEIGHTS")


def test_developer_shell_vars_cleared():
    leaked = sorted(n for n in INHERITED if n in os.environ)
    assert leaked == [], f"开发者 shell 的变量漏进了测试环境：{leaked}"


def test_subprocesses_have_no_controlling_terminal():
    """开发者终端不得进入测试起的子进程 —— 结果不能随「在不在终端里跑」变化。

    坏样本（2026-10-26）：产品的确认只读 `/dev/tty`，测试用 `stdin=DEVNULL` 模拟「无 tty」；
    在交互终端里跑 `check.sh`，子进程仍继承控制终端 ⇒ 提示弹到开发者屏幕上，
    卸载用例卡 30 s 超时，`rm --weights` 用例在开发者键入 qwen 后真删了（沙箱里的）权重。
    无终端的环境里两条都绿。判据不看此刻有没有终端：子进程必须自成会话（无控制终端）。
    """
    import subprocess
    r = subprocess.run(["/usr/bin/python3", "-c", "import os; print(os.getsid(0) == os.getpid())"],
                       capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "True", "测试起的子进程没有脱离控制终端（应由 conftest 统一设 start_new_session）"
