# 多轮协议请求体夹具

按 Anthropic Messages 与 OpenAI Responses 的公开请求结构**构造**（写计划时拿不到真实客户端流量）。
覆盖 Claude Code 的 `system` 数组与 `cache_control`、`thinking` 块（含 `signature`）、`tool_use`、`tool_result` 内嵌图片，
Codex 的 `reasoning` 项（含 `encrypted_content`）、`function_call`、字符串 `output` 的 `function_call_output`、`output_text`、`input_image`。
真机验收前置步骤会抓真实请求体替换这两个文件，`tests/unit/test_inbound.py` 的断言不变。
