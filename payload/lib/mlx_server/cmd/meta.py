"""D · `version` / `completion show` / `internal confirm`。"""
from ..result import Done, Refused

# **版本串的唯一定义点**（`svc.py:309` import 它，注释明写「不在这里写第二遍」）。
# 文档里的版本由守卫 `test_version_string_is_single_sourced` 钉在这上面 ——
# 发版时漏改文档是最容易发生的那种不一致。
VERSION = "1.2.0"


def run(ctx, stack, inv, deps=None):
    deps = deps or {}
    if inv.verb == "version":
        # 设计（附录 A）要求：T1 版本 / **两个解释器** / 依赖三包 / caps 采集时间。
        # 只报一个版本号，用户排查「跑在哪个 python 上」「依赖漂移了吗」时无处可看。
        import os
        import sys

        # **当前跑在哪个解释器上** —— 垫片优先 venv、回落系统 python3，
        # 所以这一行的值本身就是「走了哪条路径」的证据（R-DEPLOY-40 的验收用它）
        payload = {"kind": "version", "version": VERSION,
                   "python": f"{sys.version.split()[0]}  {sys.executable}"}

        venv_py = os.path.join(ctx.root_code, ".venv", "bin", "python")
        payload["venv_python"] = venv_py if os.path.exists(venv_py) else None

        # 能力未采集不是错误 —— **`version` 必须在任何状态下都能跑通**，
        # 它常常是用户排查时敲的第一条命令。
        from .. import caps as caps_mod
        cached = caps_mod.get(ctx) or {}
        pkgs = cached.get("packages")
        if pkgs:
            # **缺失只看必需包**。可选包没装是常态，不是缺陷 —— 把它报成
            # 「未装：mlx_vlm」会让用户以为装漏了（实测有人因此来问）。
            missing = [k for k in caps_mod.REQUIRED if k not in pkgs]
            txt = ", ".join(pkgs)
            if missing:
                txt += "（**缺必需包**：" + "、".join(missing) + " —— 重跑安装器）"
            absent_opt = [k for k in caps_mod.OPTIONAL if k not in pkgs]
            if absent_opt:
                txt += "；可选未装：" + "、".join(
                    f"{k}（{caps_mod.OPTIONAL[k]}）" for k in absent_opt)
            payload["packages"] = txt
        else:
            payload["packages"] = ("venv 里一个后端包都没装 —— 跑 `mlx caps refresh` 复采"
                                   if cached else "尚未采集 —— 跑 `mlx caps refresh`")
        fp = cached.get("fingerprint")
        if fp:                       # 空指纹不显示 —— 空字段只会占地方
            payload["fingerprint"] = fp
        payload["caps_collected_at"] = (
            cached.get("collected_at") or "尚未采集 —— 跑 `mlx caps refresh`")
        return Done(payload)
    if inv.verb == "confirm":
        # `internal confirm` 是 T0 委托 T1 的破坏性确认通道（边 #8）。
        # **EOF / 无 tty ⇒ 拒绝**；三种绕过（管道 / flag / 环境变量）全拒。
        confirm = deps.get("confirm")
        if confirm is None:
            return Refused(5, "meta.no_tty")
        expect = (inv.args or {}).get("expect") or ""
        return Done({"confirmed": True}) if confirm(expect) \
            else Refused(5, "meta.confirm_declined")
    return Refused(2, "cmd.unknown_verb", verb=inv.verb)
