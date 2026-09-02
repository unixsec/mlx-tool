"""L2 · C 层七个模块的关键行为，全部经 3.9.6 下限解释器驱动。"""
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402


def _floor(payload, sys_py, body, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(payload / "lib")}
    env.update(env_extra or {})
    # 逐行 dedent：CTX 是无缩进的，追加块有缩进，整体 dedent 会失效
    prog = "\n".join(textwrap.dedent(part) for part in body.split("\n@@\n"))
    r = subprocess.run([sys_py, "-c", prog],
                       capture_output=True, text=True, timeout=25, check=False, env=env)
    assert r.returncode == 0, f"下限解释器执行失败：{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


CTX = '''
import json, os
from mlx_server.result import Ok
class C:
    root_code = {r!r}; root_data = {r!r}; root_weights = {w!r}
    venv = os.path.join({r!r}, "venv")
    gateway_port = 8080; port_range = (8100, 8103)
    self_started_at = Ok("Sat Aug 30 01:00:00 2026")
ctx = C()
'''


def test_l2_store_never_rebuilds_empty(payload, sys_py, tmp_path):
    """**INV-16**：整文件不可解析 ⇒ 抛，绝不空表重建（v6 的数据丢失路径）。"""
    d = tmp_path / "d"; d.mkdir(); (tmp_path / "w").mkdir()
    (d / "models.jsonl").write_text("{ 坏\n} 也坏\n")
    got = _floor(payload, sys_py, CTX.format(r=str(d), w=str(tmp_path / "w")) + '\n@@\n' + '''
        from mlx_server import store
        try:
            store.load(ctx, store.MODELS); print(json.dumps({"raised": False}))
        except store.StoreParseError:
            print(json.dumps({"raised": True}))
    ''')
    assert got["raised"], "整文件坏行时没抛 ⇒ 可能返回了空表（数据丢失路径）"


def test_l2_store_bad_line_reports_lineno(payload, sys_py, tmp_path):
    """坏行**跳过并报行号** —— 「静默丢数据」与「跳过并指名」是两回事。"""
    d = tmp_path / "d"; d.mkdir(); (tmp_path / "w").mkdir()
    (d / "models.jsonl").write_text('{"id":"ok1"}\n坏行\n{"id":"ok2"}\n')
    got = _floor(payload, sys_py, CTX.format(r=str(d), w=str(tmp_path / "w")) + '\n@@\n' + '''
        from mlx_server import store
        res = store.load(ctx, store.MODELS)
        print(json.dumps({"ids": [r.id for r in res.records],
                          "bad": [(b.lineno, b.reason_key) for b in res.bad_lines]}))
    ''')
    assert got["ids"] == ["ok1", "ok2"]
    assert got["bad"] == [[2, "store.bad_json"]], f"坏行报告不对：{got['bad']}"


def test_l2_atomic_lock_refuses_unknown_started_at(payload, sys_py, tmp_path):
    """**裁决 R4-4**：`self_started_at` 不可证 ⇒ 释放锁并抛，绝不写占位符。

    坏样本：写 `""` ⇒ 下一个进程读到内容非空、比较不等 ⇒ 判 RECYCLED
    ⇒ **清掉一个活进程的锁** ⇒ 两个进程同写 models.jsonl。
    """
    d = tmp_path / "d"; d.mkdir(); (tmp_path / "w").mkdir()
    got = _floor(payload, sys_py, f'''
        import json, os
        from mlx_server import atomic
        from mlx_server.cleanup import CleanupStack
        from mlx_server.result import Unknown
        class C: self_started_at = Unknown("PS_FAILED", "ps 挂了")
        target = os.path.join({str(d)!r}, "models.jsonl")
        raised = False
        try:
            with atomic.lock(target, CleanupStack(), C()): pass
        except atomic.StaleLockUnknown:
            raised = True
        lockdir = os.path.join({str(d)!r}, ".models.jsonl.lock")
        print(json.dumps({{"raised": raised, "lock_left": os.path.exists(lockdir)}}))
    ''')
    assert got["raised"], "started_at 不可证时没抛"
    assert not got["lock_left"], "锁未释放 ⇒ 后续所有命令会被这把锁挡死"


def test_l2_port_owner_uses_pgid_membership(payload, sys_py, tmp_path):
    """**裁决 R1-5**：pgid 归属判据，不是对二元组做集合成员测试。

    场景（launcher/worker 型后端）：记录 pid=1000 pgid=42973，
    而端口上监听的是 worker pid=42975 —— 它既 != 记录 pid，也 != 记录 pgid。
    · 二元集合判据（错）：42975 ∉ {1000, 42973} ⇒ Ok(False) ⇒ may_kill 的否决票被触发
    · pgid 归属判据（对）：pgid_of(42975) == 42973 ⇒ Ok(True)

    两个探针各用一个专属桩脚本，避免共用一份回放字节。
    """
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    (run / "qwen.json").write_text(json.dumps(
        {"pid": 1000, "pgid": 42973, "port": 8100, "started_at": "x"}))

    lsof = tmp_path / "lsof_stub"
    lsof.write_text("#!/bin/bash\necho 42975\n")        # 监听者是 worker
    lsof.chmod(0o755)
    ps = tmp_path / "ps_stub"
    ps.write_text("#!/bin/bash\nprintf '%5d\\n' 42973\n")  # 它的 pgid == 记录 pgid
    ps.chmod(0o755)

    got = _floor(payload, sys_py,
                 CTX.format(r=str(tmp_path), w=str(tmp_path / "weights")) + "\n@@\n" + f'''
        from mlx_server import supervisor
        out = supervisor.port_owner(ctx, "qwen", lsof_path={str(lsof)!r}, ps_path={str(ps)!r})
        print(json.dumps({{"unknown": out.is_unknown(),
                           "value": None if out.is_unknown() else out.value}}))
    ''')
    assert got["unknown"] is False, f"探针失败：{got}"
    assert got["value"] is True, (
        "port_owner 判成 False —— 说明用的是二元集合成员测试而非 pgid 归属判据，"
        "launcher/worker 型后端上会误触发 may_kill 的否决票"
    )


def test_l2_vault_refuses_code_root(payload, sys_py, tmp_path):
    """**裁决 R1-2**：`ctx.root_code` 不在整根删除授权集合内（INV-08）。"""
    for d in ("code", "data", "weights"):
        (tmp_path / d).mkdir()
    got = _floor(payload, sys_py, f'''
        import json, os
        from mlx_server import vault
        from mlx_server.result import Ok
        class C:
            root_code = {str(tmp_path / "code")!r}; root_data = {str(tmp_path / "data")!r}
            root_weights = {str(tmp_path / "weights")!r}; venv = "/nonexistent"
        out = vault.purge(C(), C.root_code)
        print(json.dumps({{"rc": out.rc, "key": getattr(out, "msg_key", None),
                           "still_there": os.path.isdir(C.root_code)}}))
    ''')
    assert got["rc"] == 6 and got["still_there"], f"代码根被删或未拒：{got}"


def test_l2_weights_size_follows_symlinks(payload, sys_py, tmp_path):
    """**T-07**：`size()` 逐文件 stat 求和，不是 `du`。

    HF snapshot 全是指向 blobs/ 的符号链接，`du` 只算链接本身（每个约百字节）
    ⇒ 28GB 模型报成几 KB ⇒ 容量判档永远 FIT。
    """
    blobs = tmp_path / "blobs"; blobs.mkdir()
    snap = tmp_path / "snapshots" / "abc"; snap.mkdir(parents=True)
    (blobs / "b1").write_bytes(b"x" * 100_000)
    (snap / "model.bin").symlink_to(blobs / "b1")
    got = _floor(payload, sys_py, f'''
        import json
        from mlx_server.weights import size
        print(json.dumps({{"size": size({str(snap)!r}).value}}))
    ''')
    assert got["size"] == 100_000, f"符号链接未被跟随（du 语义）：{got['size']}"
    Cardinality.records.append(("weights.size·follows_symlink", 1, None, "informational"))


def test_l2_stale_lock_is_cleaned_but_live_lock_is_not(payload: Path, sys_py: str, tmp_path: Path):
    """陈旧锁**按持有者存活判定后清理**；持有者活着时**等待而不是抢**。

    坏样本（本次真实发生）：`store.commit` 调 `atomic.lock` 时**不传 `owns`**，
    于是 `atomic.py` 里那整段四态判定是死代码 —— 写注册表期间进程被强杀留下的
    锁目录永远没人清，此后**每一条**写命令都等满 10 秒再抛 `LockTimeout`。
    清理机制就在那里，只是永不触发；用户唯一的出路是手工删一个他不知道
    存在的隐藏目录。

    反方向同样要守：`Unknown`（ps 自己出错）**不得**当成「已死」——
    塌了会清掉一个活进程正持有的锁，两个进程同时改注册表。
    """
    from tv.sandbox import Sandbox
    sb = Sandbox(tmp_path).write_constants()
    env = sb.env(payload)
    data = tmp_path / "data"
    lockdir = data / ".models.jsonl.lock"

    def _register(name, port):
        return subprocess.run(
            [str(payload / "bin" / "mlx"), "model", "register", name,
             f"--repo=o/{name}", "--type=text", f"--port={port}"],
            capture_output=True, text=True, timeout=30, check=False, env=env)

    # ① 死持有者 ⇒ 清掉并继续（**不等满超时**）
    lockdir.mkdir(parents=True, exist_ok=True)
    (lockdir / "holder").write_text("99999\nX\n")     # 一个确定不存在的 pid
    t0 = time.time()
    r = _register("a", 8151)
    assert r.returncode == 0, f"陈旧锁没被清理：rc={r.returncode} {r.stderr}"
    assert time.time() - t0 < 5, "清理了但仍等满了超时 ⇒ 走的不是清理路径"

    # ② 活持有者 ⇒ 等待、超时、**锁保留**
    live = subprocess.Popen(["sleep", "30"])
    try:
        lockdir.mkdir(parents=True, exist_ok=True)
        (lockdir / "holder").write_text(f"{live.pid}\nX\n")
        r2 = _register("b", 8152)
        assert r2.returncode == 7, f"活锁应 rc 7（资源冲突），实得 {r2.returncode}"
        assert lockdir.exists(), "活进程持有的锁被抢了 —— 两个进程会同时改注册表"
    finally:
        live.kill()
        live.wait()


def test_l2_download_success_path_returns_done(payload: Path, sys_py: str, tmp_path: Path):
    """**下载的成功路径要真的被执行一次**，并断言它产出 `Done` 且权重落在规划目录。

    坏样本（真实发生，用户在真机上撞到）：`weights.download` 的成功路径
    没有 return，函数走完隐式返回 `None`，调用方 `out.rc` 当场
    `AttributeError: 'NoneType' object has no attribute 'rc'`。

    **为什么 150 项全绿却没抓到**：测试环境没有真 `hf`，`download` 永远走
    失败路径 —— 成功路径**一次都没被执行过**。这与「空桩藏在空夹具后面」
    同族，方向相反：那次是夹具让分支走不到，这次是**环境**让分支走不到。

    做法：注一个假的 `fetch`（`fetch` 本来就是注入点，L2 用桩、L4 用真 hf），
    让它在临时目录里造出文件并报 ok。这样成功路径就被真的走了一遍。
    """
    got = _floor(payload, sys_py, f'''
        import json, os
        from mlx_server import weights
        from mlx_server.ctx import Ctx
        d = {str(tmp_path)!r}
        ctx = Ctx(d, d, d, os.path.join(d, "venv"), 8340, (8100, 8199), None)

        def fake_fetch(repo, revision, dest):
            """假下载器：在 dest 里造两个文件，报成功。"""
            os.makedirs(dest, exist_ok=True)
            open(os.path.join(dest, "config.json"), "w").write("{{}}")
            open(os.path.join(dest, "model.safetensors"), "w").write("x" * 1024)
            return {{"ok": True, "revision": "main"}}

        out = weights.download(ctx, "org/demo", None, fake_fetch)
        # 成功路径必须产出 Outcome，不是 None
        info = {{"is_none": out is None}}
        if out is not None:
            info["rc"] = out.rc
            info["path"] = (out.payload or {{}}).get("path", "")
            # 落点必须是规划目录，且文件真的在那儿
            info["landed"] = os.path.exists(
                os.path.join(info["path"], "model.safetensors"))
        print(json.dumps(info))
    ''')
    assert not got["is_none"], (
        "download 的成功路径返回了 None —— 调用方 `out.rc` 会 AttributeError。"
        "这条路径此前从没被执行过（测试环境没有真 hf）")
    assert got["rc"] == 0, f"下载成功却不是 rc 0：{got}"
    assert "hub/models--org--demo/snapshots/main" in got["path"], \
        f"没落在规划目录：{got['path']}"
    assert got["landed"], "报了成功，但权重文件不在它说的那个位置"
