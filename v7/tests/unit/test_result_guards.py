"""`L0·result-01` / `-02` / `-03` —— 三条 AST 守卫，第一次被真实代码求值（格 6）。

求值面是产品源码的 AST，不是 markdown。这是 47k 行设计里写了三轮的守卫，
本片第一次给它们一个真正的求值对象。
"""
import ast
from pathlib import Path

REFUSED_OK = {2, 3, 5, 6, 7, 8, 9}
FAILED_OK = {1, 3, 4, 10}


def _product_trees(payload: Path):
    for f in sorted((payload / "lib").rglob("*.py")):
        yield f, ast.parse(f.read_text(), filename=str(f))


def test_result01_code_literals_in_registered_sets(payload: Path):
    """`L0·result-01`：`Refused` 的 code 字面量 ⊆ {2,3,5,6,7,8,9}、`Failed` 的 ⊆ {1,3,4,10}。

    坏样本：`Refused(4)` —— 4 是 Failed 的码，混用会让「停止未达成」与「拒绝」不可区分。
    """
    bad = []
    for f, tree in _product_trees(payload):
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("Refused", "Failed") or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, int):
                allowed = REFUSED_OK if name == "Refused" else FAILED_OK
                if first.value not in allowed:
                    bad.append(f"{f.name}:{node.lineno} {name}({first.value})")
    assert not bad, f"退出码不在登记集合内：{bad}"


def test_result02_refused_precondition_is_zero_resource(payload: Path):
    """`L0·result-02`〔按裁决改写〕：`Refused` 的唯一前提是**零资源已创建**。

    **不是**「必须在 stage_split 之前构造」—— 原写法与 `BCR-R-01` 直接互斥：
    `supervisor.up` 必然在 stage_split 之后，也返回 `Refused(3)`/`Refused(9)`，
    原写法在四组共同约定的正确实现上**恒红**，一旦被放宽，
    `Refused` 的真前提（零资源）就再也没有守卫。

    可求值的形态：构造 `Refused` 的函数体内，不得在该构造点**之前**出现资源创建调用。
    """
    CREATORS = {"mkdir", "makedirs", "commit", "spawn", "spawn_supervised",
                "materialize", "lock", "open"}

    def _creates(node):
        """这次调用是否**创建**资源。

        `open` 要看模式：只读的 `open(p)` / `open(p, encoding=...)` 什么都不建，
        把它算进来会让「先读文件再校验、校验失败 Refused」这种完全正当的
        写法被判红 —— 守卫的提取面比不变式宽，报的是假阳性。
        判据：第二个位置参或 `mode=` 里含 `w`/`a`/`x`/`+`。
        """
        nm = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if nm != "open":
            return nm in CREATORS
        mode = None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        return bool(mode) and any(c in str(mode) for c in "wax+")
    bad = []
    for f, tree in _product_trees(payload):
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            created = []          # (lineno, name)
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    nm = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if _creates(node):
                        created.append((node.lineno, nm))
                    elif nm == "Refused" and created:
                        earlier = [c for c in created if c[0] < node.lineno]
                        if earlier:
                            bad.append(
                                f"{f.name}:{node.lineno} 在 {earlier[-1][1]}@{earlier[-1][0]} 之后构造 Refused"
                            )
    assert not bad, f"违反 BCR-R-01（Refused 的前提是零资源已创建）：{bad}"


def test_result03_probe_or_not_in_verdict_closure(payload: Path):
    """`L0·result-03`：`probe_or` 的调用点 ∩ `@verdict` 传递闭包 == ∅。

    坏样本：在 `running` 的实现里写 `probe_or(False, owns(...))`
    ⇒ `Unknown` 塌成「没在跑」⇒ 起第二份 28GB 后端。
    """
    bad = []
    for f, tree in _product_trees(payload):
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            decorated = any(
                (getattr(d, "id", None) or getattr(getattr(d, "func", d), "id", None)) == "verdict"
                for d in fn.decorator_list
            )
            if not decorated:
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    nm = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                    if nm == "probe_or":
                        bad.append(f"{f.name}:{node.lineno} 判定函数 {fn.name} 里用了 probe_or")
    assert not bad, f"probe_or 出现在判定路径上（fail-open）：{bad}"


def test_failed_requires_committed(payload: Path, sys_py: str):
    """`Failed` 必须带 `committed` —— 它是「已落地了什么」的唯一收敛线索。"""
    import subprocess
    import textwrap
    r = subprocess.run(
        [sys_py, "-c", textwrap.dedent("""
            from mlx_server.result import Failed, Refused
            try:
                Failed(1, "k", None); print("NO_RAISE_none")
            except ValueError: print("ok_none")
            try:
                Refused(4, "k"); print("NO_RAISE_code")
            except ValueError: print("ok_code")
            print(Failed(1, "k", []).committed == [])
        """)],
        capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(payload / "lib")},
    )
    assert "NO_RAISE" not in r.stdout, f"构造校验缺失：{r.stdout!r} / {r.stderr}"
    assert "True" in r.stdout, "committed=[] 应可区分于 None（零落地 ≠ 没这个概念）"


def test_outcome_producers_never_fall_through(payload: Path):
    """**产出 `Outcome` 的函数，每条路径都要 return** —— 走完隐式返回 `None` 即红。

    坏样本（真实发生，用户在真机上撞到）：`weights.download` 的**成功路径**
    没有 return，函数走完隐式返回 `None`，调用方 `out.rc` 当场 AttributeError。

    成因是一次「按硬编码行号切文件」的批量删除（`539c2f2` 删 `_rmtree_files`
    时用了 `lines[:143]`），把它后面的 `return Done(...)` 与 `Done` 的 import
    一起切掉了。**行号切割是最脆的批量改法**，这条守卫是它的对冲。

    为什么 150 项测试全绿却没抓到：测试环境没有真 `hf`，`download` 永远走
    失败路径 —— 成功路径**一次都没被执行过**。这是「测试够不到的分支」，
    与「空桩藏在空夹具后面」同族，只是方向相反：那次是夹具让分支走不到，
    这次是环境让分支走不到。静态守卫不依赖执行，正好补上这一面。
    """
    import ast as _ast

    def _returns_outcome(fn):
        """函数体里出现过 `return Done/Refused/Failed/Aggregate(...)` 就算产出 Outcome。"""
        for n in _ast.walk(fn):
            if (isinstance(n, _ast.Return) and isinstance(n.value, _ast.Call)
                    and getattr(n.value.func, "id", None)
                    in ("Done", "Refused", "Failed", "Aggregate")):
                return True
        return False

    def _terminates(stmts):
        """这串语句执行完之后，控制流**还能不能往下走**。

        判据是简化的可达性，不是「末条语句的类型」——
        后者把 `try` 一律判红，而 try 体与每个 handler 都 return 的写法
        （`wire.request`）是完全正当的，报的是假阳性。

        · `return` / `raise` ⇒ 走不下去
        · `if` ⇒ **两支都走不下去且有 else** 才算
        · `try` ⇒ try 体（或 else 子句）走不下去 **且** 每个 handler 都走不下去
        · `with` ⇒ 看它的体
        · 其它（循环、赋值、表达式）⇒ 保守地认为能走下去
        """
        if not stmts:
            return False
        last = stmts[-1]
        if isinstance(last, (_ast.Return, _ast.Raise)):
            return True
        if isinstance(last, _ast.If):
            return bool(last.orelse) and _terminates(last.body) and _terminates(last.orelse)
        if isinstance(last, _ast.Try):
            body_ok = _terminates(last.orelse or last.body)
            return body_ok and all(_terminates(h.body) for h in last.handlers)
        if isinstance(last, _ast.With):
            return _terminates(last.body)
        return False

    def _falls_through(fn):
        return not _terminates(fn.body)

    bad = []
    for f in sorted((payload / "lib").rglob("*.py")):
        for fn in [n for n in _ast.walk(_ast.parse(f.read_text()))
                   if isinstance(n, _ast.FunctionDef)]:
            if _returns_outcome(fn) and _falls_through(fn):
                bad.append(f"{f.relative_to(payload)}:{fn.lineno} {fn.name}() "
                           f"末条是 {type(fn.body[-1]).__name__}，会走完并隐式返回 None")
    assert sorted(bad) == [], (
        "这些函数产出 Outcome 却可能走完 —— 调用方拿到 None，"
        "下一句 `.rc` 就是 AttributeError：\n  " + "\n  ".join(sorted(bad)))
