"""D1：mlx_lm 后端的鉴权包装（`payload/libexec/mlx_lm_auth.py`）。上游没有鉴权，端口只绑回环，
本机任何进程（含同机其他用户）都能绕过网关令牌直接调用。包装给请求处理器加 Bearer 校验；
上游结构对不上就拒绝启动，不在无鉴权下静默起来。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import http.client, json, sys, threading, types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, "@PAYLOAD@/libexec")
import mlx_lm_auth
class APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _ok(self):
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")
    do_GET = do_POST = do_OPTIONS = _ok
fake = types.SimpleNamespace(APIHandler=APIHandler, main=lambda: None)
r = {"install": mlx_lm_auth.install(fake, "K" * 43)}
srv = ThreadingHTTPServer(("127.0.0.1", 0), APIHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
def call(method, auth=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request(method, "/v1/models", body=b"{}" if method == "POST" else None,
              headers={"Authorization": auth} if auth else {})
    st = c.getresponse().status; c.close(); return st
r["no_auth"] = [call(m) for m in ("GET", "POST", "OPTIONS")]
r["wrong"] = call("POST", "Bearer nope")
r["right"] = [call(m, "Bearer " + "K" * 43) for m in ("GET", "POST")]
r["broken"] = mlx_lm_auth.install(types.SimpleNamespace(main=lambda: None), "k")
r["no_method"] = mlx_lm_auth.install(types.SimpleNamespace(APIHandler=type("H", (), {}), main=lambda: None), "k")
print(json.dumps(r))
"""


def test_shim_rejects_requests_without_the_backend_key(payload, sys_py):
    got = run_floor(payload, sys_py, PROG)
    assert got["install"] is None, got
    assert got["no_auth"] == [401, 401, 401] and got["wrong"] == 401, got
    assert got["right"] == [200, 200], got
    assert got["broken"] and got["no_method"], f"上游结构不符应报原因（据此拒绝启动）：{got}"
