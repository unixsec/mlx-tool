"""T2 · 协议感知代理。**网关是链上唯一同时看得见两端的环节**，承担 id 翻译。

入站：请求体的 model 字段 短 id → 后端参数
回程：改回短 id
忠实性承诺（`R-API-23` 改写后）：状态码与 content-type 忠实回传；
成功响应体经**协议感知改写**；**错误响应体不透传**（防上游内部信息泄漏）。
"""
import json


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


def outbound(body, short_id):
    # type: (dict, str) -> dict
    """回程改写：后端回显的 model 值 → 短 id（`R-API-21` 三向一致）。"""
    out = dict(body or {})
    if "model" in out:
        out["model"] = short_id
    return out


def rewrite_frame(frame, short_id):
    # type: (bytes, str) -> bytes
    """流式逐帧改写。**在 Frame 上改，不对 chunk 做字节级替换**（INV-12）。

    坏样本：对 chunk 做 `.replace()` ⇒ 跨块的 model 字段被改一半。
    """
    if not frame.startswith(b"data: "):
        return frame
    payload = frame[len(b"data: "):].strip()
    if payload == b"[DONE]":
        return frame                       # 结束标记原样过
    try:
        obj = json.loads(payload)
    except ValueError:
        return frame                       # 不可解析的帧原样过，不猜
    if "model" in obj:
        obj["model"] = short_id
    return b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n"


def build_error(status, msg_key, request_id=None):
    # type: (int, str, str) -> tuple
    """错误响应的**唯一构造点**。**签名里没有 body 入参** ——

    有了它，上游的错误体就能顺着传出去（`X-T2-17-b` 的坏样本）。
    """
    body = {"error": {"message": msg_key, "type": "upstream_error"}}
    if request_id:
        body["error"]["request_id"] = request_id
    return (status, body)
