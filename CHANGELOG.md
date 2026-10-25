# 变更记录 · Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### 新增

- **按权重自动选择后端**：每次 `mlx up` 读 `config.json` 判定多模态，走 `mlx_vlm.server` 或 `mlx_lm.server`；
  `--dry-run` 打印判定规则、依据、thinking 默认、effort 值域与就绪等待上限
- **两把密钥**：`backend.api_key`（mlx_vlm 后端）与 `api.token`（网关），安装时由 `mlx bootstrap keys` 生成，
  `settings.json` 与运行记录一律 0600，所有显示打码，`config get --reveal` 看原值；`status` 提示轮换后需重启的服务
- **新配置键**：`modality`（auto / text / vision）、`max_concurrency`（默认 8）
- `mlx model info` 显示模态依据、模板默认的思考开关与 effort 值域；`model ls` 增「模态」列；`status` 增「后端」「并发」列
- `mlx model repair` 清除 1.0.0 旧字段与退役配置键
- **多来源接入**：网关放行本机、同一 Wi-Fi / 有线网段、Parallels / VMware 共享网络的虚拟机（按硬件端口类型与虚拟机网桥判定，VPN 与雷雳网桥拒绝），可用 `api.allow_cidrs` 追加网段
- **端点**：`/v1/responses`（含按 id 查询、删除、取消）与 Anthropic `/v1/messages`（多模态后端）；路径可省略 `/v1`
- **可选 TLS**：`api.tls_cert` / `api.tls_key`，最低 TLS 1.2
- `mlx api info` 列出各来源的 OpenAI 与 Anthropic base URL、放行网段、令牌与 TLS 状态；`mlx check` 增至 12 项（放行网段、网关端口外来占用、settings.json 权限）
- **媒体代取**：`http(s)://` 图片、音频、视频由网关取回再交给模型，模型进程不再对外连接；虚拟机与 Wi-Fi 设备只能取公网与放行网段，重定向逐跳校验；视频落临时文件（`<数据根>/run/media/`），请求结束即删。上限 `api.media_max_mb`（128，只限代取字节）、`api.media_max_parts`（32）、`api.inflight_max_mb`（2048，超出 503 `gateway_busy` 并带 `Retry-After: 5`）；视频临时文件写不下返回 507 `media_storage_failed`
- **保活与断连取消**：流式请求在模型无输出满 15 秒时收到 `: keepalive`；客户端断开即取消生成（日志事件 `client_disconnected`）
- 回程补 `reasoning_content`（文本模型只给 `reasoning`）；responses 与 messages 事件里的 `model` 同样改回短 id

### 变更

- `max_tokens` 默认 4096 → **81920**
- `--type` 变为可选，含义改为「模态覆盖」，写进该模型的 `modality`；不给不再报「无法判定类型」
- 安装器默认安装 **mlx-vlm**（会带入 torch 等依赖，安装时间变长）；采集失败时打印原因
- `MLX_GATEWAY_TOKEN` 仍可覆盖，但须满足令牌值域（32–128 位 `[A-Za-z0-9_-]`），过短的会被 `mlx up api` 拒绝
- **`api.bind` 默认 127.0.0.1 → 0.0.0.0**（不兼容）。只给本机用：`mlx config set api.bind --value=127.0.0.1`
- **所有来源都要令牌**，本机也不例外（不兼容）。1.0.0 在回环上免鉴权
- **端点收紧**（不兼容）：此前任意 POST 路径都转发给后端；现在白名单之外一律 404，后端零请求
- 后端 4xx 状态码透传（本机来源原文、远端来源去掉路径与异常片段）；后端 5xx 与鉴权失败返回 502
- 网关请求体上限 8 MB → 256 MB（`api.max_body_mb`）；令牌与来源校验在读请求体之前完成
- `mlx up api` 在令牌为空、TLS 配置不完整或私钥权限过宽、网关端口已被别的进程占用时拒绝启动
- **网关到模型上游一律流式**：非流式请求由网关聚合成完整 JSON，81920 token 的长输出不再被读超时截断
- 思考强度 `effort` 按模型模板接受的取值映射（qwen38：`minimal`→`low`，`high` / `max`→`xhigh`），越界不再让后端报错
- 多模态模型上 `max_completion_tokens` 生效（网关换成后端认的 `max_tokens`）；文本模型收到 `thinking_budget` 返回 400 `thinking_budget_unsupported`；多模态后端的 `enable_thinking` / `reasoning_effort` 由网关写到请求顶层
- 请求里的 `adapters`、`adapter_path`、`draft_model` 被网关剥离，客户端不能让模型加载别的权重
- **连不上模型上游由 504 改为 502 `upstream_error`**；日志事件 `sse_stream_aborted` 改名 `upstream_aborted`，与客户端自己断开的 `client_disconnected` 区分；按 id 的查询 / 删除 / 取消端点上后端对 2xx 回非 JSON 体同样是 502，请求体不是 JSON 对象是 400

### 移除

- `share/types.tsv`、`backend` 配置键、登记期类型判定链

### 升级须知

1. 重跑 `bash deployer/install.sh`（会删除退役的 `share/types.tsv`、装 mlx-vlm、生成密钥）
2. 跑一次 `mlx model repair` 清除旧字段
3. MTP drafter 不再可用（mlx-vlm #2033）；配过 `draft_model` 指向 MTP 模型的，`mlx config unset <id> --key=draft_model`
4. 网关与模型进程不再继承 shell 里的 `MLX_BACKEND_API_KEY` / `MLX_GATEWAY_TOKEN`：后端密钥请用 `mlx config set backend.api_key --value=…`（或 `mlx bootstrap keys` 生成）；网关令牌仍可用 `MLX_GATEWAY_TOKEN=… mlx up api` 临时覆盖
5. 网关现在对局域网与虚拟机开放。只给本机用的：`mlx config set api.bind --value=127.0.0.1`；
   所有客户端都要改为带令牌（`mlx config get --key=api.token --reveal`）

---

## [1.0.0] — 2026-09-02

首个发布版本。全链路已在真机（Apple Silicon / macOS 26.6）跑通并验证：
下载 27.5 GB 模型 → 登记 → 起服务 → 网关 → Cherry Studio 接入 → 流式推理。

### 能做什么

- **模型生命周期**：`model install` / `register` / `ls` / `info` / `rm` / `gc` /
  `repair`，权重删除进回收站可恢复（`model trash {ls,restore,empty}`）
- **服务管理**：`up` / `down` / `restart`，支持多目标与 `--all`，
  启动前做容量判档，不让内存装不下的模型起来
- **OpenAI 兼容网关**：`up api` + `api info`，`/v1/models` 与
  `/v1/chat/completions`，支持流式
- **配置**：`config get/set/unset/ls/diff/export/import/validate`，
  用户段与机器段按 `schema.tsv` 的 scope 分离
- **诊断**：`check` 九项健康检查、`status` 双语义、`version` 含两个解释器与
  后端包指纹、`caps refresh/show`
- **交互**：`chat` 多轮 REPL、`bench` 单次压测

### 设计要点

- **T1 零第三方依赖**：整个 CLI 只 import 标准库，venv 损坏时仍能自我诊断。
  解释器按 venv → Homebrew → 系统三级回落
- **三根分离**：代码 / 状态 / 权重三个独立目录，重装只重写代码根
- **六张 TSV 表为 SSOT**：解析器、`help`、补全均由表生成
- **类型化结果**：`Done` / `Refused` / `Failed`，`Refused` 保证零资源已创建
  （静态守卫强制）
- **三态探针**：`Ok` / `Unknown`，「判不出来」不塌成「否」

### 已知缺口

见 [KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md)。主要是：`mlx up` 报「已启动」
时后端可能仍在加载模型；`model rm --dry-run` 等若干动词尚未实现预览；
`logs` / `model convert` / `model upgrade` 未实现。

### 环境要求

macOS on Apple Silicon，Homebrew Python ≥ 3.10。**不能用 macOS 自带的
3.9.6** —— `mlx ≥ 0.30.4` 只发 cp310+ 的 wheel，而跑当前模型架构需要
`mlx-lm ≥ 0.31.0`（它依赖前者）。
