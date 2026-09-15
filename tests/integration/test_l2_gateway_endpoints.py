"""spec §4.2：网关按目标后端能力开放端点；非白名单与不支持的端点后端零请求。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import WORLD, run_floor  # noqa: E402

TOK = "t" * 43

GW = r"""
import json
from gateway.app import Gateway
from mlx_server.result import Ok
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R("vlm", 8100), R("vlm2", 8102), R("txt", 8101), R("old", 8103)]
EP_V = ["chat", "responses", "messages"]
snaps = {"vlm": {"model_arg": "vlm", "backend": "mlx_vlm", "endpoints": EP_V},
         "vlm2": {"model_arg": "vlm2", "backend": "mlx_vlm", "endpoints": EP_V},
         "txt": {"model_arg": "txt", "backend": "mlx_lm", "endpoints": ["chat", "completions"]},
         "old": {"model_arg": "old"}}
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: snaps.get(s),
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
g = Gateway(None, facade, token="@TOK@")
g.refresh_routes()
H = {"Content-Type": "application/json", "Authorization": "Bearer @TOK@"}
calls, canned = [], {}
def fwd(port, path, body, method="POST", timeout_sec=600, headers=None, host="127.0.0.1"):
    calls.append([port, method, path])
    return canned.get((port, method, path),
                      (200, {"id": "resp_1", "model": (body or {}).get("model") or "BACKEND"}))
def post(path, model):
    return g.handle("POST", path, headers=H, body={"model": model}, forward=fwd)
r = {}
st, _ = post("/chat/completions", "txt")
r["chat_no_v1"] = [st, calls[-1]]
n = len(calls); st, body = post("/v1/completions", "vlm")
r["completions_on_vlm"] = [st, body["error"]["type"], len(calls) - n]
n = len(calls); r["messages_on_txt"] = [post("/v1/messages", "txt")[0], len(calls) - n]
n = len(calls); r["legacy_completions"] = [post("/v1/completions", "old")[0], len(calls) - n]
n = len(calls)
r["blocked"] = {p: post(p, "vlm") for p in ("/unload", "/v1/settings", "/v1/embeddings",
                                            "/v1/images/generations")}
r["blocked"] = {p: [v[0], v[1]["error"]["type"]] for p, v in r["blocked"].items()}
r["blocked_calls"] = len(calls) - n
r["get_chat"] = g.handle("GET", "/v1/chat/completions", headers=H, forward=fwd)[0]
canned[(8100, "GET", "/v1/responses/resp_1")] = (404, {})
canned[(8102, "GET", "/v1/responses/resp_1")] = (200, {"id": "resp_1", "model": "vlm2"})
n = len(calls); st, body = g.handle("GET", "/v1/responses/resp_1", headers=H, forward=fwd)
r["fanout_get"] = [st, body.get("model"), sorted({c[0] for c in calls[n:]})]
n = len(calls)
r["delete"] = [g.handle("DELETE", "/v1/responses/resp_1", headers=H, forward=fwd)[0],
               sorted({c[1] for c in calls[n:]})]
n = len(calls)
r["input_items"] = [g.handle("GET", "/v1/responses/resp_1/input_items?limit=2", headers=H,
                             forward=fwd)[0], sorted({c[2] for c in calls[n:]})]
r["bad_id"] = g.handle("GET", "/v1/responses/..", headers=H, forward=fwd)[0]
n = len(calls); st, _ = post("/v1/messages?beta=true", "vlm")
r["body_route_query_dropped"] = [st, calls[-1][2] if len(calls) > n else None]
print(json.dumps(r))
"""


def test_gateway_endpoint_routing(payload, sys_py):
    got = run_floor(payload, sys_py, GW, with_gateway=True, TOK=TOK)
    assert got["chat_no_v1"] == [200, [8101, "POST", "/v1/chat/completions"]], "不带 /v1 应规范化后转发"
    assert got["completions_on_vlm"] == [404, "endpoint_not_supported_by_backend", 0]
    assert got["messages_on_txt"] == [404, 0]
    assert got["legacy_completions"] == [200, 1], "没有 endpoints 字段的旧记录按 mlx_lm 处理"
    assert all(v == [404, "endpoint_not_supported"] for v in got["blocked"].values()), got["blocked"]
    assert got["blocked_calls"] == 0, "非白名单端点不得打到后端"
    assert got["get_chat"] == 404
    st, model, ports = got["fanout_get"]
    assert (st, model) == (200, "vlm2"), "扇出应返回 2xx 的那个后端，并把 model 改回短 id"
    assert 8102 in ports and set(ports) <= {8100, 8102}, "扇出只打支持 responses 的后端"
    assert got["delete"] == [200, ["DELETE"]]
    assert got["input_items"] == [200, ["/v1/responses/resp_1/input_items?limit=2"]], "查询串要带上"
    assert got["bad_id"] == 404
    assert got["body_route_query_dropped"] == [200, "/v1/messages"], \
        "按 body 路由的端点转发时应丢掉查询串（mlx_lm 按路径精确匹配）"


RECORD = WORLD + """
from mlx_server import backendarg, route, schema, supervisor
from mlx_server.tables import TablesView
tv = TablesView(SHARE)
B = {r["backend"]: r for r in tv.backends()}
v = schema.defaults_from(tv)
v.update({"port": 8123})
cfg = schema.ServiceConfig("m", v, {}, "/py")
out = {}
for name in ("mlx_lm", "mlx_vlm"):
    rt = route.Route(name, B[name], "R?", False, [], "", 1.0, 8, None)
    supervisor.write_record(ctx, backendarg.spec("m", cfg, rt), 1, 1, 8123, "t", "n")
    with open(os.path.join(DATA, "run", "m.json")) as fh:
        out[name] = json.load(fh)["endpoints"]
print(json.dumps(out))
"""


def test_run_record_endpoints_come_from_backends_table(payload, sys_py):
    got = run_floor(payload, sys_py, RECORD)
    assert got == {"mlx_lm": ["chat", "completions"], "mlx_vlm": ["chat", "responses", "messages"]}
