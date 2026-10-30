"""T2 · SSE 帧解析与回程改写（spec §4.6、§4.7）。**在完整帧上改**（INV-12），不对字节块做替换。

回程要做的事：`model` 改回短 id（任何深度的字符串值，用户数据子树除外）；有 `reasoning` 无 `reasoning_content` 时补上
（客户端普遍读后者；实测只有 mlx_lm 缺）；客户端没要 usage 时丢掉网关为聚合而开的 usage 帧；
中途错误帧对远端来源净化（mlx_vlm 把异常原文写进错误帧）。
"""
import copy
import json


def parse(frame):
    # type: (bytes) -> tuple
    """→ `(event, data 文本)`。注释帧（如 `: keepalive`）⇒ `(None, None)`。"""
    event, data = None, None
    for line in frame.decode("utf-8", "replace").split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].lstrip(" ")
    return event, data


# 用户数据所在的子树：工具入参、调用方元数据、JSON schema、工具定义。里面叫 `model` 的键是用户的，不改
_USER_SUBTREES = frozenset({"input", "metadata", "parameters", "schema", "tools", "input_schema"})


def _rewrite_model(value, short_id):
    # type: (object, str) -> None
    """任何深度上**字符串值**的 `model` 改成短 id（上游新增嵌套位置时不漏出模型目录名）。"""
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "model" and isinstance(v, str):
                value[k] = short_id
            elif k not in _USER_SUBTREES and isinstance(v, (dict, list)):
                _rewrite_model(v, short_id)
    elif isinstance(value, list):
        for v in value:
            _rewrite_model(v, short_id)


def _patch(obj, short_id):
    # type: (dict, str) -> dict
    if "model" in obj:
        obj["model"] = short_id
    _rewrite_model(obj, short_id)
    for choice in obj.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        for key in ("delta", "message"):
            msg = choice.get(key)
            if isinstance(msg, dict) and isinstance(msg.get("reasoning"), str) \
                    and not isinstance(msg.get("reasoning_content"), str):
                msg["reasoning_content"] = msg["reasoning"]
    return obj


_ERROR_EVENTS = ("error", "response.failed")


def track(frame, state):
    # type: (bytes, dict) -> None
    """记下已下发帧里的 `sequence_number`（Responses 的错误事件要接着编号）与是否已有错误帧。"""
    event, data = parse(frame)
    if not data or data.strip() == "[DONE]":
        return
    try:
        obj = json.loads(data)
    except ValueError:
        return
    if isinstance(obj, dict):
        seq = obj.get("sequence_number")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq > state["seq"]:
            state["seq"] = seq
    if is_error_frame(event, obj):
        state["errored"] = True


def error_event(family, message, err_type="upstream_error", code="upstream_aborted", seq=0):
    # type: (str, str, str, str, int) -> bytes
    """响应头已发之后的错误，按各协议的原生格式写成一帧（客户端 SDK 据此报错，而不是当成正常结束）。"""
    if family == "messages":
        api_type = "invalid_request_error" if err_type == "invalid_request_error" else "api_error"
        event, obj = "error", {"type": "error", "error": {"type": api_type, "message": message}}
    elif family == "responses":
        event, obj = "error", {"type": "error", "code": code, "message": message, "param": None,
                               "sequence_number": seq}
    else:
        event, obj = None, {"error": {"message": message, "type": err_type, "code": code}}
    head = "event: " + event + "\n" if event else ""
    return (head + "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def is_error_frame(event, obj):
    # type: (object, object) -> bool
    """帧是否带上游错误内容（rewrite 的净化判据与 Aggregator 的出错判据共用）。

    白名单口径：只有「正常内容帧」不算 —— dict、无 `error` / `detail` / 字符串型顶层 `message`、
    事件与 `type` 都不是 error / response.failed、`response.error` 为空。
    （Anthropic `message_start` 的 `message` 是 dict，属正常内容，不算。）
    """
    if not isinstance(obj, dict):
        return True
    if event in _ERROR_EVENTS or obj.get("type") in _ERROR_EVENTS:
        return True
    if "error" in obj or "detail" in obj or isinstance(obj.get("message"), str):
        return True
    inner = obj.get("response")
    return isinstance(inner, dict) and bool(inner.get("error"))


def rewrite(frame, short_id, peer_class="REMOTE", drop_usage=False):
    # type: (bytes, str, str, bool) -> bytes
    """缺省 `peer_class="REMOTE"`：漏传时 fail closed。只有 LOOPBACK 看到上游原文。"""
    from .translate import sanitize, scrub_error
    event, data = parse(frame)
    if data is None or data.strip() == "[DONE]":
        return frame
    head = "event: " + event + "\n" if event else ""
    try:
        obj = json.loads(data)
    except ValueError:
        if peer_class == "LOOPBACK":
            return frame                               # 不可解析的帧原样过，不猜
        return (head + "data: " + sanitize(data) + "\n\n").encode("utf-8")
    if isinstance(obj, dict):
        if drop_usage and obj.get("choices") == [] and obj.get("usage") is not None \
                and not is_error_frame(event, obj):
            return b""
        if peer_class != "LOOPBACK" and is_error_frame(event, obj):
            obj = scrub_error(obj)
        _patch(obj, short_id)
    elif peer_class == "LOOPBACK":
        return frame
    else:
        obj = sanitize(obj)
    return (head + "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def patch_body(body, short_id):
    # type: (dict, str) -> dict
    """非流式回程改写。不修改入参。"""
    return _patch(copy.deepcopy(body or {}), short_id)
