"""T2 · 上游流式 → 非流式响应（spec §4.6、D22）。

网关对上游**一律发流式**：非流式请求在后端线程里跑完才返回，81920 token 可能跑十几分钟，
单次读等待必然超时；而且非流式在 mlx_vlm 上**不感知客户端断开**（openai.py 2146-2179），
客户端放弃后后端仍占着一个并发槽跑到底。流式则每个 token 一次读、断开即取消。
客户端要非流式时，由这里把帧拼回上游自己非流式响应的形状。
"""
import json

from .frames import is_error_frame, parse


class AggregateError(Exception):
    """上游中途出错或流被截断。消息可能含上游原文 —— 回给客户端前按来源净化。"""


class Aggregator:
    FAMILIES = ("chat", "completions", "responses", "messages")

    def __init__(self, family):
        # type: (str) -> None
        if family not in self.FAMILIES:
            raise ValueError("不支持的端点族：" + str(family))
        self.family = family
        self.done = False
        self.error = None
        self._base = None
        self._content, self._reasoning, self._logprobs = [], [], []
        self._tools, self._order = {}, []
        self._finish = None
        self._usage = None
        self._completed = None
        self._message = None
        self._blocks, self._partial = {}, {}

    def feed(self, frame):
        # type: (bytes) -> None
        event, data = parse(frame)
        if data is None:
            return
        if data.strip() == "[DONE]":
            self.done = True
            return
        try:
            obj = json.loads(data)
        except ValueError:
            return
        if isinstance(obj, dict):
            getattr(self, "_feed_" + self.family)(event, obj)

    # ── chat / completions ──────────────────────────────────────────
    def _feed_chat(self, event, obj):
        if is_error_frame(event, obj):
            err = obj.get("error") or obj.get("detail") or obj.get("message") or "upstream error"
            self.error = err if isinstance(err, str) else json.dumps(err, ensure_ascii=False)
            return
        if self._base is None:
            self._base = dict((k, obj[k]) for k in ("id", "created", "model", "system_fingerprint")
                               if k in obj)
        if obj.get("usage"):
            self._usage = obj["usage"]
        for choice in obj.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                self._content.append(delta["content"])
            reasoning = delta.get("reasoning_content")
            if not isinstance(reasoning, str):
                reasoning = delta.get("reasoning")
            if isinstance(reasoning, str):
                self._reasoning.append(reasoning)
            for call in delta.get("tool_calls") or []:
                idx = call.get("index", len(self._order))
                cur = self._tools.get(idx)
                if cur is None:
                    cur = {"id": call.get("id"), "type": call.get("type") or "function",
                           "function": {"name": "", "arguments": ""}}
                    self._tools[idx] = cur
                    self._order.append(idx)
                fn = call.get("function") or {}
                if call.get("id"):
                    cur["id"] = call["id"]
                if fn.get("name"):
                    cur["function"]["name"] = fn["name"]
                if isinstance(fn.get("arguments"), str):
                    cur["function"]["arguments"] += fn["arguments"]
            logprobs = choice.get("logprobs")
            if isinstance(logprobs, dict) and isinstance(logprobs.get("content"), list):
                self._logprobs.extend(logprobs["content"])
            if isinstance(choice.get("text"), str):
                self._content.append(choice["text"])
            if choice.get("finish_reason"):
                self._finish = choice["finish_reason"]

    _feed_completions = _feed_chat

    def _result_chat(self):
        message = {"role": "assistant", "content": "".join(self._content) or None}
        reasoning = "".join(self._reasoning)
        if reasoning:
            message["reasoning"] = reasoning
            message["reasoning_content"] = reasoning
        if self._order:
            message["tool_calls"] = [self._tools[i] for i in sorted(self._order)]
        choice = {"index": 0, "finish_reason": self._finish, "message": message,
                  "logprobs": {"content": self._logprobs} if self._logprobs else None}
        out = dict(self._base or {}, object="chat.completion", choices=[choice])
        if self._usage is not None:
            out["usage"] = self._usage
        return out

    def _result_completions(self):
        choice = {"index": 0, "finish_reason": self._finish, "text": "".join(self._content)}
        out = dict(self._base or {}, object="text_completion", choices=[choice])
        if self._usage is not None:
            out["usage"] = self._usage
        return out

    # ── responses ───────────────────────────────────────────────────
    def _feed_responses(self, event, obj):
        kind = obj.get("type") or event
        if kind == "response.completed":
            self._completed = obj.get("response")
            self.done = True
        elif kind in ("error", "response.failed"):
            err = obj.get("error") or (obj.get("response") or {}).get("error") or {}
            self.error = err.get("message") if isinstance(err, dict) else str(err)

    # ── Anthropic messages ──────────────────────────────────────────
    def _feed_messages(self, event, obj):
        kind = obj.get("type") or event
        if kind == "error":
            self.error = (obj.get("error") or {}).get("message") or "upstream error"
        elif kind == "message_start":
            self._message = dict(obj.get("message") or {})
        elif kind == "content_block_start":
            self._blocks[obj["index"]] = dict(obj.get("content_block") or {})
        elif kind == "content_block_delta":
            block = self._blocks.setdefault(obj["index"], {})
            delta = obj.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                block["text"] = block.get("text", "") + delta.get("text", "")
            elif dtype == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
            elif dtype == "signature_delta":
                block["signature"] = block.get("signature", "") + delta.get("signature", "")
            elif dtype == "input_json_delta":
                self._partial[obj["index"]] = self._partial.get(obj["index"], "") + delta.get("partial_json", "")
        elif kind == "content_block_stop":
            idx = obj["index"]
            if idx in self._partial:
                try:
                    self._blocks[idx]["input"] = json.loads(self._partial.pop(idx) or "{}")
                except ValueError:
                    self.error = "tool_use input is not valid JSON"
        elif kind == "message_delta":
            if self._message is None:
                self._message = {}
            delta = obj.get("delta") or {}
            for key in ("stop_reason", "stop_sequence"):
                if key in delta:
                    self._message[key] = delta[key]
            if isinstance(obj.get("usage"), dict):
                self._message["usage"] = dict(self._message.get("usage") or {}, **obj["usage"])
        elif kind == "message_stop":
            self.done = True

    def result(self):
        # type: () -> dict
        if self.error is not None:
            raise AggregateError(self.error)
        if self.family == "responses":
            if self._completed is None:
                raise AggregateError("upstream stream ended before response.completed")
            return self._completed
        if self.family == "messages":
            if self._message is None or not self.done:
                raise AggregateError("upstream stream ended before message_stop")
            return dict(self._message, content=[self._blocks[i] for i in sorted(self._blocks)])
        if not self.done and self._finish is None:
            raise AggregateError("upstream stream ended before completion")
        return self._result_chat() if self.family == "chat" else self._result_completions()
