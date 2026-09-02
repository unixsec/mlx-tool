"""A 层 · 清理栈原语。幂等、逆序、每项只执行一次。

由 `entry` 创建并作为**独立参数**下传，**不并进 `ctx`**
（边 #10 的理由：冻结数据类持可变栈会在拷贝与相等比较时产生别名问题）。
"""


class CleanupStack:
    """`push(closure) -> handle` · `drop(handle)` · `run(handle)` · `unwind()` · `depth()`。"""

    __slots__ = ("_items", "_next")

    def __init__(self):
        # type: () -> None
        self._items = {}     # handle → (closure, done)
        self._next = 0

    def push(self, closure):
        # type: (object) -> int
        h = self._next
        self._next += 1
        self._items[h] = [closure, False]
        return h

    def drop(self, handle):
        # type: (int) -> None
        """撤销登记（资源已由别处接管），**不执行**。"""
        self._items.pop(handle, None)

    def run(self, handle):
        # type: (int) -> None
        """执行单项。**每项只执行一次**（幂等）。"""
        item = self._items.get(handle)
        if item is None or item[1]:
            return
        item[1] = True
        item[0]()

    def unwind(self):
        # type: () -> list
        """**逆序**执行全部未执行项。返回执行中抛出的异常列表（不中断其余项）。"""
        errors = []
        for h in sorted(self._items, reverse=True):
            item = self._items[h]
            if item[1]:
                continue
            item[1] = True
            try:
                item[0]()
            except Exception as e:      # noqa: BLE001 —— 清理不得因单项失败中断
                errors.append((h, e))
        return errors

    def depth(self):
        # type: () -> int
        """未执行项数。INV-21 按**阶段**断言它（`DR-02`）。"""
        return len([1 for c, done in self._items.values() if not done])
