"""语言下限 3.9.6 的守卫（裁决 `D-1` / `04b-facts-python.md`）。

**为什么不用 mypy --python-version 3.9**：实测 mypy 2.3.1 已不支持 3.9
（最低 3.10）——静态检查工具够不着我们的下限了。
改用**真解释器编译**：它比版本模拟更强，因为它就是产品实际运行的那个解释器
（归档教训 L2：可执行路径 × **真实产物** × 退出码绑出口）。
"""
import subprocess
from pathlib import Path


def test_all_product_py_compiles_on_floor(payload: Path, sys_py: str):
    """所有 T1 产品代码必须能被 3.9.6 编译。

    坏样本：用 `match` / `X | Y` 求值注解 / `dataclass(slots=True)` / `TypeAlias`
    ⇒ 3.9 上 SyntaxError 或 ImportError ⇒ 而 T1 的存在理由正是
    「venv 坏了 mlx check 也能跑」，那时唯一可用的解释器就是它。
    """
    files = sorted((payload / "lib").rglob("*.py"))
    assert files, "没有找到任何 T1 产品代码 —— 本条守卫会空真通过，必须先有代码"

    failed = []
    for f in files:
        r = subprocess.run(
            [sys_py, "-m", "py_compile", str(f)],
            capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            failed.append((f.name, r.stderr.strip().splitlines()[-1] if r.stderr else "?"))
    assert not failed, f"这些文件无法在 3.9.6 上编译：{failed}"


def test_no_310plus_syntax(payload: Path):
    """AST 层挡住 3.10+ 写法，给出比 SyntaxError 更清楚的信息。

    **必须用 AST 不能用正则**：第一版用正则，被 `__main__.py` 的 docstring
    「不得用 match / slots=True / TypeAlias」**自己误报**——文本里提到禁用写法
    与真的使用禁用写法，正则分不出来（归档教训 L6.1：正则的求值面比以为的宽）。
    AST 只看语法结构，字符串与注释里的内容不进 AST。
    """
    import ast

    hits = []
    for f in sorted((payload / "lib").rglob("*.py")):
        tree = ast.parse(f.read_text(), filename=str(f))
        for node in ast.walk(tree):
            # match 语句（3.10+）
            if type(node).__name__ == "Match":
                hits.append(f"{f.name}:{node.lineno} match 语句（3.10+）")
            # @dataclass(slots=True)（3.10+）
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "slots":
                        hits.append(f"{f.name}:{node.lineno} dataclass(slots=True)（3.10+）")
            # TypeAlias 的 import 或注解（3.10+）
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "TypeAlias":
                        hits.append(f"{f.name}:{node.lineno} 导入 TypeAlias（3.10+）")
            # 求值的 X | Y 注解（3.10+）——注解位置的 BinOp(BitOr)
            if (isinstance(node, ast.AnnAssign)
                    and isinstance(node.annotation, ast.BinOp)
                    and isinstance(node.annotation.op, ast.BitOr)):
                hits.append(f"{f.name}:{node.lineno} 求值的 X | Y 注解（3.10+）")
    assert not hits, f"用到了 3.9 不支持的写法：{hits}"
