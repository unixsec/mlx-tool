# v7 已知缺口

2026-09-01 用五个独立上下文的 agent 分头审查（空桩普查 / 动词台账 / flag 与配置 /
接口契约 / 用户旅程），共约 60 条发现。**已修的不在这里**（见 git log），
这份文件只记**尚未修**的，每条给症状、落点与设计依据 —— 不写「大概在某处」。

按「用户会不会撞上」排序。

**已修的四条**（`register` 查网关端口 · `api info` 与网关对账 ·
`--dry-run` 的 argv 可执行 · 回收站 `trash ls/restore/empty` · `chat` 多轮 REPL · 容量判档 `fitness` · 陈旧锁清理 · 三处探针塌陷 · caps.json 落点 · 快照 model_arg · 确认分档 + flag 归属校验 · MLX_BIN_* 注入面 · 层序反向边 · 信号屏蔽窗口 · schema/types 两表 ·
config 四命令 · Record 段归属 · ui 渲染器归位）
已从本表移除，见 git log。

---

## 一、用户会撞上的

### `mlx up` 报「已启动」时后端可能是坏的（仅 mlx_lm）

`wait_ready` 的判据是 `port_owner`（端口上的监听者是不是我们记录的那个 pid）。
它挡住了「端口被别人占」，但挡不住这一类：**`mlx_lm.server` 先 bind 端口、
首次请求才加载模型**，所以端口 LISTEN 时模型还没碰过。加载失败也不会让进程
立刻退出 —— 它就那样挂着，端口开着，什么也服务不了。
mlx_vlm 加载完权重才监听，端口 LISTEN 即模型可用，不受此影响。

2026-09-02 实测：`mlx up qwen38`（绑了一个 mlx_lm 不支持的 drafter）报
`✓ qwen38 已启动 port 8100 pid 94134`，五分钟后 `mlx chat` 仍连不上，
而 `mlx status` 一直说「在跑」—— **两条命令对同一事实给出相反答案**。
真因只在后端日志里：`ValueError: Model type qwen3_5_mtp not supported`。

**已缓解**：`chat` / `bench` 的 `backend_unreachable` 文案不再把用户支使去
`mlx status`（它会说「在跑」），改为指向后端日志路径。

**未修的本体**：`wait_ready` 仍不验证模型能否加载。落点是加一次
`GET /v1/models` 的探测 —— 但要先解决超时：27 GB 模型的首次加载远超现在的
`timeout_sec=20`，而 `up` 又不该阻塞几分钟。可能的形状是「端口就绪即返回，
但文案说清『首次请求时才加载模型』」，把承诺降到实现做得到的程度。

### `--dry-run` 仍未覆盖的动词（**已不再危险，但也预览不了**）

2026-09-02 的普查之后，`--dry-run` 的登记面收窄成十个真正实现了预览的动词。
其余动词现在**拒绝**这个 flag（rc 2，明确说它属于谁），不再静默真执行。

尚未实现预览、因而敲不进去的：
`model rm` · `model repair` · `config set/unset/import 之外的子命令` · `chat` ·
`caps refresh` · `api info` · `bootstrap verify`。

其中 **`model rm --dry-run` 最值得补** —— 它是仅次于 `trash empty` 的破坏性动作，
而「先看看会删什么」正是用户最需要预览的场景。落点：`model_reclaim.run` 的
`rm` 分支，在 `store.commit`（:107）之前返回，payload 里给出会删的登记行与
（`--weights` 时）会移进回收站的权重路径。

补任何一个都要同步改 `cli.tsv:20` 的 `owner_verb` —— 守卫
`test_dry_run_ownership_equals_implementation` 是**相等断言**，两边不同步会红。

---

## 一之二、1.1.0 的已知限制（有意为之）

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

### `config set` 设密钥时值短暂出现在进程列表

命令行参数对同机其他用户的 `ps` 可见。敏感环境下改用 `mlx bootstrap keys` 生成后 `--reveal` 读取。

### 判定依赖钉住的上游副本

附属 drafter 的 model_type 集合（`share/drafters.tsv`）与 effort 取值顺序取自 mlx-vlm 0.6.17；升级上游后需复核。

### 模板渲染沙箱的边界是 jinja2，不是进程隔离

`mlx up` / `mlx model info` 会在子进程里渲染模型自带的聊天模板，以判定思考默认值与 effort 值域。
子进程使用 jinja2 的沙箱环境，并只继承 `PATH`（不继承密钥类环境变量），但它与你**同一用户**运行：
一旦模板绕过 jinja2 沙箱，它能读取当前用户可读的文件（包括 0600 的 `settings.json`）。
只登记来源可信的模型；环境变量隔离是纵深防御，jinja2 沙箱才是实际边界。

### `mlx up api` 硬依赖 `lsof`

启动前检查端口上有没有外来监听者（spec D24）要调 `lsof`；探不到（`TOOL_UNAVAILABLE` /
`TIMEOUT` / `PARSE_FAILED`）一律 `Refused(9, "ports.probe_undeterminable")`——
fail-closed 是 D24 的设计意图，不是缺陷。后果：机器上没有 `/usr/sbin/lsof`，或
`MLX_BIN_LSOF` 指到了一个不对的路径，网关**完全起不来**，而这在本版之前是可以的。
更容易踩到的边界：`lsof` 退出非零但 stderr 有内容时判定成 `PS_FAILED`（同样是 `Unknown`，
同样挡启动）——不止「命令不存在」这一种形状会触发它。

### `up api --dry-run` 打印的命令行，解释器可能与真启动不同

网关用哪个 python3 是**启动那一刻**才判定的（spec D20：venv 的解释器真的 import
得动网关就用它，否则回落 `/usr/bin/python3`）；`--dry-run` 打印 argv 时这一步还没跑，
`argv[0]` 是占位值。照着 `--dry-run` 打印的命令行手工复现，跑起来的解释器可能与
`mlx up api` 实际用的不是同一个。

---

## 二、机制层面的缺口

（本节已清空。2026-09-01 夜间把七条逐条讨论后全部做完，
见 git log 从 `3bc4a85` 到 `HEAD`。）

---

## 三、未实现的命令

敲了会告诉你「没有这个命令」（rc 2），已从 `verbs.tsv` 摘掉，因此 `mlx help` 不再列它们。

| 命令 | 说明 |
|---|---|
| `mlx logs` | 日志查看。多处错误文案会指向数据根的日志路径，`G-PC-05` 守卫为此立过 |
| `mlx bootstrap render` | 补全脚本与 plist 的渲染器。设计要求补全由动词台账生成（`01-requirements.md:1040` 是 P0），`install.sh` 的 I13 调用点也要一起补 |
| `mlx model convert / quantize` | 格式转换与量化 |
| `mlx model upgrade / rollback` | 版本升降级（`gate` 模块的模板指纹基线已就位） |

---

## 四、一条订正

本文件此前有一条说 `--live` / `--brief` / `--only` / `--tail`
「在 `cli.tsv` 里登记了、能解析、不报参数错」——**三句都与实测相反**：
它们本就不在表里，裸写会被正确拒绝。

真正的根因是 `cli.py` 对 `--k=v` 形态**完全不查表**，
于是 `--tail=50` 确实不被拒、确实让用户以为生效了 —— 但这不限于那四个 flag，
而是覆盖全部拼写错误、全部未来 flag，并且撞上内部键名时会把解释器打出 traceback。
**已修**（见 `a64fc62`）。

记下这条是因为它本身就是个教训：我当时按症状写了根因，没去核对表。