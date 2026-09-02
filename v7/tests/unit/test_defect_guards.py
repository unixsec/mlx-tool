"""审视报告 4 条 P0/P1 缺陷各自的守卫。

修复没有守卫就会退回去 —— 这四条正是「修了但没人拦着再改回来」的典型。
"""
import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402


def _floor(payload, sys_py, body):
    r = subprocess.run(
        [sys_py, "-c", textwrap.dedent(body)], capture_output=True, text=True,
        timeout=15, check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(payload / "lib")},
    )
    assert r.returncode == 0, f"下限解释器执行失败：{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


# ── 缺陷 1：DEAD / UNKNOWN 必须是两个可判定的态 ──────────────────

def test_d1_unknown_kind_is_closed_set(payload: Path, sys_py: str):
    """`Unknown` 的 kind 取值 == 闭集（相等断言），且自由文本被拒。

    坏样本：`Unknown("dead")` 小写自由文本 —— T-26 §二② 的致命形态是
    「只看 rc != 0 就判 DEAD」⇒ ps 被改 PATH/权限不足都被读成「进程不存在」
    ⇒ 清掉一个活着的 28GB 后端的锁。
    """
    got = _floor(payload, sys_py, """
        import json
        from mlx_server.result import UNKNOWN_KINDS, Unknown
        rejected = []
        for bad in ("dead", "ps_failed", "", "Dead"):
            try:
                Unknown(bad); rejected.append((bad, False))
            except ValueError:
                rejected.append((bad, True))
        print(json.dumps({
            "kinds": sorted(UNKNOWN_KINDS),
            "rejected": rejected,
            "dead_is_dead": Unknown("DEAD").is_dead(),
            "psfail_is_dead": Unknown("PS_FAILED").is_dead(),
        }))
    """)
    Cardinality.check("Unknown.kinds", got["kinds"],
                      ["DEAD", "NOT_COLLECTED", "NO_LISTENER", "PARSE_FAILED",
                       "PS_FAILED", "TIMEOUT", "TOOL_UNAVAILABLE"], rel="equal")
    assert all(ok for _, ok in got["rejected"]), f"自由文本没被拒：{got['rejected']}"
    assert got["dead_is_dead"] and not got["psfail_is_dead"], \
        "DEAD 与 PS_FAILED 的 is_dead() 必须相反 —— 它们的处置相反"


def test_d1_proc_maps_three_states(payload: Path, sys_py: str):
    """`started_at_raw` 的三态映射与 T-26 逐条对上（用真 ps 桩造三种 rc）。"""
    got = _floor(payload, sys_py, """
        import json, os, stat, tempfile
        from mlx_server.proc import started_at_raw
        d = tempfile.mkdtemp()
        def stub(name, body):
            p = os.path.join(d, name)
            open(p, "w").write("#!/bin/bash\\n" + body)
            os.chmod(p, 0o755)
            return p
        ok   = stub("ok",   'echo "Sat Aug 30 01:00:00 2026"; exit 0')
        dead = stub("dead", 'exit 1')                              # T-26.5 stderr 空
        fail = stub("fail", 'echo "ps: no perm" >&2; exit 1')      # T-26.6 stderr 非空
        print(json.dumps({
            "ok":   [started_at_raw(1, ok).is_unknown(), None],
            "dead": [True, started_at_raw(1, dead).kind],
            "fail": [True, started_at_raw(1, fail).kind],
        }))
    """)
    assert got["ok"][0] is False, "rc=0 应返回 Ok"
    assert got["dead"][1] == "DEAD", f"stderr 空的失败应判 DEAD，实得 {got['dead'][1]}"
    assert got["fail"][1] == "PS_FAILED", f"stderr 非空应判 PS_FAILED，实得 {got['fail'][1]}"


# ── 缺陷 2：Ctx 的冻结要有守卫 ────────────────────────────────

def test_d2_ctx_frozen_rejects_set_and_del(payload: Path, sys_py: str, tmp_path: Path):
    """对每个 `__slots__` 成员分别断言赋值抛、删除抛。"""
    share = tmp_path / "share"; share.mkdir()
    share.joinpath("constants.env").write_text(
        "MLX_ROOT_CODE=/tmp/c\nMLX_ROOT_DATA=/tmp/d\nMLX_ROOT_WEIGHTS=/tmp/w\n"
        "MLX_GATEWAY_PORT=8080\nMLX_PORT_RANGE=8100-8199\n")
    got = _floor(payload, sys_py, f"""
        import json
        from mlx_server import ctx
        c = ctx.build({str(share)!r}, environ={{}})
        res = {{}}
        for f in ctx.Ctx.__slots__:
            try: setattr(c, f, "x"); res[f + ".set"] = False
            except AttributeError: res[f + ".set"] = True
            try: delattr(c, f); res[f + ".del"] = False
            except AttributeError: res[f + ".del"] = True
        print(json.dumps({{"res": res, "slots": list(ctx.Ctx.__slots__)}}))
    """)
    bad = [k for k, ok in got["res"].items() if not ok]
    assert not bad, f"这些成员可被改/删：{bad}"
    Cardinality.check("Ctx.frozen_checks", list(got["res"]), rel="subset",
                      right=list(got["res"]))
    assert "_frozen" not in got["slots"], "_frozen 槽位从未被读取，应删掉而不是留着装饰"


def test_d2_object_setattr_only_in_ctx_init(payload: Path):
    """`object.__setattr__` 是语言层不可堵的绕过 —— 用 AST 守卫限定它的出现点。"""
    sites = []
    for f in sorted((payload / "lib").rglob("*.py")):
        tree = ast.parse(f.read_text())
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "__setattr__"):
                    sites.append(f"{f.name}:{fn.name}")
    Cardinality.require_nonempty("object.__setattr__ 出现点", sites,
                                 "产品里一处都没有 ⇒ 这条守卫空真")
    assert set(sites) == {"ctx.py:__init__"}, \
        f"object.__setattr__ 只允许出现在 Ctx.__init__，实得 {sorted(set(sites))}"


# ── 缺陷 3：退出码闭集不得是手打副本 ──────────────────────────

def test_d3_exit_code_sets_come_from_product(payload: Path, sys_py: str):
    """码集从**产品** import，不在测试里手打；且 0 不在任何一个集合里。

    坏样本：`_REFUSED_CODES` 加个 0 ⇒ `Refused(0).rc == 0` ⇒ 一次拒绝对外报成功。
    上一版测试用的是自己手打的 `REFUSED_OK`，与产品的 frozenset 从不对账。
    """
    got = _floor(payload, sys_py, """
        import json
        from mlx_server.result import _FAILED_CODES, _REFUSED_CODES, Done, Failed, Refused
        zero = []
        for ctor, codes in ((Refused, _REFUSED_CODES), (Failed, _FAILED_CODES)):
            try:
                ctor(0, "k", []) if ctor is Failed else ctor(0, "k")
                zero.append([ctor.__name__, False])
            except ValueError:
                zero.append([ctor.__name__, True])
        print(json.dumps({
            "refused": sorted(_REFUSED_CODES), "failed": sorted(_FAILED_CODES),
            "zero_rejected": zero, "done_rc": Done(None).rc,
        }))
    """)
    Cardinality.check("Refused.codes", got["refused"], [2, 3, 5, 6, 7, 8, 9], rel="equal")
    Cardinality.check("Failed.codes", got["failed"], [1, 3, 4, 10], rel="equal")
    assert all(ok for _, ok in got["zero_rejected"]), \
        f"0 被某个码集接受了 ⇒ 失败会对外报成功：{got['zero_rejected']}"
    assert got["done_rc"] == 0, "Done 必须是唯一产生 rc 0 的构造子"


# ── 缺陷 4：VALIDATE_STEPS 必须真的驱动执行 ────────────────────

def test_d4_validators_match_steps_exactly(payload: Path, sys_py: str):
    """`set(VALIDATORS) == set(VALIDATE_STEPS)`（**相等**，不是子集）。

    坏样本：把 `VALIDATE_STEPS` 删成两项 —— 上一版能全绿，因为元组只是文档。
    现在 `run()` 按它 dispatch，删项即 KeyError 或漏步。
    """
    got = _floor(payload, sys_py, """
        import json
        from mlx_server.cmd.svc import VALIDATE_STEPS, VALIDATORS
        print(json.dumps({"steps": list(VALIDATE_STEPS), "validators": sorted(VALIDATORS)}))
    """)
    Cardinality.check("VALIDATORS↔VALIDATE_STEPS", got["validators"],
                      sorted(got["steps"]), rel="equal")
    assert got["steps"].index("ports.conflict_check") < got["steps"].index("backendarg.spec")


def test_d4_ports_conflict_wins_over_spec(payload: Path, sys_py: str):
    """`BCR-R-27` 的实际后果：端口冲突必须在 spec 之前被判出 ⇒ rc 7 而非 5。

    这是 `DE-T-016` 的 L1 版。不钉死顺序时 rc 会在 6/7 之间随实现漂移。
    """
    got = _floor(payload, sys_py, """
        import json, os, tempfile
        from mlx_server.ctx import Ctx
        from mlx_server.cmd.svc import Invocation, run
        from mlx_server.result import Refused
        # **真 ctx**：校验段步 2/3 要读注册表与权重，传 None 时它们一崩，
        # 这条断言就永远到不了它要测的第 4/5 步。
        _d = tempfile.mkdtemp()
        os.makedirs(os.path.join(_d, "data"), exist_ok=True)
        _snap = os.path.join(_d, "w", "hub", "models--org--qwen", "snapshots", "main")
        os.makedirs(_snap, exist_ok=True)
        with open(os.path.join(_d, "data", "models.jsonl"), "w") as _fh:
            _fh.write(json.dumps({"id": "qwen",
                                  "user": {"repo": "org/qwen", "type": "text"},
                                  "machine": {"port": 8177, "revision": "main"}}) + "\\n")
        ctx = Ctx(_d, os.path.join(_d, "data"), os.path.join(_d, "w"),
                  os.path.join(_d, "venv"), 8340, (8100, 8199), None)
        calls = []
        deps = {
            "backendarg_spec": lambda t, layers=None: calls.append("spec") or None,
            # 桩返回**产品的真类型**（`conflict_check` 产的是带 detail 的
            # `Refused(7)`，不是 bool）。返回裸 True 时，消费点一旦改成读
            # `.rc` 就静静失效 —— 而那正是「端口冲突：」后面空着的成因。
            "ports_conflict": lambda t: Refused(7, "ports.conflict",
                                                detail="a 与 api 都用 8390"),
            "caps_drift": lambda: False,
        }
        out = run(ctx, None, Invocation("up", "qwen"), deps)   # 端口冲突在校验段，到不了执行段
        print(json.dumps({"rc": out.rc, "spec_called": calls}))
    """)
    assert got["rc"] == 7, f"端口冲突应 rc 7，实得 {got['rc']}"
    assert got["spec_called"] == [], "spec 在端口冲突之后仍被调用 ⇒ 顺序没被钉死"


# ── D 层签名一致性 ──────────────────────────────────────────────

def test_d_layer_run_signatures_identical(payload: Path):
    """所有 D 层 `run` 的形参表**逐字相同**：`(ctx, stack, inv, deps)`。

    坏样本（本次真实发生）：`model_query.run` 只收三个参数 ⇒
    `entry` 统一按四参调用时 `TypeError`，而 `mlx ls` 是**唯一**走它的路径，
    单元测试直接调那个函数时用的是三参，抓不到。
    """
    import ast
    sigs = {}
    d = payload / "lib" / "mlx_server" / "cmd"
    for f in sorted(d.glob("*.py")):
        if f.name == "__init__.py":
            continue
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.FunctionDef) and n.name == "run":
                sigs[f.name] = [a.arg for a in n.args.args]
    Cardinality.require_nonempty("D层 run", list(sigs), "一个 D 层模块都没有 ⇒ 空真")
    wrong = {k: v for k, v in sigs.items() if v != ["ctx", "stack", "inv", "deps"]}
    assert not wrong, f"这些 D 层模块的 run 签名不是 (ctx, stack, inv, deps)：{wrong}"


def test_verbs_ledger_matches_dispatchable_set(payload: Path):
    """**动词台账 == 代码实际能分派的集合**（PROD-08 的落点）。

    这是「两份真实命令集合」的守卫。v6 有 dispatcher 分支、libexec 文件名、
    help 文本、补全脚本四份各自维护的集合，两条守卫会对同一动词给出相反结论。
    本项目把动词面收敛成 `share/verbs.tsv` 一张表，解析 / 分派 / help 都读它 ——
    但**「都读它」本身需要一个求值器**，否则代码里再长出第二份表没人会发现。

    错误实现能否通过：
      · 表里加一行而 `MODULES` 没有对应 impl ⇒ 该动词永远 route 不到 ⇒ 红
      · 代码里留一个没有任何台账行引用的模块 ⇒ 死代码 ⇒ 红
      · impl 写错字 ⇒ 红
    """
    sys.path.insert(0, str(payload / "lib"))
    from mlx_server import entry
    from mlx_server.cmd.svc import Invocation
    from mlx_server.tables import TablesView

    rows = TablesView(str(payload / "share")).verbs()
    assert len(rows) >= 10, f"verbs.tsv 只有 {len(rows)} 行 —— 表几乎必然没读对"

    impls = {r["impl"] for r in rows}
    missing = sorted(impls - set(entry.MODULES))
    assert not missing, f"台账里的 impl 在 entry.MODULES 里没有对应模块: {missing}"

    unused = sorted(set(entry.MODULES) - impls - {"cli"})
    assert not unused, f"entry.MODULES 里的模块没有任何台账行用到 ⇒ 死代码: {unused}"

    for r in rows:
        if r["impl"] == "cli":
            continue            # help 在 main 里短路，不进 D 层（另有用例）
        grouped = bool(r["parent"])
        inv = Invocation(r["verb"], None,
                         args={"_group": r["parent"]} if grouped else {},
                         dry_run=False)
        assert entry.route_for(inv, rows) is not None, (
            f"台账行 `{r['parent']} {r['verb']}` route 不到模块")


def test_shell_vars_braced_before_multibyte(repo: Path):
    """**`$VAR` 紧跟多字节字符必须写成 `${VAR}`。**

    bash 在这里会把多字节字符的首字节并进变量名：`$CODE_ROOT（代码根）`
    被解析成变量 `CODE_ROOT\\xef...` ⇒ `set -u` 下 unbound variable ⇒ 脚本当场死。

    这个错误在本项目里犯过两次（第一次在卸载器的确认提示，用户手动卸载时
    第一行就崩；第二次在安装器新写的 rc 写入日志里）。**第二次说明「下次仔细点」
    的预期收益是零** —— 需要的是一个求值器，不是决心。

    错误实现能否通过：写 `$VAR（` ⇒ 本用例红。
    """
    import re
    bad = []
    for sh in sorted((repo / "v7" / "deployer").glob("*.sh")):
        for i, line in enumerate(sh.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            for m in re.finditer(r'\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])', line):
                bad.append(f"{sh.name}:{i} ${m.group(1)} 后面紧跟多字节字符")
    Cardinality.check("裸 $VAR 紧跟多字节字符", bad, rel="empty")


def test_self_deploy_preload_imports_exist(payload: Path):
    """`cmd.self._preload` 里的每个模块都必须真实存在。

    坏样本（本轮真实发生）：预加载列表里写了一个**不存在的 `paths` 模块** ⇒
    `mlx uninstall` 在 `_preload()` 就 ImportError ⇒ 一键卸载完全不可用。
    而 dry-run 在 `_preload` 之前就 return 了，所以既有用例照不到它。

    这条是**静态**检查：不需要跑到那条路径就能发现。
    """
    import ast
    src = (payload / "lib" / "mlx_server" / "cmd" / "self_deploy.py").read_text()
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_preload"), None)
    assert fn is not None, "找不到 _preload —— 它是重装/卸载安全性的前提，不得删除"

    names = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.ImportFrom) or n.level != 2:
            continue
        if n.module is None:
            names += [a.name for a in n.names]   # from .. import mod1, mod2
        else:
            names.append(n.module.split(".")[0])  # from ..mod import Symbol
    Cardinality.require_nonempty("_preload 的模块", names,
                                 "预加载列表为空 ⇒ 该函数没有任何作用")
    pkg = payload / "lib" / "mlx_server"
    missing = [m for m in names if not (pkg / f"{m}.py").exists()]
    assert not missing, f"_preload 引用了不存在的模块：{missing}"


def test_no_test_writes_to_real_home_rc(repo: Path):
    """**没有任何测试会写到真实 `$HOME` 下的 shell rc。**

    两道防线：
      ① conftest 的 autouse fixture 把 `MLX_RC_FILES` 默认指向沙箱；
      ② 本用例静态断言：调用 `install.sh` 的测试代码里，凡是自己构造 env 的，
         要么设了 `MLX_RC_FILES`，要么继承了 fixture 的设置（不显式清空它）。

    坏样本（本轮真实发生）：`cli` fixture 用
    `env = dict(os.environ, PATH=..., MLX_ROOT_*=...)` 造环境却没有 `MLX_RC_FILES`
    ⇒ 安装器回落默认值 `$HOME/.zshrc` ⇒ **写了用户的真实配置文件**。
    """
    import re
    offenders = []
    for f in sorted((repo / "v7" / "tests").rglob("test_*.py")):
        src = f.read_text()
        if "install.sh" not in src:
            continue
        # 显式构造 env 且把 os.environ 排除在外的地方，必须自己给 MLX_RC_FILES
        for m in re.finditer(r"env\s*=\s*\{[^}]*\}", src, re.S):
            block = m.group(0)
            if "MLX_ROOT_CODE" in block and "MLX_RC_FILES" not in block:
                line = src[:m.start()].count("\n") + 1
                offenders.append(f"{f.name}:{line} 构造了不含 MLX_RC_FILES 的 env")
    Cardinality.check("可能写到真实 rc 的测试", offenders, rel="empty")


def test_message_keys_used_are_all_declared(payload: Path):
    """**代码里用的每个文案键都必须在 `messages.tsv` 里**。

    这是 12 个对账维度里的「messages 表 ↔ 实现中的文案」。
    坏样本（本轮真实发生）：19 个键代码在用而表里没有 ⇒ 用户遇到这 19 种
    错误时，拿到的是 `config.unknown_key` 这样的机器标识符，
    而**出错正是他最需要看懂的时刻**。

    错误实现能否通过：新增一个 Refused 却忘了加文案 ⇒ 红。
    """
    import re
    table = payload / "share" / "messages.tsv"
    declared = {l.split("\t")[0] for l in table.read_text().splitlines()
                if l.strip() and not l.startswith("#")}
    Cardinality.require_nonempty("messages.tsv", declared, "表读不到 ⇒ 空真")

    used = set()
    for f in (payload / "lib").rglob("*.py"):
        src = f.read_text()
        used |= set(re.findall(
            r'(?:Refused|Failed|Aggregate)\(\s*\d+\s*,\s*"([a-z][a-z_.]+)"', src))
        used |= set(re.findall(r'msg_key\s*=\s*"([a-z][a-z_.]+)"', src))
    Cardinality.require_nonempty("代码里的文案键", used, "一个都没解析到 ⇒ 空真")
    Cardinality.check("用了但未登记的文案键", sorted(used - declared), rel="empty")


def test_every_message_renders_with_its_own_params(payload: Path):
    """**表里每条文案都能用它自己声明的参数渲染出来**。

    坏样本（本轮真实发生）：`tables.text(self, key, **args)` 的形参叫 `key`，
    而 `config.unknown_key` 的文案参数**也叫 `{key}`** ⇒
    `TypeError: got multiple values for argument 'key'` ⇒ 那条永远渲染不出来。
    这类冲突只在「参数名恰好撞上形参名」时发生，靠读代码看不出来。

    做法：从文案里解析出 `{占位符}`，用假值填进去调一次真的渲染。
    """
    import re
    import sys
    sys.path.insert(0, str(payload / "lib"))
    from mlx_server.tables import TablesView

    tv = TablesView(str(payload / "share"))
    rows = [l.split("\t") for l in
            (payload / "share" / "messages.tsv").read_text().splitlines()
            if l.strip() and not l.startswith("#")]
    Cardinality.require_nonempty("messages.tsv 行", rows, "表读不到 ⇒ 空真")

    broken = []
    for key, text in ((r[0], r[1]) for r in rows if len(r) >= 2):
        params = {p: "X" for p in re.findall(r"\{(\w+)\}", text)}
        try:
            tv.text(tv.msg(key), **params)
        except Exception as exc:                    # noqa: BLE001
            broken.append(f"{key}: {type(exc).__name__} {exc}")
    Cardinality.check("渲染不出来的文案", broken, rel="empty")


def test_message_params_match_construction_sites(payload: Path):
    """**每个构造点传入的参数 ⊇ 该文案模板的占位符**。

    这是 `test_every_message_renders_with_its_own_params` 的盲区：
    那一条用**假参数填满**再渲染，测的是「模板本身能渲染」；
    而真正会出事的是「模板要 `{cmd}`，构造点一个参数都没传」——
    `str.format(**{})` 抛 KeyError，被 entry 的兜底吞掉，
    用户看到的是裸键 `svc.target_required`（实测发生过两处）。

    做法：静态解析每个 `Refused/Failed/Aggregate(code, "key", ..., k=v)` 的
    关键字参数名，与 messages.tsv 里该键的 `{占位符}` 集合对拍。
    """
    import ast
    import re

    table = payload / "share" / "messages.tsv"
    tmpl = {}
    for line in table.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            tmpl[parts[0]] = set(re.findall(r"\{(\w+)\}", parts[1]))
    Cardinality.require_nonempty("messages 模板", tmpl, "表读不到 ⇒ 空真")

    bad = []
    for f in sorted((payload / "lib").rglob("*.py")):
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fn = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if fn not in ("Refused", "Failed", "Aggregate"):
                continue
            # 第二个位置参数是文案键（Aggregate 的键在 msg_key）
            key = None
            for a in n.args[1:3]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                        and "." in a.value:
                    key = a.value
                    break
            for kw in n.keywords:
                if kw.arg == "msg_key" and isinstance(kw.value, ast.Constant):
                    key = kw.value.value
            if key is None or key not in tmpl:
                continue
            passed = {kw.arg for kw in n.keywords if kw.arg} | {"detail"}
            missing = tmpl[key] - passed
            if missing:
                bad.append(f"{f.name}:{n.lineno} {key} 的模板要 "
                           f"{sorted(tmpl[key])}，构造点只传了 "
                           f"{sorted(passed - {'detail'})}，缺 {sorted(missing)}")
    Cardinality.check("模板占位符与构造点参数不匹配", bad, rel="empty")


def test_g_pc_05_commands_in_messages_exist(payload: Path):
    """**`G-PC-05`**：文案里提到的 `mlx <命令>` 必须真的存在于 `verbs.tsv`。

    坏样本（本轮真实发生）：`svc.spawn_failed` 的文案是
    「启动 {service} 失败。看日志：**mlx logs** {service}」，
    而 `logs` 这个动词**本版没有实现** —— 用户照做会得到「未知命令」。
    错误文案把人指向一个不存在的命令，比不给下一步还糟。

    这条守卫写在设计的 SSOT 表第 15 行（`G-PC-05`），至今没有求值面。
    它是靠写文档时逐条核验才被发现的 —— 那不是可持续的办法。
    """
    import csv
    import re

    share = payload / "share"
    rows = list(csv.DictReader(
        (l for l in (share / "verbs.tsv").read_text().splitlines()
         if l.strip() and not l.startswith("#")),
        fieldnames=["verb", "parent", "impl", "aliases", "positionals",
                    "stage_split", "exit_codes", "stdout", "machine", "empty",
                    "stderr", "creates", "effect", "visibility", "doc",
                    "completion"], delimiter="\t"))
    Cardinality.require_nonempty("verbs.tsv", rows, "表读不到 ⇒ 空真")
    real = {f"{r['parent']} {r['verb']}".strip() for r in rows} | {r["verb"] for r in rows}

    bad = []
    for line in (share / "messages.tsv").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, _, text = line.partition("\t")
        # **最长匹配到三段**：动词名本身可以含空格（`trash ls`，parent 是 `model`），
        # 完整路径因此是三段 `model trash ls`。守卫此前只取两段，
        # 于是 `mlx model trash ls` 被截成 `mlx model trash` 判为不存在 ——
        # 守卫自己的提取面比被测对象窄，报的是假阳性。
        for m in re.finditer(r"mlx ([a-z]+(?: [a-z]+){0,2})", text):
            words = m.group(1).split()
            # 从长到短试：三段命中就不再拿两段去判
            if any(" ".join(words[:n]) in real for n in range(len(words), 0, -1)):
                continue
            bad.append(f"{key} 指向不存在的 `mlx {m.group(1)}`")
    Cardinality.check("文案指向不存在的命令", sorted(set(bad)), rel="empty")


def test_d5_dry_run_has_exactly_one_read_surface(payload: Path):
    """`dry_run` **只能从 `inv.dry_run` 读**，产品里不得出现 `args.get("dry_run")`。

    坏样本（本次真实发生）：`cmd/model_reclaim.py` 读 `inv.args.get("dry_run")`，
    而 `cli.py` 明确不把 `dry-run` 放进 `flags` ⇒ 条件恒假 ⇒ 预览分支不可达 ⇒
    `mlx model gc --dry-run` **真的把权重删进回收站**，还报「实际回收 3.0 MB」。

    这是遍历型判据：新写的动词照抄错误写法时当场红，不依赖有没有人为它写用例。
    """
    bad = []
    for f in sorted((payload / "lib" / "mlx_server").rglob("*.py")):
        src = f.read_text()
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "get"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                continue
            if node.args[0].value not in ("dry_run", "dry-run"):
                continue
            # **只查从 `args` 读**。渲染器读的是 payload 里的 `dry_run` 字段
            # （`config import --dry-run` 要在输出里说明「这是预览」），
            # 那是**结论的一部分**，不是命令行标志的读取点 ——
            # 守卫的提取面不区分的话，正确的写法也会被判红。
            recv = ast.unparse(node.func.value) if hasattr(ast, "unparse") else ""
            if "args" not in recv:
                continue
            bad.append(f"{f.relative_to(payload)}:{node.lineno}")
    assert bad == [], (
        "这些地方从 args 里读 dry_run，而它永远不在 args 里 —— "
        f"预览分支不可达，破坏性动作会真的执行：{bad}")


def test_d6_every_injected_dep_has_a_producer(payload: Path, sys_py: str):
    """**`deps.get(K)` 的 K 必须在 `runtime.build_deps` 的键集里**（遍历型）。

    坏样本（本次真实发生，一次四个）：`ports_conflict` / `types` / `ask` / `render`
    四个键**从来没有生产者**。`.get()` 的默认返回 `None` 让缺失变成「优雅降级」
    而不是 `KeyError`，于是每一处都静静跳过：
      · `ports_conflict` 缺 ⇒ 网关端口互斥检查恒空跑（设计原话「不得因降级模式恒空跑」）
      · `types` 缺 ⇒ `--type=banana` 落盘，两道域校验用的是同一个空值
      · `ask` 缺 ⇒ type 四级判定链只剩第一级，不给 `--type` 必然 rc 9
      · `render` 缺 ⇒ `bootstrap render` 恒 rc 3，还把实现缺失说成「装配缺陷，请重装」

    四条各自都有实现体、有注释、有测试（测试注了自己的桩）——
    **测试证明了逻辑，没有证明它会被执行**。这条守卫查的正是那道缝。
    """
    got = _floor(payload, sys_py, """
        import json, os, tempfile
        from mlx_server import runtime
        from mlx_server.ctx import Ctx
        d = tempfile.mkdtemp()
        ctx = Ctx(d, d, d, os.path.join(d, "venv"), 8340, (8100, 8199), None)
        print(json.dumps({"produced": sorted(runtime.build_deps(ctx))}))
    """)
    produced = set(got["produced"])

    consumed = {}
    for f in sorted((payload / "lib" / "mlx_server").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "get"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            recv = getattr(node.func.value, "id", None)
            # `deps.get(K, 默认值)` 是**有意的可选注入**（如 `now` 的时钟），
            # 缺失时有确定行为，不算「分支永不执行」。只查单参形式。
            if recv == "deps" and len(node.args) == 1:
                consumed.setdefault(node.args[0].value, []).append(
                    f"{f.relative_to(payload)}:{node.lineno}")

    Cardinality.require_nonempty("deps 消费点", sorted(consumed),
                                 "一个都没扫到 ⇒ 这条守卫空真")
    orphan = sorted(k for k in consumed if k not in produced)
    assert orphan == [], (
        "这些注入点没有生产者 —— 它们的分支在生产上永远不执行，"
        f"而各自的测试注了自己的桩所以全绿：{ {k: consumed[k] for k in orphan} }")


def test_d7_fitclass_has_exactly_three_members(payload: Path, sys_py: str):
    """**`FitClass` 的成员集合 == `{FIT, TIGHT, OVER}`**（相等断言，`L0·vt-02`）。

    设计逐字：「`UNKNOWN` **不是档位**。给 `FitClass` 加第四个成员 = 把
    『不确定』伪装成一个档位」—— 而两者的处置完全相反：一个是「拒绝并说清
    哪个输入不确定」，一个是「这台机器放得下 / 放不下」。

    同时钉住两条：`classify` 对任一 `Unknown` 输入返回 `Unknown` 且**指名**
    是哪个输入；以及**禁止浮点** —— `round()` 是银行家舍入
    （`round(0.5)==0`、`round(2.5)==2`），边界样例上两个消费者会算出不同档位，
    而判档是 fail-closed 的闸门，两处不一致意味着它有时开有时关。
    """
    got = _floor(payload, sys_py, """
        import json
        from mlx_server import fitness
        from mlx_server.result import Ok, Unknown
        th = fitness.FitThresholds(20, 75, 95)
        named = []
        for i, args in enumerate(([Unknown("TIMEOUT", "x"), Ok(1), Ok(0)],
                                  [Ok(1000), Unknown("PARSE_FAILED", "y"), Ok(0)],
                                  [Ok(1000), Ok(1), Unknown("PS_FAILED", "z")])):
            out = fitness.classify(*(args + [th]))
            named.append([out.is_unknown(), out.reason])
        # 边界：need*100 == budget*over_pct 必须落在 OVER 一侧（>=）
        edge = fitness.classify(Ok(1000), Ok(760), Ok(0), th)   # budget=800, 95% = 760
        print(json.dumps({"members": sorted(fitness.FIT_CLASSES),
                          "unknown": named, "edge": edge.value}))
    """)
    Cardinality.check("FitClass 成员", got["members"], ["FIT", "OVER", "TIGHT"], rel="equal")
    for is_unk, reason in got["unknown"]:
        assert is_unk, "任一输入 Unknown 时必须返回 Unknown，不得塌成某一档"
        assert reason.split(":")[0] in ("mem_budget_mb", "size_mb", "running_sum_mb"), \
            f"Unknown 没有指名是哪个输入：{reason!r}"
    assert got["edge"] == "OVER", f"边界值应落在 OVER 一侧，实得 {got['edge']}"


def test_d7_fitness_is_pure_no_probe_calls(payload: Path):
    """`fitness` **不调探针**（探针值由调用方注入）——那是它能被逐样例对拍的前提。

    坏样本：在 `classify` 里直接读内存 ⇒ 它的返回值依赖跑测试的那台机器当时的
    空闲内存，边界样例再也写不出来。
    """
    src = (payload / "lib" / "mlx_server" / "fitness.py").read_text()
    tree = ast.parse(src)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "probe" not in (imported or set()), "fitness 不得 import probe"
    assert "subprocess" not in src, "fitness 不得碰外部命令"
    # 禁止浮点：**在 AST 上查**，不扫源码文本。
    # 文本扫描会命中文档串里的 `fit.reserve_pct / fit.tight_pct` 这种斜杠 ——
    # 守卫的提取面比被测对象宽，报的是假阳性（本轮第四次遇到同一形态）。
    div = [n.lineno for n in ast.walk(tree)
           if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)]
    assert div == [], f"判档禁止浮点除法（银行家舍入会让两个消费者算出不同档位）：行 {div}"
    rounds = [n.lineno for n in ast.walk(tree)
              if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "round"]
    assert rounds == [], f"判档禁止 round()：行 {rounds}"


def test_d8_probes_do_not_collapse_unknown_into_a_value(payload: Path, sys_py: str):
    """**探测失败不得伪装成确定结果**（三处，各自都有真实后果）。

    ① `weights.size` 读不动目录时返回 `Unknown`，不是 `Ok(0)` ——
       `os.walk` 的 `onerror` 默认是 `None`（静默跳过不可读目录），
       加上 `except OSError: continue`，一个权限不对的 28 GB 模型会报成
       **0 字节** ⇒ `size_mb: 0.0` 写进记录 ⇒ 容量判档恒判 FIT。
       那个函数的注释里早就记着「用 du 会让判档永远 FIT」的同类教训，
       而实现里留着同一个洞的另外两半。
    ② `weights.scan_installed` 任一条目 `Unknown` ⇒ 整体 `Unknown`，
       上层拒绝重建（契约：**不用部分事实覆盖记录**）。
    ③ `supervisor.snapshot` 对**存在但不可解析**的运行记录抛，
       不返回 `None` —— 「读不到」与「没在跑」的处置完全相反：
       后者让 `mlx down` 报「已停止」而什么都不做，28 GB 的后端继续跑着。
    """
    got = _floor(payload, sys_py, """
        import json, os, tempfile
        from mlx_server import supervisor, weights
        from mlx_server.ctx import Ctx
        d = tempfile.mkdtemp()
        ctx = Ctx(d, d, d, os.path.join(d, "venv"), 8340, (8100, 8199), None)

        # ① 不可读目录
        blocked = os.path.join(d, "hub", "models--o--x", "snapshots")
        os.makedirs(blocked)
        open(os.path.join(blocked, "f"), "w").write("x")
        os.chmod(blocked, 0o000)
        try:
            size_unknown = weights.size(os.path.join(d, "hub", "models--o--x")).is_unknown()
            scan_unknown = weights.scan_installed(ctx).is_unknown()
        finally:
            os.chmod(blocked, 0o755)

        # ③ 坏掉的运行记录
        os.makedirs(os.path.join(d, "run"), exist_ok=True)
        open(os.path.join(d, "run", "svc.json"), "w").write("NOT JSON")
        try:
            supervisor.snapshot(ctx, "svc")
            raised = False
        except supervisor.RunRecordUnreadable:
            raised = True
        # 缺失仍然是 None（「没在跑」是正常态，不该抛）
        missing_is_none = supervisor.snapshot(ctx, "nosuch") is None
        print(json.dumps({"size": size_unknown, "scan": scan_unknown,
                          "raised": raised, "missing_none": missing_is_none}))
    """)
    assert got["size"], "读不动的目录被算成 0 字节 —— 容量判档会因此恒判 FIT"
    assert got["scan"], "扫描不完整却返回了部分事实 —— 会把扫不到的模型当成孤儿"
    assert got["raised"], "不可解析的运行记录被读成「没在跑」—— down 会假报已停止"
    assert got["missing_none"], "记录缺失是正常态，不该抛"


def test_d9_display_width_has_exactly_one_implementation(payload: Path):
    """**INV-23：显示宽度对齐只有一个 `ui.pad()`**（实现点 == 1）。

    坏样本（本次真实发生）：`ui.pad` / `ui.display_width` 是「唯一实现」，
    而它们**零调用点** —— 真正在用的是 `ui._pad`/`ui._w` 与 `cli._pad`/`cli._w`
    两个逐字相同的私有副本。「唯一」写在不变式里而实现有三份，守卫只是装饰。
    代价是真实的：任一处漏掉全角判断就出现 CJK 列错位（v6 的老症状）。

    判据是**东亚宽度表的读取点**（`east_asian_width` 的出现次数），
    不是函数名 —— 换个名字再抄一份同样会被抓住。
    """
    hits = []
    for f in sorted((payload / "lib").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "east_asian_width"):
                hits.append(f"{f.relative_to(payload)}:{node.lineno}")
    Cardinality.require_nonempty("east_asian_width 读取点", hits, "一处都没有 ⇒ 空真")
    assert len(hits) == 1, (
        f"显示宽度有 {len(hits)} 份实现，INV-23 要求恰好 1 份：{hits}")
    assert hits[0].startswith("lib/mlx_server/ui.py"), \
        f"唯一实现应在 ui（B 层的渲染原语），实得 {hits[0]}"


def test_d10_yes_flag_cannot_reach_the_irreversible_action(payload: Path):
    """**`--yes` 不得作用于 `model trash empty`** —— 那是唯一不可恢复的动作。

    确认强度按可恢复性分了三档：`model rm` 不问（登记可完全恢复）、
    `model rm --weights` 问但认 `--yes`（进回收站，仍可恢复）、
    `model trash empty` 问且**三种绕过全拒**。

    这条守卫钉住第三档。判据是**两条合取**：
    ① `cli.tsv` 里 `--yes` 的 owner_verb 不含 `model trash empty`；
    ② 解析器**真的按 owner_verb 拒绝**（此前那一列只用来渲染提示，不参与拒绝 ——
       于是 `mlx version --repo=x` 也 rc 0 被静默接受）。
    少了 ②，①就只是一句没人执行的声明。
    """
    rows = [ln.split("\t") for ln in (payload / "share" / "cli.tsv").read_text().splitlines()
            if ln.startswith("--")]
    yes = [r for r in rows if r[0] == "--yes"]
    assert yes, "--yes 没有登记在 cli.tsv 里"
    owners = [x.strip() for x in yes[0][1].split(",")]
    assert "model trash empty" not in owners and "*" not in owners, \
        f"--yes 能作用于 trash empty（唯一不可恢复的动作）：owner_verb={yes[0][1]!r}"

    # ② 解析器按 owner_verb 拒绝 —— 在源码里找那处判定，而不是只信表
    src = (payload / "lib" / "mlx_server" / "cli.py").read_text()
    assert "owner_verb" in src and "wrong_owner" in src, \
        "解析器没有按 owner_verb 拒绝 —— 那一列就只是渲染提示用的装饰"


def test_d11_g_res_09_three_conjuncts(payload: Path):
    """**`G-RES-09` 三合取**（`02-architecture.md` §6.2 第 3 行）。

    (a) `pthread_sigmask` **实现点 == 1**（`proc.masked` / `reset_child_mask`）；
    (b) `proc.masked` 的**调用点集合 == 三元**（相等断言，多一个红、少一个也红）：
        `{spawn 的交接段, atomic.lock 的临界区, supervisor.down 的停止段}`；
    (c) `gw.*` 中出现 `masked()` 即红 —— 网关侧受这条相等断言保护，
        不是「不在射程内」。

    它关闭的是一个真实事故（E-7）：180 秒就绪等待里按 Ctrl-C，shell 退出而
    后台 python 继续把 28 GB 读进内存 —— 因为 spawn 与「把 pid 记下来」
    之间被信号截断，**没有人再知道该去杀谁**。

    (c) 单列的理由：网关是独立进程，屏蔽 INT/TERM 会让它自己变得难以停止，
    而它没有需要保护的交接段。
    """
    root = Path(__file__).resolve().parents[2] / "payload"

    # (a) 实现点 == 1（`proc` 模块内，两个函数共用同一张表 —— 数的是文件）
    impl = set()
    for f in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "pthread_sigmask"):
                impl.add(str(f.relative_to(root)))
    Cardinality.check("pthread_sigmask 实现点", sorted(impl),
                      ["lib/mlx_server/proc.py"], rel="equal")

    # (b) 调用点集合 == 三元
    callers = set()
    for f in sorted(root.rglob("*.py")):
        src = f.read_text()
        if ".masked()" in src and f.stem != "proc":
            callers.add(str(f.relative_to(root)))
    Cardinality.check("proc.masked 调用点", sorted(callers),
                      ["lib/mlx_server/atomic.py",
                       "lib/mlx_server/runtime.py",
                       "lib/mlx_server/supervisor.py"], rel="equal")

    # (c) 网关侧不得出现
    gw = [str(f.relative_to(root)) for f in sorted((root / "gateway").rglob("*.py"))
          if ".masked()" in f.read_text()]
    assert gw == [], f"网关里出现了 masked() —— 它会让网关自己变得难以停止：{gw}"


def test_d11_spawn_passes_child_mask_reset(payload: Path):
    """**BC-C-05：spawn 必须给子进程重置掩码。**

    静态钉住这条的理由：它的动态断言（`test_l2_masked_child_restores_default_mask`）
    要起真进程、发真信号，慢且依赖时序；而这条缺失的后果极重 ——
    「exec 保留掩码、只重置处理器」，漏了它**所有后端都杀不掉**，
    `down` 的 TERM 阶梯失效。两条一起守，快的那条先红。
    """
    src = (payload / "lib" / "mlx_server" / "runtime.py").read_text()
    tree = ast.parse(src)
    # **只查被监管的后端进程**（`start_new_session=True` 那些）。
    # BC-C-05 要防的是「后端继承父进程的屏蔽掩码 ⇒ 杀不掉」，
    # 而那个风险只存在于 `down` 要去终止的进程上。
    # `hf download` 这类前台子进程不新建会话、不进运行记录、
    # 随父进程一起结束 —— 给它 `preexec_fn` 没有意义，
    # 而守卫不区分的话，加一个正当的 `Popen` 就会红。
    popens = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "Popen"
              and any(k.arg == "start_new_session" for k in n.keywords)]
    Cardinality.require_nonempty("runtime 里的 Popen", popens, "一个都没有 ⇒ 空真")
    for call in popens:
        kws = {k.arg for k in call.keywords if k.arg}
        assert "preexec_fn" in kws, (
            f"第 {call.lineno} 行的 Popen 没给 preexec_fn —— 子进程会继承父进程的"
            "屏蔽掩码，后端永久屏蔽 INT/TERM，`down` 的 TERM 阶梯失效（BC-C-05）")


def test_d12_record_segments_reject_wrong_side_reads(payload: Path, sys_py: str):
    """**从错误的段读已知字段 ⇒ 抛，不静默返回 `None`**（`G-PC-02` 的实质）。

    契约要求「记录的两段是冻结数据类（不是 `dict[str, Any]`），`G-PC-02`
    因此能做 AST 属性引用扫描」。这里没做成 `@dataclass(frozen=True)` ——
    两段都是**开放集合**（`config set --key=<任意配置键>` 往 user 段写新键，
    `rebuild_machine` 往 machine 段填新字段），固定字段集会把它们挡在外面。
    真正要抓的不是「有没有多余字段」，是**字段归属错了**。

    这类错误已经发生过两次，两次都是**静默**的：
      · `weights` 产 `bytes` 而 `rebuild_machine` 找 `size_mb` ⇒ 体积那一格永远是「—」
      · `model rm --weights` 从 user 段读 `revision` ⇒ 永远 `None` ⇒
        `resolve` 退化成「取最新的那个 revision」，同一 repo 存了两个版本时
        删的可能不是这条记录登记的那一个（本轮修）

    根因是 `dict.get` 打错键静默返回 `None`，而这个项目里 `None` 到处是
    合法值（未设置 / 未下载 / 不可判定）—— 它不会以异常的形式暴露。
    """
    got = _floor(payload, sys_py, """
        import json
        from mlx_server.store import Record, WrongSegment
        r = Record("x", {"repo": "o/x"}, {"revision": "main", "port": 8100})
        out = {"ok_user": r.user.get("repo"), "ok_machine": r.machine.get("revision")}
        for seg, key in (("user", "revision"), ("machine", "repo")):
            try:
                getattr(r, seg).get(key)
                out[seg + "." + key] = "没抛"
            except WrongSegment:
                out[seg + "." + key] = "抛了"
        # 两段都可能承载的字段不该抛
        out["both"] = [r.machine.get("port"), r.user.get("port")]
        print(json.dumps(out))
    """)
    assert got["ok_user"] == "o/x" and got["ok_machine"] == "main", \
        f"正确的读被挡住了：{got}"
    assert got["user.revision"] == "抛了", \
        "从 user 段读 revision 没抛 —— 它会静默返回 None，而 None 是合法值"
    assert got["machine.repo"] == "抛了", "从 machine 段读 repo 没抛"
    assert got["both"] == [8100, None], f"两段都可能有的字段不该抛：{got['both']}"


def test_d12_static_segment_ownership(payload: Path):
    """**静态**扫「`rec.<段>.get("<字段>")`」，字段归属错了当场红。

    运行期那条（`_Seg.get` 抛 `WrongSegment`）只覆盖**被执行到**的路径 ——
    而 `model rm --weights` 那个缺陷正好在一条要 tty 确认才走得到的分支上，
    活了整整一版。静态这条不依赖执行。

    权威是 `schema.tsv` 的 `scope` 列 + `store._KNOWN_SEG`（后者补齐
    `repo`/`type` 这些非配置键）。两者今天一致 —— 若将来分叉，
    这条守卫会先红在归属表上，那正是它该做的。
    """
    sys.path.insert(0, str(payload / "lib"))
    from mlx_server.store import _KNOWN_SEG  # noqa: PLC0415

    bad = []
    for f in sorted((payload / "lib" / "mlx_server").rglob("*.py")):
        if f.name == "store.py":
            continue                     # 归属表自己在这里定义
        for node in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "get"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                continue
            recv = ast.unparse(node.func.value) if hasattr(ast, "unparse") else ""
            seg = ("user" if recv.endswith(".user")
                   else "machine" if recv.endswith(".machine") else None)
            if seg is None:
                continue
            key = node.args[0].value
            want = _KNOWN_SEG.get(key)
            if want and want not in (seg, "both"):
                bad.append(f"{f.relative_to(payload)}:{node.lineno} "
                           f"从 {seg} 段读 {key!r}（它在 {want} 段）")
    Cardinality.check("读错段的已知字段", sorted(bad), rel="empty")


def test_required_packages_are_actually_installed_by_the_installer(payload, repo):
    """`caps.REQUIRED` ⊆ `install.sh` 真正装的那些。

    这两份清单**故意不等**：探测面（`PACKAGES`）比安装面宽，才能发现用户
    手工装的包。但「必需」是个更强的断言 —— 声明某个包必需，却没有任何
    地方去装它，那条「必需」就只是一句话。

    这条守卫立在一次真实误读上：`mlx version` 曾把探测清单里没装的一律
    渲染成「未装：mlx_vlm」，用户据此以为 `mlx_lm` 没装（它装了 0.31.3）。
    修法是把清单拆成必需/可选两份，而拆完就需要这条把「必需」钉在
    安装器上 —— 否则下次有人往 `REQUIRED` 里加一个安装器不装的包，
    用户会看到「**缺必需包**」却怎么重装都不消失。

    **不做相等断言**：`huggingface_hub` 安装器装但不在探测面里
    （它不是后端，不进 fingerprint），那是对的。
    """
    import re
    sys.path.insert(0, str(payload / "lib"))
    from mlx_server.caps import OPTIONAL, PACKAGES, REQUIRED  # noqa: PLC0415

    src = (repo / "v7" / "deployer" / "install.sh").read_text()
    m = re.search(r"for\s+pkg\s+in\s+([^\n;]+?)\s*;\s*do", src)
    assert m, "install.sh 里找不到装包的 for 循环 —— 守卫的提取面失效了"
    # pip 名 → import 名（`mlx-lm` 装出来的模块叫 `mlx_lm`）
    installed = {p.replace("-", "_") for p in m.group(1).split()}

    bad = [p for p in REQUIRED if p not in installed]
    bad += [f"{p}（在 REQUIRED 又在 OPTIONAL）" for p in REQUIRED if p in OPTIONAL]
    bad += [f"{p}（REQUIRED 但不在探测面 PACKAGES 里，version 永远显示它缺）"
            for p in REQUIRED if p not in PACKAGES]
    Cardinality.check("声明必需却没人装的包", sorted(bad), rel="empty")


def test_value_flags_are_shown_in_equals_form(payload, repo):
    """面向用户的文案里，取值 flag 必须写成 `--k=v`。

    解析器**只认等号形态**（`cli.py:337-341`：`takes_value=yes` 而没有 `=`
    一律 rc 2）。所以任何印给用户看的 `--repo <值>` 都是一条照抄就报错的
    指令 —— 用户没有理由怀疑我们自己印出来的命令。

    这条立在一次现行犯上：2026-09-02 我在 `caps.py` 写下
    `"仅 --type vision 需要"`，它经 `cmd/meta.py` 直接印进 `mlx version`
    的主产物。**同一次会话里我刚因为给错形态让 Alex 的命令失败过**，
    转头又把错形态写进了产品输出 —— 靠记性防不住，得靠判据。

    只查 `takes_value=yes` 的 flag：布尔 flag 后面跟空格是正常中文行文
    （「`--weights` 连权重一起删」），不是形态错误。
    """
    import re
    rows = [ln.split("\t") for ln in
            (payload / "share" / "cli.tsv").read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]
    value_flags = [r[0] for r in rows if len(r) > 2 and r[2] == "yes"]
    assert value_flags, "cli.tsv 里一个取值 flag 都没读到 —— 守卫的提取面失效了"

    # `--repo` 后跟空格再跟一个「像值」的 token（ASCII 字母数字或 `<`）。
    # 中文说明（「用 --repo 指定」）不会这样，所以不会误伤。
    pat = re.compile(r"(" + "|".join(re.escape(f) for f in value_flags)
                     + r")\s+[<A-Za-z0-9]")
    targets = [payload / "share" / "messages.tsv",
               repo / "v7" / "docs" / "USER-GUIDE.md"]
    targets += sorted((payload / "lib" / "mlx_server").rglob("*.py"))

    # **别的程序的 flag 不归本守卫管**。`hf` 与 `mlx_lm.server` 都是 GNU 风格，
    # 它们的 `--revision <sha>` / `--port <n>` 写成空格形态是**对的**。
    # 而 flag 名本身区分不了归属（`--port` 两边都有），只能看上下文 ——
    # 守卫的提取面必须窄到和它要守的东西一样窄，否则假阳性会逼后人关掉它。
    FOREIGN = ("hf download", "hf_hub", "huggingface", "mlx_lm", "mlx_vlm",
               "后端参数", "backendarg", "-m gateway")

    # **教学反例要允许存在**。文档说不清「什么是错的」就教不会人，
    # 而说清它必须把错的形态写出来。判据是同一行带否定标记 ——
    # 靠标记而不是靠行号豁免，这样反例挪了位置守卫也不会失灵。
    COUNTEREXAMPLE = ("✗", "rc 2", "报错", "不接受", "会被拒", "撞")

    bad = []
    for f in targets:
        lines = f.read_text().splitlines()
        for i, line in enumerate(lines, 1):
            m = pat.search(line)
            if not m:
                continue
            # 上下文窗口：外部命令的参数表往往跨行（表格、注释块）
            window = "\n".join(lines[max(0, i - 5):i + 2])
            if any(k in window for k in FOREIGN):
                continue
            if any(k in line for k in COUNTEREXAMPLE):
                continue
            bad.append(f"{f.name}:{i} {m.group(0)!r} —— 应写成 {m.group(1)}=")
    Cardinality.check("用户可见文案里的空格形态取值 flag", sorted(bad), rel="empty")


def test_target_existence_checked_before_any_resource_creation(payload):
    """按外部输入索引注册表的命令，**存在性校验必须在任何资源创建之前**。

    `R-SAFETY-11` 逐字：「任何按外部输入索引内部映射之前必须有存在性校验」，
    来源栏点名的 v6 症状是「详情命令对未注册短名崩溃」。

    这条守卫立在一次真实误用上：`mlx chat` 的第一个位置参数是**模型 id**，
    而用户很自然会把问题写在那儿 —— 2026-09-02 Alex 敲了
    `mlx chat "如何查询手机sn号"`。校验缺席时那句话被当成服务名，一路走到
    「连不上后端 如何查询手机sn号」，还报出 `logs/如何查询手机sn号.log`
    这样一个不存在的路径；`sessions.new` 又在校验之前就落了盘，
    于是每敲错一次留一个会话文件（实测目录里真有一个以那句话命名的）。

    判据是**行号序**：`by_id` 之后的第一个 `Refused` 必须早于任何
    「创建资源」的调用。这同时也是 `BCR-R-01` 要求的（`Refused` 的前提是
    零资源已创建）。
    """
    # **只查「消费者」命令**：那些语义上要求 target 必须已存在的。
    # 一刀切扫「所有 by_id 调用」会误伤两类：
    #   · `model install` / `register` 是**生产者** —— target 不存在才是常态，
    #     它那句 `by_id` 查的是「会不会覆盖已有记录」，不是存在性校验
    #   · `config.py` 的 `run()` 管 8 个子命令，跨分支比行号会把 `import`
    #     分支的 commit 和 `set` 分支的 Refused 凑成一对
    # 两者都是本守卫第一版的假阳性。清单显式列出，每加一条要写明它为什么
    # 是消费者 —— 比自动推断诚实，也更好审。
    CONSUMERS = {
        "chat.py": "对着一个已在跑的模型说话，target 不存在就无事可做",
        "bench.py": "压测一个已在跑的模型，同上",
    }
    CREATORS = ("sessions.new", "store.commit", "vault.trash", "atomic.replace",
                "os.makedirs", "supervisor.up")
    bad = []
    for fname, why in sorted(CONSUMERS.items()):
        f = payload / "lib" / "mlx_server" / "cmd" / fname
        if not f.exists():
            bad.append(f"{fname}: 文件不在 —— 守卫的提取面失效了（{why}）")
            continue
        tree = ast.parse(f.read_text())
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "run"), None)
        if fn is None:
            bad.append(f"{fname}: 没有 run()")
            continue

        # 存在性校验：`if rec is None:` 里的 Refused
        checks = []
        for n in ast.walk(fn):
            if not isinstance(n, ast.If):
                continue
            t = ast.unparse(n.test) if hasattr(ast, "unparse") else ""
            if "is None" not in t or "rec" not in t:
                continue
            checks += [c.lineno for c in ast.walk(n)
                       if isinstance(c, ast.Call)
                       and getattr(c.func, "id", None) == "Refused"]
        creations = [n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Call)
                     and any(c in (ast.unparse(n.func) if hasattr(ast, "unparse") else "")
                             for c in CREATORS)]
        if not checks:
            bad.append(f"{fname}: 没有 `if rec is None: return Refused(...)` "
                       f"⇒ 用户把别的东西写在 target 位置时会一路走下去")
        elif creations and min(checks) > min(creations):
            bad.append(f"{fname}: 存在性校验在 :{min(checks)}，"
                       f"但 :{min(creations)} 已经创建了资源 "
                       f"⇒ 敲错一次留一份垃圾（违反 BCR-R-01）")
    Cardinality.check("存在性校验缺席或晚于资源创建", sorted(bad), rel="empty")


def test_running_state_is_not_inferred_from_port(payload):
    """判断「服务在不在跑」必须问 `running`，**不能拿「有没有端口」当代理**。

    `supervisor.effective_port` 在没有运行记录时**回落到注册表登记的端口**
    （`supervisor.py:213`：`snap.get("port") or registered`）。所以服务停着
    它照样返回一个端口 —— 以 `port is None` 为判据的代码在服务停止时
    **一次都不会命中**。

    2026-09-02 实测：我按 `port is None` 写了「没在跑」的分支，服务确实停着，
    而用户看到的仍是「进程在跑，但连不上它的端口 8100」。
    **能拿到端口不等于有人在监听它** —— 前者是登记事实，后者是运行事实。

    判据：凡用 `effective_port` 的结果去构造 `*.not_running` 文案的，都是
    把两种事实混为一谈。
    """
    bad = []
    for f in sorted((payload / "lib" / "mlx_server" / "cmd").rglob("*.py")):
        src = f.read_text()
        if "not_running" not in src:
            continue
        tree = ast.parse(src)
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "run"]:
            # 每个 not_running 的构造点，往上找最近的那个 if 的判据
            parent = {}
            for n in ast.walk(fn):
                for ch in ast.iter_child_nodes(n):
                    parent[ch] = n
            guards = []
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Call)
                        and getattr(n.func, "id", None) == "Refused"
                        and any(isinstance(a, ast.Constant)
                                and str(a.value).endswith(".not_running")
                                for a in n.args)):
                    continue
                cur, guard = n, None
                while cur in parent:
                    cur = parent[cur]
                    if isinstance(cur, ast.If):
                        guard = ast.unparse(cur.test) if hasattr(ast, "unparse") else ""
                        break
                if guard is None:
                    continue
                guards.append((n.lineno, guard))
            # **判据是「有没有一条真正的运行状态判据」，不是「每条都得是」**。
            # `port is None` 留作兜底（注册表与运行记录都没端口）是合理的，
            # 错的是**只有**它 —— 那种写法在服务停止时一次都不会命中。
            # 守卫的第一版按「每条都得是」写，把正当的兜底也判成了错。
            if guards and not any("running" in g or "alive" in g
                                  for _, g in guards):
                ln, g = guards[0]
                bad.append(f"{f.name}:{ln} not_running 的判据只有 `{g}` —— "
                           f"effective_port 会回落到注册表登记的端口，"
                           f"服务停着它照样有值，这个分支永远不命中")
            # 反向：模块里必须真的问过 running
            if "not_running" in src and "supervisor.running" not in src:
                bad.append(f"{f.name}: 报 not_running 却从没调用 "
                           f"supervisor.running —— 运行状态无来源")
    Cardinality.check("拿端口当运行状态代理的判据", sorted(set(bad)), rel="empty")


def test_version_string_is_single_sourced(payload, repo):
    """版本串只有一个定义点，且**文档里的版本与它一致**。

    `meta.VERSION` 是唯一定义点（`svc.py:309` import 它，注释明写「不在这里
    写第二遍版本串」）。但文档是另一回事：`USER-GUIDE.md` 的抬头和示例输出里
    各写了一次版本号，它们**不会因为改了常量而自动跟上**。

    发版时漏改文档是最容易发生的那种不一致 —— 用户看到手册说 7.0.0-dev、
    命令说 1.0.0，两者都不足信。这条把文档钉在常量上。
    """
    import re
    src = (payload / "lib" / "mlx_server" / "cmd" / "meta.py").read_text()
    m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
    assert m, "meta.py 里找不到 VERSION 定义 —— 守卫的提取面失效了"
    version = m.group(1)

    # 别处不得再写一份版本串字面量
    dup = []
    for f in sorted((payload / "lib").rglob("*.py")):
        if f.name == "meta.py":
            continue
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if f'"{version}"' in line or f"'{version}'" in line:
                dup.append(f"{f.name}:{i} 又写了一份版本串 {version!r}")

    # 文档里**标注为本工具版本**的地方必须与常量一致。
    # 判据只认这两种明确写法 —— 扫所有 `x.y.z` 会把 Python 3.9.6、
    # mlx 0.30.4、设计文档章节号 §0.4.2 全都误伤（守卫第一版就是那样，
    # 5 条命中全是假阳性）。提取面要跟被守的东西一样窄。
    PATTERNS = (r"版本\s+(\d+\.\d+\.\d+(?:-\w+)?)",
                r"工具版本\s+(\d+\.\d+\.\d+(?:-\w+)?)",
                r"[Vv]ersion[:\s]+(\d+\.\d+\.\d+(?:-\w+)?)")
    stale = []
    for doc in [repo / "v7" / "docs" / "USER-GUIDE.md",
                repo / "README.md", repo / "README_cn.md",
                repo / "CHANGELOG.md"]:
        if not doc.exists():
            continue
        for i, line in enumerate(doc.read_text().splitlines(), 1):
            for pat in PATTERNS:
                for v in re.findall(pat, line):
                    if v != version:
                        stale.append(f"{doc.name}:{i} 标着「版本 {v}」，"
                                     f"而 meta.VERSION 是 {version}")
    Cardinality.check("版本串的重复定义与文档失配", sorted(dup + stale), rel="empty")
