"""格 1（`BCR-4-08`）· 格 2（`BCR-R-27`）· 格 3（`enter_execute` 两个口径）。

三格咬合在一起：`DE-T-018` 断言「stage_split 之后外部命令计数 == 0」，
而 `BCR-R-27` 把 venv 漂移排在执行段 —— 两条同真的唯一解是
`--dry-run` 在 `enter_execute()` 之前返回。
"""
import ast
import json
import re
import subprocess
import textwrap
from pathlib import Path

from cardinality import Cardinality


def _run_on_floor(payload: Path, sys_py: str, body: str):
    """在 **3.9.6 下限解释器**里跑一段驱动产品的代码，回传它打印的 JSON。

    `conftest` 的约束逐字：「不得 import 产品模块后在 venv 解释器里直接调用
    —— 那测的是另一个解释器」。本文件初版违反了它（sys.path.insert + 直接 import），
    在 3.14 上验证 3.9 产品的行为。
    """
    prog = textwrap.dedent("""
        import json, os, tempfile
        from mlx_server import backendarg, schema
        from mlx_server.ctx import Ctx
        from mlx_server.cmd.svc import EXECUTE_STEPS, VALIDATE_STEPS, Invocation, run

        # **真 ctx + 真注册表 + 真权重目录。**
        # 此前这里传 `ctx=None`，于是校验段的「读记录」「解析权重」两步
        # 无论实现成什么样都跑不到 —— 它们空了整整一版没人发现，
        # 症状是模型被起在默认端口 8100、别名目录里空无一物。
        # 这与本文件顶上那条「桩必须返回产品的真类型」是同一条纪律的下一层：
        # 空 ctx 让被测代码走不到真实分支，绿色只证明它没崩。
        _d = tempfile.mkdtemp()
        for sub in ("code", "data", "w"):
            os.makedirs(os.path.join(_d, sub), exist_ok=True)
        _snap = os.path.join(_d, "w", "hub", "models--org--qwen", "snapshots", "main")
        os.makedirs(_snap, exist_ok=True)
        open(os.path.join(_snap, "config.json"), "w").write("{}")
        with open(os.path.join(_d, "data", "models.jsonl"), "w") as _fh:
            _fh.write(json.dumps({"id": "qwen",
                                  "user": {"repo": "org/qwen", "type": "text"},
                                  "machine": {"port": 8177, "revision": "main"}}) + "\\n")
        ctx = Ctx(os.path.join(_d, "code"), os.path.join(_d, "data"),
                  os.path.join(_d, "w"), os.path.join(_d, "venv"),
                  8340, (8100, 8199), None)

        calls = []
        deps = {
            # 桩必须返回**产品的真类型**，不是 dict——否则是拿桩和桩对拍（审视 P0-1）
            "backendarg_spec": lambda t, layers=None: backendarg.spec(
                t, schema.effective(t, layers or {}), {}),
            "caps_drift": lambda: (calls.append("caps.drift"), False)[1],
        }
    """) + textwrap.dedent(body)
    r = subprocess.run(
        [sys_py, "-c", prog], capture_output=True, text=True, timeout=15, check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(payload / "lib")},
    )
    assert r.returncode == 0, f"下限解释器上执行失败：{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_gate1_dry_run_runs_zero_external_commands(payload: Path, sys_py: str):
    """`DE-T-018`：`up --dry-run` 后，stage_split 之后的外部命令计数 == 0。

    坏样本：把 `caps.drift()` 提到校验段 ⇒ dry-run 也跑 venv 探测 ⇒ 计数 ≠ 0。
    """
    got = _run_on_floor(payload, sys_py, """
        out = run(ctx, None, Invocation("up", "qwen", dry_run=True), deps)
        print(json.dumps({"rc": out.rc, "calls": calls}))
    """)
    assert got["rc"] == 0, f"dry-run 应成功，实得 {got}"
    assert got["calls"] == [], f"dry-run 跑了外部命令：{got['calls']}"


def test_gate1_dry_run_prints_spec_argv(payload: Path, sys_py: str):
    """`BCR-R-27`：`--dry-run` 必须打印 `spec().argv`（它是 dry-run 的全部价值）。"""
    got = _run_on_floor(payload, sys_py, """
        out = run(ctx, None, Invocation("up", "qwen", dry_run=True), deps)
        print(json.dumps({"payload": {"argv": list(out.payload["argv"])}}))
    """)
    assert "argv" in got["payload"], f"dry-run 没给出 argv：{got['payload']}"
    # **argv[0] 必须是能执行的东西**，不是包名。此前它是 `mlx_lm`，
    # 而 `runtime.spawn` 在启动前才换成解释器路径 —— argv 因此有两个产出点，
    # 且 `--dry-run` 看不见第二个：用户照打印出来的命令手工复现必然失败
    # （`mlx_lm` 是包名），而那正是 `--dry-run` 存在的全部理由（INV-02）。
    argv = got["payload"]["argv"]
    assert argv[0] != "mlx_lm", f"argv[0] 仍是包名，不可执行：{argv}"
    assert argv[1:3] == ["-m", "mlx_lm.server"], \
        f"后端模块名应来自产品 DEFAULTS，不是测试字面量：{argv}"


def test_gate1_real_run_does_reach_execute_stage(payload: Path, sys_py: str):
    """反向：**非** dry-run 必须真的进执行段并跑 caps.drift。

    没有这一条，「dry-run 计数 == 0」可以靠「永远不跑执行段」空真满足。
    """
    got = _run_on_floor(payload, sys_py, """
        # 用上面那个**有记录、有权重**的真 ctx —— 原先这里另造了一个空壳类，
        # 注册表是空的，校验段第一步就 Refused(6)，执行段照样进不去；
        # 断言「进了执行段」在那种 ctx 下永远只能靠桩的副作用蒙混。
        deps["caps_drift"] = lambda: (calls.append("caps.drift"), False)[1]
        run(ctx, None, Invocation("up", "qwen", dry_run=False), deps)
        print(json.dumps({"calls": calls}))
    """)
    assert got["calls"], "非 dry-run 也没进执行段 ⇒ 上一条断言是空真的"


def test_gate2_validate_order_is_pinned(payload: Path, sys_py: str):
    """`BCR-R-27` 的顺序写成数据，不散在代码里的 if。

    不钉死的后果：「端口冲突 + 容量 OVER」时 rc 在 6/7 之间随实现顺序漂移，
    而 `G-CLI-03` 两边都不红。
    """
    got = _run_on_floor(payload, sys_py, """
        print(json.dumps({"v": list(VALIDATE_STEPS), "e": list(EXECUTE_STEPS)}))
    """)
    v, e = got["v"], got["e"]
    assert v.index("ports.conflict_check") < v.index("backendarg.spec")
    assert "venv 漂移" in e, "venv 漂移必须在执行段（BCR-R-27）"
    assert "venv 漂移" not in v, "venv 漂移被提到了校验段 ⇒ dry-run 会跑 venv 探测"


def test_gate3_enter_execute_two_readings_agree(payload: Path):
    """格 3：`enter_execute` 的静态口径与运行期口径必须指同一个点。

    静态：AST 上 `run()` 里 `enter_execute()` 的调用位置。
    运行期：dry-run 的 return 必须在它**之前**（行号更小）。
    两者错位则守卫求值面与被测行为对不上。
    """
    src = (payload / "lib" / "mlx_server" / "cmd" / "svc.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "run")
    split = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == "enter_execute"]
    assert len(split) == 1, f"run() 里 enter_execute 的调用点必须恰好一个，实得 {split}"

    dry_returns = [n.lineno for n in ast.walk(fn)
                   if isinstance(n, ast.Return) and n.lineno < split[0]]
    assert dry_returns, "dry-run 的 return 不在 stage_split 之前 ⇒ 执行段会被跑到"


# 设计 `02-architecture.md:1992` 的 `--dry-run` 行**点名了四个命令**
# （`所有读取点` 列）。`cli.tsv` 把它登记成 `owner_verb == *`（全部动词），
# 两者登记面不同 —— 那条差异单独记在 KNOWN-ISSUES，本守卫按设计的四个查。
_DRY_RUN_CMDS = {
    "model install": ("model_acquire.py", ("store.commit",)),
    "up":            ("svc.py", ("enter_execute",)),
    "config import": ("config.py", ("store.commit", "atomic.replace")),
    "model gc":      ("model_reclaim.py", ("vault.trash",)),
}


def test_de_t_035_dry_run_returns_before_any_resource_creation(payload: Path):
    """`DE-T-035`：每个支持 `--dry-run` 的命令，都必须在**创建任何资源之前**返回。

    判据逐字（`04-interfaces.md:3386`）：「`install --dry-run`：
    `commit_user_placeholder()` **未被调用**（计数 0）∧ 全轮沙箱 diff == ∅」，
    坏样本逐字：「dry-run 只跳过下载不跳过提交点① ⇒ 注册表里多出一条幽灵记录」。

    **这条判据此前在 tests/ 下零实现**，而已有的 `test_gate3` 写死了 `svc.py`
    一个文件 —— 它结构上不可能抓到 `model install`。于是设计点了名的坏样本
    就是实际行为：2026-09-02 实测 `model install --dry-run` 在 `models.jsonl`
    里留下一条记录后返回 rc 3。**判据、实现、守卫三者同时缺席，所以全绿。**

    守卫按 `verbs.tsv` 之外的一张显式表遍历，不写死单个文件 ——
    上一版正是死在「求值面只覆盖一个文件」上。
    """
    bad = []
    for cmd, (fname, creators) in sorted(_DRY_RUN_CMDS.items()):
        f = payload / "lib" / "mlx_server" / "cmd" / fname
        if not f.exists():
            bad.append(f"{cmd}: 找不到 {fname} —— 守卫的提取面失效了")
            continue
        tree = ast.parse(f.read_text())
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "run"), None)
        if fn is None:
            bad.append(f"{cmd}: {fname} 里没有 run()")
            continue

        # **只看该动词自己的分支**。`run()` 常常处理同模块的多个动词
        # （`model_reclaim.run` 管 rm/gc/trash empty/repair 四个），
        # 扫整个函数会把 `trash empty` 的 `vault.purge` 算到 `gc` 头上 ——
        # 那是本守卫第一版的假阳性。判据要跟被判的东西一样窄。
        verb = cmd.split()[-1]
        scope = fn
        for n in ast.walk(fn):
            if not isinstance(n, ast.If):
                continue
            # `ast.unparse` 用**单引号**输出字符串常量 —— 按双引号去匹配
            # 永远匹配不上，而失配是静默的（scope 保持整个 run()）。
            # 归一化两种引号，别让守卫死在自己的字符串形态上。
            t = (ast.unparse(n.test) if hasattr(ast, "unparse") else "").replace('"', "'")
            if f"inv.verb == '{verb}'" in t:
                scope = n
                break

        # 第一个「创建资源」的调用点
        first = None
        for n in ast.walk(scope):
            if not isinstance(n, ast.Call):
                continue
            name = ast.unparse(n.func) if hasattr(ast, "unparse") else ""
            if any(c in name for c in creators):
                first = n.lineno if first is None else min(first, n.lineno)
        if first is None:
            continue          # 该命令在这条路径上不创建资源，无从违反

        # 读了 inv.dry_run 的分支里、位于它之前的 return
        guarded = []
        for n in ast.walk(scope):
            if not isinstance(n, ast.If):
                continue
            test = ast.unparse(n.test) if hasattr(ast, "unparse") else ""
            if "dry_run" not in test:
                continue
            guarded += [r.lineno for r in ast.walk(n)
                        if isinstance(r, ast.Return) and r.lineno < first]
        if not guarded:
            bad.append(f"{cmd}({fname}): 创建资源在 :{first}，"
                       f"但它之前没有任何读 inv.dry_run 的 return "
                       f"⇒ --dry-run 会真的动盘")
    Cardinality.check("dry-run 会动盘的命令", sorted(bad), rel="empty")


def test_dry_run_ownership_equals_implementation(payload: Path):
    """`cli.tsv` 里 `--dry-run` 的归属面 **==** 真正读了 `inv.dry_run` 的动词集合。

    **相等断言，不是子集。** 两个方向的失衡各有一种事故：

    · 登记 ⊋ 实现 —— flag 被接受但没人读它，命令**照常执行**。
      2026-09-02 实测：`--dry-run` 登记为 `owner_verb == *`（全部动词），
      而 18 个 cmd 模块只有 5 个 opt-in。于是
      `mlx down api --dry-run` 真杀进程、`mlx model rm x --dry-run` 真删登记
      （不进回收站、不可恢复）、`mlx model register id ... --dry-run` 真覆盖
      已有记录并自己打印「旧值已不可恢复」。**用来安全预览的 flag，
      在最危险的几条命令上执行了它声称不会做的事。**

    · 登记 ⊊ 实现 —— 代码里写了预览分支，用户却敲不进来（rc 2），
      那段代码是死的，且没人会发现。

    判据取自实现本身（AST 找 `inv.dry_run` 的读取点 → 反查该模块处理的动词），
    不是另抄一份清单 —— 抄的那份会跟着一起腐烂。
    """
    lib = payload / "lib" / "mlx_server"

    # ① 登记面
    row = next((ln.split("\t") for ln in (payload / "share" / "cli.tsv").read_text()
                .splitlines() if ln.startswith("--dry-run\t")), None)
    assert row, "cli.tsv 里没有 --dry-run 行 —— 守卫的提取面失效了"
    assert row[1] != "*", ("--dry-run 登记成 owner_verb=* ⇒ 全部动词都接受它，"
                           "而多数动词根本不读它，flag 被静默忽略后命令照常执行")
    declared = {v.strip() for v in row[1].split(",")}

    # ② 实现面。**粒度是动词分支，不是模块** —— `config.py` 管 8 个子命令，
    # 而它的 dry-run 门只在 `import` 分支里；按模块算会把另外 7 个也算成
    # 「实现了」，那正是本守卫要抓的那类失衡。
    def _verb_scope(tree, node):
        """`node` 落在哪个 `inv.verb == "x"` 分支里；不在任何分支内则 None
        （= 对该模块处理的全部动词生效）。"""
        parent = {}
        for n in ast.walk(tree):
            for ch in ast.iter_child_nodes(n):
                parent[ch] = n
        cur = node
        while cur in parent:
            cur = parent[cur]
            if isinstance(cur, ast.If):
                t = (ast.unparse(cur.test) if hasattr(ast, "unparse") else "")
                t = t.replace('"', "'")
                m = re.findall(r"inv\.verb == '([^']+)'", t)
                if m:
                    return set(m)
                # `inv.verb in ("a", "b")` 也是一种分支写法
                m2 = re.findall(r"inv\.verb in \(([^)]*)\)", t)
                if m2:
                    return set(re.findall(r"'([^']+)'", m2[0]))
        return None

    def _reachable_verbs(tree, gate_lineno):
        """能走到 `gate_lineno` 的动词集合；判不出来就 None（= 不设限）。

        判据只认最简单也最常见的一种形态：顶层 `if inv.verb == "x": ... return`
        且**无 else**。这种分支一旦命中就走不到后面 —— 那正是 `svc.py` 里
        `status` / `down` 的写法。判不出来时返回 None 而不是猜，
        猜错的方向是「漏报」，比误报更难发现，所以宁可让守卫更严。
        """
        early = set()
        for n in ast.walk(tree):
            if not (isinstance(n, ast.If) and n.lineno < gate_lineno and not n.orelse):
                continue
            t = (ast.unparse(n.test) if hasattr(ast, "unparse") else "").replace('"', "'")
            vs = set(re.findall(r"inv\.verb == '([^']+)'", t))
            if not vs:
                continue
            # body 必须**必定** return（最后一句是 Return）
            if n.body and isinstance(n.body[-1], ast.Return):
                early |= vs
        return None if not early else ("ALL_MINUS", early)

    reading = {}                      # 模块 stem → 该模块里被 dry-run 门覆盖的 verb，或 None
    for f in sorted((lib / "cmd").rglob("*.py")):
        src = f.read_text()
        if "inv.dry_run" not in src:
            continue
        tree = ast.parse(src)
        for n in ast.walk(tree):
            # 只算真正的分支判定，不算把它转手传下去（svc.py 有两处转发）
            if not (isinstance(n, ast.If)
                    and "inv.dry_run" in (ast.unparse(n.test)
                                          if hasattr(ast, "unparse") else "")):
                continue
            scope = _verb_scope(tree, n)
            if scope is None:
                # 门不在任何 verb 分支里 ⇒ 覆盖「能走到它那一行」的动词。
                # **不是全部动词** —— 在它之前无条件 return 的分支够不到它。
                # `svc.py` 就是这样：`status`(:335) 与 `down`(:375) 各自
                # 直接 return，而门在 :398。把它们算成「实现了」，
                # 守卫就会要求登记两个实际上收不到预览的动词。
                scope = _reachable_verbs(tree, n.lineno)
            prev = reading.get(f.stem, set())
            # **多个门取并集**（一个模块可以有好几道，各覆盖一段）。
            # 此前这里是覆盖：`svc.py` 的第二道门（:398，够不到 down）
            # 盖掉了第一道（:375 之前，够得到 down），于是 down 被判成
            # 「登记了但没实现」—— 而它明明刚实现。
            def _wider(a, b):
                if a is None or b is None:
                    return None                       # None = 全部，最宽
                am = isinstance(a, tuple); bm = isinstance(b, tuple)
                if am and bm:
                    return ("ALL_MINUS", a[1] & b[1])   # 各自减的越少越宽 ⇒ 交集
                if am:
                    return a                          # ALL_MINUS 比具体集合宽
                if bm:
                    return b
                return a | b
            reading[f.stem] = _wider(prev, scope)

    # ③ 模块 → 它处理的动词（从 verbs.tsv 的 impl 列反查，不另抄清单）
    impl_to_verbs = {}
    for ln in (payload / "share" / "verbs.tsv").read_text().splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        c = ln.split("\t")
        if len(c) < 3 or not c[2]:
            continue
        # `cmd.model.acquire` → `model_acquire`；`cmd.svc` → `svc`
        mod = c[2].replace("cmd.", "").replace(".", "_")
        full = f"{c[1]} {c[0]}".strip() if c[1] else c[0]
        impl_to_verbs.setdefault(mod, set()).add(full)

    # `impl` 列与文件名并非逐字相等：`cmd.self` 的实现是 `self_deploy.py`。
    # 精确匹配不上就退到**前缀**匹配 —— 但要求前缀后面接 `_`，
    # 免得 `caps` 撞上 `caps_something` 这类将来可能出现的邻居。
    implemented = set()
    for mod, scoped in reading.items():
        hit = impl_to_verbs.get(mod)
        if hit is None:
            hit = set()
            for k, v in impl_to_verbs.items():
                if mod == k or mod.startswith(k + "_"):
                    hit |= v
        # 门在某个 verb 分支里 ⇒ 只有那些动词算实现了
        if isinstance(scoped, tuple) and scoped[0] == "ALL_MINUS":
            hit = {v for v in hit if v.split()[-1] not in scoped[1]}
        elif scoped is not None:
            hit = {v for v in hit if v.split()[-1] in scoped}
        implemented |= hit

    Cardinality.check("登记了 --dry-run 但没实现的动词",
                      sorted(declared - implemented), rel="empty")
    Cardinality.check("实现了 --dry-run 但没登记的动词",
                      sorted(implemented - declared), rel="empty")
