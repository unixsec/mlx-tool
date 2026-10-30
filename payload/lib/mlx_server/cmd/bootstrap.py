"""D · `verify`（按 manifest.tsv 校验产物集合）· `keys`（生成缺失密钥）。"""
import os

from ..result import Done, Refused


def run(ctx, stack, inv, deps=None):
    deps = deps or {}
    tables = deps.get("tables")
    if tables is None:
        return Refused(3, "bootstrap.no_tables")

    if inv.verb == "keys":
        return _keys(ctx, stack, tables)

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
    # 与 `install.sh` 的 I13 调用点一并留待以后。
    return Refused(2, "cmd.unknown_verb", verb=inv.verb)


def _keys(ctx, stack, tables):
    # type: (object, object, object) -> object
    """生成缺失的密钥（spec §5.2）。**只输出键名，从不输出值** —— 这条命令的输出会进
    安装日志、终端回滚与 CI 记录，任何一处留下值都等于明文存盘。

    判据取自 `schema.tsv`（secret == yes ∧ scope == user ∧ level == global），
    不在这里写键名清单：将来加第三把密钥只改表。
    """
    import secrets

    from .. import schema, store
    defs = schema.keydefs(tables)
    wanted = sorted(k for k, d in defs.items()
                    if d.get("secret") == "yes" and d.get("scope") == "user"
                    and d.get("level") == "global")
    current = store.load_settings(ctx)
    missing = [k for k in wanted if not current.get(k)]
    kept = [k for k in wanted if current.get(k)]
    if missing:
        store.commit(ctx, store.SETTINGS,
                     store.SettingsTx({k: secrets.token_urlsafe(32) for k in missing}), stack)
    p = store.settings_path(ctx)
    if os.path.exists(p):
        os.chmod(p, 0o600)      # 1.0.0 时代以 0644 落盘、且本次无需写入的既有文件
    return Done({"kind": "keys", "generated": missing, "kept": kept})
