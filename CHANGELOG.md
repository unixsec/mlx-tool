# 变更记录 · Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [1.1.0] — 2026-10-27

### 新增

- **按权重自动选择后端**：每次 `mlx up` 读 `config.json` 判定多模态，走 `mlx_vlm.server` 或 `mlx_lm.server`；
  `--dry-run` 打印判定规则、依据、thinking 默认、effort 值域与就绪等待上限
- **两把密钥**：`backend.api_key`（mlx_vlm 后端）与 `api.token`（网关），安装时由 `mlx bootstrap keys` 生成，
  `settings.json` 与运行记录一律 0600，所有显示打码，`config get --reveal` 看原值；`status` 提示轮换后需重启的服务
- **新配置键**：`modality`（auto / text / vision）、`max_concurrency`（默认 8）
- `mlx model info` 显示模态依据、模板默认的思考开关与 effort 值域；`model ls` 增「模态」列；`status` 增「后端」「并发」列
- `mlx model repair` 清除 1.0.0 旧字段与退役配置键
- **安装器可放心重跑**：代码、配置、PATH 都没变且自检通过时直接跳过（不停服务），`--force` 强制重装；重装前把 `settings.json`、`models.jsonl` 备份进数据根 `backups/`（与最近一份相同则不重复，shell rc 的备份同样去重）；每次安装输出另存 `logs/install/`；结尾跑 `mlx check` 并给出安装结果清单与下一步；屏幕上的路径把 home 显示成 `~`
- 安装器开头检查平台：不是 Apple Silicon 直接停下（rc 70，不改任何东西）；缺 ≥3.10 的 Homebrew Python 时先提醒
- **多来源接入**：网关放行本机、同一 Wi-Fi / 有线网段、Parallels / VMware 共享网络的虚拟机（按硬件端口类型与虚拟机网桥判定，VPN 与雷雳网桥拒绝），可用 `api.allow_cidrs` 追加网段
- **端点**：`/v1/responses`（含 `input_tokens`、按 id 查询 / 删除 / 取消、`input_items`）与 Anthropic `/v1/messages`、`/v1/messages/count_tokens`（多模态后端）；`/v1/completions` 只开给文本后端；路径可省略 `/v1`
- **CORS**：预检请求在鉴权之前回 204，浏览器里的 Web 客户端可以直接调网关（真正的请求仍要令牌，`POST` 须 `application/json`）
- **可选 TLS**：`api.tls_cert` / `api.tls_key`，最低 TLS 1.2
- `mlx api info` 列出各来源的 OpenAI 与 Anthropic base URL、放行网段、令牌与 TLS 状态；`mlx check` 增至 12 项（放行网段、网关端口外来占用、settings.json 权限）
- **媒体代取**：`http(s)://` 图片、音频、视频由网关取回再交给模型，模型进程不再对外连接；虚拟机与 Wi-Fi 设备只能取公网与放行网段，重定向逐跳校验；视频落临时文件（`<数据根>/run/media/`），请求结束即删。上限 `api.media_max_mb`（128，只限代取字节）、`api.media_max_parts`（32）、`api.inflight_max_mb`（2048，超出 503 `gateway_busy` 并带 `Retry-After: 5`）；视频临时文件写不下返回 507 `media_storage_failed`
- **保活与断连取消**：流式请求在模型无输出满 15 秒时收到 `: keepalive`；客户端断开即取消生成（日志事件 `client_disconnected`）
- 回程补 `reasoning_content`（文本模型只给 `reasoning`）；responses 与 messages 事件里的 `model` 同样改回短 id
- 网关日志落盘：网关以 `python3 -u` 启动，启动信息与每条 http 访问记录（带来源地址）都写进 `<数据根>/logs/api.log`
- `mlx status` / `mlx api info` 在网关运行记录 `run/api.json` 损坏时不再整屏失败：显示已知部分，并给出恢复步骤

### 变更

- `max_tokens` 默认 4096 → **81920**
- **权重根默认 `~/lab/models/mlx` → `~/.local/share/mlx_models`**（与另外两根同样走 XDG 布局）。只影响全新安装：已安装的重装时从 `constants.env` 沿用原来的权重根与数据根，已下载的模型不受影响
- `--type` 变为可选，含义改为「模态覆盖」，写进该模型的 `modality`；不给不再报「无法判定类型」
- 安装器默认安装 **mlx-vlm**（会带入 torch 等依赖，安装时间变长）；采集失败时打印原因
- `MLX_GATEWAY_TOKEN` 仍可覆盖，但须满足令牌值域（32–128 位 `[A-Za-z0-9_-]`），过短的会被 `mlx up api` 拒绝
- **`api.bind` 默认 127.0.0.1 → 0.0.0.0**（不兼容）。只给本机用：`mlx config set api.bind --value=127.0.0.1`
- **所有来源都要令牌**，本机也不例外（不兼容）。1.0.0 在回环上免鉴权
- **端点收紧**（不兼容）：此前任意 POST 路径都转发给后端；现在白名单之外一律 404，后端零请求
- 后端 4xx 状态码透传（本机来源原文、远端来源去掉路径与异常片段）；后端 5xx 与鉴权失败返回 502
- 网关请求体上限 8 MB → 256 MB，可用新配置键 `api.max_body_mb` 调（超出 413）；令牌与来源校验在读请求体之前完成
- **`mlx restart` 先校验、后停止**：目标、登记、权重、生效配置、后端判定（含网关令牌与 TLS）与登记端口冲突在停之前查，不过时服务保持原状（此前先停后校验，被拒时服务已经没了）；容量判档、后端能力漂移与网关端口外来占用仍在停止之后才查，这几条被拒时服务已停；`--ignore-fitness` 对 `restart` 生效（此前被丢弃）
- 多目标 `up` / `down` / `restart` 的汇总：失败的目标全是「拒绝」时整体也是拒绝并保留最差的 rc（如 6），有目标真的落地了东西才是失败、并逐字列出要收敛的记录（此前一律报失败、rc 塌成 1）
- `mlx bootstrap keys`、安装器与 `mlx help bootstrap keys` 给出的查看密钥命令改为能执行的 `mlx config get <键名> --reveal`（此前印的写法把键名放在 `--key=` 里、没有位置参数，照抄 rc 2）
- `mlx up api` 在令牌为空、TLS 配置不完整或私钥权限过宽、网关端口已被别的进程占用时拒绝启动
- **网关到模型上游一律流式**：非流式请求由网关聚合成完整 JSON，81920 token 的长输出不再被读超时截断
- 思考强度 `effort` 按模型模板接受的取值映射（qwen38：`minimal`→`low`，`high` / `max`→`xhigh`），越界不再让后端报错
- 多模态模型上 `max_completion_tokens` 生效（网关换成后端认的 `max_tokens`）；文本模型收到 `thinking_budget` 返回 400 `thinking_budget_unsupported`；多模态后端的 `enable_thinking` / `reasoning_effort` 由网关写到请求顶层
- 请求里的 `adapters`、`adapter_path`、`draft_model` 被网关剥离，客户端不能让模型加载别的权重
- **按请求体 `model` 路由的端点，连不上模型上游由 504 改为 502 `upstream_error`**；按 id 查询 / 删除 / 取消的扇出端点，后端连不上或回 5xx 是 502，10 秒内有后端没回话仍是 504 `upstream_timeout`。日志事件 `sse_stream_aborted` 改名 `upstream_aborted`，与客户端自己断开的 `client_disconnected` 区分；流开始后未见终止帧（半帧、断在帧边界）记 `sse_truncated`；扇出端点上后端对 2xx 回非 JSON 体同样是 502，请求体不是 JSON 对象是 400

### 修复

- 安装器：没有服务在跑、但数据根下 `run/` 已存在时（用过一阵的机器的常态），重装不再一行不打地 rc 1 退出
- `mlx down --all --dry-run` 不再真的停掉服务：`--all` 路径此前没有把 `--dry-run` 传给逐个停服务的循环；现在只预览。
  配套新增按类的守卫，由命令表派生「声明 `--dry-run` 的动词 × 接受的目标形态」，在真在跑的进程上断言零状态变化
- `mlx config get <模型> --key=port` 报的端口改为 `up` 实际使用的值（登记端口，来源 machine；模型自己设过则为 user），
  不再显示出厂 8100。`config get port`（不带模型）报网关端口（来源 cli），不再是 settings 里的值或出厂 8100；
  `MLX_GATEWAY_TOKEN` 存在时 `config get api.token` 反映它（来源 cli）
- `mlx help model rm` 不再说「删除前会要求键入模型 id」：只有带 `--weights` 才确认；`mlx help restart` 改为说明哪些在停之前校验、哪些在停之后才查
- `mlx config set` / `config import` 按键的层级校验：全局级键（`api.*`、`backend.api_key`、`fit.*`）不能写到模型上，
  模型级键（`port`、`modality`、`draft_model`、`num_draft_tokens`）不能写成全局，否则 rc 6 `config.level_mismatch`——此前写得进去，
  但 `up` 与 `config get` 都会忽略，是死旋钮（全局 `port` 对模型被登记端口遮住、对网关被网关端口遮住）；
  `config get / diff <拼错的模型id>` 报「模型不存在」，不再按网关显示端口。`config unset` 仍可清理旧版留下的值
- `mlx config validate` 真的检查值域：与 `up` 用同一套判定，非法的键逐个标 failed（rc 65）。此前恒报 ok，`up` 却拒绝
- `mlx config import` 认得 `config export --json` 的输出（此前把信封的 `kind` / `values` 等当成未知键全部拒掉，rc 0 却什么都没导入）
- 模型自设的端口（`config set <模型> --key=port`）参与端口互斥：与其它模型的端口或网关端口相同时 rc 7；
  `up` / `restart` 停前的登记端口冲突检查、端口分配与 `model ls` 的端口列都按生效端口（自设优先于登记）
- `mlx config diff` 不再列出来自安装或环境的值（来源 cli，如网关端口）：它们不是改过的配置
- 网关：读请求体有总时限（按到货字节计时间额度，最低 256 KiB/s、起步宽限 30 s、上限 1200 s），客户端停住或滴灌时回 408 并退还在途预算，
  停住的单条连接不再永久占着预算（持令牌者反复重连仍能占满，见 KNOWN-ISSUES「持令牌者可占满在途预算」）；体还没读就拒绝时（401 / 413 / 503 等）先排空再关连接，客户端能读到状态码与 `Retry-After`；
  带 `Expect: 100-continue` 的请求在发 100 之前先预留预算，预算满直接 503
- 网关代取：「本机自身地址一律拒」覆盖所有网卡（含 VPN 等被排除接口）的所有地址，不再能用 IPv6 `%网卡` 后缀或同网段的第二个本机地址绕过；
  网段视图取不到（系统命令失败）或本机地址刚变化、还没到刷新周期时，按内核能否绑定该地址判定本机地址，不再漏判；
  部署 venv（Python 3.14）下以关连接结束的图片服务器（如 `python3 -m http.server`）不再一律 `connect_failed`；
  非 ASCII 路径按 UTF-8 编码、IDN 主机名与站点本地地址 `fec0::/10` 按 `address_denied` 拒绝、异常的 `Content-Length` 不再让请求回零字节
- 非流式请求上游断在终止帧之前时记 `sse_truncated`（此前记 `upstream_aborted`），与流式一致

### 移除

- `share/types.tsv`、`backend` 配置键、登记期类型判定链

### 升级须知

1. 重跑 `bash deployer/install.sh`（会删除退役的 `share/types.tsv`、装 mlx-vlm、采集后端能力、生成密钥，并打印网关暴露面警告）
2. 跑一次 `mlx model repair` 清除旧字段
3. MTP drafter 不再可用（mlx-vlm #2033）；配过 `draft_model` 指向 MTP 模型的，`mlx config unset <id> --key=draft_model`
4. 网关与模型进程**不再从父 shell 隐式继承** `MLX_BACKEND_API_KEY` / `MLX_GATEWAY_TOKEN`（起进程时从继承的环境里去掉这两个变量，只传工具从配置算出的值）。后端密钥请用 `mlx config set backend.api_key --value=…`（或 `mlx bootstrap keys` 生成）。网关令牌另有一个**显式**入口：`MLX_GATEWAY_TOKEN=… mlx up api` 时 `mlx` 读这个变量，作为这一次启动的令牌覆盖配置（须满足令牌值域）
5. 网关现在对局域网与虚拟机开放。只给本机用的：`mlx config set api.bind --value=127.0.0.1`；
   所有客户端都要改为带令牌（`mlx config get api.token --reveal`）

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
