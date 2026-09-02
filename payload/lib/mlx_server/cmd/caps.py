"""D · `show` / `refresh` —— **只编排，零自有资源**。

`caps.json` 的读写与解释在 C 层的 `caps` 模块。此前它们都在这里，
而契约明写「D 层全部成员 `owns_resource` 为空」——后果是 `checks`(B) 与
`cmd.meta`(D) 要读能力视图就只能反向 import 一个 D 层模块。
"""
import time

from .. import caps
from ..result import Done, Refused

# 兼容旧引用点：`PACKAGES` 的唯一定义在 C 层，这里只是转发。
PACKAGES = caps.PACKAGES


def get(ctx):
    """转发到 C 层。保留是为了不让既有调用点一次性全改。"""
    return caps.get(ctx)


def drift(ctx, probe_fingerprint=None):
    """转发到 C 层。"""
    return caps.drift(ctx, probe_fingerprint)


def run(ctx, stack, inv, deps=None):
    deps = deps or {}
    if inv.verb == "show":
        c = caps.get(ctx)
        return Done(dict(c, kind="caps")) if c else Refused(6, "caps.not_collected")
    if inv.verb == "refresh":
        prev = caps.get(ctx)               # 写盘**之前**取旧值，用于算差异
        fp = deps.get("fingerprint")
        if fp is None:
            return Refused(3, "caps.no_prober")
        cur = fp()
        view = caps.build_view(cur, deps.get("now", time.time))
        if view is None:
            # 探测确定失败（非 TOOL_UNAVAILABLE）⇒ 不写盘、指名 kind
            return Refused(9, "caps.undeterminable", kind=cur.kind)
        caps.write(ctx, view)
        return Done({"kind": "caps_diff", "changes": caps.diff(prev, view),
                     "collected_at": view.get("collected_at")})
    return Refused(2, "cmd.unknown_verb", verb=inv.verb)
