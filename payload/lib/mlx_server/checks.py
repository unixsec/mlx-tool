"""B 层 · 九项健康检查的**生产实现**。

`cmd.check` 只聚合、不实现任何一项（`02` §4.1）—— 本模块是那九项的落点。
每项返回 `Probe[bool]`：`Ok(True)` 通过 · `Ok(False)` 不通过 · `Unknown` 判不出。
"""
import os
import subprocess

from .result import Ok, Unknown


def _dir_ok(path, what):
    if not path:
        return Unknown("PARSE_FAILED", f"{what} 未配置")
    if not os.path.isdir(path):
        return Ok(False)
    return Ok(os.access(path, os.W_OK))


def code_root(ctx):
    """代码根存在且含 bin/mlx。"""
    r = _dir_ok(ctx.root_code, "代码根")
    if r.is_unknown() or r.value is False:
        return r
    return Ok(os.path.exists(os.path.join(ctx.root_code, "bin", "mlx")))


def data_root(ctx):
    return _dir_ok(ctx.root_data, "数据根")


def weights_root(ctx):
    return _dir_ok(ctx.root_weights, "权重根")


# 推理后端的解释器下限。**不是随便定的**：
#   · `mlx 0.29.3` 是最后一个有 cp39 wheel 的版本
#   · `mlx 0.30.4` 起只发 cp310+
#   · `mlx-lm ≥ 0.31.0`（新架构如 `qwen3_5` 所需）依赖 `mlx ≥ 0.30.4`
# ⇒ venv 是 3.9 时，`pip install -U mlx-lm` 以 ResolutionImpossible 失败，
#   而那条错误信息**一个字都不提解释器版本**，用户无从下手（实测踩过）。
VENV_PY_MIN = (3, 10)


def venv(ctx):
    """venv 解释器在不在，**且版本够不够装新的推理后端**。

    「不在」不是错误 —— 只有推理才需要它；但「在，却是 3.9」是错误：
    那个 venv 装不了 `mlx ≥ 0.30`，用户要等到 `mlx up` 失败（或
    `pip install` 报一条不提版本的依赖冲突）才发现。
    这一项的价值就是**把那个发现提前到 check**。
    """
    root = getattr(ctx, "venv", "") or ""
    p = os.path.join(root, "bin", "python3")
    if not root or not os.path.exists(p):
        return Ok(False)
    try:
        r = subprocess.run(                                  # noqa: S603
            [p, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return Unknown("TOOL_UNAVAILABLE", str(e)[:80])
    if r.returncode != 0:
        return Unknown("TOOL_UNAVAILABLE", (r.stderr or "").strip()[:80])
    try:
        ver = tuple(int(x) for x in r.stdout.strip().split("."))
    except ValueError:
        return Unknown("PARSE_FAILED", r.stdout.strip()[:40])
    return Ok(ver >= VENV_PY_MIN)


def tables(ctx):
    """三张表齐不齐。"""
    from .ctx import share_of
    share = share_of(ctx)
    need = ("manifest.tsv", "messages.tsv", "constants.env")
    return Ok(all(os.path.exists(os.path.join(share, f)) for f in need))


def models_file(ctx):
    """注册表可解析 —— **坏了要报出来**，不是当成空表。"""
    from . import store
    try:
        res = store.load(ctx, store.MODELS)
    except store.StoreParseError as e:
        return Unknown("PARSE_FAILED", str(e)[:120])
    return Ok(not res.bad_lines)


def run_dir(ctx):
    """运行记录目录可写（没有也算通过 —— 首次使用时它还不存在）。"""
    d = os.path.join(ctx.root_data, "run")
    return Ok(True) if not os.path.exists(d) else Ok(os.access(d, os.W_OK))


def ports(ctx):
    """端口登记面无冲突。"""
    from . import ports as ports_mod
    from . import store
    try:
        res = store.load(ctx, store.MODELS)
    except Exception:                                    # noqa: BLE001
        return Unknown("PARSE_FAILED", "注册表读不出来")
    return Ok(ports_mod.conflict_check(ctx, res.records) is None)


def caps(ctx):
    """能力信息采集过没有。**没采集不是失败**，是「还没做」。"""
    from . import caps as caps_mod
    return Ok(bool(caps_mod.get(ctx)))


ALL = {
    "code_root": code_root, "data_root": data_root, "weights_root": weights_root,
    "venv": venv, "tables": tables, "models_file": models_file,
    "run_dir": run_dir, "ports": ports, "caps": caps,
}
