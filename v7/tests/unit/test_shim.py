"""bin/mlx 垫片 —— 52 个产品模块里唯一前置集合为空的那个。

它同时是元守卫 #2 `L0·lang-02` 的唯一求值对象（05-plan.md §三.7 / 裁-07）。
"""
import subprocess
from pathlib import Path


def test_shim_exists(payload: Path):
    """垫片必须存在且可执行。"""
    shim = payload / "bin" / "mlx"
    assert shim.is_file(), f"垫片不存在：{shim}"
    assert shim.stat().st_mode & 0o111, "垫片没有可执行位"


def test_shim_prefers_venv_then_homebrew_then_system(payload: Path):
    """`L0·lang-02`〔BCR-2026-09-02 改判据〕：垫片的解释器是**三级回落**。

    顺序：**venv → Homebrew python3 → 系统 python3**（`00-inheritance` §0.4.2）。

    前两级都不碰系统解释器 —— 那是本次变更的实质：
    系统 python 是 3.9，而 `mlx ≥ 0.30` 只发 cp310+，用它建的 venv
    **永远装不上能跑新架构的后端**（`qwen3_5` 那次实测踩到）。

    但第三级**不能去掉**：§0.4.1 的论证依赖「venv 坏了 `mlx check` 也能跑」，
    只认前两级会让它在最该工作的时刻连启动都做不到。
    那一级只执行纯标准库代码 —— 不装包、不写 site-packages。

    坏样本：只认 venv（诊断能力丢失）· 漏掉 Homebrew 这一级（venv 坏了就直接
    掉回 3.9）· 写裸 `python3`（走 PATH，随环境漂移）。
    """
    import re
    text = (payload / "bin" / "mlx").read_text()

    assert ".venv/bin/python3" in text, "垫片没有优先取 venv 解释器"
    assert "/opt/homebrew/bin/python3" in text, (
        "垫片没有 Homebrew 这一级 —— venv 一坏就直接掉回系统 3.9，"
        "而那个解释器装不了 mlx ≥ 0.30")
    assert "/usr/bin/python3" in text, (
        "垫片没有最后一级回落 —— venv 与 Homebrew 都没有时 `mlx check` "
        "就启动不了，§0.4.1「与推理 venv 完全解耦」的论证随之失效")

    # 顺序必须对：venv 在 Homebrew 之前，Homebrew 在系统之前
    i_venv = text.index(".venv/bin/python3")
    i_brew = text.index("/opt/homebrew/bin/python3")
    i_sys = text.index("/usr/bin/python3")
    assert i_venv < i_brew < i_sys, (
        f"回落顺序不对（venv={i_venv} homebrew={i_brew} system={i_sys}）—— "
        "顺序错了等于没有优先级")

    # 回落必须是**条件性**的（有可执行性判断），不能只是并列写三个路径
    assert re.search(r'\[ -x ', text), (
        "看不到可执行性判断 —— 回落可能不是真的")

    # 不得出现走 PATH 的裸调用。**只扫非注释行** ——
    # 注释里写「回落系统 python3」是说明，不是调用
    code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    bare = [ln.strip() for ln in code
            if re.search(r'(?<![/\w."$])python3\b(?![\w"/])', ln)]
    assert not bare, f"垫片里有走 PATH 的裸 python3 调用：{bare}"


def test_shim_runs_on_floor_interpreter(payload: Path, sys_py: str):
    """垫片在 3.9.6 上必须能跑起来（哪怕只是打印用法）。"""
    r = subprocess.run(
        ["/bin/bash", str(payload / "bin" / "mlx")],
        capture_output=True, text=True, timeout=10, check=False,
        env={"PATH": "/usr/bin:/bin", "MLX_CODE_ROOT": str(payload)},
    )
    assert r.returncode != 127, f"垫片无法执行：{r.stderr}"


def test_shim_returns_registered_rc(payload: Path):
    """无参数调用必须返回 rc 2（`BC-E-01`：用法错 == 2），不是 0 也不是 1。

    坏样本：`main()` 末尾漏 return ⇒ Python 返回 None ⇒ sys.exit(None) ⇒ rc 0
    ⇒ 调用方（脚本 / CI）把「用法都不对」当成功。
    上一版断言只写了 `!= 127`，对这个坏样本恒绿 —— 本条是它的补强。
    """
    r = subprocess.run(
        [str(payload / "bin" / "mlx")],
        capture_output=True, text=True, timeout=10, check=False,
        env={"PATH": "/usr/bin:/bin", "MLX_CODE_ROOT": str(payload)},
    )
    assert r.returncode == 2, f"期望 rc 2，实得 {r.returncode}；stderr={r.stderr!r}"
    assert r.stdout == "", f"用法信息必须走 stderr（INV-17），stdout 实得 {r.stdout!r}"
    assert "usage" in r.stderr.lower()
