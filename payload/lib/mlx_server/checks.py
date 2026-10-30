"""B 层 · 十二项健康检查的**生产实现**。

`cmd.check` 只聚合、不实现任何一项（`02` §4.1）—— 本模块是那十二项的落点。
每项返回 `Probe[bool]`：`Ok(True)` 通过 · `Ok(False)` 不通过 · `Unknown` 判不出。
"""
import os
import subprocess

from .result import Ok, Probe, Unknown


def fail(cause, path):
    # type: (str, str) -> object
    """`Ok(False)` 并带上成因与路径（`reason` 是给人看的，判定只读 value）—— `cmd.check`
    据此给出能照做的修法，而不是「检查权限 / 磁盘挂载」。"""
    return Probe(True, value=False, reason=f"{cause}|{path}")


def _dir_cause(path):
    # type: (str) -> object
    if os.path.isdir(path):
        return None if os.access(path, os.W_OK) else "not_writable"
    if os.path.exists(path):
        return "not_dir"
    parts = os.path.abspath(path).split(os.sep)
    if len(parts) > 2 and parts[1] == "Volumes" and not os.path.isdir(os.sep.join(parts[:3])):
        return "unmounted"
    return "missing"


def _dir_ok(path, what):
    if not path:
        return Unknown("PARSE_FAILED", f"{what} 未配置")
    cause = _dir_cause(path)
    return Ok(True) if cause is None else fail(cause, path)


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
#   · `mlx 0.30.0` 起只发 cp310+（2026-10-31 查 PyPI：0.30.0 / 0.30.3 / 0.30.4 都没有 cp39）
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
    if not os.path.exists(d):
        return Ok(True)
    cause = _dir_cause(d)
    return Ok(True) if cause is None else fail(cause, d)


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


def gateway_nets(ctx):
    """网关的放行网段是解析出来的（`parsed`），不是回落值。

    通过条件是「没有运行记录，**或**运行记录里压根没有 `nets_source` 这个键」——
    后者不止「网关没在跑」，也覆盖「网关在跑，但门面从没把 `nets_source` 并进
    运行记录」这种接线缺口（本项要防的正是「算出来的网段不是 parsed」，
    这两种情形都无从判定「算出来的是什么」，所以都放行，不当失败）。
    """
    from . import supervisor
    try:
        snap = supervisor.snapshot(ctx, "api")
    except supervisor.RunRecordUnreadable as e:
        return Unknown("PARSE_FAILED", str(e)[:120])
    if not snap or "nets_source" not in snap:
        return Ok(True)
    return Ok(snap["nets_source"] == "parsed")


def gateway_port(ctx):
    """网关端口上没有外来监听者（spec D24）。有记录 ⇒ 全部监听者须属网关；无记录 ⇒ 须无人监听。

    `supervisor.port_owner` 在「记录还在，但端口上此刻确知无人监听」时报
    `Unknown("NO_LISTENER")`（进程可能已静默退出，记录是陈旧的）——这不是
    「外来监听者占用端口」要防的那件事，**在这里特意塌成 `Ok(True)`**（D8：
    Unknown 折叠必须声明）；其余 `Unknown`（探测本身失败，如 `PS_FAILED`）
    原样穿透，不塌成通过或失败——判不出就是判不出。
    """
    from . import supervisor
    try:
        snap = supervisor.snapshot(ctx, "api")
    except supervisor.RunRecordUnreadable as e:
        return Unknown("PARSE_FAILED", str(e)[:120])
    if snap:
        own = supervisor.port_owner(ctx, "api")
        if own.is_unknown():
            return Ok(True) if own.kind == "NO_LISTENER" else own
        return Ok(own.value is True)
    fl = supervisor.foreign_listeners(ctx, ctx.gateway_port)
    return fl if fl.is_unknown() else Ok(not fl.value)


def settings_mode(ctx):
    """settings.json 权限不宽于 0600（里面有两把密钥）。文件不存在 ⇒ 通过。

    `os.path.exists` 与 `os.stat` 之间有 TOCTOU 窗口（文件被并发删除、
    上级目录不可读等）；这里不让 `OSError` 原样穿出去 —— `cmd.check` 的
    12 行主产物一项探测失败不能连累另外 11 项全丢
    （`test_l3_check_failure_keeps_the_twelve_rows` 护的就是这条）。
    """
    import stat

    from . import store
    p = store.settings_path(ctx)
    try:
        if not os.path.exists(p):
            return Ok(True)
        return Ok(not (stat.S_IMODE(os.stat(p).st_mode) & 0o077))
    except OSError as e:
        return Unknown("PARSE_FAILED", str(e)[:120])


ALL = {
    "code_root": code_root, "data_root": data_root, "weights_root": weights_root,
    "venv": venv, "tables": tables, "models_file": models_file,
    "run_dir": run_dir, "ports": ports, "caps": caps,
    "gateway_nets": gateway_nets, "gateway_port": gateway_port, "settings_mode": settings_mode,
}
