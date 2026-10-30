"""pytest 全局夹具。

约束（来自 04-interfaces.md §0）：
- 产品代码的运行时下限是 `/usr/bin/python3`（本机 3.9.6），
  测试自身跑在 .venv（3.14）上，两者不可混用。
- 任何断言产品行为的测试，必须经 SYS_PY 调起被测对象，
  不得 import 产品模块后在 venv 解释器里直接调用 —— 那测的是另一个解释器。
"""
import socket
import subprocess
from pathlib import Path

import pytest

# **产品根** —— `payload/` `deployer/` `tests/` 的共同父目录，
# 也就是 `tests/` 的上一级。**不是仓库根**，两者不一定相同：
#   · 扁平布局（发布仓库）：产品根 == 仓库根
#   · `v7/` 布局（开发仓库）：产品根 == `<仓库根>/v7`
# 按「相对 tests 的位置」算而不是按「仓库根往下数」算，两种布局下
# 同一份测试代码都对 —— 否则发布仓库一扁平化，测试就要改一遍，
# 两边从此分叉。
REPO = Path(__file__).resolve().parents[1]
SYS_PY = "/usr/bin/python3"


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session")
def payload(repo: Path) -> Path:
    return repo / "payload"


@pytest.fixture(scope="session")
def sys_py() -> str:
    """产品运行时下限解释器。测试断言产品行为时一律经它。"""
    out = subprocess.run([SYS_PY, "-V"], capture_output=True, text=True, check=False)
    assert out.returncode == 0, f"{SYS_PY} 不可用：{out.stderr}"
    return SYS_PY

@pytest.fixture(autouse=True)
def _never_touch_real_rc(tmp_path_factory, monkeypatch):
    """**任何测试都不得写到真实的 shell rc。**

    坏样本（本轮真实发生）：`cli` fixture 调 `install.sh` 时没设 `MLX_RC_FILES`，
    安装器于是用默认值 `$HOME/.zshrc` —— 把一行指向 pytest 临时目录的 PATH
    写进了用户的真实配置文件。用户第二天才发现，而那时临时目录早已不存在。

    这条 autouse fixture 让默认值指向沙箱：**忘了设 MLX_RC_FILES 不再有后果**。
    不能靠「每个 fixture 记得设」—— 那正是「更仔细」预期收益为零的形态。
    """
    sandbox_rc = tmp_path_factory.mktemp("rcguard") / "zshrc"
    sandbox_rc.write_text("# sandbox rc\n")
    monkeypatch.setenv("MLX_RC_FILES", str(sandbox_rc))
    # venv 照建（解释器优先级与回落路径都要测），但**不装推理依赖** ——
    # `pip install mlx-lm` 要几分钟，乘以每个 e2e 用例就是测试套件不可用。
    # 真装依赖的验证走单独一条用例。
    monkeypatch.setenv("MLX_SKIP_DEPS", "1")
    # **开发者 shell 导出的变量不得进入测试** —— 结果不能随机器变化。
    # `MLX_GATEWAY_TOKEN` 经 `ctx` 折入 cli 层的 cfg；`MLX_ROOT_*` 可能把安装
    # 指向真实目录。需要它们的用例经自己的 env dict 显式传入，不受影响。
    for name in ("MLX_GATEWAY_TOKEN", "MLX_BACKEND_API_KEY",
                 "MLX_ROOT_CODE", "MLX_ROOT_DATA", "MLX_ROOT_WEIGHTS"):
        monkeypatch.delenv(name, raising=False)
    # **本机真实的网关端口同样不得进入测试**。三根是沙箱，端口却不是：没设端口的安装用例
    # 装出来的是默认 8080，自检探到开发者正在跑的网关 → `gateway_port failed` →
    # 「没变就跳过」不成立（2026-10-29 验收期间 2 个重跑用例必红，停掉网关才绿）。
    # 默认给每个用例一个空闲端口；需要固定端口的用例经自己的 env dict 显式传入，不受影响。
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        monkeypatch.setenv("MLX_GATEWAY_PORT", str(s.getsockname()[1]))
    # **开发者的终端同样不得进入测试**：产品的确认只读 `/dev/tty`，`stdin=DEVNULL`
    # 挡不住它。在交互终端里跑时子进程继承控制终端，提示会弹到开发者屏幕上并真等输入
    # （2026-10-26 实测：卸载用例卡到超时，`rm --weights` 被键入的 qwen 确认）。
    # 默认让每个子进程自成会话；显式传了 `start_new_session` 的（pty 用例）不动。
    real_init = subprocess.Popen.__init__

    def _detached_init(self, *args, **kwargs):
        kwargs.setdefault("start_new_session", True)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "__init__", _detached_init)
    yield
