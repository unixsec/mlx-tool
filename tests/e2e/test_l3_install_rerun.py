"""L3 · 安装器面向「任何人的 Mac」：可反复运行、平台不符就拒绝、输出不带开发环境的信息。

一键安装的承诺是**再跑一次也没有副作用**：没变就跳过（不停服务、不拷文件），
变了才重装。「变了」有四类来源（代码、配置、PATH、自检），每一类都要能触发重装 ——
漏掉一类，用户手上坏掉的那一处就永远修不好。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tv.sandbox import seed_model  # noqa: E402

pytestmark = pytest.mark.e2e


def _roots(base):
    return {k: base / v for k, v in
            (("MLX_ROOT_CODE", "code"), ("MLX_ROOT_DATA", "data"), ("MLX_ROOT_WEIGHTS", "weights"))}


def _run(repo, env, *args):
    return subprocess.run(["bash", str(repo / "deployer" / "install.sh"), *args],
                          capture_output=True, text=True, timeout=90, check=False, env=env)


@pytest.fixture()
def env(tmp_path):
    return dict(os.environ, PATH="/usr/bin:/bin", **{k: str(v) for k, v in _roots(tmp_path).items()})


@pytest.fixture()
def installed(repo, env):
    r = _run(repo, env)
    assert r.returncode == 0, r.stderr
    assert "（已安装）" in r.stderr, r.stderr
    return env


def _reinstalled(r):
    return "== 建三根" in r.stderr and "（已安装）" in r.stderr


def test_rerun_skips_when_nothing_changed(repo, installed, tmp_path):
    mlx = tmp_path / "code" / "bin" / "mlx"
    before = mlx.stat().st_mtime_ns
    backups = sorted(p.name for p in (tmp_path / "data" / "backups").iterdir())
    r = _run(repo, installed)
    assert r.returncode == 0, r.stderr
    assert "已是当前版本" in r.stderr and "（已是最新，未重装）" in r.stderr, r.stderr
    assert "收敛正在运行的服务" not in r.stderr and "== 拷载荷" not in r.stderr, "没变却走了重装"
    assert mlx.stat().st_mtime_ns == before, "没变却重写了代码根"
    assert sorted(p.name for p in (tmp_path / "data" / "backups").iterdir()) == backups


@pytest.mark.parametrize("change", ["config", "path", "code", "check"])
def test_rerun_reinstalls_when_something_changed(repo, installed, tmp_path, change):
    env = dict(installed)
    if change == "config":
        env["MLX_GATEWAY_PORT"] = "8181"
    elif change == "path":
        Path(env["MLX_RC_FILES"]).write_text("# 用户重写了 rc，PATH 行没了\n")
    elif change == "code":
        f = tmp_path / "code" / "lib" / "mlx_server" / "cli.py"
        f.write_text(f.read_text() + "\n# 被改坏\n")
    else:
        import shutil
        shutil.rmtree(tmp_path / "code" / ".venv")
    r = _run(repo, env)
    assert r.returncode == 0, r.stderr
    assert _reinstalled(r), f"{change} 变了却跳过了重装：{r.stderr[-600:]}"
    if change == "config":
        assert "MLX_GATEWAY_PORT=8181" in (tmp_path / "code" / "share" / "constants.env").read_text()
    if change == "path":
        assert "mlx_server" in Path(env["MLX_RC_FILES"]).read_text()
    if change == "code":
        assert "被改坏" not in (tmp_path / "code" / "lib" / "mlx_server" / "cli.py").read_text()
    if change == "check":
        assert (tmp_path / "code" / ".venv" / "bin" / "python3").exists()


def test_force_reinstalls_even_when_unchanged(repo, installed):
    r = _run(repo, installed, "--force")
    assert r.returncode == 0 and _reinstalled(r), r.stderr


def test_unknown_argument_refused_before_touching_disk(repo, env, tmp_path):
    r = _run(repo, env, "--frce")
    assert r.returncode == 2 and "未知参数" in r.stderr, r.stderr
    assert not (tmp_path / "data").exists() and not (tmp_path / "code").exists()


def test_non_apple_silicon_refused_before_touching_disk(repo, env, tmp_path):
    """判据是 `hw.optional.arm64`，不是 `uname -m`（Rosetta 下 M 系列也报 x86_64）。"""
    fake = tmp_path / "sysctl"
    fake.write_text("#!/bin/sh\necho 0\n")
    fake.chmod(0o755)
    r = _run(repo, dict(env, MLX_SYSCTL=str(fake)))
    assert r.returncode == 70, r.stderr
    assert "Apple Silicon" in r.stderr and "未做任何改动" in r.stderr, r.stderr
    assert not (tmp_path / "data").exists() and not (tmp_path / "code").exists()


def test_default_weights_root_is_not_a_personal_layout(repo, tmp_path):
    """新用户的 home 下不得冒出 `~/lab`：权重根默认走 XDG，独立于代码根。"""
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, PATH="/usr/bin:/bin", HOME=str(home))
    for k in ("MLX_ROOT_CODE", "MLX_ROOT_DATA", "MLX_ROOT_WEIGHTS"):
        env.pop(k, None)
    r = _run(repo, env)
    assert r.returncode == 0, r.stderr
    const = (home / ".local/share/mlx_server/share/constants.env").read_text()
    assert f"MLX_ROOT_WEIGHTS={home}/.local/share/mlx_models\n" in const, const
    assert not (home / "lab").exists()


def test_reinstall_keeps_custom_weights_root_without_env(repo, env, tmp_path):
    """老用户的权重在旧默认（或自定义）位置：重装时不带环境变量，也必须沿用 constants.env 里的根。"""
    custom = tmp_path / "my-weights"
    assert _run(repo, dict(env, MLX_ROOT_WEIGHTS=str(custom))).returncode == 0
    bare = {k: v for k, v in env.items() if k not in ("MLX_ROOT_WEIGHTS", "MLX_ROOT_DATA")}
    r = _run(repo, bare, "--force")
    assert r.returncode == 0, r.stderr
    const = (tmp_path / "code" / "share" / "constants.env").read_text()
    assert f"MLX_ROOT_WEIGHTS={custom}\n" in const, const
    assert f"MLX_ROOT_DATA={tmp_path / 'data'}\n" in const, const


def test_screen_shows_home_as_tilde_log_keeps_full_paths(repo, tmp_path):
    """用户贴安装输出求助时不带出用户名；日志里保留完整路径以便排查。"""
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, PATH="/usr/bin:/bin", HOME=str(home))
    for k in ("MLX_ROOT_CODE", "MLX_ROOT_DATA", "MLX_ROOT_WEIGHTS"):
        env.pop(k, None)
    r = _run(repo, env)
    assert r.returncode == 0, r.stderr
    assert str(home) not in r.stderr, [ln for ln in r.stderr.splitlines() if str(home) in ln][:3]
    assert "~/.local/state/mlx_server/logs/install/" in r.stderr, r.stderr[-400:]
    logs = list((home / ".local/state/mlx_server/logs/install").glob("*.log"))
    assert len(logs) == 1 and str(home) in logs[0].read_text()


def test_summary_is_generic_and_points_to_next_step(repo, installed, tmp_path):
    """结尾清单只含任何用户都需要的信息，下一步随状态变化。不得出现开发环境的东西。"""
    r = _run(repo, installed, "--force")
    summary = r.stderr[r.stderr.index("== 安装结果"):]
    for want in ("✓ 版本      1.", "✓ 自检", "✓ 密钥      已就绪", "· 模型      已登记 0 个",
                 "mlx model install", "· 日志"):
        assert want in summary, f"缺 {want!r}：\n{summary}"
    for unwanted in ("git", "check.sh", "VM ", "main @", "$T", "BK"):
        assert unwanted not in summary, f"清单里有开发环境信息 {unwanted!r}：\n{summary}"

    seed_model(tmp_path / "data", tmp_path / "weights")
    summary = _run(repo, installed).stderr
    assert "已登记 1 个" in summary and "mlx up qwen" in summary, summary


def test_summary_never_prints_key_values(repo, installed, tmp_path):
    s = json.loads((tmp_path / "data" / "settings.json").read_text())
    r = _run(repo, installed, "--force")
    out = r.stdout + r.stderr
    for k in ("api.token", "backend.api_key"):
        assert s[k] not in out and s[k][:8] not in out, f"{k} 的值出现在安装输出里"
