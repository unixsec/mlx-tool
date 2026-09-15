"""L2 · T2 网关 —— **外部应用接入的全部**。

Cherry Studio 等配置 base url + model id + api key 三项即用，
这批断言就是「三项真的够用」的求值面。
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402


def _floor(payload, sys_py, body):
    prog = "\n".join(textwrap.dedent(p) for p in body.split("\n@@\n"))
    r = subprocess.run([sys_py, "-c", prog], capture_output=True, text=True,
                       timeout=25, check=False,
                       env={"PATH": "/usr/bin:/bin",
                            "PYTHONPATH": f"{payload}:{payload}/lib"})
    assert r.returncode == 0, f"下限解释器执行失败：{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


PRE = '''
import json
from gateway.app import Gateway
from mlx_server.result import Ok, Unknown
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R("qwen", 8100), R("llama", 8101)]
def facade(running=None):
    return {"store.load": lambda c: Res(),
            "supervisor.running": running or (lambda c, s: Ok(True)),
            "supervisor.port_owner": lambda c, s: Ok(True),
            "supervisor.snapshot": lambda c, s: {"model_arg": s},
            "supervisor.merge_record": lambda c, s, f, pid=None: True}
'''


def test_l2_models_ids_come_from_route_keys(payload, sys_py):
    """`/v1/models` 的 id **逐字取自路由键集**，不从 models.jsonl 另读。

    坏样本：另读一遍 ⇒ 第二个真理源 ⇒ 路由表刷新后两者分叉
    ⇒ 客户端拿到一个转发时命不中的 id（`R-API-21` 三向一致的反例）。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        g = Gateway(None, facade()); g.refresh_routes()
        st, body = g.handle("GET", "/v1/models")
        print(json.dumps({"status": st, "ids": [m["id"] for m in body["data"]],
                          "keys": g.view.route_keys()}))
    ''')
    assert got["status"] == 200
    Cardinality.check("/v1/models ↔ route_keys", got["ids"], got["keys"], rel="equal")


def test_l2_three_way_id_consistency(payload, sys_py):
    """**`R-API-21` 三向一致**：列表里的 id 原样发回去，转发成功且回程仍是它。

    这是「三项配置够用」的核心 —— 列表里能看见、发过去用不了就是白搭。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        g = Gateway(None, facade()); g.refresh_routes()
        _, body = g.handle("GET", "/v1/models")
        results = []
        for m in body["data"]:
            st, rb = g.handle("POST", "/v1/chat/completions", body={"model": m["id"]},
                              forward=lambda p, pa, b: (200, {"model": b["model"]}))
            results.append([m["id"], st, rb.get("model")])
        print(json.dumps({"results": results}))
    ''')
    for mid, status, echoed in got["results"]:
        assert status == 200, f"{mid} 转发失败 —— 列表里有但用不了"
        assert echoed == mid, f"{mid} 回程 id 变成了 {echoed} —— 三向一致断裂"


def test_l2_models_endpoint_not_blocked_by_route_guard(payload, sys_py):
    """**裁决 R2-1 的 S6 集合**：`/v1/models` 在 `EXEMPT_PATHS` 内。

    坏样本：不豁免 ⇒ GET 无请求体被 route_guard 当作「`.model` 取不到」打掉
    ⇒ 注册了第 18 个符号也等于没加。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        from gateway.guard import ROUTE_EXEMPT
        g = Gateway(None, facade()); g.refresh_routes()
        st, _ = g.handle("GET", "/v1/models")
        print(json.dumps({"status": st, "exempt": sorted(ROUTE_EXEMPT)}))
    ''')
    assert got["status"] == 200, "/v1/models 被 route_guard 打掉了"
    Cardinality.check("ROUTE_EXEMPT", got["exempt"], ["/healthz", "/v1/models"], rel="equal")


def test_l2_building_returns_503_not_empty_list(payload, sys_py):
    """表在 `building` ⇒ **503 + 诊断，不返回空列表**。

    空列表让客户端显示「没有模型」而不是「服务未就绪」，用户处置完全不同。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        g = Gateway(None, facade())          # 不 refresh ⇒ state == building
        st, body = g.handle("GET", "/v1/models")
        print(json.dumps({"status": st, "has_data": "data" in body}))
    ''')
    assert got["status"] == 503 and not got["has_data"]


def test_l2_unknown_running_not_routed(payload, sys_py):
    """`running == Unknown` 的服务**不进路由表** —— 转发到所有权不可证的端口
    会把请求发给别人的进程。"""
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        g = Gateway(None, facade(lambda c, s: Unknown("PS_FAILED") if s == "qwen" else Ok(True)))
        g.refresh_routes()
        print(json.dumps({"keys": g.view.route_keys()}))
    ''')
    assert got["keys"] == ["llama"], f"Unknown 的服务进了路由表：{got['keys']}"


def test_l2_upstream_error_body_not_forwarded(payload, sys_py):
    """上游 5xx 的响应体**不透传** —— 防上游内部信息泄漏（`X-T2-17-b`）。"""
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        g = Gateway(None, facade()); g.refresh_routes()
        st, body = g.handle("POST", "/v1/chat/completions", body={"model": "qwen"},
                            forward=lambda p, pa, b: (500, {"internal": "SECRET_MARKER"}))
        print(json.dumps({"status": st, "raw": json.dumps(body)}))
    ''')
    assert got["status"] == 502
    assert "SECRET_MARKER" not in got["raw"], "上游错误体被透传了"


def test_l2_sse_decoder_reassembles_across_chunks(payload, sys_py):
    """`R-API-23`：**自行缓冲拼帧，不假设分块对齐帧边界**。

    坏样本：对 chunk 做字节级 `.replace()` ⇒ 跨块的 model 字段被改一半。
    """
    got = _floor(payload, sys_py, '''
        import json
        from mlx_server.wire import SseDecoder
        from gateway.translate import rewrite_frame
        d = SseDecoder()
        raw = b'data: {"model": "backend-name", "x": 1}\\n\\ndata: [DONE]\\n\\n'
        frames = []
        for i in range(0, len(raw), 7):            # 故意切在帧中间
            frames += d.feed(raw[i:i + 7])
        out = [rewrite_frame(f, "qwen").decode() for f in frames]
        print(json.dumps({"frames": out, "pending": d.pending().decode()}))
    ''')
    assert len(got["frames"]) == 2, f"跨块分帧失败：{got['frames']}"
    assert '"model": "qwen"' in got["frames"][0].replace('"model":"qwen"', '"model": "qwen"')
    assert "[DONE]" in got["frames"][1], "结束标记未原样通过"
    assert got["pending"] == "", "流末仍有未终结的帧"


def test_l2_models_requires_auth_when_nonlocal(payload, sys_py):
    """**安全审查 auth-bypass #1**：`/v1/models` 是资产清单，非本地绑定时必须鉴权。

    坏样本（上一版就是）：把它放进一个同时被 source_guard 与 route_guard
    使用的豁免集 ⇒ 为了绕开 route_guard 而**连鉴权一起豁免了**
    ⇒ 任何人都能拉到本机装了哪些模型。
    两个 guard 挡的是不同的东西，豁免面必须各算各的。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        from gateway.guard import AUTH_EXEMPT, ROUTE_EXEMPT
        g = Gateway(None, facade(), bind_addr="0.0.0.0", token="s3cret")
        g.refresh_routes()
        r = {}
        r["no_token"] = g.handle("GET", "/v1/models")[0]
        r["bad_token"] = g.handle("GET", "/v1/models",
                                  headers={"Authorization": "Bearer wrong"})[0]
        r["good_token"] = g.handle("GET", "/v1/models",
                                   headers={"Authorization": "Bearer s3cret"})[0]
        r["healthz"] = g.handle("GET", "/healthz")[0]
        print(json.dumps({**r, "auth_exempt": sorted(AUTH_EXEMPT),
                          "route_exempt": sorted(ROUTE_EXEMPT)}))
    ''')
    assert got["no_token"] == 401, "非本地绑定下模型列表可匿名拉取"
    assert got["bad_token"] == 401, "token 只看前缀不校验值"
    assert got["good_token"] == 200
    assert got["healthz"] == 200, "/healthz 应始终匿名（只回状态，不含资产）"
    Cardinality.check("AUTH_EXEMPT", got["auth_exempt"], ["/healthz"], rel="equal")
    Cardinality.check("ROUTE_EXEMPT", got["route_exempt"],
                      ["/healthz", "/v1/models"], rel="equal")


def test_l2_nonlocal_without_token_refuses_service(payload, sys_py):
    """**安全审查 auth-bypass #2**：非本地绑定却未配令牌 ⇒ **503 拒绝服务，不放行**。

    坏样本：`expected_token` 为空时跳过校验 ⇒ 忘配令牌等于关掉鉴权。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        g = Gateway(None, facade(), bind_addr="0.0.0.0")   # 未配 token
        g.refresh_routes()
        print(json.dumps({"status": g.handle("GET", "/v1/models",
                          headers={"Authorization": "Bearer anything"})[0]}))
    ''')
    assert got["status"] == 503, f"未配令牌时放行了：{got['status']}"


def test_l2_exposure_from_bind_addr_only(payload, sys_py):
    """**安全审查 trust-boundary**：暴露等级**由绑定地址唯一决定**（`R-API-25`）。

    `bind_addr` 与 `token` 都在装配段注入，请求处理路径改不了它们 ——
    否则请求头就能把自己声明成「本地」从而关掉鉴权。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        from gateway.guard import exposure_level
        g = Gateway(None, facade(), bind_addr="0.0.0.0", token="t")
        g.refresh_routes()
        # 伪造转发头，试图把自己声明成本地
        spoofed = g.handle("GET", "/v1/models", headers={
            "X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1",
            "Host": "localhost"})[0]
        print(json.dumps({"spoofed": spoofed,
                          "lo": exposure_level("127.0.0.1"),
                          "nonlo": exposure_level("0.0.0.0")}))
    ''')
    assert got["spoofed"] == 401, "转发头伪造成功地绕过了鉴权"
    assert got["lo"] == "LOOPBACK" and got["nonlo"] == "NONLOCAL"


def test_l2_streaming_request_reaches_backend_as_sse(payload, sys_py):
    """**`stream: true` 必须走流式转发路径**，且帧里的 model 改回短 id。

    坏样本（本次真实发生）：`do_POST` 恒用非流式 `forward` ⇒ 后端回 SSE，
    而 `forward` 用 `json.loads` 解析它 ⇒ ValueError ⇒ None ⇒ 客户端收到
    504 `gateway.upstream_timeout`。Cherry Studio 这类客户端**默认开流式**，
    等于照手册接进去、发第一条消息就失败 —— 而非流式的 curl 一切正常，
    所以它在手工验证里完全不可见。

    这里起一个**真的 SSE 上游**（不是桩返回值），让网关真转发一次。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        import threading, urllib.error, urllib.request
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from gateway.server import make_handler

        # ── 真上游：吐两帧 SSE，model 是**后端参数名**（与短 id 不同）──
        class Up(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for c in ("hi", "!"):
                    frame = ('data: ' + json.dumps(
                        {"model": "BACKEND-ARG", "choices": [{"delta": {"content": c}}]})
                        + "\\n\\n").encode()
                    # 故意**从帧中间切断**再分两次写 —— 分帧器必须自己拼回来
                    self.wfile.write(frame[:9]); self.wfile.flush()
                    self.wfile.write(frame[9:]); self.wfile.flush()
                self.wfile.write(b"data: [DONE]\\n\\n")

        up = ThreadingHTTPServer(("127.0.0.1", 0), Up); up.daemon_threads = True
        threading.Thread(target=up.serve_forever, daemon=True).start()
        up_port = up.server_address[1]

        g = Gateway(None, facade()); g.refresh_routes()
        g.view._routes["qwen"] = {"port": up_port}      # 把路由指向这个假上游

        gwsrv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(g))
        gwsrv.daemon_threads = True
        threading.Thread(target=gwsrv.serve_forever, daemon=True).start()

        req = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % gwsrv.server_address[1],
            data=json.dumps({"model": "qwen", "stream": True,
                             "messages": [{"role": "user", "content": "x"}]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        # 坏样本（不分流）下上游会回 504，urlopen 抛 HTTPError。
        # 捕获它 —— 否则子进程崩掉，测试报的是「returncode != 0」而不是原因。
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                ctype, raw = r.headers.get("Content-Type"), r.read().decode()
        except urllib.error.HTTPError as e:
            ctype, raw = e.headers.get("Content-Type"), e.read().decode()
        print(json.dumps({"ctype": ctype, "raw": raw}))
    ''')
    assert got["ctype"] == "text/event-stream", \
        f"流式请求没走 SSE 路径，Content-Type={got['ctype']!r}（非流式转发会给 application/json）"
    assert "BACKEND-ARG" not in got["raw"], \
        f"回程帧里漏出了后端参数名，没做 id 改回：{got['raw'][:200]}"
    assert got["raw"].count('"model": "qwen"') == 2, \
        f"两帧都该被改回短 id，实得：{got['raw'][:300]}"
    assert "[DONE]" in got["raw"], "结束标记没原样透传"


def test_l2_port_owner_false_or_unknown_not_routed(payload, sys_py):
    """**`port_owner` 是路由的第二道合取项**，`Ok(False)` 与 `Unknown` 都不进表。

    坏样本（本次真实发生）：`supervisor.port_owner` 登记在网关的四符号门面
    白名单里，却**零调用点** —— 这道 `R-API-28` 的 P0 安全判定从未生效。
    症状是端口易主之后网关照转不误：请求被发给占住那个端口的**别人的进程**。

    方向与 `may_kill` 相反是**有意的**（MR-1）：那里 `Unknown` 放行，
    被保护对象是「不要留孤儿进程」；这里 `Unknown` 拒绝，
    被保护对象是「不要把请求发给别人的进程」。
    """
    got = _floor(payload, sys_py, PRE + "\n@@\n" + '''
        out = {}
        for name, owner in (("owner_true",   lambda c, s: Ok(True)),
                            ("owner_false",  lambda c, s: Ok(False)),
                            ("owner_unknown",lambda c, s: Unknown("TOOL_UNAVAILABLE"))):
            f = facade(); f["supervisor.port_owner"] = owner
            g = Gateway(None, f); g.refresh_routes()
            out[name] = g.view.route_keys()
        print(json.dumps(out))
    ''')
    assert got["owner_true"] == ["llama", "qwen"], \
        f"port_owner 为真时该正常路由，实得 {got['owner_true']}"
    assert got["owner_false"] == [], \
        f"端口不归它却仍在路由表里 —— 请求会被转给别人的进程：{got['owner_false']}"
    assert got["owner_unknown"] == [], \
        f"所有权不可证仍进了路由表（Unknown 必须落在拒绝一侧）：{got['owner_unknown']}"
