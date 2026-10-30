"""D · 十二项健康检查的独立求值 + 聚合。**只聚合，不实现任何一项。**

机读输出契约（`R-OBSERVE-24`）：每行 `name<TAB>verdict<TAB>detail`，
`verdict ∈ verdicts 表`，行数 == 检查项数，**主产物中不含无法判定的塌陷值**。
"""
from ..result import Aggregate, Done, is_unknown

VERDICTS = ("ok", "failed", "undeterminable")
ITEMS = ("code_root", "data_root", "weights_root", "venv", "tables",
         "models_file", "run_dir", "ports", "caps",
         "gateway_nets", "gateway_port", "settings_mode")


# 每项失败时的**下一步**。检查报告的价值在于它告诉你该做什么，
# 而不是让你拿着一个 False 去猜。
def _cause_hint(tables, probe):
    # type: (object, object) -> str
    """带成因的失败（`checks.fail`）→ 写着真实路径的修法；没有成因 ⇒ 空串（退回按项的通用提示）。"""
    reason = getattr(probe, "reason", None) or ""
    cause, sep, path = reason.partition("|")
    if not sep or tables is None:
        return ""
    parts = path.split("/")
    volume = parts[2] if len(parts) > 2 and parts[1] == "Volumes" else ""
    try:
        return tables.text(tables.msg("check.hint.dir." + cause), path=path, volume=volume)
    except Exception:                              # noqa: BLE001
        return ""


def _hint(tables, item):
    # type: (object, str) -> str
    """某一项失败时的**下一步**。文案在 `messages.tsv`（键 `check.hint.<项>`）。

    检查报告的价值在于它告诉你该做什么，而不是让你拿着一个 False 去猜。
    此前这九条是本文件里的一个字典字面量 —— 逃在 `G-PC-05`（文案里提到的
    `mlx <命令>` 必须真的存在）射程之外，而它们每一条都提到了某条命令。
    """
    if tables is None:
        return ""
    try:
        return tables.text(tables.msg("check.hint." + item))
    except Exception:                              # noqa: BLE001
        return ""




def run(ctx, stack, inv, deps=None):
    """每项独立求值 ⇒ 聚合。**`Unknown` 报 undeterminable 并计入补集，不塌成 ok。**"""
    deps = deps or {}
    checks = deps.get("checks") or {}
    rows, undeterminable, failed = [], [], []
    for name in ITEMS:
        fn = checks.get(name)
        if fn is None:
            rows.append((name, "undeterminable", "检查项未注入"))
            undeterminable.append(name)
            continue
        r = fn(ctx)
        if is_unknown(r):
            # 判不出的也要说出**为什么**（此前只有 kind，如 PARSE_FAILED）
            rows.append((name, "undeterminable",
                         (r.kind or "") + (f"：{r.reason}" if r.reason else "")))
            undeterminable.append(name)
        elif r.value is True:
            # `Ok` 是工厂函数不是类型 —— `isinstance(r, Ok)` 会 TypeError。
            # 判 Probe 的真假一律读 .value，这也是 INV-09 要的形态。
            rows.append((name, "ok", ""))
        else:
            # **detail 必须可操作**。此前填 `str(r.value)` ⇒ 用户看到的是
            # `venv  failed  False` —— 既没说哪里不对，也没说该做什么。
            rows.append((name, "failed", _cause_hint(deps.get("tables"), r) or _hint(deps.get("tables"), name)))
            failed.append(name)

    payload = {"rows": rows, "undeterminable": undeterminable, "failed": failed}
    if failed:
        return Aggregate(65, dict(payload, kind="checks"), "check.failed")
    if undeterminable:
        return Aggregate(64, dict(payload, kind="checks"), "check.undeterminable")
    return Done(dict(payload, kind="checks"))
