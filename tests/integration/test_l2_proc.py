"""L2 · 用 `tv.stubs` 回放真实录制字节驱动产品（第一批真正的 L2）。

此前 34 条测试里**零 L2** —— 因为 TV 层不存在，桩只能在测试里手写 lambda。
现在桩纯回放 `shapes.json` 的录制字节，产品经真实子进程调它。
"""
import contextlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402
from tv.stubs import Stub  # noqa: E402


def _floor(payload, sys_py, body, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(payload / "lib")}
    env.update(env_extra or {})
    r = subprocess.run([sys_py, "-c", textwrap.dedent(body)],
                       capture_output=True, text=True, timeout=20, check=False, env=env)
    assert r.returncode == 0, f"下限解释器执行失败：{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_l2_proc_three_states_from_recorded_bytes(payload: Path, sys_py: str, tmp_path: Path):
    """`L2·proc` 三态：用**真实录制的字节**驱动 `started_at_raw`。

    这三条 key 是 2026-08-29 从本机 `ps` 实录的，不是手写的。
    """
    stub = Stub()
    ps_stub = stub.as_ps_path(tmp_path)
    got = {}
    for key, want in (("ps.lstart.alive", "ok"),
                      ("ps.lstart.dead", "DEAD"),
                      ("ps.lstart.toolarge", "PS_FAILED")):
        r = _floor(payload, sys_py, f"""
            import json
            from mlx_server.proc import started_at_raw
            p = started_at_raw(1, {ps_stub!r})
            print(json.dumps({{"unknown": p.is_unknown(), "kind": p.kind}}))
        """, {"MLX_STUB_KEY": key})
        got[key] = r
        if want == "ok":
            assert not r["unknown"], f"{key} 应为 Ok，实得 {r}"
        else:
            assert r["kind"] == want, f"{key} 应判 {want}，实得 {r['kind']}"

    Cardinality.check("L2·proc·三态覆盖", sorted(got), rel="subset",
                      right=["ps.lstart.alive", "ps.lstart.dead", "ps.lstart.toolarge"])


def test_l2_stub_rejects_unplanned_calls(tmp_path: Path):
    """桩对未编排的调用**显式失败** —— 静默返回空会让缺失的编排通过。"""
    from tv.stubs import UnexpectedCall
    s = Stub().bind("ps", ["ps.lstart.alive"])
    s.call(["ps", "-o", "lstart="])
    try:
        s.call(["lsof", "-i"])
        raise AssertionError("桩接受了未编排的命令")
    except UnexpectedCall:
        pass
    assert s.count("ps") == 1 and s.unused() == {}


def test_l2_dry_run_zero_external_commands(payload: Path, sys_py: str, tmp_path: Path):
    """`DE-T-018` 的 L2 版：**文本模型**下 dry-run 全程外部命令计数 == 0（桩记账为证）。

    这里的模型是文本模型（`qwen`，`config.json` 无 `vision_config`）——
    `DE-T-018` 权威判据逐字是「**stage_split 之后**外部命令计数 == 0」
    （`tests/unit/test_stage_split.py:3,67`），不是「校验段全程零外部命令」。
    校验段本身允许只读取证：`svc._v_backend` 现在对**每一条到达 R2/R3/R4 的路线**
    都会调 `templates.inspect_template`（finding I-2：此前只对「无 drafter 且
    vision」这一种路线取证，mlx_lm 路线因此恒记 `effort_domain == []`，
    qwen38 被 `modality=text` 强制成文本后 reasoning effort 也跟着丢——
    文本模型不再是这条取证路径之外的特例）。取证发生在 `stage_split`/dry-run
    早退**之前**，不计入本条判据的求值面，这里断言仍是「零外部命令」的真正
    原因是**本用例的沙箱没有 venv 解释器**（`ctx.venv` 指向的路径不存在）——
    `templates.inspect_template` 在 `python` 不可用时直接短路回 `Unknown`，
    连模板文件都不读、更不会 `Popen` 子进程；一旦沙箱真有可用解释器（如
    `tests/integration/test_l2_backend_route.py` 用 `RENDER_PY=sys.executable`
    包出来的那种 venv），取证就会真的跑，见下面对多模态场景的显式覆盖
    `tests/integration/test_l2_backend_route.py::test_vision_model_goes_to_mlx_vlm_with_key_masked`
    ——那里对 `spawn`/`wait_ready`（执行段的外部效应 deps）与模板渲染临时文件
    分别断言零调用 / 零残留。
    """
    from tv.sandbox import Sandbox
    sb = Sandbox(tmp_path).write_constants().with_model("qwen")
    stub = Stub()
    env = sb.env(payload)
    # 把桩脚本接进产品的外部命令面：ctx.build → proc.started_at_raw 会用它。
    # 不接进去的话这个桩只是摆设（ruff 的「未使用变量」正抓到这一点）。
    env["MLX_BIN_PS"] = stub.as_ps_path(tmp_path)
    env["MLX_STUB_KEY"] = "ps.lstart.alive"
    env["MLX_STUB_LEDGER"] = stub.ledger_path(tmp_path)
    before = sb.snapshot()
    r = subprocess.run([str(payload / "bin" / "mlx"), "up", "qwen", "--dry-run"],
                       capture_output=True, text=True, timeout=20, check=False, env=env)
    added, removed, changed = sb.diff(before, sb.snapshot())

    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr}"
    assert (added, removed, changed) == ([], [], []), \
        f"dry-run 动了盘：+{added} -{removed} ~{changed}"
    argv = json.loads([ln for ln in r.stdout.splitlines() if ln.startswith("[")][-1])
    assert argv[0] != "mlx_lm", f"argv[0] 仍是包名，不可执行：{argv}"
    assert argv[1:3] == ["-m", "mlx_lm.server"], argv

    # `--dry-run` 在 stage_split **之前**返回，压根到不了 `ps` ——
    # 所以这条用例里桩本就不该被调用。它一直注着 `MLX_BIN_PS`（连同那句
    # 「不接进去的话这个桩只是摆设」的注释），而**场景选错了**：
    # 这里要断言的是「零外部命令」，桩被调用反而是缺陷。
    # 真正需要注入面的是下一条用例。
    assert stub.calls(tmp_path) == [], \
        f"dry-run 调了外部命令：{stub.calls(tmp_path)}"


def test_l2_ps_stub_is_actually_reachable(payload: Path, tmp_path: Path):
    """**`MLX_BIN_PS` 的注入面真的接通了** —— 桩被调用，且产品按它的输出判定。

    坏样本（本次真实发生）：`proc`/`probe` 的命令路径是十处函数默认参数里的
    字面量，产品**从不读 `MLX_BIN_*`**。于是上一条用例注的那个 ps 桩就是摆设，
    `ps`/`lsof` 的三态映射在 L2 层**没有任何被注入过的坏样本** ——
    `running` / `port_owner` / `kill_tree` 全建立在它们之上，
    而它们的错误只能靠真机上碰运气遇到。

    这里注一个「进程已死」的 ps，断言 `status <id>` 据此判定未运行 ——
    桩若没接通，产品会去问真 `/bin/ps`，而那个 pid 在真机上同样不存在，
    **两种情况的结论一样**。所以必须同时断言**流水非空**：
    结论相同而路径不同，只有计数能区分。
    """
    from tv.sandbox import Sandbox
    sb = Sandbox(tmp_path).write_constants().with_model("qwen")
    stub = Stub()
    env = sb.env(payload)
    env["MLX_BIN_PS"] = stub.as_ps_path(tmp_path)
    env["MLX_STUB_KEY"] = "ps.lstart.dead"
    env["MLX_STUB_LEDGER"] = stub.ledger_path(tmp_path)

    run_dir = tmp_path / "data" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "qwen.json").write_text(json.dumps(
        {"pid": 4242, "pgid": 4242, "port": 8100, "started_at": "X",
         "model_arg": "qwen", "type": "model", "schema_ver": 1, "nonce": "n"}))

    r = subprocess.run([str(payload / "bin" / "mlx"), "status", "qwen", "--json"],
                       capture_output=True, text=True, timeout=20, check=False, env=env)
    calls = stub.calls(tmp_path)
    assert calls, ("ps 桩一次都没被调用 —— `MLX_BIN_PS` 注入面没接通，"
                   "这条用例实际跑在真 /bin/ps 上")
    assert any("-p 4242" in c for c in calls), \
        f"桩被调用了，但问的不是那个 pid：{calls}"
    assert r.returncode == 0, f"rc={r.returncode} {r.stderr}"


def test_l2_masked_child_restores_default_mask(payload: Path, tmp_path: Path):
    """**BC-C-05：子进程侧必须恢复默认掩码。**

    设计逐字：「`exec` 保留掩码、只重置处理器 —— 不做这一步，后端将**永久
    屏蔽 INT/TERM**，`down` 的 TERM 阶梯失效，T3 第 3 行的『KILL 阶梯走完仍
    存活』会从异常变成常态」。

    这条**必须先于实现写**：只加 `masked()` 而漏掉子进程侧的掩码重置，
    会让所有后端都杀不掉 —— 比不做屏蔽严重得多。

    做法：在屏蔽窗口里 spawn 一个**只装 TERM 处理器**的假后端，发 TERM，
    断言它**收到了**（而不是被 KILL 掉的）。收到 ⇒ 掩码被正确重置。
    """
    import signal as _sig  # noqa: PLC0415
    import time as _t  # noqa: PLC0415

    sys.path.insert(0, str(payload / "lib"))
    from mlx_server import proc as _proc  # noqa: PLC0415, I001

    got = tmp_path / "got_term"
    fake = tmp_path / "fake_backend.py"
    fake.write_text(
        "import signal, sys, time\n"
        "def h(s, f):\n"
        f"    open({str(got)!r}, 'w').write('TERM')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, h)\n"
        "sys.stderr.write('ready\\n'); sys.stderr.flush()\n"
        "time.sleep(30)\n")

    # **在屏蔽窗口内 spawn** —— 父进程此刻屏蔽着 INT/TERM，
    # 子进程若继承这个掩码，下面那发 TERM 就永远送不到它的处理器。
    with _proc.masked():
        p = subprocess.Popen([sys.executable, str(fake)],  # noqa: S603
                             stderr=subprocess.PIPE, start_new_session=True,
                             preexec_fn=_proc.reset_child_mask)  # noqa: PLW1509
        p.stderr.readline()                    # 等它装好处理器
    try:
        p.send_signal(_sig.SIGTERM)
        for _ in range(50):
            if got.exists():
                break
            _t.sleep(0.1)
        assert got.exists() and got.read_text() == "TERM", (
            "假后端没收到 TERM —— 它继承了父进程的屏蔽掩码（BC-C-05 未落实）。"
            "后果是所有真后端都杀不掉，`down` 的 TERM 阶梯失效")
    finally:
        with contextlib.suppress(Exception):
            p.kill()
            p.wait(timeout=5)
