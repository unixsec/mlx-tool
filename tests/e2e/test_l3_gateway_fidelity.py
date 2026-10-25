"""L3 · 真安装产物上由 `mlx up api` 起的真网关，**默认间隔**（spec §4.6：保活 15 s、断连探测 1 s）与真实代取（§4.4、D23）。

后端是一个吐 SSE 的假进程，按最后一条用户消息切换行为（`SILENT` 静默 34 s、`ENDLESS` 持续输出），把收到的请求体
与写失败时刻记进 JSON Lines —— 断言看的是后端**真的**收到了什么、什么时候发现连接没了。运行记录用它真实的
pid / pgid / 启动时刻，`running()` 与 `port_owner()` 会去核对。
REMOTE 来源真实触发：客户端 socket 绑定本机 Wi-Fi / 有线 / 虚拟机网桥上的 IPv4。
"""
import base64
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floor import run_floor  # noqa: E402

pytestmark = pytest.mark.e2e

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

FAKE_SSE = r'''
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
LOG = sys.argv[2]
def note(**kw):
    with open(LOG, "a") as fh:
        fh.write(json.dumps(dict(kw, t=time.time())) + "\n")
def frame(text, finish=None):
    return ("data: " + json.dumps({"id": "c1", "object": "chat.completion.chunk", "created": 1,
            "model": "BACKEND-ARG", "choices": [{"index": 0, "delta": {"content": text},
            "finish_reason": finish}]}) + "\n\n").encode()
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        content = (body.get("messages") or [{}])[-1].get("content")
        text = content if isinstance(content, str) else next(
            (p.get("text") for p in content or [] if p.get("type") == "text"), "")
        # 代取落盘的文件此刻必须真在磁盘上：后端读得到才算写入侧没有空转
        vids = [p.get("video") for p in content if isinstance(p, dict) and p.get("type") == "video"] \
            if isinstance(content, list) else []
        note(event="request", text=text, body=body,
             video_files=[[v, os.path.exists(v), os.path.getsize(v) if os.path.exists(v) else None]
                          for v in vids if isinstance(v, str) and v.startswith("/")])
        self.send_response(200); self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close"); self.end_headers(); self.wfile.flush()
        try:
            if text == "SILENT":
                time.sleep(34)
            if text == "ENDLESS":
                for _ in range(3000):
                    self.wfile.write(frame("a")); self.wfile.flush(); time.sleep(0.02)
            self.wfile.write(frame("ok", "stop") + b"data: [DONE]\n\n"); self.wfile.flush()
        except OSError:
            note(event="eof", text=text)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait(port, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _events(path, **match):
    if not path.exists():
        return []
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    return [e for e in rows if all(e.get(k) == v for k, v in match.items())]


def _call(host, port, method, path, token, body=None, source=None, timeout=60):
    conn = http.client.HTTPConnection(host, port, timeout=timeout,
                                      source_address=(source, 0) if source else None)
    headers = {"Authorization": "Bearer " + token}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    return r.status, raw


def _log_since(path, offset):
    """只读测试开始之后新增的日志 —— 不假设日志文件是空的。"""
    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read().decode("utf-8", "replace")


def _open_raw(port, token, body):
    payload = json.dumps(body).encode()
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall((f"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
               f"Authorization: Bearer {token}\r\nContent-Length: {len(payload)}\r\n\r\n").encode() + payload)
    return s


def _lan_ipv4(payload, sys_py):
    got = run_floor(payload, sys_py, """
        import json
        from gateway import peers
        from mlx_server.ctx import DEFAULT_BINS
        hw, ifc = peers.command_loader(DEFAULT_BINS)()
        n = peers.compute_nets(peers.parse_hardware_ports(hw), peers.parse_ifconfig(ifc))
        print(json.dumps([a["addr"] for a in n["allowed"]
                          if a["kind"] in ("wifi", "ethernet", "vm") and a["addr"]
                          and ":" not in a["addr"]]))
    """, with_gateway=True)
    assert got, ("本机没有 Wi-Fi / 有线 / 虚拟机网桥上的 IPv4 地址 —— REMOTE 来源无法真实触发。"
                 "spec §8.2 要求此时失败而不是跳过：连上 Wi-Fi 或启动 Parallels 后重跑")
    return got[0]


@pytest.fixture()
def stack(tmp_path, repo):
    roots = {"MLX_ROOT_CODE": tmp_path / "code", "MLX_ROOT_DATA": tmp_path / "data",
             "MLX_ROOT_WEIGHTS": tmp_path / "weights"}
    gw_port, be_port = _free_port(), _free_port()
    env = dict(os.environ, PATH="/usr/bin:/bin", MLX_GATEWAY_PORT=str(gw_port),
               **{k: str(v) for k, v in roots.items()})
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh")],
                       capture_output=True, text=True, timeout=120, check=False, env=env)
    assert r.returncode == 0, r.stderr
    code, data = roots["MLX_ROOT_CODE"], roots["MLX_ROOT_DATA"]

    backend_log = tmp_path / "backend.jsonl"
    be = subprocess.Popen(["/usr/bin/python3", "-c", FAKE_SSE, str(be_port), str(backend_log)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert _wait(be_port), "假后端没起来"
    (data / "models.jsonl").write_text(json.dumps(
        {"id": "vis", "user": {"repo": "mlx/vis"}, "machine": {"port": be_port}}) + "\n")
    lstart = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(be.pid)],
                            capture_output=True, text=True, check=False).stdout.strip()
    pgid = subprocess.run(["/bin/ps", "-o", "pgid=", "-p", str(be.pid)],
                          capture_output=True, text=True, check=False).stdout.strip()
    (data / "run").mkdir(parents=True, exist_ok=True)
    record = data / "run" / "vis.json"
    record.write_text(json.dumps(
        {"pid": be.pid, "pgid": int(pgid), "port": be_port, "started_at": lstart,
         "model_arg": "BACKEND-ARG", "type": "model", "schema_ver": 1, "nonce": "n0",
         "backend": "mlx_vlm", "endpoints": ["chat", "responses", "messages"],
         "effort_domain": ["low", "medium", "xhigh"], "reads_completion_tokens": False,
         "native_template_kwargs": False, "backend_auth": False}))
    record.chmod(0o600)

    def mlx(*argv):
        return subprocess.run([str(code / "bin" / "mlx"), *argv], capture_output=True, text=True,
                              timeout=90, check=False, env=dict(env, MLX_SHARE_ROOT=str(code / "share")))

    token = json.loads((data / "settings.json").read_text())["api.token"]
    try:
        r = mlx("up", "api")
        assert r.returncode == 0, r.stdout + r.stderr
        end = time.time() + 20
        while True:
            st, raw = _call("127.0.0.1", gw_port, "GET", "/v1/models", token)
            if st == 200 and "vis" in [m["id"] for m in json.loads(raw)["data"]]:
                break
            assert time.time() < end, f"20 s 内 /v1/models 没有列出假后端：{st} {raw[:200]!r}"
            time.sleep(0.5)
        yield {"port": gw_port, "token": token, "backend_log": backend_log,
               "media_dir": data / "run" / "media", "api_log": data / "logs" / "api.log"}
    finally:
        mlx("down", "api")
        be.terminate()
        try:
            be.wait(timeout=5)
        except subprocess.TimeoutExpired:
            be.kill()


def test_keepalive_at_default_interval(stack):
    """上游静默 34 s ⇒ 客户端约第 15 s、第 30 s 各收到一行 `: keepalive`，随后正常结束，帧里的 model 已改回短 id。"""
    conn = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=60)
    conn.request("POST", "/v1/chat/completions", headers={
        "Authorization": "Bearer " + stack["token"], "Content-Type": "application/json"},
        body=json.dumps({"model": "vis", "stream": True,
                         "messages": [{"role": "user", "content": "SILENT"}]}).encode())
    t0 = time.monotonic()
    resp = conn.getresponse()
    assert resp.status == 200 and resp.getheader("Content-Type") == "text/event-stream"
    beats, raw = [], b""
    while True:
        line = resp.fp.readline()
        if not line:
            break
        if line.startswith(b": keepalive"):
            beats.append(round(time.monotonic() - t0, 1))
        raw += line
    conn.close()
    assert len(beats) == 2 and 14.5 <= beats[0] <= 17.0 and 29.5 <= beats[1] <= 33.0, beats
    assert b"BACKEND-ARG" not in raw and b'"model": "vis"' in raw
    assert raw.rstrip().endswith(b"data: [DONE]")


def test_disconnect_cancels_upstream(stack):
    """客户端断开 ⇒ 流式 1 s 内、非流式 2 s 内后端发现连接被关闭（spec §8.2「保活与断连」）；网关记 `client_disconnected`。"""
    log = stack["backend_log"]
    offset = stack["api_log"].stat().st_size
    for stream, limit in ((True, 1.0), (False, 2.0)):
        before = len(_events(log, event="request"))
        s = _open_raw(stack["port"], stack["token"], {"model": "vis", "stream": stream,
                                                      "messages": [{"role": "user", "content": "ENDLESS"}]})
        end = time.time() + 10
        while len(_events(log, event="request")) == before:
            assert time.time() < end, f"stream={stream}：10 s 内请求没有到达后端"
            time.sleep(0.05)
        time.sleep(0.5)
        closed_at = time.time()
        s.close()
        eof, end = [], time.time() + 10
        while not eof and time.time() < end:
            eof = [e for e in _events(log, event="eof") if e["t"] >= closed_at]
            time.sleep(0.05)
        assert eof, f"stream={stream}：10 s 内后端都没发现连接被关闭"
        assert eof[0]["t"] - closed_at < limit, f"stream={stream}：{eof[0]['t'] - closed_at:.2f}s"
    time.sleep(0.5)
    assert _log_since(stack["api_log"], offset).count('"client_disconnected"') >= 2


def test_remote_fetch_rules_and_media_cleanup(stack, payload, sys_py):
    """远端来源：指向网关本机自身地址（含放行网段上的 LAN 地址）与回环 ⇒ 400 `address_denied` 且网关没有连过去；
    本机路径 ⇒ 400；本机来源的本机路径原样交给后端；data:video 落临时文件、请求结束即删；代取日志不记完整 URL。

    与计划原文的偏差：计划让图片服务器绑在本机 LAN 地址、期望被代取成 data URI；而 `fetch.target_allowed`
    明确拒绝「本机自身地址」（放行网段里列出的每个 `addr`），单机上不存在第二个放行网段内的可连地址，
    成功代取只能由另一台机器 / 公网地址触发 —— 留给真机验收 #9（虚拟机取宿主 http.server）与 #10。"""
    lan = _lan_ipv4(payload, sys_py)
    port, tok, log = stack["port"], stack["token"], stack["backend_log"]
    offset = stack["api_log"].stat().st_size

    hits = []

    class Img(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)

    img = ThreadingHTTPServer((lan, 0), Img)
    img.daemon_threads = True
    threading.Thread(target=img.serve_forever, daemon=True).start()
    img_port = img.server_address[1]

    def chat(part, source):
        before = len(_events(log, event="request"))
        st, raw = _call(source or "127.0.0.1", port, "POST", "/v1/chat/completions", tok,
                        {"model": "vis", "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "hi"}, part]}]}, source=source)
        return st, json.loads(raw), _events(log, event="request")[before:]

    def sent(reqs, key):
        return reqs[0]["body"]["messages"][0]["content"][1][key]

    try:
        st, body, reqs = chat({"type": "image_url", "image_url": {
            "url": f"http://{lan}:{img_port}/red.png?sig=S3CRET"}}, lan)
        assert (st, body["error"]["type"], body["error"].get("reason"), len(reqs)) == \
            (400, "media_fetch_failed", "address_denied", 0), "本机自身的 LAN 地址不得作代取目标"

        st, body, reqs = chat({"type": "image_url", "image_url": {
            "url": f"http://127.0.0.1:{img_port}/red.png"}}, lan)
        assert (st, body["error"]["type"], body["error"].get("reason"), len(reqs)) == \
            (400, "media_fetch_failed", "address_denied", 0)

        st, body, reqs = chat({"type": "image_url", "image_url": {"url": "/etc/hosts"}}, lan)
        assert (st, body["error"]["type"], len(reqs)) == (400, "media_source_rejected", 0)

        st, body, reqs = chat({"type": "image_url", "image_url": {"url": "/tmp/x.png"}}, None)
        assert st == 200 and sent(reqs, "image_url")["url"] == "/tmp/x.png", "本机来源的本机路径应原样交给后端"

        video = "data:video/mp4;base64," + base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + b"v" * 256).decode()
        st, body, reqs = chat({"type": "video", "video": video}, lan)
        path = sent(reqs, "video")
        media_dir = os.path.realpath(stack["media_dir"])
        assert st == 200 and os.path.realpath(path).startswith(media_dir) and path.endswith(".mp4"), path
        assert reqs[0]["video_files"] == [[path, True, 12 + 256]], \
            f"后端收到请求时临时文件应已落盘且字节数一致：{reqs[0]['video_files']}"
        end = time.time() + 3
        while os.path.isdir(media_dir) and os.listdir(media_dir) and time.time() < end:
            time.sleep(0.1)
        assert not os.path.isdir(media_dir) or os.listdir(media_dir) == [], "请求结束后临时目录应删除"
    finally:
        img.shutdown()
    assert hits == [], f"网关不应连到被拒绝的目标：{hits}"
    text = _log_since(stack["api_log"], offset)
    fetched = [json.loads(x) for x in text.splitlines() if '"media_fetched"' in x]
    assert [(e["host"], e["reason"]) for e in fetched] == [(lan, "address_denied"), ("127.0.0.1", "address_denied")], fetched
    assert "S3CRET" not in text and "red.png" not in text, "代取日志不得记完整 URL"
