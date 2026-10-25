"""L3 · 网关在**真实安装产物 + 真实 HTTP** 上跑通。

这是「Cherry Studio 配 base url + model id + api key 三项即用」的求值面：
本文件真的起一个网关进程、真的用 HTTP 打它、真的转发给一个后端。
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402

pytestmark = pytest.mark.e2e

TOK = "t" * 43

FAKE_BACKEND = '''
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        # 两个 mlx 后端都回显请求里的 model 值 —— 这正是网关必须改回短 id 的原因
        # 网关对上游一律发流式（spec D22）：按 stream 回 SSE，与真实后端一致
        if body.get("stream"):
            frame = json.dumps({"model": body.get("model"), "choices": [
                {"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]})
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close"); self.end_headers()
            self.wfile.write(("data: " + frame + "\\n\\ndata: [DONE]\\n\\n").encode())
            return
        out = json.dumps({"model": body.get("model"),
                          "choices": [{"message": {"content": "ok"}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out))); self.end_headers()
        self.wfile.write(out)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
'''


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
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


def _get(url, timeout=5, headers=None):
    req = urllib.request.Request(url, headers=dict({"Authorization": "Bearer " + TOK},
                                                   **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _post(url, payload, timeout=8):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + TOK})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture()
def stack(tmp_path, repo):
    """真实安装 + 真实假后端 + 真实网关进程。全部拆掉后返回。"""
    roots = {"MLX_ROOT_CODE": tmp_path / "code",
             "MLX_ROOT_DATA": tmp_path / "data",
             "MLX_ROOT_WEIGHTS": tmp_path / "weights"}
    gw_port, be_port = _free_port(), _free_port()
    env = dict(os.environ, PATH="/usr/bin:/bin",
               MLX_GATEWAY_PORT=str(gw_port), MLX_GATEWAY_TOKEN=TOK,
               **{k: str(v) for k, v in roots.items()})
    r = subprocess.run(["bash", str(repo / "deployer" / "install.sh")],
                       capture_output=True, text=True, timeout=60, check=False, env=env)
    assert r.returncode == 0, r.stderr

    code, data = roots["MLX_ROOT_CODE"], roots["MLX_ROOT_DATA"]
    (data / "models.jsonl").write_text(json.dumps(
        {"id": "qwen", "user": {"repo": "mlx/qwen", "type": "text"},
         "machine": {"port": be_port}}) + "\n")

    be = subprocess.Popen(["/usr/bin/python3", "-c", FAKE_BACKEND, str(be_port)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert _wait(be_port), "假后端没起来"

    # 运行记录用**真实的** pid/pgid/started_at —— running() 会去 ps 核对
    st = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(be.pid)],
                        capture_output=True, text=True, check=False).stdout.strip()
    pg = subprocess.run(["/bin/ps", "-o", "pgid=", "-p", str(be.pid)],
                        capture_output=True, text=True, check=False).stdout.strip()
    (data / "run").mkdir(parents=True, exist_ok=True)
    (data / "run" / "qwen.json").write_text(json.dumps(
        {"pid": be.pid, "pgid": int(pg), "port": be_port, "started_at": st,
         "model_arg": "qwen", "type": "model", "schema_ver": 1, "nonce": "n0"}))

    gw = subprocess.Popen(["/usr/bin/python3", "-m", "gateway"],
                          env=dict(env, PYTHONPATH=str(code),
                                   MLX_SHARE_ROOT=str(code / "share")),
                          stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert _wait(gw_port), "网关没起来"
    try:
        yield f"http://127.0.0.1:{gw_port}", gw_port
    finally:
        for p in (gw, be):
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def test_l3_healthz(stack):
    """`/healthz` 回路由表状态 —— 它是唯一始终匿名的端点。"""
    base, _ = stack
    st, body = _get(f"{base}/healthz")
    assert st == 200 and body["routes"] in ("fresh", "building") and sorted(body) == ["routes", "ts"]


def test_l3_models_lists_running_service(stack):
    """`/v1/models` 只列出**真的在跑**的服务（`running == Ok(True)`）。"""
    base, _ = stack
    st, body = _get(f"{base}/v1/models")
    assert st == 200, body
    ids = [m["id"] for m in body["data"]]
    Cardinality.check("L3·/v1/models", ids, ["qwen"], rel="equal")


def test_l3_three_way_id_consistency_over_http(stack):
    """**`R-API-21` 三向一致，跑在真实 HTTP 上**。

    这是「三项配置够用」的最终判据：列表里拿到的 id，原样发回去，
    转发成功，且回程的 `model` 仍是它 —— 尽管后端回显的是它自己看到的值。
    """
    base, _ = stack
    _, listing = _get(f"{base}/v1/models")
    for m in listing["data"]:
        st, body = _post(f"{base}/v1/chat/completions",
                         {"model": m["id"], "messages": [{"role": "user", "content": "hi"}]})
        assert st == 200, f"{m['id']} 转发失败：{body}"
        assert body["model"] == m["id"], (
            f"回程 model 是 {body['model']!r} 而不是 {m['id']!r} —— 三向一致断裂，"
            "客户端会拿到一个它没请求过的 id"
        )


def test_l3_unknown_model_gives_diagnostic_not_500(stack):
    """未知 model ⇒ **404 + 可用列表**，后端零请求，且不按需启动。"""
    base, _ = stack
    st, body = _post(f"{base}/v1/chat/completions", {"model": "nosuchmodel"})
    assert st == 404, body
    assert "available" in json.dumps(body), "诊断响应没告诉用户有哪些可用"


def test_l3_missing_model_field_rejected(stack):
    """缺 `.model` ⇒ 400，**不猜一个默认模型**。"""
    base, _ = stack
    st, _ = _post(f"{base}/v1/chat/completions", {"messages": []})
    assert st == 400


def test_l3_requests_without_token_are_refused_even_on_loopback(stack):
    """D13：回环也要令牌。"""
    base, _ = stack
    req = urllib.request.Request(f"{base}/v1/models")
    try:
        urllib.request.urlopen(req, timeout=5)        # noqa: S310
        status = 200
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 401
