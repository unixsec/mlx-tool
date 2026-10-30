"""片 1 的 E1 —— `mlx up <id> --dry-run` 在**真实产物**上跑通（L3）。

审视报告 P0-1 逐字指出上一版的问题：
  「唯一的 spec 产者是测试文件里的 lambda 桩，测试在拿桩和桩对拍」
本文件的断言两侧**都来自产品**：一侧是 `bin/mlx` 子进程的 stdout，
另一侧是产品 `backendarg.spec()` 的返回值。测试自己不造 argv。
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


@pytest.fixture()
def installed(tmp_path: Path, payload: Path):
    """最小「安装产物」：**真实的 share/ 表** + constants.env + 三根目录。

    表**从 `payload/share/` 拷贝，不手工造** —— 手工造的夹具会与产物脱节：
    新增 `verbs.tsv` 时，手工夹具里没有它，而产品在真实安装下能读到，
    于是测试红得莫名其妙、或者更糟：夹具恰好造出一份**形状不同**的表，
    测试全绿而产品在用户那里崩（`verify-against-reality`）。
    """
    import shutil
    share = tmp_path / "share"; share.mkdir()
    for tsv in sorted((payload / "share").glob("*.tsv")):
        shutil.copy2(tsv, share / tsv.name)
    for d in ("code", "data", "weights"):
        (tmp_path / d).mkdir()
    # 装好的系统总有后端密钥（安装器 `bootstrap keys`）；1.2.0 起 mlx_lm 后端也要它
    (tmp_path / "data" / "settings.json").write_text('{"backend.api_key": "' + "B" * 43 + '"}')
    (share / "constants.env").write_text(
        f"MLX_ROOT_CODE={tmp_path}/code\n"
        f"MLX_ROOT_DATA={tmp_path}/data\n"
        f"MLX_ROOT_WEIGHTS={tmp_path}/weights\n"
        "MLX_GATEWAY_PORT=8080\nMLX_PORT_RANGE=8100-8199\n"
    )
    # 模型要**登记且权重在盘** —— `up --dry-run` 走完整个校验段才分叉
    # （`BCR-4-08`），缺任何一半都在到达 argv 之前被拒。
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tv.sandbox import seed_model
    seed_model(tmp_path / "data", tmp_path / "weights")
    return tmp_path


def _mlx(payload: Path, installed: Path, *argv):
    env = dict(os.environ)
    env.update({
        "PATH": "/usr/bin:/bin",
        "MLX_CODE_ROOT": str(payload),
        "MLX_SHARE_ROOT": str(installed / "share"),
    })
    return subprocess.run(
        [str(payload / "bin" / "mlx"), *argv],
        capture_output=True, text=True, timeout=20, check=False, env=env,
    )


def test_e1_dry_run_prints_argv_on_real_product(payload: Path, installed: Path):
    """**E1 主判据**：`mlx up qwen --dry-run` 在真实产物上打印 argv 到 stdout。

    上一版的 `cmd_svc` 在 payload 里零 import，主命令实际输出 usage + rc 2。
    """
    r = _mlx(payload, installed, "up", "qwen", "--dry-run")
    assert r.returncode == 0, f"rc={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    assert "usage" not in r.stdout.lower(), "主命令仍走到 usage 分支 ⇒ 没有被 dispatch"
    assert r.stdout.strip(), "stdout 为空 —— dry-run 的全部价值就是打印 argv"


def test_e1_argv_comes_from_product_spec_not_from_test(payload: Path, installed: Path, sys_py: str):
    """**两侧都来自产品**：子进程 stdout 的 argv == 产品 `backendarg.spec()` 的 argv。

    这一条是 P0-1 的直接对治。测试不构造任何 argv 字面量。
    """
    got = _mlx(payload, installed, "up", "qwen", "--dry-run")
    assert got.returncode == 0, got.stderr

    # 另一侧：在下限解释器里直接问产品的 spec()
    ref = subprocess.run(
        [sys_py, "-c", textwrap.dedent("""
            import json
            from mlx_server import backendarg, route, schema
            from mlx_server.tables import TablesView
            tv = TablesView("{share}")
            cfg = schema.effective("qwen", {{}})
            row = next(r for r in tv.backends() if r["backend"] == "mlx_lm")
            rt = route.Route("mlx_lm", row, "R4", False, [], "", None,
                             cfg.values.get("max_concurrency"), None)
            print(json.dumps(list(backendarg.spec("qwen", cfg, rt).argv)))
        """).format(share=payload / "share")],
        capture_output=True, text=True, check=False, timeout=15,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(payload / "lib")},
    )
    assert ref.returncode == 0, f"产品 spec() 直调失败：{ref.stderr}"
    expected = json.loads(ref.stdout.strip().splitlines()[-1])

    printed = [ln for ln in got.stdout.splitlines() if ln.strip().startswith("[")]
    assert printed, f"stdout 里没有 argv 行：{got.stdout!r}"
    assert json.loads(printed[-1]) == expected, "dry-run 打印的 argv 与产品 spec() 不一致"


def test_e1_dry_run_creates_nothing(payload: Path, installed: Path):
    """`DE-T-018` 的沙箱面：dry-run 全轮 diff == ∅。"""
    before = {str(p) for p in installed.rglob("*")}
    _mlx(payload, installed, "up", "qwen", "--dry-run")
    after = {str(p) for p in installed.rglob("*")}
    assert before == after, f"dry-run 动了盘：新增 {after - before}，删除 {before - after}"


def test_unknown_verb_returns_registered_rc(payload: Path, installed: Path):
    """审视报告 P0-2：带参分支也必须返回登记码，不能 fall-through 成 rc 0。"""
    r = _mlx(payload, installed, "nosuchverb")
    assert r.returncode == 2, f"未知动词应 rc 2，实得 {r.returncode}；stdout={r.stdout!r}"
    assert r.stdout == "", "错误信息必须走 stderr（INV-17）"
