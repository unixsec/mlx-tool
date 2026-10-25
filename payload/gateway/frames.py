"""T2 · SSE 帧解析与回程改写（spec §4.6、§4.7）。**在完整帧上改**（INV-12），不对字节块做替换。

回程要做的事：`model` 改回短 id（chat / completions 顶层、responses 事件里的 `response.model`、
Anthropic `message_start` 的 `message.model`）；有 `reasoning` 无 `reasoning_content` 时补上
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


def _patch(obj, short_id):
    # type: (dict, str) -> dict
    if "model" in obj:
        obj["model"] = short_id
    for key in ("response", "message"):
        inner = obj.get(key)
        if isinstance(inner, dict) and "model" in inner:
            inner["model"] = short_id
    for choice in obj.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        for key in ("delta", "message"):
            msg = choice.get(key)
            if isinstance(msg, dict) and isinstance(msg.get("reasoning"), str) \
                    and not isinstance(msg.get("reasoning_content"), str):
                msg["reasoning_content"] = msg["reasoning"]
    return obj


def rewrite(frame, short_id, peer_class="LOOPBACK", drop_usage=False):
    # type: (bytes, str, str, bool) -> bytes
    event, data = parse(frame)
    if data is None or data.strip() == "[DONE]":
        return frame
    try:
        obj = json.loads(data)
    except ValueError:
        return frame                                   # 不可解析的帧原样过，不猜
    if not isinstance(obj, dict):
        return frame
    if drop_usage and obj.get("choices") == [] and obj.get("usage") is not None:
        return b""
    is_error = event == "error" or ("error" in obj and "choices" not in obj)
    if is_error and peer_class != "LOOPBACK":
        from .translate import sanitize
        obj = sanitize(obj)
    _patch(obj, short_id)
    head = "event: " + event + "\n" if event else ""
    return (head + "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def patch_body(body, short_id):
    # type: (dict, str) -> dict
    """非流式回程改写。不修改入参。"""
    return _patch(copy.deepcopy(body or {}), short_id)
