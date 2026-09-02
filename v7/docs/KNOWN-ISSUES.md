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

### `mlx up` 报「已启动」时后端可能是坏的

`wait_ready` 的判据是 `port_owner`（端口上的监听者是不是我们记录的那个 pid）。
它挡住了「端口被别人占」，但挡不住这一类：**`mlx_lm.server` 先 bind 端口、
首次请求才加载模型**，所以端口 LISTEN 时模型还没碰过。加载失败也不会让进程
立刻退出 —— 它就那样挂着，端口开着，什么也服务不了。

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

### 投机解码：`Qwen3.8-27B-MTP-4bit` 在 mlx_lm 上用不了

`mlx-community/Qwen3.8-27B-MTP-4bit` 的 `model_type` 是 `qwen3_5_mtp`，
而 `mlx_lm 0.31.3` 的 `_get_classes` 不认识它：
`ValueError: Model type qwen3_5_mtp not supported`。

MTP（Multi-Token Prediction）是 Qwen3.8 自带的多 token 预测头，
**不是通用的 draft model**。mlx_lm 的 `--draft-model` 要的是一个能独立
`load()` 的完整小模型。两者不是一回事。

要用投机解码，drafter 得换成 mlx_lm 支持的架构（例如同系列的小参数量模型），
且必须与主模型**同 tokenizer**。权重已下（0.24 GB），留着不碍事；
`qwen38` 的 `draft_model` 已 unset。

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

### `--type vision` 起不来时不说原因

`types.tsv` 有 `vision → mlx_vlm`，`--type vision` 因此**能通过域校验**登记成功。
但安装器只装 `huggingface_hub mlx mlx-lm`（`install.sh:170`，故意 —— `mlx_vlm`
会拖进 torch/transformers），于是 `backendarg` 拼出的 `python3 -m mlx_vlm.server`
在子进程里 `ModuleNotFoundError`，`svc.up` 只报 `svc.died_before_ready`（rc 10）。

**用户看到的**：「起来前就死了」。**看不到的**：死因是缺一个包，以及装法。

落点：`cmd/svc.py:474` 那条 `svc.died_before_ready` 之前，应查
`v['backend'] in caps.get(ctx)['packages']`，缺则 `Refused(6)` 并给出
`pip install mlx-vlm` + `mlx caps refresh`。判据是 fail-closed 提前到执行段之前
（与 `caps.drift` 同一位置，`BCR-R-27`）。

发现于 2026-09-02：修 `mlx version` 的「依赖三包」措辞时顺着自己新写的
「仅 `--type vision` 需要」这句话查下去撞到的 —— 那句话把用户指向一条
失败信息不可读的路径。

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