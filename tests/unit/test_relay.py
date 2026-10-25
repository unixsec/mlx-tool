"""spec §4.6 保活与断连 / §8.2「保活与断连」。真 socket：本地 HTTP 上游 + socketpair 当客户端。间隔注入为亚秒级。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

PROG = r"""
import json, socket, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from gateway import relay as R

EVENTS = {}
class Up(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        mode = body.get("mode")
        if mode == "bad":
            raw = b'{"error": {"message": "bad request", "type": "invalid_request_error"}}'
            self.send_response(400); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if mode == "slow_head":
            time.sleep(1.5)
        if mode == "abort":
            # 分块传输：声明 0x20 字节的块，只发 15 字节就断开 ⇒ 客户端 read1 抛 IncompleteRead
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            self.wfile.write(b"20\r\n" + b'data: {"half": '); self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers(); self.wfile.flush()
        try:
            if mode == "quiet":
                time.sleep(1.3)
                self.wfile.write(b'data: {"x": 1}\n\n'); self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
            elif mode == "endless":
                for i in range(200):
                    self.wfile.write(b'data: {"i": %d}\n\n' % i); self.wfile.flush(); time.sleep(0.05)
            elif mode == "abort":
                pass                              # 见下面的分块分支
            else:
                self.wfile.write(b'data: {"ok": 1}\n\ndata: [DONE]\n\n'); self.wfile.flush()
        except OSError:                           # macOS 写已关闭的 socket 也可能是 errno 41
            EVENTS[mode] = time.monotonic()
up = ThreadingHTTPServer(("127.0.0.1", 0), Up); up.daemon_threads = True
threading.Thread(target=up.serve_forever, daemon=True).start()
PORT = up.server_address[1]

def run(mode, keepalive_sec=0.5, probe_sec=0.1, disconnect_after=None, write_fails_after=None):
    client, peer = socket.socketpair()
    got = {"heads": [], "chunks": b"", "keepalives": 0}
    t0 = time.monotonic()
    def on_head(head):
        got["heads"].append(head[0] if head[0] != "error" else ["error", head[1], head[2]])
    def on_chunk(chunk):
        if write_fails_after is not None and time.monotonic() - t0 > write_fails_after:
            raise BrokenPipeError(32, "Broken pipe")
        got["chunks"] += chunk
    def keepalive():
        got["keepalives"] += 1
    if disconnect_after is not None:
        threading.Timer(disconnect_after, peer.close).start()
    res = R.relay(lambda: R.open_upstream(PORT, "/v1/chat/completions", {"mode": mode}),
                  on_head, on_chunk, lambda: R.client_alive(client), keepalive,
                  keepalive_sec=keepalive_sec, probe_sec=probe_sec)
    elapsed = time.monotonic() - t0
    client.close()
    try:
        peer.close()
    except OSError:
        pass
    return {"outcome": res["outcome"], "heads": got["heads"], "chunks": got["chunks"].decode(),
            "keepalives": got["keepalives"], "elapsed": round(elapsed, 2), "bytes": res["bytes"]}
r = {}
r["ok"] = run("ok")
r["quiet"] = run("quiet", keepalive_sec=0.4)
t_disc = time.monotonic()
r["endless_disconnect"] = run("endless", disconnect_after=0.4)
time.sleep(0.5)
r["upstream_saw_close"] = "endless" in EVENTS and EVENTS["endless"] - t_disc < 2.0
r["write_fail"] = run("endless", write_fails_after=0.3)
r["slow_head_disconnect"] = run("slow_head", disconnect_after=0.3)
r["bad"] = run("bad")
r["abort"] = run("abort")
r["failed"] = R.relay(lambda: R.open_upstream(1, "/x", {}), lambda h: None, lambda c: None,
                      lambda: True, None, probe_sec=0.05)
r["failed"] = [r["failed"]["outcome"], r["failed"]["head"][0]]
a, b = socket.socketpair()
r["alive"] = [R.client_alive(a)]
b.close()
r["alive"].append(R.client_alive(a))
print(json.dumps(r))
"""


def test_relay_keepalive_disconnect_and_errors(payload, sys_py):
    got = run_floor(payload, sys_py, PROG, with_gateway=True, timeout=60)
    assert got["ok"]["outcome"] == "ok" and got["ok"]["heads"] == ["stream"]
    assert got["ok"]["chunks"] == 'data: {"ok": 1}\n\ndata: [DONE]\n\n' and got["ok"]["bytes"] == 31
    q = got["quiet"]
    assert q["outcome"] == "ok" and q["keepalives"] >= 2, f"上游静默期间应按间隔保活：{q}"
    e = got["endless_disconnect"]
    assert e["outcome"] == "client_disconnected" and e["elapsed"] < 1.2, \
        f"上游持续出数据时也要按间隔探测客户端（非流式聚合不写客户端，只能靠探测）：{e['outcome']} {e['elapsed']}"
    assert got["upstream_saw_close"] is True, "客户端断开后上游连接必须被关闭（后端据此取消生成）"
    assert got["write_fail"]["outcome"] == "client_disconnected" and got["write_fail"]["elapsed"] < 1.2
    s = got["slow_head_disconnect"]
    assert s["outcome"] == "client_disconnected" and s["heads"] == [] and s["elapsed"] < 1.2, \
        f"等上游响应头期间也要探测断连：{s}"
    assert got["bad"]["outcome"] == "upstream_error"
    assert got["bad"]["heads"] == [["error", 400, {"error": {"message": "bad request",
                                                             "type": "invalid_request_error"}}]]
    assert got["abort"]["outcome"] == "upstream_aborted"
    assert got["failed"] == ["upstream_failed", "failed"]
    assert got["alive"] == [True, False]
