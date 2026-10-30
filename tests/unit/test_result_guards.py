"""`L0·result-01` / `-02` / `-03` —— 三条 AST 守卫，第一次被真实代码求值（格 6）。

求值面是产品源码的 AST，不是 markdown。这是 47k 行设计里写了三轮的守卫，
本片第一次给它们一个真正的求值对象。
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402

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


CREATORS = {"mkdir", "makedirs", "commit", "spawn", "spawn_supervised",
            "materialize", "lock", "open",
            # `run/<service>.json` 的**唯一写者**（`supervisor.py:208` 逐字）。
            # 漏了它，「起服务时写完运行记录之后再 Refused」这一整类违规不可见。
            "write_record"}

# **限定名**匹配的创建点。末段单独拿出来太常见 —— `str.replace` / `bytes.replace`
# 遍地都是，把 `replace` 放进 `CREATORS` 会把一大片无关调用判红（守卫的提取面
# 比不变式宽 ⇒ 假阳性）。所以这一类按 `模块.函数` 的全名匹配。
QUALIFIED_CREATORS = {"atomic.replace", "_atomic.replace",
                      # **销毁也是副作用**（1.2.0）：只数「创建」时，`restart` 先 `supervisor.down`
                      # 再 Refused 的 7 处违规一处都看不见 —— Refused 的承诺是「什么都没动」，
                      # 停掉一个在跑的服务、删掉文件同样违背它
                      "supervisor.down", "vault.trash", "vault.purge", "os.unlink", "os.remove",
                      "os.rename", "shutil.rmtree", "os.kill", "os.killpg"}


def _dotted(func):
    """调用目标的限定名：`a.b.c(...)` → `"a.b.c"`，`f(...)` → `"f"`，其余 → `""`。"""
    if isinstance(func, ast.Attribute):
        base = _dotted(func.value)
        return (base + "." + func.attr) if base else func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _creates(node):
    """这次调用是否**创建**资源。

    〔收尾评审 Critical 10〕创建者词汇表此前漏掉了产品**自己指定的两个写入原语**：
    `atomic.replace`（`atomic.py:1`：唯一的原子替换实现点）与
    `supervisor.write_record`（`supervisor.py:208`：`run/<service>.json` 的唯一写者）。
    把这两个注入到 `route.decide()` 里某个 `Refused` 之前，19 条守卫用例全绿 ——
    **代码越忠实地把写入收敛到单一写点，这条守卫就越瞎**：它认得的是
    `open`/`commit` 这些泛化名字，而真正落盘的调用早就改叫别的了。

    `open` 要看模式：只读的 `open(p)` / `open(p, encoding=...)` 什么都不建，
    把它算进来会让「先读文件再校验、校验失败 Refused」这种完全正当的
    写法被判红 —— 守卫的提取面比不变式宽，报的是假阳性。
    判据：第二个位置参或 `mode=` 里含 `w`/`a`/`x`/`+`。
    """
    dotted = _dotted(node.func)
    if dotted in QUALIFIED_CREATORS:
        return True
    nm = dotted.rsplit(".", 1)[-1] if dotted else None
    if nm != "open":
        return nm in CREATORS
    mode = None
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        mode = node.args[1].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return bool(mode) and any(c in str(mode) for c in "wax+")


_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_TRY = tuple(t for t in (getattr(ast, "Try", None), getattr(ast, "TryStar", None)) if t)


def _creators_in(node):
    """`node` 内部的创建调用 [(行, 名)]。不进入内嵌函数 / lambda —— 定义不等于执行。"""
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Call) and _creates(n):
            out.append((n.lineno, getattr(n.func, "id", None) or getattr(n.func, "attr", None)))
        stack.extend(c for c in ast.iter_child_nodes(n) if not isinstance(c, _FUNC))
    return out


def _creators_before_next(stmts):
    """执行完这串语句、**控制流继续往下走**时，可能已发生的创建调用。

    `if` 的某个分支以 `return` / `raise` 结尾 ⇒ 走进该分支就不会到达后续语句，
    其内创建不计；否则递归看分支（`elif` 链的 orelse 末条是 `If`，要继续往里看）。
    其它语句（with / for / try / 表达式 …）内部的创建一律计入。
    """
    out = []
    for st in stmts:
        if isinstance(st, ast.If):
            out += _creators_in(st.test)
            for branch in (st.body, st.orelse):
                if branch and isinstance(branch[-1], (ast.Return, ast.Raise)):
                    continue
                out += _creators_before_next(branch)
        elif not isinstance(st, _FUNC):
            out += _creators_in(st)
    return out


def _creators_reaching(call, parent):
    """执行到 `call` 之前、同一函数内可能已发生的创建调用（「可达前序语句」）。

    自 `call` 逐层上溯到最近的函数边界，每层收集：
    · 所在语句列表里、包含 `call` 那条语句**之前**的兄弟语句；
    · 外层复合语句先于该体执行的部分 —— `if`/`while` 条件、`for` 迭代、`with` 项；
    · `except` 体：整个 `try` 体都计入（异常可能在创建之后抛出）；
    · `try` 的 `else` 体 / 循环的 `else` 体：主体已正常走完；
    · `finally` 体：`try` 体、各 `except`、`else` 全部计入（`return` 也会途经 finally）。
    同一条语句内先于 `Refused` 求值的创建（如条件表达式）不处理。
    """
    out, child = [], call
    while child in parent:
        node = parent[child]
        for field in ("body", "orelse", "finalbody"):
            seq = getattr(node, field, None)
            if not isinstance(seq, list) or child not in seq:
                continue
            out += _creators_before_next(seq[:seq.index(child)])
            if isinstance(node, (ast.If, ast.While)):
                out += _creators_in(node.test)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                out += _creators_in(node.iter)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    out += _creators_in(item.context_expr)
            if field == "orelse" and isinstance(node, (ast.For, ast.AsyncFor, ast.While) + _TRY):
                out += _creators_before_next(node.body)
            if field == "finalbody":
                for part in [node.body, node.orelse] + [h.body for h in node.handlers]:
                    for st in part:
                        out += _creators_in(st)
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                out += _creators_in(node.type)
            for st in parent[node].body:
                out += _creators_in(st)
        if isinstance(node, _FUNC):
            return out
        child = node
    return None                         # 不在函数内（模块级），不在本守卫的求值面上


def _violations(tree):
    """`L0·result-02` 的判定：返回 [(Refused 行, 创建调用名, 创建调用行)]。

    按**执行顺序**判：`Refused(...)` 的可达前序语句里出现创建调用即违规。
    旧判定边 `ast.walk`（广度优先）边收集，结论取决于 AST 深度 ——
    深处的 `commit` 之后顶层 `Refused` 漏报；`except` 里的 `Refused` 误报。
    """
    parent = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            parent[c] = n
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) != "Refused":
            continue
        reaching = _creators_reaching(node, parent)
        if reaching:
            line, nm = max(reaching)
            out.append((node.lineno, nm, line))
    return sorted(out)


# 语义样本：(id, 源码, 是否应报)。前七条逐字取自 P2-pre A 简报。
_R02_SAMPLES = [
    ("stop-service-then-refused",
     'def f():\n    supervisor.down(c, t)\n    return Refused(6, "k")\n', True),
    ("unlink-then-refused",
     'def f():\n    os.unlink(p)\n    return Refused(6, "k")\n', True),
    ("probe-deep-commit-then-top-refused",
     'def f():\n    if a:\n        with b:\n            store.commit(x)\n'
     '    return Refused(6, "k")\n', True),
    ("early-return-branch-commit-ignored",
     'def f(v):\n    if v == "u":\n        store.commit(x)\n        return Done(1)\n'
     '    return Refused(6, "k")\n', False),
    ("refused-in-except-try-body-clean",
     'def f():\n    try:\n        val = coerce(v)\n    except E as e:\n'
     '        return Refused(6, "k")\n    store.commit(val)\n', False),
    ("refused-in-except-after-try-body-commit",
     'def f():\n    try:\n        store.commit(x)\n    except E:\n'
     '        return Refused(6, "k")\n', True),
    ("order-ok-refused-before-commit",
     'def f():\n    if bad:\n        return Refused(6, "k")\n    store.commit(x)\n', False),
    ("read-mode-open-before",
     'def f():\n    open(p)\n    return Refused(6, "k")\n', False),
    ("write-mode-open-before",
     'def f():\n    open(p, "w")\n    return Refused(6, "k")\n', True),
    # 为之改写过 `cmd/config.py::_coerce` 的假阳性形态：
    # 早返回分支里的 commit 行号更前、AST 更浅，旧判定误报 except 里的 Refused。
    ("early-return-commit-then-refused-in-except",
     'def f(v):\n    if v == "u":\n        store.commit(x)\n        return Done(1)\n'
     '    try:\n        val = coerce(v)\n    except E:\n        return Refused(6, "k")\n'
     '    return Done(0)\n', False),
    # elif 链：orelse 的末条是 If 而不是 return，要递归看它的分支才不误报。
    ("elif-chain-all-early-return",
     'def f(v):\n    if v == "a":\n        store.commit(x)\n        return Done(1)\n'
     '    elif v == "b":\n        store.commit(y)\n        return Done(2)\n'
     '    return Refused(6, "k")\n', False),
    ("elif-chain-one-falls-through",
     'def f(v):\n    if v == "a":\n        store.commit(x)\n        return Done(1)\n'
     '    elif v == "b":\n        store.commit(y)\n'
     '    return Refused(6, "k")\n', True),
    # 外层复合语句的头部（with 项、if 条件、for 迭代）先于体执行。
    ("refused-inside-with-lock",
     'def f():\n    with store.lock(p):\n        return Refused(6, "k")\n', True),
    ("refused-in-else-of-try-after-commit",
     'def f():\n    try:\n        store.commit(x)\n    except E:\n        pass\n'
     '    else:\n        return Refused(6, "k")\n', True),
    ("refused-in-finally-after-early-return-commit",
     'def f():\n    try:\n        if a:\n            store.commit(x)\n            return Done(1)\n'
     '    finally:\n        return Refused(6, "k")\n', True),
    # 未被调用的内嵌函数体不在执行路径上。
    ("nested-def-body-not-executed",
     'def f():\n    def g():\n        store.commit(x)\n    return Refused(6, "k")\n', False),
]


@pytest.mark.parametrize("name,src,expect", _R02_SAMPLES, ids=[s[0] for s in _R02_SAMPLES])
def test_result02_violations_semantics(name, src, expect):
    """`_violations` 的判定按**执行顺序**，不按 AST 深度或行号。"""
    got = _violations(ast.parse(src))
    assert bool(got) is expect, f"{name}：期望{'报' if expect else '不报'}，实得 {got}"


def _refused_call_sites(tree):
    """本文件里所有 `Refused(...)` 调用点的行号 —— `L0·result-02` 的被评估对象集合。

    与 `_violations` 分开算：`_violations` 只返回**违规**的那些，
    「零个违规」与「零个被检查的 `Refused` 调用」在它的返回值上不可区分，
    而两者含义相反 —— 前者是「全部合规」，后者是这条断言根本没求值到任何东西。
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == "Refused":
            out.append(node.lineno)
    return out


def test_result02_refused_precondition_is_zero_resource(payload: Path):
    """`L0·result-02`〔按裁决改写〕：`Refused` 的唯一前提是**零资源已创建**。

    **不是**「必须在 stage_split 之前构造」—— 原写法与 `BCR-R-01` 直接互斥：
    `supervisor.up` 必然在 stage_split 之后，也返回 `Refused(3)`/`Refused(9)`，
    原写法在四组共同约定的正确实现上**恒红**，一旦被放宽，
    `Refused` 的真前提（零资源）就再也没有守卫。

    可求值的形态：构造 `Refused` 的函数体内，不得在该构造点**之前**出现资源创建调用。

    **非空下限**（参照 `G-CLI-01` 的写法）：`bad` 没有下限 —— 若 `_product_trees`
    一个文件都没产出、或产出的文件里一个 `Refused` 调用都没有，`assert not bad`
    照样通过，但那是空真，什么也没证明。`Cardinality.require_nonempty` 断言
    被评估的 `Refused` 调用集合非空，把这个空真风险变得可见。
    """
    bad = []
    evaluated = []
    for f, tree in _product_trees(payload):
        evaluated += [f"{f.name}:{ln}" for ln in _refused_call_sites(tree)]
        for line, nm, cline in _violations(tree):
            bad.append(f"{f.name}:{line} 在 {nm}@{cline} 之后构造 Refused")
    Cardinality.require_nonempty(
        "L0·result-02·被评估的 Refused 调用", evaluated,
        "_product_trees 没产出文件，或产出的文件里一个 Refused 调用都没有 ⇒ 该断言空真通过",
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
