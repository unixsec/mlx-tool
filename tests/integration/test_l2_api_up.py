"""spec §4.1 api 校验 / D12 / D15 / D20 / D24。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

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
from mlx_server import backendarg
from mlx_server.cmd.svc import Invocation, run
deps = {"backendarg_spec": backendarg.spec}
cert, key = os.path.join(DATA, "cert.pem"), os.path.join(DATA, "key.pem")
for p in (cert, key):
    with open(p, "w") as fh:
        fh.write("x")
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
r["ok_tls"] = up({"api.token": T, "api.tls_cert": cert, "api.tls_key": key})
r["ok_plain"] = up({"api.token": T})
print(json.dumps(r))
"""


def test_api_validation_in_validate_stage(payload, sys_py):
    got = run_floor(payload, sys_py, VALIDATE, TOK=TOK)
    assert got["no_token"] == [6, "api.token_missing"]
    assert got["cert_only"] == [6, "api.tls_incomplete"]
    assert got["unreadable"] == [6, "api.tls_unreadable"]
    assert got["permissive"] == [6, "api.tls_key_permissive"]
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
