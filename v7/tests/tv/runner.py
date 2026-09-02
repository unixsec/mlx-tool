"""`tv.runner` —— 统一运行器。

总结行**在同一屏**打印补集；skip 与 pending **各自独立计数且不计入通过数**。
"""


class Report:
    __slots__ = ("passed", "failed", "skipped", "pending", "complement")

    def __init__(self):
        self.passed = self.failed = self.skipped = self.pending = 0
        self.complement = []          # 补集：本轮**没被任何用例覆盖**的对象

    def summary(self):
        """**通过数不含 skip 与 pending** —— 「没跑」不是「跑过了」。"""
        total = self.passed + self.failed + self.skipped + self.pending
        lines = [
            f"通过 {self.passed} · 失败 {self.failed} · "
            f"跳过 {self.skipped} · 待录 {self.pending} · 合计 {total}",
        ]
        if self.complement:
            lines.append(f"补集（{len(self.complement)} 项未被覆盖）：" +
                         "、".join(map(str, self.complement[:8])))
        else:
            lines.append("补集：空")
        return "\n".join(lines)


def coverage_complement(universe, covered):
    """补集 = 应覆盖 − 已覆盖。**空集要显式打印**，否则「没算」与「算了是空」不可分。"""
    return sorted(set(universe) - set(covered))
