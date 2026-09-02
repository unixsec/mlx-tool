"""B 层 · **拥有传输**。`HTTPError` 在这一层归一；SSE 分帧是增量解析器。

同步与异步两侧喂**同一个** `SseDecoder` 实例类型（INV-12：跨块的 model 字段
不得被字节级替换改一半）。
"""
import json
import urllib.error
import urllib.request

from .result import Done, Failed, Ok, Unknown


class TransportOutcome:
    """一次传输的结局。`classify` 的入参形态（裁决 R1-1 取窄式）。"""

    __slots__ = ("status", "content_type", "body")

    def __init__(self, status, content_type, body=b""):
        self.status = status
        self.content_type = content_type or ""
        self.body = body


class TransportError(Exception):
    """传输层失败（超时 / 连接重置 / DNS）。`kind` ∈ {TIMEOUT, RESET, UNREACHABLE}。"""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


# WireClass 五元值域（不含「未终结的帧」—— 那要第二个维度）
OK, UPSTREAM_STATUS, PROTOCOL, TIMEOUT, RESET = (
    "OK", "UPSTREAM_STATUS", "PROTOCOL", "TIMEOUT", "RESET")


def classify(outcome):
    # type: (object) -> str
    """一次传输结局的**归一分类唯一读取点**（裁决 R1-1：窄式入参）。

    取窄式的理由：`OK`/`PROTOCOL` 两支要读 `outcome.content_type`，
    而 `Response` 只有 `headers` ⇒ 宽式在这两支上运行期 AttributeError。
    """
    if isinstance(outcome, TransportError):
        return {"TIMEOUT": TIMEOUT, "RESET": RESET}.get(outcome.kind, RESET)
    if outcome.status >= 500:
        return UPSTREAM_STATUS
    if outcome.status == 200 and "text/event-stream" in outcome.content_type:
        return OK
    if outcome.status == 200:
        return PROTOCOL if "json" not in outcome.content_type else OK
    return UPSTREAM_STATUS


class SseDecoder:
    """增量分帧。**自行缓冲拼帧，不假设分块对齐帧边界**（`R-API-23` 四条之一）。"""

    __slots__ = ("_buf",)

    def __init__(self):
        self._buf = b""

    def feed(self, chunk):
        # type: (bytes) -> list
        """喂一块字节，吐出**完整帧**列表。半截帧留在缓冲里。"""
        self._buf += chunk
        frames, parts = [], self._buf.split(b"\n\n")
        self._buf = parts.pop()               # 最后一段可能是半截
        for p in parts:
            if p.strip():
                frames.append(p + b"\n\n")
        return frames

    def pending(self):
        # type: () -> bytes
        """未终结的帧。流结束时它非空 ⇒ 上游截断，**不得当作正常结束**。"""
        return self._buf


def request(url, payload=None, headers=None, timeout_sec=30, opener=None):
    # type: (str, object, dict, int, object) -> object
    """一次 HTTP 请求。**HTTPError 在这一层归一**，不向上抛。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        op = opener or urllib.request.urlopen
        with op(req, timeout=timeout_sec) as resp:
            return Done(TransportOutcome(resp.status,
                                         resp.headers.get("Content-Type"),
                                         resp.read()))
    except urllib.error.HTTPError as e:
        return Done(TransportOutcome(e.code, e.headers.get("Content-Type"), e.read()))
    except TimeoutError:
        return Failed(3, "wire.timeout", [], kind="TIMEOUT", seconds=timeout_sec)
    except urllib.error.URLError as e:
        return Failed(3, "wire.unreachable", [], url=url, detail=str(e.reason))


def connect_ex(host, port, timeout_sec=1):
    # type: (str, int, int) -> object
    """TCP 可连通性。三态：`Ok(True)` / `Ok(False)` / `Unknown`。"""
    import socket
    s = socket.socket()
    s.settimeout(timeout_sec)
    try:
        return Ok(s.connect_ex((host, port)) == 0)
    except socket.timeout:
        return Unknown("TIMEOUT")
    except OSError as e:
        return Unknown("TOOL_UNAVAILABLE", str(e))
    finally:
        s.close()
