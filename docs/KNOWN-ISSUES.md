# 已知问题

对应 1.1.0（改动见 [CHANGELOG](../CHANGELOG.md) 的 `[1.1.0]` 段）。
只记**尚未修**的缺陷与**有意为之**的限制，每条给症状、影响与绕开的办法。已修的不在这里（见 git log）。

按「用户会不会撞上」排序。

---

## 一、尚未修的缺陷

### `mlx up` 报「已启动」时后端可能是坏的（仅 mlx_lm）

就绪判据是端口上的监听者是不是我们记录的那个 pid。它挡住了「端口被别人占」，但挡不住这一类：
**`mlx_lm.server` 先 bind 端口、首次请求才加载模型**，所以端口 LISTEN 时模型还没碰过。加载失败也不会让进程
立刻退出 —— 它就那样挂着，端口开着，什么也服务不了。
mlx_vlm 加载完权重才监听，端口 LISTEN 即模型可用，不受此影响。

实测：`mlx up qwen38`（绑了一个 mlx_lm 不支持的 drafter）报已启动，五分钟后 `mlx chat` 仍连不上，
而 `mlx status` 一直说「在跑」。真因只在后端日志里：`ValueError: Model type qwen3_5_mtp not supported`。

**已缓解**：`chat` / `bench` 在进程在跑而端口连不上时，文案直接指向后端日志
（`<数据根>/logs/<服务>.log`），不再把用户支去 `mlx status`。

**未修的本体**：就绪判定仍不验证模型能否加载。就绪等待上限是 mlx_lm 20 秒、mlx_vlm `20 + 3 × 体积 GB` 秒；
要验证加载就得等首次加载完成，27 GB 的模型远超 20 秒，而 `up` 又不该阻塞几分钟。

### `mlx up <模型> --dry-run` 不是零外部命令

校验段要判定模型模板的思考默认值与 effort 值域，会起一个渲染子进程（venv 的 python3 + jinja2 沙箱），
渲染期间每 50 ms 调一次 `/bin/ps` 查它的内存（上限 1 GB、墙钟 5 秒）。`--dry-run` 在校验段之后才返回，
所以预览一个带聊天模板的模型时，会起 1 个渲染进程和若干个 `ps`（每轮轮询一次，渲染越慢越多：
实测 Qwen3.8-27B 的模板热缓存 0.07 s、冷启动 0.76 s 渲染完，都只调了 1 次 `ps`；开发记录里出现过约 15 个进程）。
这些都是只读动作，不改任何状态，但与「dry-run 零外部动作」的说法不符。`mlx model info <id>` 同样会渲染模板。

（机制由读代码确认：`cmd/svc.py` 的后端判定步调用 `templates.inspect_template`，后者轮询 `ps`；
实测在开发 venv 上用真实权重目录做，未在真实安装上复测。）

### `--dry-run` 仍未覆盖的动词（不危险，但预览不了）

`--dry-run` 只登记给真正实现了预览的十个动词：`up` `down` `restart` `bench` `install` `uninstall`
`config import` `model gc` `model install` `model register`。其余动词**拒绝**这个 flag（rc 2，并说明它属于谁），
不会静默真执行。

其中 **`model rm --dry-run` 最值得补** —— 它是仅次于 `trash empty` 的破坏性动作，
而「先看看会删什么」正是用户最需要预览的场景。

### `api.inflight_max_mb` 可以配得比 `api.media_max_mb` 小

两个键各有取值域（`inflight_max_mb` 64–65536，`media_max_mb` 1–2048），配置时不做交叉校验。
每一次代取都要先按 `media_max_mb` 整额预留在途额度，`inflight_max_mb` 比它小时预留永远失败，
**所有代取（本机来源也一样）都回 503 `gateway_busy`**，看上去像网关一直很忙。
绕开：保持 `api.inflight_max_mb` ≥ `api.media_max_mb`，改完 `mlx restart api`。

---

## 二、有意为之的限制

### MTP drafter 不支持

mlx_vlm 在并发请求下会静默输出损坏的内容（上游 mlx-vlm #2033，2026-09 本机两路并发复现）。
附属 drafter 被 `mlx up` 拒绝；上游修复并经本机并发逐字比对复验后另立设计。

### 81920 × 8 在长上下文下可能超出内存

`max_tokens` 81920 与 `max_concurrency` 8 同时打满时 KV 缓存最坏约 14 GB/路。
缓解：调低 `max_concurrency`，或提高 `iogpu.wired_limit_mb`（sudo，重启失效）。

### mlx_lm 后端端口无鉴权

上游没有鉴权机制。它只绑 127.0.0.1；挂完整 drafter 时串行处理请求。

### mlx_vlm 不读 `chat_template_kwargs`

除 `enable_thinking` / `reasoning_effort` 外的模板参数（如 `preserve_thinking`）对 mlx_vlm 不生效。
这两个例外是网关按后端把它们写到请求顶层的。

### `config set` 设密钥时值短暂出现在进程列表

命令行参数对同机其他用户的 `ps` 可见。敏感环境下改用 `mlx bootstrap keys` 生成后 `--reveal` 读取。

### 判定依赖钉住的上游副本

附属 drafter 的 model_type 集合（`share/drafters.tsv`）取自 mlx-vlm 0.6.17；effort 的取值顺序
（minimal / low / medium / high / xhigh / max）写在网关的 `gateway/effort.py`，模板接受哪些取值则每次渲染模板现场判定。
升级上游后需复核前两者。

### 模板渲染沙箱的边界是 jinja2，不是进程隔离

`mlx up` / `mlx model info` 会在子进程里渲染模型自带的聊天模板，以判定思考默认值与 effort 值域。
子进程使用 jinja2 的沙箱环境，并只继承 `PATH`（不继承密钥类环境变量），但它与你**同一用户**运行：
一旦模板绕过 jinja2 沙箱，它能读取当前用户可读的文件（包括 0600 的 `settings.json`）。
只登记来源可信的模型；环境变量隔离是纵深防御，jinja2 沙箱才是实际边界。

### `mlx up api` 硬依赖 `lsof`

启动前要检查网关端口上有没有外来监听者，这要调 `/usr/sbin/lsof`；判不出来（命令不存在、超时、输出解析不了，
或 `lsof` 退出非零且 stderr 有内容）一律拒绝启动（rc 9，「端口 … 的占用状态不可判定」）。
fail-closed 是有意的。后果：机器上没有 `/usr/sbin/lsof`，或 `MLX_BIN_LSOF` 指到了不对的路径，网关**完全起不来**。

### `up api --dry-run` 打印的命令行，解释器可能与真启动不同

网关用哪个 python3 是**启动那一刻**才判定的（venv 的解释器真的 import 得动网关就用它，否则回落 `/usr/bin/python3`）；
`--dry-run` 打印 argv 时这一步还没跑，`argv[0]` 是占位值。照着 `--dry-run` 打印的命令行手工复现，
跑起来的解释器可能与 `mlx up api` 实际用的不是同一个。

### TLS 关闭时同网段可旁听

默认不开 TLS。同一 Wi-Fi 上的设备能看到请求内容与令牌。`mlx api info` 在这种情况下给出警告；开启方法见手册 {#remote}。

### 放行网段依赖系统命令的输出格式

放行网段由 `networksetup -listallhardwareports` 与 `ifconfig` 的输出解析。macOS 改变输出格式时解析会失败：
网关沿用上一份结果（从未成功过则只放行本机），`mlx check` 的 `gateway_nets` 项不通过。

### 端口复用保持开启

`allow_reuse_address` 关掉会让 `mlx restart api` 在网关刚处理过请求后必然失败（实测 `errno 48`），所以保持开启。
代价是别的进程可能同时占着 `127.0.0.1:<网关端口>`：`mlx up api` 起之前会检查并拒绝；网关起来**之后**才出现的占用者，
由 `mlx check` 的 `gateway_port` 项发现。

### 持令牌者可占满在途预算

在途预算按请求**声明的** `Content-Length` 先计入。持令牌的客户端声明大体却不发、收到 408 后立即重连，
几乎不耗带宽就能让预算一直满着，其他请求收到 503 + `Retry-After`。读体总时限只限住单条连接，管不了重连。
令牌持有者属于受信任方，按到货字节计费会改动「先按声明计费、预算满即拒」的语义，暂不做；遇到时换令牌即可切断。

### 持有令牌的来源能看到全部在跑的模型

`/v1/models` 对所有持令牌的请求列出全部在跑模型，不按来源区分。

### 未开放的端点

embeddings、rerank、images、audio、realtime 不经网关（它们只对相应类型的模型有意义，对生成模型调用会再加载一份权重）。
后端的管理端点（如 `/unload`、`/v1/settings`）同样不经网关。这些路径一律 404 `endpoint_not_supported`，后端收不到请求。

### 非流式客户端无法保活

非流式请求要等模型生成完才能回一个完整 JSON，这期间没有字节可发。排队、读长上下文或长思考超过客户端自身的 HTTP 超时，
客户端会断开（明文连接上网关随即取消生成，`api.log` 记 `client_disconnected`）。长任务请用流式。

### TLS 下非流式请求的断连要到写回时才发现（流式也只在写失败时发现）

明文连接上网关每秒探测一次客户端是否还在；TLS 连接不支持这种不读走数据的探测，非流式请求的客户端断开要到网关写回响应时才发现，
模型会把这次生成跑完。流式请求同样受影响：TLS 下客户端断开只在网关写失败时才发现，上游响应头到达之前（mlx_lm 排队可能数分钟）
发现不了、上游不会被关；头到达之后靠转发帧或保活写入发现，静默段最长约 16 秒，且对端已关时第一次写可能仍然成功。

### 流式中途断流时客户端看不到错误

上游在流开始后断开（崩溃、OOM），响应头已经发出，网关只能结束流并在 `api.log` 记 `sse_truncated` 或 `upstream_aborted`，
不会补错误帧。OpenAI 风格客户端可能把缺 `[DONE]` 的流当成正常结束，表现为回答突然截断。

### 代取可以跨放行网段

持令牌的 Wi-Fi 设备可以让网关去取虚拟机网段上的 http 资源，反之亦然。回环、链路本地、组播、保留、广播与 Teredo 地址无条件拒绝，不在放行网段的私网与 CGNAT 地址也拒绝；
但**任何公网地址一律放行**：经 VPN 可达的公网段地址，远端来源也能让网关去取。

### 本机路径原样交给模型

本机来源发来的媒体本机路径与 `file://` 原样交给模型进程：本机任何持令牌的进程都能让模型读取当前用户可读的文件。
这与该进程自己去读没有区别，不构成提权；虚拟机与 Wi-Fi 设备发这类值一律 400。

### 流式帧里只改写已知的 `model` 位置

网关把 chat / completions 顶层、responses 事件里的 `response.model`、messages 的 `message.model` 改回短 id。
上游新增其他嵌套位置时，客户端可能看到模型目录名（不含路径）。

---

## 三、未实现的命令

敲了会告诉你「未知命令」或「没有子命令」（rc 2），`mlx help` 不列它们。

| 命令 | 说明 |
|---|---|
| `mlx logs` | 日志查看。错误文案会直接给出数据根下的日志路径（`logs/<服务>.log`），先用 `tail` 看 |
| `mlx bootstrap render` | 补全脚本与 launchd plist 的渲染器 |
| `mlx model convert / quantize` | 格式转换与量化 |
| `mlx model upgrade / rollback` | 版本升降级 |
