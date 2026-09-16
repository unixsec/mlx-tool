"""spec §4.1 api 校验 / D12 / D15 / D20 / D24。"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402
from tv.sandbox import Sandbox  # noqa: E402
from tv.stubs import Stub  # noqa: E402

TOK = "T" * 43


def _stub(path, body):
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)
    return str(path)


def test_listener_pids_and_port_owner_require_all_pids(payload, sys_py, tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "qwen.json").write_text(json.dumps(
        {"pid": 1000, "pgid": 42973, "port": 8100, "started_at": "x"}))
    ps = _stub(tmp_path / "ps", 'for a; do last=$a; done\n'
                                'if [ "$last" = 42975 ]; then echo 42973; else echo 777; fi')
    stubs = {
        "two": _stub(tmp_path / "l_two", "echo 42975\necho 5555"),
        "worker": _stub(tmp_path / "l_one", "echo 42975"),
        "none": _stub(tmp_path / "l_none", "exit 1"),
        "failed": _stub(tmp_path / "l_fail", "echo boom >&2; exit 1"),
        "garbage": _stub(tmp_path / "l_garb", "echo notapid"),
    }
    got = run_floor(payload, sys_py, """
        import json
        from mlx_server import probe, supervisor
        from mlx_server.ctx import Ctx
        ctx = Ctx("@R@", "@R@", "@R@", "/nonexistent", 8340, (8100, 8199), None)
        S = @STUBS@
        def show(p):
            return ["U", p.kind] if p.is_unknown() else ["O", p.value]
        out = {k: show(probe.listener_pids(8100, v)) for k, v in S.items()}
        out["owner_two"] = show(supervisor.port_owner(ctx, "qwen", lsof_path=S["two"], ps_path="@PS@"))
        out["owner_worker"] = show(supervisor.port_owner(ctx, "qwen", lsof_path=S["worker"], ps_path="@PS@"))
        out["foreign_none"] = show(supervisor.foreign_listeners(ctx, 8100, lsof_path=S["none"]))
        out["foreign_two"] = show(supervisor.foreign_listeners(ctx, 8100, lsof_path=S["two"]))
        out["foreign_failed"] = show(supervisor.foreign_listeners(ctx, 8100, lsof_path=S["failed"]))
        print(json.dumps(out))
    """, R=tmp_path, PS=ps, STUBS=json.dumps(stubs))
    assert got["two"] == ["O", [42975, 5555]]
    assert got["worker"] == ["O", [42975]]
    assert got["none"] == ["U", "NO_LISTENER"] and got["failed"] == ["U", "PS_FAILED"]
    assert got["garbage"] == ["U", "PARSE_FAILED"]
    assert got["owner_two"] == ["O", False], "端口上还有别的监听者时不得判为本服务独占"
    assert got["owner_worker"] == ["O", True]
    assert got["foreign_none"] == ["O", []] and got["foreign_two"] == ["O", [42975, 5555]]
    assert got["foreign_failed"] == ["U", "PS_FAILED"]


VALIDATE = WORLD + """
import subprocess
from mlx_server import backendarg
from mlx_server.cmd.svc import Invocation, run
deps = {"backendarg_spec": backendarg.spec}
# **真证书**（复检 Important 1 要求的第三条判据要真的加载得动，不能靠垫片文本
# 混过去）：`/usr/bin/openssl` 生成到临时目录，不进仓库、不留密钥material。
cert, key = os.path.join(DATA, "cert.pem"), os.path.join(DATA, "key.pem")
subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048",
               "-keyout", key, "-out", cert, "-days", "1", "-nodes",
               "-subj", "/CN=mlxtest"], check=True, capture_output=True)
os.chmod(key, 0o600)
# **读得到、权限对，但内容不是证书** —— 只有真加载一次（`load_cert_chain`）
# 才能拦住它；前两道判据（可读、权限）对它都会放行。
garbage_cert, garbage_key = os.path.join(DATA, "g_cert.pem"), os.path.join(DATA, "g_key.pem")
for p in (garbage_cert, garbage_key):
    with open(p, "w") as fh:
        fh.write("x")
os.chmod(garbage_key, 0o600)
def up(settings):
    put_settings(settings)
    out = run(ctx, None, Invocation("up", "api", dry_run=True), deps)
    return [out.rc, getattr(out, "msg_key", None)]
T = "@TOK@"
r = {"no_token": up({})}
r["cert_only"] = up({"api.token": T, "api.tls_cert": cert})
r["unreadable"] = up({"api.token": T, "api.tls_cert": cert, "api.tls_key": key + ".nope"})
os.chmod(key, 0o644)
r["permissive"] = up({"api.token": T, "api.tls_cert": cert, "api.tls_key": key})
os.chmod(key, 0o600)
r["unusable"] = up({"api.token": T, "api.tls_cert": garbage_cert, "api.tls_key": garbage_key})
r["ok_tls"] = up({"api.token": T, "api.tls_cert": cert, "api.tls_key": key})
r["ok_plain"] = up({"api.token": T})
print(json.dumps(r))
"""


def test_api_validation_in_validate_stage(payload, sys_py):
    got = run_floor(payload, sys_py, VALIDATE, TOK=TOK, timeout=60)
    assert got["no_token"] == [6, "api.token_missing"]
    assert got["cert_only"] == [6, "api.tls_incomplete"]
    assert got["unreadable"] == [6, "api.tls_unreadable"]
    assert got["permissive"] == [6, "api.tls_key_permissive"]
    assert got["unusable"] == [6, "api.tls_unusable"], (
        "读得到、权限对但加载不动的证书/私钥必须在校验段被拒——"
        "tls_context 的第三条判据（`load_cert_chain`）也要独立复核")
    assert got["ok_tls"][0] == 0 and got["ok_plain"][0] == 0


EXECUTE = WORLD + """
from mlx_server import backendarg
from mlx_server.cmd.svc import Invocation, run
from mlx_server.result import Ok, Unknown
put_settings({"api.token": "@TOK@"})
spawned = []
def deps(listeners):
    return {"backendarg_spec": backendarg.spec,
            "foreign_listeners": lambda port: listeners,
            "gateway_interpreter": lambda: {"path": "/opt/venv/bin/python3", "source": "venv",
                                            "version": "3.14.3", "tls_max": "TLSv1.3"},
            "spawn": lambda spec, cwd: (spawned.append(list(spec.argv)),
                                        {"pid": 999999, "pgid": 999999, "started_at": "t"})[1],
            "wait_ready": lambda spec, port, timeout_sec: None,
            "nonce": lambda: "n0"}
r = {}
out = run(ctx, None, Invocation("up", "api"), deps(Ok([4321])))
r["foreign"] = [out.rc, out.msg_key, out.args, len(spawned)]
out = run(ctx, None, Invocation("up", "api"), deps(Unknown("PS_FAILED", "x")))
r["undeterminable"] = [out.rc, out.msg_key, len(spawned)]
out = run(ctx, None, Invocation("up", "api"), deps(Ok([])))
r["ok"] = [out.rc, out.payload, spawned[-1][0] if spawned else None]
with open(os.path.join(DATA, "run", "api.json")) as fh:
    rec = json.load(fh)
r["record"] = {k: rec.get(k) for k in ("bind", "tls", "interpreter", "interpreter_source",
                                        "interpreter_version", "tls_max")}
print(json.dumps(r))
"""


def test_api_execute_stage(payload, sys_py):
    got = run_floor(payload, sys_py, EXECUTE, TOK=TOK)
    rc, key, args, n = got["foreign"]
    assert (rc, key, n) == (7, "ports.foreign_listener", 0), "有外来监听者时不得 spawn"
    assert args["pids"] == "4321" and args["port"] == 8340
    assert got["undeterminable"] == [9, "ports.probe_undeterminable", 0]
    rc, payload_, argv0 = got["ok"]
    assert rc == 0 and argv0 == "/opt/venv/bin/python3", "argv[0] 应换成判定出的网关解释器"
    assert (payload_["bind"], payload_["tls"], payload_["interpreter"]) == \
        ("0.0.0.0", False, "/opt/venv/bin/python3")
    assert got["record"] == {"bind": "0.0.0.0", "tls": False,
                             "interpreter": "/opt/venv/bin/python3", "interpreter_source": "venv",
                             "interpreter_version": "3.14.3", "tls_max": "TLSv1.3"}


INTERP = r"""
import json, os, sys, tempfile
from mlx_server import runtime
from mlx_server.ctx import Ctx
ctx = Ctx("@PAYLOAD@", "/tmp", "/tmp", "/nonexistent", 8340, (8100, 8199), None)
d = tempfile.mkdtemp()
broken = os.path.join(d, "python3")
with open(broken, "w") as fh:
    fh.write("#!/bin/sh\nexit 1\n")
os.chmod(broken, 0o755)
print(json.dumps({"venv": runtime.gateway_interpreter(ctx, "@VENVPY@"),
                  "broken": runtime.gateway_interpreter(ctx, broken),
                  "none": runtime.gateway_interpreter(ctx, None)}))
"""


def test_gateway_interpreter_prefers_importable_venv(payload, sys_py):
    got = run_floor(payload, sys_py, INTERP, timeout=60, VENVPY=sys.executable)
    assert got["venv"]["path"] == sys.executable and got["venv"]["source"] == "venv"
    assert got["venv"]["tls_max"] == "TLSv1.3"
    for name in ("broken", "none"):
        assert got[name]["path"] == "/usr/bin/python3" and got[name]["source"] == "system"
        assert got[name]["version"] == "3.9.6" and got[name]["tls_max"] == "TLSv1.2"


WIRING = r"""
import json, os
from mlx_server import backendarg, runtime
from mlx_server.cmd.svc import Invocation, run
from mlx_server.ctx import Ctx
from mlx_server.result import Ok
# **`root_code` 是真的产品 payload**（不是 WORLD 的空 tempdir）——
# `gateway_interpreter` 探测子进程要 `import gateway.app, gateway.server`，
# 那个包只存在于这里；这也是 build_deps 生产实现里 venv_py 真正会被
# 接去跑的路径（复检 Important 2：直调 `gateway_interpreter` 或在执行段
# 桩掉它都测不到 `build_deps` 那一行「真的把 `ctx.venv` 接过去」）。
ctx = Ctx("@PAYLOAD@", "@DATA@", "@DATA@", "@VENV@", 8340, (8100, 8199), None,
          share_root="@PAYLOAD@/share")
os.makedirs("@DATA@", exist_ok=True)
with open(os.path.join("@DATA@", "settings.json"), "w") as fh:
    fh.write(json.dumps({"api.token": "T" * 43}))
deps = runtime.build_deps(ctx)          # **真实装配点**，不是手写字典
deps["backendarg_spec"] = backendarg.spec
# **外来监听者检查要打桩**（账本 A3）：不桩它，这条用例会对**真机的 8340 端口**
# 发一次没打桩的 `lsof` —— 串行跑时极少撞上，并行化立刻暴露；而且它测的是
# 解释器接线，与端口上有没有别人无关。
deps["foreign_listeners"] = lambda port: Ok([])
spawned = []
deps["spawn"] = lambda spec, cwd: (spawned.append(list(spec.argv)),
                                   {"pid": 999999, "pgid": 999999, "started_at": "t"})[1]
deps["wait_ready"] = lambda spec, port, timeout_sec: None
out = run(ctx, None, Invocation("up", "api"), deps)
with open(os.path.join("@DATA@", "run", "api.json")) as fh:
    rec = json.load(fh)
print(json.dumps({"rc": out.rc, "key": getattr(out, "msg_key", None),
                  "argv0": spawned[0][0] if spawned else None,
                  "interpreter": rec.get("interpreter"),
                  "interpreter_source": rec.get("interpreter_source")}))
"""


def test_gateway_interpreter_wiring_reaches_ctx_venv(payload, sys_py, tmp_path):
    """`runtime.build_deps` 里那一行 `gateway_interpreter(ctx, venv_py)` 必须真的把
    `ctx.venv` 接到探测上（spec 519 行）—— 这是复检 Important 2 点名的缝：
    直调 `runtime.gateway_interpreter` 自己传 `venv_python`（上一条用例）、
    执行段测试自己注入 `gateway_interpreter` 桩（`test_api_execute_stage`）、
    `test_d6` 只查键存在，三者都测不到 `build_deps` 那一行真的接通了 `ctx.venv`。

    坏样本：那一行被换成 `gateway_interpreter(ctx, None)`（D20 之前的硬编码行为）——
    网关会悄悄跑在 `/usr/bin/python3` 上、TLS 封顶 1.2，而运行记录仍然老实写下
    `interpreter_source: "system"`，此前全套件不会有任何一条因此变红。
    """
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    wrapper = venv_bin / "python3"
    # **零依赖包装脚本**：网关本体只用标准库（backendarg.spec 的注释原话），
    # 任何 python3 都 import 得动，所以不需要真的建一个不同版本的 venv——
    # 只要证明 `ctx.venv` 这条路径被接上了、被真的拿去跑了探测子进程。
    wrapper.write_text('#!/bin/sh\nexec /usr/bin/python3 "$@"\n')
    wrapper.chmod(0o755)

    got = run_floor(payload, sys_py, WIRING, timeout=30,
                    DATA=str(tmp_path / "data"), VENV=str(tmp_path / "venv"))
    assert got["rc"] == 0, got
    assert got["interpreter_source"] == "venv", (
        "build_deps 没把 ctx.venv 接到 gateway_interpreter 上：" + json.dumps(got))
    assert got["interpreter"] == got["argv0"] == str(wrapper), got


def test_api_dry_run_makes_zero_external_calls_including_lsof(payload, tmp_path):
    """`DE-T-018` 的求值面从没盖过 api target、也没盖过 `lsof`（复检 Important 3）：

    `tests/unit/test_stage_split.py:67` 只数 target `qwen` 的 `caps_drift` 调用；
    `tests/integration/test_l2_proc.py` 的 dry-run 用例只数 target `qwen` 的
    `MLX_BIN_PS` 账本；e2e 的 dry-run 用例只做文件系统 diff。三者都测不到
    「`foreign_listeners`（本任务新增，唯一会调 `lsof` 的检查）被错放进校验段」
    这一种回归——`mlx up api --dry-run` 会因此真的 shell 出 `lsof`，而这三条
    判据全部保持绿。这里直接用真实 `bin/mlx` 子进程 + `MLX_BIN_LSOF` 桩，
    断言桩账本为空。
    """
    sb = Sandbox(tmp_path).write_constants()
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"api.token": TOK}))
    stub = Stub()
    env = sb.env(payload)
    env["MLX_BIN_LSOF"] = stub.as_ps_path(tmp_path)
    env["MLX_STUB_KEY"] = "lsof.listen.free"
    env["MLX_STUB_LEDGER"] = stub.ledger_path(tmp_path)
    r = subprocess.run([str(payload / "bin" / "mlx"), "up", "api", "--dry-run"],
                       capture_output=True, text=True, timeout=20, check=False, env=env)
    assert r.returncode == 0, f"rc={r.returncode} stdout={r.stdout} stderr={r.stderr}"
    assert Stub.calls(tmp_path) == [], (
        f"`up api --dry-run` 调用了外部命令（lsof 桩记了账）：{Stub.calls(tmp_path)}")
