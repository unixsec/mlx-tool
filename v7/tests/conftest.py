"""pytest 全局夹具。

约束（来自 04-interfaces.md §0）：
- 产品代码的运行时下限是 `/usr/bin/python3`（本机 3.9.6），
  测试自身跑在 .venv（3.14）上，两者不可混用。
- 任何断言产品行为的测试，必须经 SYS_PY 调起被测对象，
  不得 import 产品模块后在 venv 解释器里直接调用 —— 那测的是另一个解释器。
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SYS_PY = "/usr/bin/python3"


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session")
def payload(repo: Path) -> Path:
    return repo / "v7" / "payload"


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
    yield
