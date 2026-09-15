# 模型夹具

**原样取自真实权重，不手写**（`verify-against-reality`：夹具的形状必须来自产生真实数据的东西）。

| 文件 | 来源 |
|---|---|
| `qwen38/config.json`、`qwen38/chat_template.jinja` | `mlx-community/Qwen3.8-27B-8bit`（snapshot `main`，2026-09-15 取样） |
| `qwen38-mtp/config.json` | `mlx-community/Qwen3.8-27B-MTP-4bit`（snapshot `main`，2026-09-15 取样） |

许可：Apache-2.0（上游模型许可），允许随仓库再分发。
`SHA256SUMS` 由 `tests/unit/test_fixtures_integrity.py` 核对 —— 手改夹具会红。
