"""P 层 · 容量判档。**纯函数，不调探针** —— 探针值由调用方注入。

被保护的对象逐字来自设计（`02-architecture.md:1804`）：
**「不要在容量不明时启动一个装不下的模型」**。
`mlx up <28GB 模型>` 在只剩 8 GB 的机器上照起不误、起完开始换页、机器卡死 ——
在这个模块存在之前，这条路径上没有任何保护。
"""
from .result import Ok, Unknown, is_unknown

# **档位枚举写死三个成员**（`02-architecture.md:1804` 是正本）。
# `UNKNOWN` **不是档位** —— 「不确定」由 `classify` 返回 `Unknown` 承载。
# 给它加第四个成员就是把「不确定」伪装成一档，而两者的处置完全相反：
# 一个是「拒绝并说清哪个输入不确定」，一个是「这台机器放得下/放不下」。
FIT, TIGHT, OVER = "FIT", "TIGHT", "OVER"
FIT_CLASSES = (FIT, TIGHT, OVER)


class FitThresholds:
    """三个**百分数整数**（`fit.reserve_pct` / `fit.tight_pct` / `fit.over_pct`）。

    设计逐字：「没有独立的绝对阈值键」—— 阈值一律是比例，绝对值来自探针。
    """

    __slots__ = ("reserve_pct", "tight_pct", "over_pct")

    def __init__(self, reserve_pct, tight_pct, over_pct):
        # type: (int, int, int) -> None
        self.reserve_pct = int(reserve_pct)
        self.tight_pct = int(tight_pct)
        self.over_pct = int(over_pct)


def classify(mem_budget_mb, size_mb, running_sum_mb, th):
    # type: (object, object, object, FitThresholds) -> object
    """这个模型在当前机器上属于哪一档。返回 `Probe[FitClass]`。

    **任一输入 `Unknown` ⇒ 返回 `Unknown` 并指名是哪个输入**，上层 fail-closed。
    塌成某一档就是在容量不明时替用户做决定，而这个判定保护的正是那种情形。

    **一律整数定点，禁止浮点**（设计逐字）。理由不是性能：`round()` 是
    银行家舍入（`round(0.5) == 0`、`round(2.5) == 2`），边界样例上两个
    消费者会算出**不同的档位** —— 而判档是个 fail-closed 的闸门，
    两处算出不同结果意味着闸门有时开有时关，且没人说得清为什么。
    """
    for name, probe in (("mem_budget_mb", mem_budget_mb),
                        ("size_mb", size_mb),
                        ("running_sum_mb", running_sum_mb)):
        if is_unknown(probe):
            return Unknown(probe.kind, f"{name}: {probe.reason}")

    total = int(mem_budget_mb.value)
    need = int(size_mb.value) + int(running_sum_mb.value)

    # 预留给系统的那一份先扣掉，剩下的才是可分配预算
    budget = total * (100 - th.reserve_pct) // 100
    if budget <= 0:
        return Ok(OVER)
    if need * 100 >= budget * th.over_pct:
        return Ok(OVER)
    if need * 100 >= budget * th.tight_pct:
        return Ok(TIGHT)
    return Ok(FIT)
