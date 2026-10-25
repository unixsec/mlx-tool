"""spec §4.1 / D13：所有来源都要令牌（预检除外），顺序是来源 → 预检 → 鉴权 → Content-Type。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import AS_STREAM, run_floor  # noqa: E402

NET = Path(__file__).resolve().parents[1] / "fixtures" / "net"
TOK = "t" * 43

PROG = AS_STREAM + r"""
import json
from gateway import peers
from gateway.app import Gateway
from mlx_server.result import Ok
HW = open("@NET@/networksetup.txt").read()
IF = open("@NET@/ifconfig.txt").read()
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R("qwen", 8100)]
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: {"model_arg": s},
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
nets = peers.NetView(lambda: (HW, IF), clock=lambda: 0.0)
nets.refresh()
g = Gateway(None, facade, bind_addr="0.0.0.0", token="@TOK@", nets=nets)
g.refresh_routes()
fwd = lambda p, pa, b, **kw: (200, {"model": b["model"]})
JSON = {"Content-Type": "application/json"}
AUTHS = {"none": {}, "bearer_ok": {"Authorization": "Bearer @TOK@"},
         "bearer_bad": {"Authorization": "Bearer nope"}, "xkey_ok": {"x-api-key": "@TOK@"},
         "both_one_ok": {"Authorization": "Bearer nope", "x-api-key": "@TOK@"},
         "both_bad": {"Authorization": "Bearer nope", "x-api-key": "nope"}}
OPS = {
    "get_models": lambda h, peer: g.handle("GET", "/v1/models", headers=h, peer=peer)[0],
    "post_chat": lambda h, peer: g.handle("POST", "/v1/chat/completions", headers=dict(JSON, **h),
                                          body={"model": "qwen"}, forward=fwd, stream=as_stream(fwd), peer=peer)[0],
    "post_stream": lambda h, peer: (lambda k: "forward" if k[0] == "forward" else k[1][0])(
        g.prepare("/v1/chat/completions", dict(JSON, **h), {"model": "qwen", "stream": True},
                  method="POST", peer=peer)),
    "preflight": lambda h, peer: g.handle("OPTIONS", "/v1/chat/completions", peer=peer,
        headers=dict(h, **{"Access-Control-Request-Method": "POST", "Origin": "http://x"}))[0],
    "options_plain": lambda h, peer: g.handle("OPTIONS", "/v1/chat/completions",
                                              headers=h, peer=peer)[0],
}
matrix = {}
for peer in ("127.0.0.1", "172.20.10.5", "10.14.0.7"):
    for op, fn in OPS.items():
        for name, h in AUTHS.items():
            matrix["%s|%s|%s" % (peer, op, name)] = fn(h, peer)
denied = g.handle("GET", "/v1/models", headers=AUTHS["bearer_ok"], peer="10.14.0.7")
hz = g.handle("GET", "/healthz", peer="172.20.10.5")
ctype = g.handle("POST", "/v1/chat/completions", peer="127.0.0.1", forward=fwd, stream=as_stream(fwd),
                 headers={"Content-Type": "text/plain", "Authorization": "Bearer @TOK@"},
                 body={"model": "qwen"})[0]
ctype_param = g.handle("POST", "/v1/chat/completions", peer="127.0.0.1", forward=fwd, stream=as_stream(fwd),
                       headers={"Content-Type": "application/json; charset=utf-8",
                                "Authorization": "Bearer @TOK@"}, body={"model": "qwen"})[0]
spoof = g.handle("GET", "/v1/models", peer="10.14.0.7",
                 headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1",
                          "Authorization": "Bearer @TOK@"})[0]
no_tok = Gateway(None, facade, token=None, nets=nets)
no_tok.refresh_routes()
print(json.dumps({"matrix": matrix, "denied": [denied[0], json.dumps(denied[1])],
                  "healthz": [hz[0], sorted(hz[1])], "ctype": ctype, "ctype_param": ctype_param,
                  "spoof": spoof,
                  "no_token": no_tok.handle("GET", "/v1/models", peer="127.0.0.1",
                                            headers={"Authorization": "Bearer x"})[0],
                  "no_nets_remote": Gateway(None, facade, token="@TOK@").classify("172.20.10.5")}))
"""

SUCCESS = {"get_models": 200, "post_chat": 200, "post_stream": "forward"}


def _expected(peer, op, auth):
    if peer == "10.14.0.7":
        return 403                                   # 来源分类最先，预检也不例外
    if op == "preflight":
        return 204
    if op == "options_plain":
        return 405
    return SUCCESS[op] if auth in ("bearer_ok", "xkey_ok", "both_one_ok") else 401


def test_auth_matrix(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, NET=NET, TOK=TOK)
    wrong = {k: v for k, v in got["matrix"].items() if v != _expected(*k.split("|"))}
    assert wrong == {}, f"与期望不符的格子：{wrong}"
    assert len(got["matrix"]) == 3 * 5 * 6


def test_edges(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, NET=NET, TOK=TOK)
    status, body = got["denied"]
    assert status == 403 and '"peer_not_allowed"' in body
    assert "10.14" not in body and "/" not in body.replace("\\/", ""), "403 体不得泄漏地址或网段"
    assert got["healthz"] == [200, ["routes", "ts"]], "/healthz 免令牌且只回两个键"
    assert got["ctype"] == 415 and got["ctype_param"] == 200
    assert got["spoof"] == 403, "转发头不得改变来源分类"
    assert got["no_token"] == 503, "未配令牌时拒绝服务，不放行"
    assert got["no_nets_remote"] is None, "没有网段视图时只认回环"
