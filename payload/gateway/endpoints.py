"""T2 · 端点白名单、路径规范化与扇出（spec §4.2、D9）。

**按后端实际能力开放**：路由之后再查目标运行记录的 `endpoints`（来自 `backends.tsv`）——
mlx_lm 没有 responses / messages，mlx_vlm 没有 completions；发错了回 404 并说明原因，后端零请求。
管理端点（`/unload`、`/v1/settings` 等）与 embeddings / rerank / images / audio 一律不经网关：
后者只对相应类型的模型有意义，对生成模型调用会在另一缓存组里再加载一份权重。
"""
import queue
import re
import threading
import time

FANOUT_TIMEOUT_SEC = 10.0
# 运行记录没有 `endpoints` 的服务是 1.1.0 之前起的，那时只有 mlx_lm
LEGACY_ENDPOINTS = ("chat", "completions")

# responses 的 id 只收保守字符集：`..`、编码斜杠等一律不匹配，不会被后端解析成别的路径
_ID = r"[A-Za-z0-9_-]{1,128}"

# (方法, 规范化路径, 端点族, 路由方式)。body = 按请求体 `.model`；
# fanout = 按 id 查询、不知道在哪个后端 ⇒ 并发问所有支持该族的在跑后端；local = 网关自己回答
RULES = (
    ("POST", r"/v1/chat/completions", "chat", "body"),
    ("POST", r"/v1/completions", "completions", "body"),
    ("POST", r"/v1/responses", "responses", "body"),
    ("POST", r"/v1/responses/input_tokens", "responses", "body"),
    ("GET", r"/v1/responses/" + _ID, "responses", "fanout"),
    ("DELETE", r"/v1/responses/" + _ID, "responses", "fanout"),
    ("POST", r"/v1/responses/" + _ID + r"/cancel", "responses", "fanout"),
    ("GET", r"/v1/responses/" + _ID + r"/input_items", "responses", "fanout"),
    ("POST", r"/v1/messages", "messages", "body"),
    ("POST", r"/v1/messages/count_tokens", "messages", "body"),
    ("GET", r"/v1/models", None, "local"),
    ("GET", r"/healthz", None, "local"),
)
_COMPILED = tuple((m, re.compile(p + r"\Z"), fam, how) for m, p, fam, how in RULES)


def normalize(raw_path):
    # type: (str) -> tuple
    """→ `(规范化路径, 查询串)`。不带 `/v1` 的补上（`/healthz` 除外）；首尾斜杠归一。"""
    path, _, query = (raw_path or "/").partition("?")
    path = "/" + path.strip("/")
    if path != "/healthz" and path != "/v1" and not path.startswith("/v1/"):
        path = "/v1" + (path if path != "/" else "/")
    return path, query


def match(method, path):
    # type: (str, str) -> object
    for m, rx, fam, how in _COMPILED:
        if m == method and rx.match(path):
            return (fam, how)
    return None


def endpoints_of(snap):
    # type: (object) -> tuple
    """键缺失、或键在但值是空列表（`backends.tsv` 的 `endpoints` 列写了 `-` 或留空，
    `write_record` 把两者都规范成 `[]`）—— 两种情况都当「未给」，回退到 `LEGACY_ENDPOINTS`。"""
    eps = (snap or {}).get("endpoints")
    return tuple(eps) if isinstance(eps, list) and eps else LEGACY_ENDPOINTS


def fanout(calls, timeout_sec=FANOUT_TIMEOUT_SEC):
    # type: (list, float) -> tuple
    """并发调用；**首个 2xx 立即返回**。否则：有超时 ⇒ 504；有 5xx 或传输失败 ⇒ 502；
    有非 404 的 4xx ⇒ 原样返回第一个；全部 404（或没有目标）⇒ 404。"""
    if not calls:
        return (404, None, None)
    q = queue.Queue()
    for sid, fn in calls:
        def run(sid=sid, fn=fn):
            try:
                q.put((sid, fn()))
            except Exception:                           # noqa: BLE001
                q.put((sid, None))
        threading.Thread(target=run, daemon=True).start()
    deadline = time.monotonic() + timeout_sec
    got = []
    while len(got) < len(calls):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            sid, out = q.get(timeout=left)
        except queue.Empty:
            break
        if out is not None and 200 <= out[0] < 300:
            return (out[0], out[1], sid)
        got.append((sid, out))
    if len(got) < len(calls):
        return (504, None, None)
    if any(out is None or out[0] >= 500 for _, out in got):
        return (502, None, None)
    for sid, out in got:
        if out[0] != 404:
            return (out[0], out[1], sid)
    return (404, None, None)
