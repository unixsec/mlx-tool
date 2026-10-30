"""T2 · 协议感知代理。**网关是链上唯一同时看得见两端的环节**，承担 id 翻译。

入站：请求体的 model 字段 短 id → 后端参数
回程：改回短 id
忠实性承诺（D21 订正）：成功响应体经**协议感知改写**；后端 4xx 的状态码透传，
体对本机来源原文、对远端来源净化（去绝对路径与异常片段）；后端 5xx 与鉴权失败
映射为 502，体不透传。
"""
import re


def list_models(route_view):
    # type: (object) -> tuple
    """**`GET /v1/models`**（裁决 R2-1 的第 18 个符号）。

    外部应用（Cherry Studio 等）拉取可用模型的**唯一端点** ——
    「base url + model id + api key 三项即用」里的 model id 就从这里取。

    每个 id **逐字取自路由键集**，不从 models.jsonl 另读一遍
    （那是第二个真理源，路由表刷新后两者会分叉 ⇒
    客户端拿到一个转发时命不中的 id）。
    """
    if route_view.state == "building":
        # **不返回空列表** —— 空列表让客户端显示「没有模型」而不是「服务未就绪」，
        # 两者的用户处置完全不同
        return (503, {"error": {"message": "route table is building", "type": "unavailable"}})
    return (200, {"object": "list",
                  "data": [{"id": k, "object": "model", "owned_by": "mlx-toolkit"}
                           for k in route_view.route_keys()]})


def inbound(body, model_arg):
    # type: (dict, str) -> dict
    """入站改写：短 id → 后端参数。**只改 `.model`，不新增任何字段。**"""
    out = dict(body or {})
    out["model"] = model_arg
    return out


class InboundError(Exception):
    """请求体里有后端不支持、网关也无法等价转换的参数（如 mlx_lm 上的 thinking_budget）。"""

    def __init__(self, status, err_type, message):
        # type: (int, str, str) -> None
        super().__init__(message)
        self.status, self.err_type, self.message = status, err_type, message

    def reply(self):
        # type: () -> tuple
        return (self.status, {"error": {"type": self.err_type, "message": self.message}})


def build_upstream(body, family, snap, peer_class, model_arg, media=None):
    # type: (dict, str, dict, str, str, object) -> tuple
    """入站管线（spec §4.3），顺序固定：**剥离加载类字段 -> 媒体 -> 思考与长度归一 -> 流式化 -> `.model`**。

    其余字段（`tools`、`tool_choice`、`response_format`、`parallel_tool_calls`、`logprobs`、`stop`、
    `top_k`、`thinking_budget`、厂商扩展、所有非媒体 part）逐字节透传。不修改入参。
    -> `(上游请求体, {"client_stream", "client_usage"})`：server 层据此决定直出还是聚合、留不留 usage 帧。
    """
    from . import effort
    from . import media as media_mod
    out = effort.strip_loading(body)
    if media is not None:
        out = media_mod.process(out, family, peer_class, media["nets"], media["limits"],
                                media["ticket"], media["store"], media.get("fetcher"))
    out, err = effort.normalize(out, family, snap or {})
    if err:
        raise InboundError(*err)
    stream_options = out.get("stream_options") if isinstance(out.get("stream_options"), dict) else {}
    meta = {"client_stream": bool(out.get("stream")),
            "client_usage": bool(stream_options.get("include_usage"))}
    # **上游一律流式**（D22）：非流式在后端线程里跑完才返回，长输出必然读超时，且不感知客户端断开
    out["stream"] = True
    if family in ("chat", "completions"):
        out["stream_options"] = dict(stream_options, include_usage=True)
    out["model"] = model_arg
    return out, meta


def outbound(body, short_id):
    # type: (dict, str) -> dict
    """回程改写：`model` 各位置改回短 id，补 `reasoning_content`（`R-API-21` 三向一致）。"""
    from .frames import patch_body
    return patch_body(body, short_id)


def rewrite_frame(frame, short_id, peer_class):
    # type: (bytes, str, str) -> bytes
    """流式逐帧改写。**在 Frame 上改，不对 chunk 做字节级替换**（INV-12）。完整实现见 `frames.rewrite`。

    `peer_class` 必填（无缺省，免得漏传 fail open）：非 LOOPBACK 来源的错误内容帧经 `sanitize`。
    """
    from .frames import rewrite
    return rewrite(frame, short_id, peer_class)


def build_error(status, err_type, message=None, request_id=None):
    # type: (int, str, str, str) -> tuple
    """网关自身错误的**唯一构造点**。**签名里没有 body 入参** ——

    有了它，上游的错误体就能顺着传出去（`X-T2-17-b` 的坏样本）。上游 4xx 的透传与净化
    走 `upstream_error`，不经这里。
    """
    body = {"error": {"message": message or err_type, "type": err_type}}
    if request_id:
        body["error"]["request_id"] = request_id
    return (status, body)


_REDACT = "<redacted>"
_TRACEBACK = re.compile(r"Traceback \(most recent call last\):[\s\S]*")
_FILE_REF = re.compile(r'File "[^"]*"')
# 以 `/`、`~/` 或 `~user/` 开头、前面不是词字符的连续非空白 ——
# 不会误伤 `mlx-community/Qwen…` 这类仓库名
_ABS_PATH = re.compile(r"(?<![\w.-])(?:~[\w.-]*/|/)[^\s'\"]+")
_KEEP_KEYS = frozenset({"type", "code", "param"})


def sanitize(value):
    # type: (object) -> object
    """去掉错误体里会暴露本机布局的片段。

    dict 的 `type` / `code` / `param` **只在值是字符串标量时**原样保留 —— 客户端按它们分支；
    值若是容器（list / dict），照常递归净化（真实场景：多模态 400 的 `param` 是一串图片路径）。
    键本身若是字符串也照常净化 —— 有些后端按文件名给错误体建 key。
    """
    if isinstance(value, str):
        return _ABS_PATH.sub(_REDACT, _FILE_REF.sub(_REDACT, _TRACEBACK.sub(_REDACT, value)))
    if isinstance(value, dict):
        return {
            (sanitize(k) if isinstance(k, str) else k):
                (v if k in _KEEP_KEYS and isinstance(v, str) else sanitize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


def upstream_error(status, body, peer_class):
    # type: (int, object, str) -> tuple
    """后端错误 → 回给客户端的 `(status, body)`（spec §4.7、D21）。

    4xx 透传状态码：客户端要靠 400 / 422 的 `type` 与 `param` 知道自己哪个参数错了，
    一律改成 502 等于让它无从修正。远端来源的体要净化：mlx_vlm 多处 `detail=str(e)`，
    会把本机权重路径与异常栈原样写进响应。
    """
    if status == 401:
        return build_error(502, "upstream_auth_failed", "backend rejected the gateway credentials")
    if status >= 500:
        return build_error(502, "upstream_error", "backend returned an error")
    if not isinstance(body, dict):
        body = {"error": {"message": str(body), "type": "upstream_error"}}
    return (status, body if peer_class == "LOOPBACK" else sanitize(body))
