"""spec §3.4 / §4.8 / §5.2：密钥从配置到进程、到请求头、到轮换提示的整条链路。

每条都断言「值不出现在不该出现的地方」—— 只断言「能用」的话，把密钥写进日志的实现也能过。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

KEY = "K" * 43
TOK = "T" * 43
ENVTOK = "E" * 43
OLDKEY = "O" * 43
SUBS = {"KEY": KEY, "TOK": TOK, "ENVTOK": ENVTOK, "OLDKEY": OLDKEY}

SPEC_PROG = WORLD + """
from mlx_server import backendarg, route, supervisor
from mlx_server.ctx import Ctx
from mlx_server.cmd.svc import VALIDATE_STEPS, VALIDATORS, Invocation, run
put_settings({"backend.api_key": "@KEY@", "api.token": "@TOK@"})
deps = {"backendarg_spec": backendarg.spec}
def raw_spec(c):
    state, inv = {"ctx": c}, Invocation("up", "api")
    for step in VALIDATE_STEPS:
        assert VALIDATORS[step](state, inv, deps) is None, step
    return state["spec"]
envctx = Ctx(_ROOT, DATA, WEIGHTS, VENV, 8340, (8100, 8199), None,
             share_root=SHARE, gateway_token_env="@ENVTOK@")
out = {}
for name, c in (("config", ctx), ("env", envctx)):
    dry = run(c, None, Invocation("up", "api", dry_run=True), deps).payload
    spec = raw_spec(c)
    supervisor.write_record(c, spec, 1, 1, 8340, "t", "n")
    with open(os.path.join(DATA, "run", "api.json")) as fh:
        text = fh.read()
    out[name] = {"dry": dry, "raw_env": spec.env, "secret_env": list(spec.secret_env),
                 "record": json.loads(text), "record_text": text}
out["fp"] = {"tok": route.key_fingerprint("@TOK@"), "envtok": route.key_fingerprint("@ENVTOK@"),
             "key": route.key_fingerprint("@KEY@")}
print(json.dumps(out))
"""


def test_api_spec_carries_keys_masked_and_record_stores_only_fingerprints(payload, sys_py):
    got = run_floor(payload, sys_py, SPEC_PROG, **SUBS)
    fp = got["fp"]
    for name, tok, fp_tok, src in (("config", TOK, fp["tok"], "config"),
                                   ("env", ENVTOK, fp["envtok"], "env")):
        g = got[name]
        assert g["dry"]["env"]["MLX_GATEWAY_TOKEN"] == "****"
        assert g["dry"]["env"]["MLX_BACKEND_API_KEY"] == "****"
        assert tok not in str(g["dry"]) and KEY not in str(g["dry"]), "dry-run 泄露了密钥"
        assert g["raw_env"]["MLX_GATEWAY_TOKEN"] == tok, f"{name}：进程拿到的令牌不对"
        assert g["raw_env"]["MLX_BACKEND_API_KEY"] == KEY
        assert sorted(g["secret_env"]) == ["MLX_BACKEND_API_KEY", "MLX_GATEWAY_TOKEN"]
        rec = g["record"]
        assert (rec["token_fp"], rec["backend_key_fp"], rec["token_source"]) == \
            (fp_tok, fp["key"], src)
        assert tok not in g["record_text"] and KEY not in g["record_text"], "运行记录里有密钥原值"
    assert got["config"]["dry"]["sources"]["api.token"] == "machine"
    assert got["env"]["dry"]["sources"]["api.token"] == "cli", "shell 里的 MLX_GATEWAY_TOKEN 应覆盖配置"


GW_PROG = r'''
import json
from gateway.app import Gateway
from mlx_server.result import Ok
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R("vlm", 8100), R("txt", 8101)]
snaps = {"vlm": {"model_arg": "vlm", "backend_auth": True},
         "txt": {"model_arg": "txt", "backend_auth": False}}
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: snaps.get(s),
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
cap = {}
def fwd(port, path, body, headers=None):
    cap[body["model"]] = headers
    return (200, {"model": body["model"]})
g = Gateway(None, facade, backend_key="@KEY@"); g.refresh_routes()
for m in ("vlm", "txt"):
    g.handle("POST", "/v1/chat/completions", body={"model": m}, forward=fwd)
g2 = Gateway(None, facade); g2.refresh_routes()
three_arg = g2.handle("POST", "/v1/chat/completions", body={"model": "txt"},
                      forward=lambda p, pa, b: (200, {"model": b["model"]}))
print(json.dumps({"cap": cap, "three_arg_status": three_arg[0],
                  "headers_vlm": g.upstream_headers("vlm"), "headers_txt": g.upstream_headers("txt")}))
'''


def test_gateway_attaches_backend_key_only_for_auth_backends(payload, sys_py):
    got = run_floor(payload, sys_py, GW_PROG, with_gateway=True, **SUBS)
    assert got["cap"]["vlm"] == {"Authorization": "Bearer " + KEY}
    assert got["cap"]["txt"] is None, "mlx_lm 后端不应收到密钥"
    assert got["three_arg_status"] == 200, "不需要鉴权的转发调用形态应与此前一致"
    assert got["headers_txt"] == {}


SEND_PROG = r'''
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from mlx_server import runtime
seen = []
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        seen.append(self.headers.get("Authorization"))
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
msgs = [{"role": "user", "content": "x"}]
r1 = runtime.send_chat(port, msgs, model="m", api_key="@KEY@")
r2 = runtime.send_chat(port, msgs, model="m")
b = runtime.bench_once(port, "x", model="m", api_key="@KEY@")
srv.shutdown()
print(json.dumps({"seen": seen, "r1": r1, "r2": r2, "bench_ok": b is not None}))
'''


def test_send_chat_and_bench_once_send_bearer_when_given(payload, sys_py):
    got = run_floor(payload, sys_py, SEND_PROG, **SUBS)
    assert got["seen"] == ["Bearer " + KEY, None, "Bearer " + KEY]
    assert got["r1"] == got["r2"] == "ok" and got["bench_ok"]


CLIENT_PROG = WORLD + """
from mlx_server import route, supervisor
from mlx_server.result import Ok
from mlx_server.cmd import bench, chat
from mlx_server.cmd.svc import Invocation, _tool_status
supervisor.running = lambda c, s: Ok(True)      # 只替身「是否在跑」这一个探针
seed("vis", "org/vis", 8102, config={})
seed("txt", "org/txt", 8103, config={})
put_settings({"backend.api_key": "@KEY@", "api.token": "@TOK@"})
os.makedirs(os.path.join(DATA, "run"), exist_ok=True)
def rec(sid, **kw):
    base = {"pid": 999999, "pgid": 999999, "started_at": "t", "schema_ver": 1, "nonce": "n"}
    base.update(kw)
    with open(os.path.join(DATA, "run", sid + ".json"), "w") as fh:
        fh.write(json.dumps(base))
rec("vis", port=8102, model_arg="vis", type="model", backend="mlx_vlm", backend_auth=True,
    max_concurrency=8, key_fp=route.key_fingerprint("@OLDKEY@"))
rec("txt", port=8103, model_arg="txt", type="model", backend="mlx_lm", backend_auth=False,
    max_concurrency=8, key_fp=None)
rec("api", port=8340, model_arg=None, type="api", token_source="config",
    token_fp=route.key_fingerprint("@TOK@"), backend_key_fp=route.key_fingerprint("@KEY@"))
cap = []
def send(port, messages, model=None, **kw):
    cap.append(["send", model, kw.get("api_key")])
    return "ok"
def bench_fn(port, prompt, model=None, **kw):
    cap.append(["bench", model, kw.get("api_key")])
    return {"elapsed_sec": 1.0, "chars": 2, "chars_per_sec": 2.0}
for m in ("vis", "txt"):
    chat.run(ctx, None, Invocation("chat", m, args={"prompt": "hi"}), {"send": send})
    bench.run(ctx, None, Invocation("bench", m, args={"prompt": "hi"}), {"bench": bench_fn})
t1 = _tool_status(ctx).payload
rec("api", port=8340, model_arg=None, type="api", token_source="env",
    token_fp=route.key_fingerprint("@ENVTOK@"), backend_key_fp=route.key_fingerprint("@OLDKEY@"))
t2 = _tool_status(ctx).payload
from mlx_server import ui
from mlx_server.cmd import render_svc  # noqa: F401
print(json.dumps({"cap": cap,
                  "t1": {"rows": {r["id"]: r["key_changed"] for r in t1["models"]},
                         "gw": t1["gateway"], "restart": t1["restart_needed"]},
                  "t2": {"gw": t2["gateway"], "restart": t2["restart_needed"],
                         "text": ui.human(t2)}}))
"""


def test_chat_bench_auth_and_rotation_hint(payload, sys_py):
    got = run_floor(payload, sys_py, CLIENT_PROG, timeout=60, **SUBS)
    assert got["cap"] == [["send", "vis", KEY], ["bench", "vis", KEY],
                          ["send", "txt", None], ["bench", "txt", None]]
    t1 = got["t1"]
    assert t1["rows"] == {"vis": True, "txt": False}
    assert (t1["gw"]["token_state"], t1["gw"]["backend_key_changed"]) == ("ok", False)
    assert t1["restart"] == ["vis"]
    t2 = got["t2"]
    assert t2["gw"]["token_state"] == "env_unknown", "shell 未设 MLX_GATEWAY_TOKEN 时不得判「已变更」"
    assert t2["gw"]["backend_key_changed"] is True
    assert t2["restart"] == ["vis", "api"]
    assert "mlx restart vis api" in t2["text"] and "无法比较" in t2["text"]
    assert KEY not in t2["text"] and TOK not in t2["text"]
