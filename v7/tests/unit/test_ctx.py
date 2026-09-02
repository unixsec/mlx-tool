"""`ctx` 构造链的四处断口（04-interfaces.md §2.2）。

每组测试对应一处断口，坏样本就是断口本身 —— 它们是 47k 行横向设计
至今没定下来的格子，本片第一次让真实代码去求值。
"""
import subprocess
import textwrap


def _run(sys_py, payload, code, env=None, tmp=None):
    """在 3.9.6 下限解释器里跑一段用 T1 的代码。"""
    e = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(payload / "lib")}
    if env:
        e.update(env)
    return subprocess.run(
        [sys_py, "-c", textwrap.dedent(code)],
        capture_output=True, text=True, timeout=15, check=False,
        env=e, cwd=str(tmp) if tmp else None,
    )


# ── 断口 ①：constants 入参没有产者 ⇒ 指派 ctx 自己读 ──────────────────

def test_gap1_ctx_reads_constants_itself(payload, sys_py, tmp_path):
    """`ctx` 是「环境读取权」的所有者，由它自己读 share/constants.env。

    坏样本：`load(constants=...)` 要调用方传进来 —— 而全书没有任何模块
    被指派去读 constants.env（tables 只读 *.tsv，T0 的 read_constants 在 bash 侧）
    ⇒ 那个入参永远没有产者。
    """
    share = tmp_path / "share"
    share.mkdir()
    (share / "constants.env").write_text(
        "MLX_ROOT_CODE=/tmp/c\nMLX_ROOT_DATA=/tmp/d\nMLX_ROOT_WEIGHTS=/tmp/w\n"
        "MLX_GATEWAY_PORT=8080\nMLX_PORT_RANGE=8100-8199\n"
    )
    r = _run(sys_py, payload, f"""
        from mlx_server import ctx
        c = ctx.build(share_root={str(share)!r}, environ={{}})
        print(c.root_data)
    """)
    assert r.returncode == 0, f"ctx.build 失败：{r.stderr}"
    assert "/tmp/d" in r.stdout


# ── 断口 ②：三根在生产环境没有来源 ⇒ environ 优先、constants 兜底 ─────

def test_gap2_environ_wins_over_constants(payload, sys_py, tmp_path):
    """取值序写死：environ 优先。"""
    share = tmp_path / "share"; share.mkdir()
    (share / "constants.env").write_text(
        "MLX_ROOT_CODE=/from/const\nMLX_ROOT_DATA=/from/const\n"
        "MLX_ROOT_WEIGHTS=/from/const\nMLX_GATEWAY_PORT=8080\nMLX_PORT_RANGE=8100-8199\n"
    )
    r = _run(sys_py, payload, f"""
        from mlx_server import ctx
        c = ctx.build(share_root={str(share)!r}, environ={{"MLX_ROOT_DATA": "/from/env"}})
        print(c.root_data)
    """)
    assert r.returncode == 0, r.stderr
    assert "/from/env" in r.stdout, "environ 未优先于 constants"


def test_gap2_clean_shell_does_not_raise(payload, sys_py, tmp_path):
    """**这是断口 ② 的核心**：干净 shell 里敲 `mlx status` 不得抛。

    坏样本（原 `L1·ctx-03` 的字面要求）：「缺 MLX_ROOT_DATA → 抛」
    ⇒ 用户装完之后第一条命令就崩，因为垫片是三行、不导出任何环境变量。
    """
    share = tmp_path / "share"; share.mkdir()
    (share / "constants.env").write_text(
        "MLX_ROOT_CODE=/tmp/c\nMLX_ROOT_DATA=/tmp/d\nMLX_ROOT_WEIGHTS=/tmp/w\n"
        "MLX_GATEWAY_PORT=8080\nMLX_PORT_RANGE=8100-8199\n"
    )
    r = _run(sys_py, payload, f"""
        from mlx_server import ctx
        c = ctx.build(share_root={str(share)!r}, environ={{}})   # 干净 shell
        print("ok", c.root_data)
    """)
    assert r.returncode == 0, f"干净 shell 下 ctx.build 抛了：{r.stderr}"


def test_gap2_both_sources_missing_raises_and_names_key(payload, sys_py, tmp_path):
    """**两个来源都缺**才抛，且必须指名是哪个键 —— 静默默认值仍要被抓住。"""
    share = tmp_path / "share"; share.mkdir()
    (share / "constants.env").write_text("MLX_ROOT_CODE=/tmp/c\n")   # 缺 DATA/WEIGHTS
    r = _run(sys_py, payload, f"""
        from mlx_server import ctx
        try:
            ctx.build(share_root={str(share)!r}, environ={{}})
            print("NO_RAISE")
        except Exception as e:
            print("RAISED", e)
    """)
    assert "NO_RAISE" not in r.stdout, "两个来源都缺时没抛 —— 可能回落了静默默认值"
    assert "MLX_ROOT_DATA" in r.stdout, f"抛了但没指名键：{r.stdout!r}"


# ── 断口 ③：BCR-A-03 的循环依赖 ⇒ started_at_raw 取标量入参 ──────────

def test_gap3_started_at_raw_takes_scalars_not_ctx(payload):
    """`started_at_raw(pid, ps_path, timeout_sec)` 必须取标量，不得接 `ctx`。

    坏样本：`proc.started_at(pid, ctx)` 作为构造期调用 ⇒ 构造 Ctx 需要 Ctx
    ⇒ 循环依赖，且 Ctx 是 frozen，dataclasses.replace 会绕过校验。
    """
    import ast
    src = (payload / "lib" / "mlx_server" / "proc.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "started_at_raw")
    args = [a.arg for a in fn.args.args]
    assert "ctx" not in args, f"started_at_raw 接了 ctx ⇒ 循环依赖：{args}"
    assert args[:1] == ["pid"], f"第一个形参应是 pid：{args}"


def test_gap3_self_started_at_is_probe_not_str(payload, sys_py, tmp_path):
    """`Ctx.self_started_at` 是 `Probe[str]` 三态，不是裸 str（裁决 R4-4）。

    坏样本：`probe_or("", st)` —— 静态类型是 str、mypy 全绿，
    而 ps 一次超时就写入 ""，下一个进程会清掉活进程的锁。
    """
    share = tmp_path / "share"; share.mkdir()
    (share / "constants.env").write_text(
        "MLX_ROOT_CODE=/tmp/c\nMLX_ROOT_DATA=/tmp/d\nMLX_ROOT_WEIGHTS=/tmp/w\n"
        "MLX_GATEWAY_PORT=8080\nMLX_PORT_RANGE=8100-8199\n"
    )
    r = _run(sys_py, payload, f"""
        from mlx_server import ctx
        c = ctx.build(share_root={str(share)!r}, environ={{}})
        v = c.self_started_at
        print(type(v).__name__, hasattr(v, "is_unknown") or hasattr(v, "value"))
    """)
    assert r.returncode == 0, r.stderr
    assert "str True" not in r.stdout and r.stdout.split()[0] != "str", \
        f"self_started_at 是裸 str，不是 Probe：{r.stdout!r}"


# ── 断口 ④：两个构造符号名各司其职 ─────────────────────────────────

def test_gap4_both_build_and_load_exist(payload):
    """`build()` 是调用方看到的名字，`load()` 是它的纯函数内核 —— 两个都要。"""
    import ast
    src = (payload / "lib" / "mlx_server" / "ctx.py").read_text()
    names = {n.name for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}
    assert "build" in names, "缺 build（边 #9 的调用方看到的名字）"
    assert "load" in names, "缺 load（build 的纯函数内核）"


def test_gap4_load_is_pure_no_io(payload):
    """`load` 是纯函数内核：不做 I/O（不 open、不读 environ、不调 subprocess）。"""
    import ast
    src = (payload / "lib" / "mlx_server" / "ctx.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "load")
    bad = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            nm = getattr(f, "id", None) or getattr(f, "attr", None)
            if nm in {"open", "getenv", "run", "read_text", "environ"}:
                bad.append(f"{nm}@{node.lineno}")
    assert not bad, f"load 里有 I/O，它必须是纯函数内核：{bad}"
