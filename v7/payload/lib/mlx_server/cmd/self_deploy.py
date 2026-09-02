"""D 层 · `install` / `uninstall` —— **工具自身**的部署面（F-14）。

**本模块不实现部署。** `deployer/{install,uninstall}.sh` 是唯一实现点（INV-20）：
在 T1 侧再写一份，立刻产生第二套三根定义与第二份分发清单遍历，
而三根一旦有两个来源，「装到哪」就有两个都「有依据」的答案。

本模块只做四件事：目标域校验 → 预加载模块 → 子进程调 T0（显式传三根）→ 退出码转译。
"""
import os
import subprocess
import sys

from ..result import Done, Failed, Passthrough, Refused

# T0 的部署硬门码。**五个各自独立**，不得塌成一个 —— 塌了用户就丢掉
# 「五项里哪一项没过」，而那正是 §4.3 引入它们的唯一理由。
HARD_GATE_CODES = frozenset({70, 71, 72, 73, 74})

SCRIPTS = {"install": "install.sh", "uninstall": "uninstall.sh"}


def _preload():
    # type: () -> None
    """把返回路径上会用到的模块**在 spawn 之前**全部加载进内存（F-14 §14.4）。

    外层的 `mlx` 是从 `<code_root>/lib/` 跑起来的解释器，而 T0 会换掉或删掉那棵树：

    · **重装**：换名后 `sys.path` 上的字符串已指向新树 ⇒ 之后任何一次惰性 import
      加载的是**新版本模块进老版本进程**，升级时正好是 API 变了的那批 ⇒
      打印装后自检时炸，**而磁盘上其实装成功了**，诊断与实际状态相反。
    · **卸载**：代码根删空后，返回路径上的 import 直接 ModuleNotFoundError ⇒
      文件确实删干净了、「残留集合 == ∅」为绿，用户看到的却是 traceback。
    """
    from .. import atomic, supervisor, tables, ui  # noqa: F401
    from ..cleanup import CleanupStack  # noqa: F401
    from ..result import Done, Failed, Refused  # noqa: F401


def _t0_env(ctx):
    # type: (object) -> dict
    """子进程环境。**三根必须显式传** ——

    不传时 T0 回落到默认路径：装在自定义根的用户执行 `mlx uninstall`，
    确认提示打印的是默认路径（那串路径长得就该是那样，看不出不对）、
    收敛前置因 `run/` 不在那里而跳过、最后去删另一个位置 ——
    要么什么都没删而报成功，要么删掉**另一套安装**。
    """
    env = dict(os.environ)
    env["MLX_ROOT_CODE"] = ctx.root_code
    env["MLX_ROOT_DATA"] = ctx.root_data
    env["MLX_ROOT_WEIGHTS"] = ctx.root_weights
    return env


def run(ctx, stack, inv, deps=None):
    # type: (object, object, object, dict) -> object
    """S1 目标域校验 → S2 预加载 → S3 子进程调 T0 → S4 退出码转译。"""
    verb = inv.verb
    script = SCRIPTS.get(verb)
    if script is None:
        return Refused(2, "self.unknown_verb", verb=verb)

    # ── S1 · 目标域校验（`cli` 已按元数拦过位置参数，这里是纵深）──
    if inv.target:
        return Refused(2, "self.takes_no_target", verb=verb, got=inv.target)

    path = os.path.join(ctx.root_code, "deployer", script)
    if not os.path.exists(path):
        # 部署器不在代码根 ⇒ 这套安装是旧版或残缺的。**指名可执行的替代路径**
        return Refused(6, "self.deployer_missing", path=path,
                       detail="部署器不在代码根内；用源码树里的 "
                              f"`bash <src>/deployer/{script}` 代替")

    if inv.dry_run:
        return Done({"kind": "self", "action": verb, "would_run": path, "roots": {
            "code": ctx.root_code, "data": ctx.root_data,
            "weights": ctx.root_weights}})

    # ── S2 · 预加载。**必须在 spawn 之前** ──
    _preload()

    # ── S3 · 子进程调 T0 ──
    runner = (deps or {}).get("run_deployer") or _run_deployer
    rc = runner(path, _t0_env(ctx))

    # ── S4 · 退出码转译：恒等 ──
    if rc == 0:
        return Done({"kind": "self", "action": verb, "roots": {
            "code": ctx.root_code, "data": ctx.root_data,
            "weights": ctx.root_weights}})
    if rc == 5:
        # **用户取消不是故障。** T0 把 rc 5 明确列为合法出口（0 走完 · 5 用户取消
        # · 6 服务未收敛），此前它并进 Failed 通道，于是「已取消，未删除任何东西」
        # 后面又跟一句「安装/卸载未完成。上面的分步输出是线索」—— 两句互相拆台，
        # 而那几行输出说的正是用户自己取消了。
        return Refused(5, "self.cancelled", action=verb)
    if rc in HARD_GATE_CODES:
        return Passthrough(rc, "self.hard_gate", gate_rc=rc)
    return Failed(rc if rc in (1, 3, 4, 10) else 1, "self.deployer_failed",
                  [], rc_from_t0=rc)


def _run_deployer(path, env):
    # type: (str, dict) -> int
    """生产实现：把 T0 的 stderr 原样透给用户，stdout 也转 stderr。

    **T0 的 stdout 恒空**（R-DEPLOY-39）：主产物只由本模块的 `--json` 产出，
    T0 那边任何漏进 stdout 的东西都会污染机读面。
    """
    p = subprocess.run([  # noqa: S603
        "/bin/bash", path], env=env, stdout=subprocess.PIPE, check=False)
    if p.stdout:
        sys.stderr.write(p.stdout.decode("utf-8", "replace"))
    return p.returncode
