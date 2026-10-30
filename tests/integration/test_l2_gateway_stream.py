"""spec §4.6 / §8.2「流式与聚合」「保活与断连」「媒体 · 临时文件」：真网关服务器 + 吐上游帧夹具的桩后端。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

FRAMES = Path(__file__).resolve().parents[1] / "fixtures" / "frames" / "frames.json"
TOK = "t" * 43

PROG = r"""
import base64, http.client, io, json, os, socket, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway.app import Gateway
from gateway.server import GatewayServer, make_handler
from mlx_server.result import Ok
F = json.load(open("@FRAMES@"))
SEEN, CLOSED, MEDIA_SEEN = {}, {}, {}

def video_paths(body):
    out = []
    for m in body.get("messages") or []:
        for p in (m.get("content") if isinstance(m.get("content"), list) else []):
            if isinstance(p, dict) and p.get("type") == "video":
                out.append(p["video"])
    return out

class Up(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        key, mode = self.server.key, self.server.mode
        SEEN[key] = body
        MEDIA_SEEN[key] = [[p, os.path.exists(p)] for p in video_paths(body)]
        if mode == "status500":
            raw = b'{"detail": "boom"}'
            self.send_response(500); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
            return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers(); self.wfile.flush()
        try:
            if mode == "quiet":
                time.sleep(1.0)
            if mode == "endless":
                for _ in range(500):
                    self.wfile.write(b'data: {"id":"x","object":"chat.completion.chunk","model":"BACKEND-ARG",'
                                     b'"choices":[{"index":0,"delta":{"content":"a"}}]}\n\n')
                    self.wfile.flush(); time.sleep(0.02)
                return
            self.wfile.write(F[self.server.fixture].encode("utf-8")); self.wfile.flush()
        except OSError:
            CLOSED[key] = time.monotonic()

def upstream(key, fixture, mode="normal"):
    s = ThreadingHTTPServer(("127.0.0.1", 0), Up); s.daemon_threads = True
    s.key, s.fixture, s.mode = key, fixture, mode
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]

LM = {"model_arg": "BACKEND-ARG", "backend": "mlx_lm", "endpoints": ["chat", "completions"],
      "effort_domain": ["low", "medium", "xhigh"], "reads_completion_tokens": True, "native_template_kwargs": True}
VLM = dict(LM, backend="mlx_vlm", endpoints=["chat", "responses", "messages"],
           reads_completion_tokens=False, native_template_kwargs=False)
MODELS = {"lm": (LM, "mlx_lm_chat_stream", "normal"), "vlm": (VLM, "mlx_vlm_chat_stream_tools", "normal"),
          "resp": (VLM, "mlx_vlm_responses_stream", "normal"), "msgs": (VLM, "mlx_vlm_messages_stream", "normal"),
          "quiet": (LM, "mlx_lm_chat_stream", "quiet"), "errvlm": (VLM, "mlx_vlm_chat_stream_error", "normal"),
          "endless_s": (LM, "", "endless"), "endless_n": (LM, "", "endless"),
          "media": (VLM, "mlx_vlm_chat_stream_tools", "normal"), "five": (VLM, "", "status500"),
          "fetch": (VLM, "mlx_vlm_chat_stream_tools", "normal"), "late": (VLM, "mlx_vlm_chat_stream_tools", "normal"),
          "endless_m": (LM, "", "endless")}
PORTS = {k: upstream(k, fx, mode) for k, (_, fx, mode) in MODELS.items()}
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R(k, p) for k, p in PORTS.items()]
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True),
          "supervisor.snapshot": lambda c, s: MODELS[s][0] if s in MODELS else None,
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
MEDIA_ROOT = tempfile.mkdtemp()
def serve(**kw):
    g = Gateway(None, facade, token="@TOK@", media_root=MEDIA_ROOT, keepalive_sec=0.3, probe_sec=0.1, **kw)
    g.refresh_routes()
    srv = GatewayServer(("127.0.0.1", 0), make_handler(g))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]
GW = serve()

def call(path, body, auth="bearer", port=None):
    h = {"Content-Type": "application/json"}
    h.update({"Authorization": "Bearer @TOK@"} if auth == "bearer" else {"x-api-key": "@TOK@"})
    c = http.client.HTTPConnection("127.0.0.1", port or GW, timeout=20)
    c.request("POST", path, body=json.dumps(body).encode(), headers=h)
    r = c.getresponse(); raw = r.read(); hdrs = dict(r.getheaders()); c.close()
    return r.status, hdrs, raw.decode("utf-8", "replace")

def disconnect_after(model, stream, wait=0.4, messages=None):
    payload = json.dumps({"model": model, "stream": stream,
                          "messages": messages or [{"role": "user", "content": "x"}]}).encode()
    s = socket.create_connection(("127.0.0.1", GW))
    s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
               "Authorization: Bearer @TOK@\r\nContent-Length: %d\r\n\r\n" % len(payload)).encode() + payload)
    time.sleep(wait)
    t0 = time.monotonic(); s.close()
    for _ in range(40):
        if model in CLOSED:
            break
        time.sleep(0.05)
    return round(CLOSED[model] - t0, 2) if model in CLOSED else None

MSG = [{"role": "user", "content": "hi"}]
r = {}
st, h, text = call("/v1/chat/completions", {"model": "lm", "stream": True, "messages": MSG,
                                            "reasoning_effort": "high", "adapters": "evil"})
r["lm_stream"] = {"status": st, "ctype": h.get("Content-Type"), "backend_arg": "BACKEND-ARG" in text,
                  "rc": '"reasoning_content": "先想"' in text, "usage": '"usage"' in text,
                  "done": text.rstrip().endswith("data: [DONE]"), "cors": h.get("Access-Control-Allow-Origin"),
                  "up": {k: SEEN["lm"].get(k) for k in ("stream", "stream_options", "model", "chat_template_kwargs")},
                  "up_has_adapters": "adapters" in SEEN["lm"]}
st, h, text = call("/v1/chat/completions", {"model": "lm", "stream": True, "messages": MSG,
                                            "stream_options": {"include_usage": True}})
r["lm_stream_usage"] = '"usage"' in text
st, h, text = call("/v1/chat/completions", {"model": "vlm", "messages": MSG, "max_completion_tokens": 77})
body = json.loads(text)
msg = body["choices"][0]["message"]
r["vlm_nonstream"] = {"status": st, "model": body["model"], "content": msg["content"],
                      "rc": msg.get("reasoning_content"), "tools": len(msg.get("tool_calls") or []),
                      "finish": body["choices"][0]["finish_reason"], "usage": body.get("usage", {}).get("total_tokens"),
                      "up_max_tokens": SEEN["vlm"].get("max_tokens"), "up_stream": SEEN["vlm"].get("stream")}
st, h, text = call("/v1/responses", {"model": "resp", "input": "hi"})
body = json.loads(text)
r["responses"] = [st, body.get("object"), body.get("model"), body.get("output_text")]
st, h, text = call("/v1/messages", {"model": "msgs", "max_tokens": 64, "messages": MSG}, auth="xkey")
body = json.loads(text)
r["messages"] = [st, body.get("type"), body.get("model"), [b["type"] for b in body.get("content", [])]]
st, h, text = call("/v1/chat/completions", {"model": "quiet", "stream": True, "messages": MSG})
r["keepalive"] = text.count(": keepalive\n\n")
st, h, text = call("/v1/chat/completions", {"model": "errvlm", "messages": MSG})
r["mid_error"] = [st, json.loads(text)["error"]["type"], "/Users/" in text]
r["disconnect_stream"] = disconnect_after("endless_s", True)
r["disconnect_nonstream"] = disconnect_after("endless_n", False)
video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x18ftyp" + b"v" * 64).decode()
vmsg = [{"role": "user", "content": [{"type": "text", "text": "看视频"}, {"type": "video", "video": video}]}]
st, h, text = call("/v1/chat/completions", {"model": "media", "messages": vmsg})
r["media"] = {"status": st, "seen": MEDIA_SEEN["media"], "under_root": all(p.startswith(MEDIA_ROOT) for p, _ in MEDIA_SEEN["media"]),
              "left": os.listdir(MEDIA_ROOT)}
st, h, text = call("/v1/chat/completions", {"model": "five", "messages": vmsg})
r["media_error_path"] = {"status": st, "seen_exists": [e for _, e in MEDIA_SEEN["five"]], "left": os.listdir(MEDIA_ROOT)}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
class Img(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(PNG))); self.end_headers(); self.wfile.write(PNG)
img = ThreadingHTTPServer(("127.0.0.1", 0), Img); img.daemon_threads = True
threading.Thread(target=img.serve_forever, daemon=True).start()
url = "http://127.0.0.1:%d/shot.png?sig=SECRETSIG" % img.server_address[1]
logbuf, real_out = io.StringIO(), sys.stdout
sys.stdout = logbuf                                   # 网关的 info 日志写 fd 1：截下来检查
st, h, text = call("/v1/chat/completions", {"model": "fetch", "messages": [{"role": "user", "content": [
    {"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": url}}]}]})
sys.stdout = real_out
logs = logbuf.getvalue()
r["fetch"] = {"status": st,
              "data_uri": SEEN["fetch"]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"),
              "events": [json.loads(x) for x in logs.splitlines() if '"media_fetched"' in x],
              "leak": "SECRETSIG" in logs or "shot.png" in logs}
late = [{"role": "user", "content": [{"type": "video", "video": video}, {"type": "input_image", "file_id": "file_1"}]}]
st, h, text = call("/v1/chat/completions", {"model": "late", "messages": late})
r["late_fail"] = [st, json.loads(text)["error"]["type"], "late" in SEEN, os.listdir(MEDIA_ROOT)]
r["disconnect_media"] = disconnect_after("endless_m", False, messages=vmsg)
for _ in range(60):                                   # handler 线程收尾后再看
    if not os.listdir(MEDIA_ROOT):
        break
    time.sleep(0.05)
r["disconnect_media_left"] = [os.listdir(MEDIA_ROOT), [e for _, e in MEDIA_SEEN.get("endless_m", [])]]
busy_port = serve(limits={"inflight_max_mb": 0})
st, h, text = call("/v1/chat/completions", {"model": "lm", "messages": MSG}, port=busy_port)
r["busy"] = [st, h.get("Retry-After"), json.loads(text)["error"]["type"]]
print(json.dumps(r, ensure_ascii=False))
"""


def test_gateway_stream_integration(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=90, FRAMES=FRAMES, TOK=TOK)
    s = got["lm_stream"]
    assert (s["status"], s["ctype"], s["cors"]) == (200, "text/event-stream", "*")
    assert s["backend_arg"] is False and s["rc"] is True and s["done"] is True
    assert s["usage"] is False, "客户端没要 include_usage ⇒ 网关为聚合开启的 usage 帧不得下发"
    assert s["up"] == {"stream": True, "stream_options": {"include_usage": True}, "model": "BACKEND-ARG",
                       "chat_template_kwargs": {"reasoning_effort": "xhigh"}}
    assert s["up_has_adapters"] is False
    assert got["lm_stream_usage"] is True
    v = got["vlm_nonstream"]
    assert (v["status"], v["model"], v["content"], v["rc"], v["tools"], v["finish"], v["usage"]) == \
        (200, "vlm", "你好，世界", "先想一想。", 2, "tool_calls", 46)
    assert (v["up_max_tokens"], v["up_stream"]) == (77, True), "上游一律流式；mlx_vlm 映射 max_completion_tokens"
    assert got["responses"] == [200, "response", "resp", "你好，世界"]
    assert got["messages"] == [200, "message", "msgs", ["thinking", "text", "tool_use"]]
    assert got["keepalive"] >= 2, "上游静默期间应向流式客户端发保活注释"
    assert got["mid_error"][:2] == [502, "upstream_error"] and got["mid_error"][2] is True, \
        "中途错误 ⇒ 502；本机来源看得到原因"
    assert got["disconnect_stream"] is not None and got["disconnect_stream"] < 1.5, \
        f"流式客户端断开后上游应在 1.5 s 内被关闭：{got['disconnect_stream']}"
    assert got["disconnect_nonstream"] is not None and got["disconnect_nonstream"] < 2.0, \
        f"非流式客户端断开后上游应在 2 s 内被关闭：{got['disconnect_nonstream']}"
    m = got["media"]
    assert m["status"] == 200 and m["under_root"] and [e for _, e in m["seen"]] == [True], \
        "data:video 应落临时文件，后端收到时文件存在"
    assert m["left"] == [], "请求结束后临时目录必须删除"
    e = got["media_error_path"]
    assert e["status"] == 502 and e["seen_exists"] == [True] and e["left"] == [], "上游 500 时临时文件同样清理"
    f = got["fetch"]
    assert f["status"] == 200 and f["data_uri"], "http 图片应由网关代取并改写为 data URI"
    assert [(e["host"], e["reason"]) for e in f["events"]] == [("127.0.0.1", "ok")] and f["events"][0]["bytes"] > 0
    assert f["leak"] is False, "代取日志只记主机名，不记路径与查询串"
    assert got["late_fail"] == [400, "media_source_rejected", False, []], \
        "后续 part 失败：400、后端零请求、已写出的临时文件删除"
    assert got["disconnect_media"] is not None
    assert got["disconnect_media_left"] == [[], [True]], "客户端断开后临时文件同样删除"
    assert got["busy"] == [503, "5", "gateway_busy"]


LOG_PROG = r"""
import json, os, subprocess, sys, tempfile, time
out = os.path.join(tempfile.mkdtemp(), "api.log")
with open(out, "ab") as fh:
    p = subprocess.Popen([sys.executable, "-c",
                          "import time\nfrom gateway.app import log_event\n"
                          "log_event('info', 'probe_info')\nlog_event('warn', 'probe_warn')\ntime.sleep(5)"],
                         stdout=fh, stderr=fh, env=dict(os.environ))
time.sleep(1.5)
text = open(out).read()
p.kill()
print(json.dumps({"info": "probe_info" in text, "warn": "probe_warn" in text}))
"""


def test_log_event_reaches_redirected_file_immediately(payload, sys_py):
    """supervisor 把网关 stdout / stderr 重定向到 `api.log`（文件 ⇒ 块缓冲）：进程还活着时事件就必须已经落盘。"""
    got = run_floor(payload, sys_py, LOG_PROG, with_gateway=True)
    assert got == {"info": True, "warn": True}, got


# ── 流式路径的补充断言 ──────────────────────────────
EXTRA_PROG = r"""
import base64, http.client, io, json, os, select, socket, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway.app import Gateway
from gateway.server import GatewayServer, make_handler
from mlx_server.result import Ok
F = json.load(open("@FRAMES@"))
SEEN, SNAP_CALLS, CLOSED = {}, {}, {}
STRUCT_ERR = ('data: {"error": {"message": "open /Users/alex/x/config.json failed", '
              '"type": "server_error", "code": "/model/load"}}\n\n')

RESP_FAILED = ('event: response.failed\ndata: {"type": "response.failed", "response": {"id": "resp_1", '
               '"status": "failed", "error": {"code": "server_error", "message": "boom"}}}\n\n')
def drop_last(text):
    # 去掉最后一帧（终止帧），保留其前的完整帧：上游断在帧边界
    frames = text.split("\n\n")
    assert frames[-1] == ""
    return "\n\n".join(frames[:-2]) + "\n\n"

class Up(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        SEEN.setdefault(self.server.key, []).append(self.headers.get("Authorization"))
        if self.server.fixture == "slowhead":
            # mlx_lm 排队：迟迟不发响应头。期间盯住自己的连接，网关一关就记下时刻
            for _ in range(200):
                r, _w, _x = select.select([self.connection], [], [], 0.05)
                if r and not self.connection.recv(1, socket.MSG_PEEK):
                    CLOSED[self.server.key] = time.monotonic()
                    return
            return
        if self.server.fixture == "struct":
            text = STRUCT_ERR
        elif self.server.fixture == "noterm":           # 整帧写完就关连接、不发 [DONE]（后端崩溃形态）
            text = F["mlx_lm_chat_stream"].replace("data: [DONE]\n\n", "")
        elif self.server.fixture == "noterm_resp":      # responses 断在 response.completed 之前（帧边界）
            text = drop_last(F["mlx_vlm_responses_stream"])
        elif self.server.fixture == "noterm_msgs":      # messages 断在 message_stop 之前（帧边界）
            text = drop_last(F["mlx_vlm_messages_stream"])
        elif self.server.fixture == "failed_resp":      # 以 response.failed 正常收尾
            text = drop_last(F["mlx_vlm_responses_stream"]) + RESP_FAILED
        else:
            text = F[self.server.fixture]
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers()
        self.wfile.write(text.encode("utf-8")); self.wfile.flush()
    def do_GET(self):                                  # 扇出端点：200 但体不是 JSON
        raw = b"<html>not json</html>"
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

def upstream(key, fixture):
    s = ThreadingHTTPServer(("127.0.0.1", 0), Up); s.daemon_threads = True
    s.key, s.fixture = key, fixture
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]

LM = {"model_arg": "BACKEND-ARG", "backend": "mlx_lm", "endpoints": ["chat", "completions"],
      "effort_domain": ["low", "medium", "xhigh"], "reads_completion_tokens": True, "native_template_kwargs": True}
VLM = dict(LM, backend="mlx_vlm", endpoints=["chat", "responses", "messages"],
           reads_completion_tokens=False, native_template_kwargs=False)
MODELS = {"auth": (dict(LM, backend_auth=True), "mlx_lm_chat_stream"),
          "err": (VLM, "mlx_vlm_chat_stream_error"), "struct": (VLM, "struct"),
          "inb": (LM, "mlx_lm_chat_stream"),
          "slow_s": (LM, "slowhead"), "slow_n": (LM, "slowhead"),
          "noterm": (LM, "noterm"), "resp": (VLM, "mlx_vlm_responses_stream"),
          "msgs": (VLM, "mlx_vlm_messages_stream"),
          "noterm_resp": (VLM, "noterm_resp"), "noterm_msgs": (VLM, "noterm_msgs"),
          "failed_resp": (VLM, "failed_resp")}
PORTS = {k: upstream(k, fx) for k, (_, fx) in MODELS.items()}
class R:
    def __init__(s, i, p): s.id, s.machine, s.user = i, {"port": p}, {}
class Res:
    records = [R(k, p) for k, p in PORTS.items()]
def snapshot(c, s):
    SNAP_CALLS[s] = SNAP_CALLS.get(s, 0) + 1
    return MODELS[s][0] if s in MODELS else None
facade = {"store.load": lambda c: Res(), "supervisor.running": lambda c, s: Ok(True),
          "supervisor.port_owner": lambda c, s: Ok(True), "supervisor.snapshot": snapshot,
          "supervisor.merge_record": lambda c, s, f, pid=None: True}
MEDIA_ROOT = tempfile.mkdtemp()

class RemoteGateway(Gateway):
    # 经回环发请求，但按远端来源分类：只为测「远端来源看到的是净化后的内容」
    def classify(self, peer):
        return "REMOTE"

def serve(cls):
    g = cls(None, facade, token="@TOK@", backend_key="BK", media_root=MEDIA_ROOT,
            keepalive_sec=0.3, probe_sec=0.1)
    g.refresh_routes()
    srv = GatewayServer(("127.0.0.1", 0), make_handler(g))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]
LOCAL, REMOTE = serve(Gateway), serve(RemoteGateway)

def call(port, path, body=None, method="POST"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Authorization": "Bearer @TOK@"}
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=None if body is None else json.dumps(body).encode(), headers=h)
    r = c.getresponse(); raw = r.read().decode("utf-8", "replace"); c.close()
    return r.status, raw

MSG = [{"role": "user", "content": "hi"}]
r = {}
SNAP_CALLS.clear()
st, _ = call(LOCAL, "/v1/chat/completions", {"model": "auth", "messages": MSG})
r["snap_nonstream"] = [st, SNAP_CALLS.get("auth"), SEEN["auth"][-1]]
SNAP_CALLS.clear()
st, _ = call(LOCAL, "/v1/chat/completions", {"model": "auth", "stream": True, "messages": MSG})
r["snap_stream"] = [st, SNAP_CALLS.get("auth"), SEEN["auth"][-1]]
st, text = call(REMOTE, "/v1/chat/completions", {"model": "err", "stream": True, "messages": MSG})
r["remote_stream_err"] = [st, "/Users/" in text, "<redacted>" in text, "半句" in text]
st, text = call(REMOTE, "/v1/chat/completions", {"model": "err", "messages": MSG})
r["remote_agg_err"] = [st, json.loads(text)["error"]["type"], "/Users/" in text]
st, text = call(REMOTE, "/v1/chat/completions", {"model": "struct", "messages": MSG})
err = json.loads(text)["error"]
r["remote_struct"] = [st, err["type"], "/Users/" in text, err.get("code")]
st, text = call(LOCAL, "/v1/chat/completions", {"model": "struct", "messages": MSG})
r["local_struct"] = [st, "/Users/" in text]
video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x18ftyp" + b"v" * 64).decode()
st, text = call(LOCAL, "/v1/chat/completions", {"model": "inb", "thinking_budget": 64, "messages": [
    {"role": "user", "content": [{"type": "video", "video": video}]}]})
r["inbound_after_media"] = [st, json.loads(text)["error"]["type"], "inb" in SEEN, os.listdir(MEDIA_ROOT)]
st, text = call(LOCAL, "/v1/chat/completions", {"model": "inb", "stream": True, "thinking_budget": 64,
    "messages": [{"role": "user", "content": [{"type": "video", "video": video}]}]})
# 流式路径的这条回复先于 do_POST 的 finally 发出（finally 是这条路径唯一的清理点）：等 handler 线程收尾再看
for _ in range(40):
    if not os.listdir(MEDIA_ROOT):
        break
    time.sleep(0.025)
r["inbound_after_media_stream"] = [st, json.loads(text)["error"]["type"], "inb" in SEEN, os.listdir(MEDIA_ROOT)]
st, text = call(LOCAL, "/v1/responses/resp_1", method="GET")
r["fanout_non_json"] = [st, json.loads(text)["error"]["type"]]
def disconnect_before_head(model, stream):
    payload = json.dumps({"model": model, "stream": stream, "messages": MSG}).encode()
    s = socket.create_connection(("127.0.0.1", LOCAL))
    s.sendall(("POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
               "Authorization: Bearer @TOK@\r\nContent-Length: %d\r\n\r\n" % len(payload)).encode() + payload)
    time.sleep(0.4)
    t0 = time.monotonic(); s.close()
    for _ in range(60):
        if model in CLOSED:
            break
        time.sleep(0.05)
    return round(CLOSED[model] - t0, 2) if model in CLOSED else None
def truncation_logged(path, body):
    buf, real = io.StringIO(), sys.stderr
    sys.stderr = buf                                   # error 级事件写 fd 2：截下来检查
    try:
        st, text = call(LOCAL, path, body)
        time.sleep(0.2)                                # handler 线程在回完流后才记日志
    finally:
        sys.stderr = real
    events = [json.loads(x) for x in buf.getvalue().splitlines() if x.startswith("{")]
    return [st, [e["event"] for e in events if e.get("event") == "sse_truncated"],
            any("messages" in e or "content" in e for e in events)]
r["truncated_no_done"] = truncation_logged("/v1/chat/completions",
                                           {"model": "noterm", "stream": True, "messages": MSG})
r["complete_chat"] = truncation_logged("/v1/chat/completions",
                                       {"model": "auth", "stream": True, "messages": MSG})
r["complete_responses"] = truncation_logged("/v1/responses", {"model": "resp", "stream": True, "input": "hi"})
r["complete_messages"] = truncation_logged("/v1/messages", {"model": "msgs", "stream": True, "max_tokens": 8,
                                                            "messages": MSG})
# 截断判定的正向用例覆盖**每个**带终止帧的端点族（终审 D I-1：此前只有 chat 一族）
for fam, path, model, extra in (("responses", "/v1/responses", "noterm_resp", {"input": "hi"}),
                                ("messages", "/v1/messages", "noterm_msgs", {"max_tokens": 8, "messages": MSG})):
    r["trunc_%s_stream" % fam] = truncation_logged(path, dict({"model": model, "stream": True}, **extra))
    r["trunc_%s_nonstream" % fam] = truncation_logged(path, dict({"model": model}, **extra))
r["failed_resp_stream"] = truncation_logged("/v1/responses", {"model": "failed_resp", "stream": True, "input": "hi"})
r["head_wait_disconnect"] = [disconnect_before_head("slow_s", True), disconnect_before_head("slow_n", False)]
print(json.dumps(r, ensure_ascii=False))
"""


def test_gateway_carry_rulings(payload, sys_py):
    got = run_floor(payload, sys_py, EXTRA_PROG, with_gateway=True, timeout=60, FRAMES=FRAMES, TOK=TOK)
    assert got["snap_nonstream"] == [200, 1, "Bearer BK"], "一次请求只读一次运行记录快照（路由与鉴权头同一份）"
    assert got["snap_stream"] == [200, 1, "Bearer BK"], "流式路径同样只读一次快照"
    assert got["remote_stream_err"] == [200, False, True, True], \
        "远端来源：SSE 已开始后的帧内错误按 frames.rewrite(peer_class) 净化，正常帧照常下发"
    assert got["remote_agg_err"] == [502, "upstream_error", False], "远端来源：聚合错误净化"
    assert got["remote_struct"] == [502, "upstream_error", False, "/model/load"], \
        "结构化上游错误先还原再净化：路径去掉，字符串型 code 保留"
    assert got["local_struct"] == [502, True], "本机来源看上游原文"
    assert got["inbound_after_media"] == [400, "thinking_budget_unsupported", False, []], \
        "媒体已落盘后入站管线才报错：400、后端零请求、临时目录删除"
    assert got["inbound_after_media_stream"] == [400, "thinking_budget_unsupported", False, []], \
        "流式客户端同一情形：临时目录由请求处理的 finally 删除"
    assert got["fanout_non_json"] == [502, "upstream_error"], "扇出上游 200 但体不是 JSON ⇒ 502，不是 504 超时"
    assert got["truncated_no_done"] == [200, ["sse_truncated"], False], \
        "上游断在帧边界、没发终止帧 ⇒ 记 sse_truncated（日志不含请求体）"
    for key in ("complete_chat", "complete_responses", "complete_messages"):
        assert got[key] == [200, [], False], f"{key}：带终止帧的正常流不得记 sse_truncated：{got[key]}"
    for fam in ("responses", "messages"):
        st, nst = got[f"trunc_{fam}_stream"], got[f"trunc_{fam}_nonstream"]
        assert st == [200, ["sse_truncated"], False], f"{fam} 流式：上游断在终止帧前 ⇒ sse_truncated：{st}"
        assert nst == [502, ["sse_truncated"], False], \
            f"{fam} 非流式：上游断在终止帧前 ⇒ 502 + sse_truncated，不得回 200 残缺体：{nst}"
    assert got["failed_resp_stream"] == [200, [], False], \
        f"以 response.failed 收尾的流是终止了的流，不得记 sse_truncated：{got['failed_resp_stream']}"
    for t in got["head_wait_disconnect"]:
        assert t is not None and t < 1.5, \
            f"上游迟迟不发响应头时客户端离开：靠 client_alive 探测在 1.5 s 内关上游（无写可失败）：{got['head_wait_disconnect']}"
