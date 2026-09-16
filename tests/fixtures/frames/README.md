# 上游帧夹具

mlx-lm 0.31.3 与 mlx-vlm 0.6.17 的流式帧与非流式响应体，**由上游帧构造函数生成**（不加载模型），2026-09-15 冻结。
生成脚本在开发仓库 `_local/superpowers/plans/fixtures/2026-09-15-frames/generate.py`（部署 venv 运行）。
responses 与 Anthropic 的事件序列在上游是端点函数内联拼的，生成脚本用同样的模型类与 `_sse_event` 按源码顺序复现。
后端看到的 `model` 统一是 `BACKEND-ARG`，网关回程必须把它改回短 id。升级上游后重新生成并更新 `SHA256SUMS`。
