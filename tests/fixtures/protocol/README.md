# 多轮协议请求体夹具

按 Anthropic Messages 与 OpenAI Responses 的公开请求结构**构造**（写计划时拿不到真实客户端流量）。
覆盖 Claude Code 的 `system` 数组与 `cache_control`、`thinking` 块（含 `signature`）、`tool_use`、`tool_result` 内嵌图片，
Codex 的 `reasoning` 项（含 `encrypted_content`）、`function_call`、字符串 `output` 的 `function_call_output`、`output_text`、`input_image`。
这两个构造夹具保留（断言按下标取值）；真实抓取另存为 `captured_*.json`，见下节。

## 真实抓取

`captured_claude_code_messages.json`、`captured_codex_responses.json` 于 2026-10-29 经 `capture_proxy`（只落请求体、不记请求头）
从 Claude Code 2.1.312（`claude --bare -p`）与 Codex 0.149.1（`codex exec --ignore-user-config`）抓取，
工作目录只含 `app.py` 与 `shot.png`。分别是各自轮次最多的那个请求体：messages 含两轮 `tool_use` / `tool_result`
（其中一个 `tool_result` 内嵌 base64 图片）与 `thinking`；responses 含两轮 `function_call` / `function_call_output`，`tools` 保留完整（20 个）。

脱敏只换值、不动结构（键集、列表长度、字符串长度都不变，原本相等的 ID 换完仍相等）：
`metadata.user_id` 里的设备与会话 ID、`client_metadata` 与 `prompt_cache_key` 里的安装 / 会话 / 轮次 ID 换成固定占位符；
路径与 `ls -l` 属主列里的本机用户名换成 `user`。每次调用随机生成的 `toolu_*` / `call_*` / `msg_*` 未换。

断言与位置无关：`tests/unit/test_inbound.py::test_captured_client_bodies_pass_through`。
