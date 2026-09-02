"""E2 · 不可 vacuous 的闭集断言（05i §4 出口条件）。

出口条件逐字：「本片新增的每条**闭集型断言**必须打印三元组
（判定, 左侧基数, 右侧基数）」。

**为什么**：闭集断言（集合相等 / 子集 / 差集为空）在**左侧为空**时恒真。
「0 个违规」与「0 个被检查对象」在断言结果上不可区分，而它们的含义完全相反。
打印基数让这个区别可见 —— 报告里 `(pass, 0, 0)` 一眼就能看出是空真。
"""


class Cardinality:
    """收集本次运行的所有闭集断言基数，供报告打印。"""

    records = []          # type: list

    @classmethod
    def check(cls, name, left, right=None, rel="subset"):
        # type: (str, object, object, str) -> None
        """记录并断言。left 为空时不静默通过 —— 调用方必须显式声明它可以为空。"""
        ln = len(left)
        rn = len(right) if right is not None else None
        cls.records.append((name, ln, rn, rel))
        if rel == "empty":
            assert ln == 0, f"{name}: 期望空集，实得 {ln} 项：{list(left)[:5]}"
        elif rel == "subset":
            assert set(left) <= set(right), f"{name}: {set(left) - set(right)} 不在右集合内"
        elif rel == "equal":
            assert set(left) == set(right), (
                f"{name}: 左多 {set(left)-set(right)}；右多 {set(right)-set(left)}"
            )

    @classmethod
    def require_nonempty(cls, name, universe, why):
        # type: (str, object, str) -> None
        """**防空真**：断言被检查的对象集合非空，否则这条闭集断言什么也没证明。"""
        n = len(universe)
        cls.records.append((f"{name}·universe", n, None, "nonempty"))
        assert n > 0, f"{name} 的被检查对象集合为空 ⇒ 该断言空真。{why}"

    @classmethod
    def report(cls):
        # type: () -> str
        lines = ["", "闭集断言基数三元组（E2）：", "  判定                                左侧  右侧  关系"]
        for name, ln, rn, rel in cls.records:
            flag = " ← 空真风险" if ln == 0 and rel != "empty" else ""
            lines.append(f"  {name:<34} {ln:>4}  {'-' if rn is None else rn:>4}  {rel}{flag}")
        return "\n".join(lines)
