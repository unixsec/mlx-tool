"""T1 入口。**不带 shebang** —— 它只能经 `bin/mlx` 垫片以写死的 `/usr/bin/python3` 调起。

带 shebang 会暗示可直接执行，而直接执行走 `env python3`（PATH）⇒ 解释器随 brew 升级漂移，
正是垫片要避免的（`ruff EXE001` 抓到这条）。

运行时下限 3.9.6：不得用 match / slots=True / TypeAlias / 求值的 A|B。
"""
import sys

from .entry import main

if __name__ == "__main__":
    rc = main(sys.argv)
    assert isinstance(rc, int), f"main() 必须返回 int，实得 {rc!r} —— 漏 return 会让 sys.exit(None) 变成 rc 0"
    sys.exit(rc)
