"""`tv.inject` —— 三类注入面。**关闭态零语义差异**。

① `ctx` 注入（路径、命令位置、时钟）
② 产品产出的可对拍面（`--dry-run` 的 argv）
③ 确定性故障 / 延迟 / 时序注入点（`inject.tsv` 台账）
"""


class Hooks:
    """注入点集合。`enabled ⊆ inject.tsv 的行集合`（`G-VER-07`）。"""

    __slots__ = ("_enabled", "_ledger")

    KNOWN = ("INJ-01", "INJ-02", "INJ-03", "INJ-04", "INJ-05",
             "INJ-06", "INJ-07", "INJ-08", "INJ-09", "INJ-10")

    def __init__(self, enabled=()):
        bad = set(enabled) - set(self.KNOWN)
        if bad:
            raise ValueError(f"未登记的注入点 {sorted(bad)} —— enabled ⊆ inject.tsv 的行集合")
        self._enabled = frozenset(enabled)
        self._ledger = []

    def fire(self, point, payload=None):
        """触发一个注入点。**未启用时返回 None 且不记账** —— 关闭态零语义差异。"""
        if point not in self._enabled:
            return None
        self._ledger.append((point, payload))
        return payload if payload is not None else True

    @property
    def ledger(self):
        return list(self._ledger)

    def enabled(self):
        return sorted(self._enabled)
