"""D · `verify`（按 manifest.tsv 校验产物集合）。`render` 本版未实现，见下。"""
import os

from ..result import Done, Refused


def run(ctx, stack, inv, deps=None):
    deps = deps or {}
    tables = deps.get("tables")
    if tables is None:
        return Refused(3, "bootstrap.no_tables")

    if inv.verb == "verify":
        missing, extra = [], []
        declared = {row["path"] for row in tables.manifest()}
        for rel in sorted(declared):
            if not os.path.exists(os.path.join(ctx.root_code, rel)):
                missing.append(rel)
        if missing:
            # **rc 6（前置拒绝，零资源）而不是 3（外部依赖不可用）**：
            # 产物缺失是本地校验的确定结论，不是网络/venv/外部命令的问题。
            # 台账给 `bootstrap verify` 登记的码集是 `0,2,6`，此前实现返回 3 ——
            # `mlx help bootstrap verify` 印出来的码里没有它。
            return Refused(6, "bootstrap.manifest_missing", missing=missing)
        return Done({"verified": len(declared), "extra": extra})

    # `render`（补全脚本 / plist）**本版没有实现**，已从 `verbs.tsv` 摘掉 ——
    # 台账头部自己写着「只登记本版已实现的动词」，而它躺在表里 + help 里，
    # 敲下去却报「渲染器未注入 —— 这是装配缺陷，请重装：mlx install」。
    # 那句话把实现缺失说成了用户的安装坏了，会把人送进无效的重装循环
    # （而重装本身在这一版还有别的问题）。摘掉之后它报的是「未知命令」，
    # 与 `mlx logs` 等其它未实现动词一致。补全的设计（由动词台账生成）
    # 与 `install.sh` 的 I13 调用点一并记在 KNOWN-ISSUES。
    return Refused(2, "cmd.unknown_verb", verb=inv.verb)
