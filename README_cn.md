<div align="center">

# 🧰 MLX Toolkit

**在 Apple Silicon 上跑本地大模型 —— 并接进任何 OpenAI 或 Anthropic 兼容的应用。**

[![Version](https://img.shields.io/badge/version-1.1.0-brightgreen)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-black)](https://support.apple.com/zh-cn/116943)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Backend](https://img.shields.io/badge/backend-mlx--lm%20%7C%20mlx--vlm-orange)](https://github.com/ml-explore/mlx-lm)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-bash%20check.sh-success)](tests)

[English](README.md) · 🌐 **简体中文**

📖 [用户手册](docs/USER-GUIDE.md) · 🐛 [已知问题](docs/KNOWN-ISSUES.md) · 📝 [变更记录](CHANGELOG.md)

</div>

---

## 🚀 最新进展

- **v1.1.0**（2026-10）—— 完整清单与升级须知见[变更记录](CHANGELOG.md)的 `[1.1.0]` 段。
  - 每次 `mlx up` 按权重选后端：多模态模型走 `mlx_vlm`，纯文本走 `mlx_lm`。
  - 网关服务本机、虚拟机（Parallels / VMware 共享网络）与同一 Wi-Fi 上的设备。
    **所有来源都要网关令牌**，TLS 可选，`api.bind` 默认改为 `0.0.0.0`。
  - 新端点：`/v1/responses` 与 Anthropic `/v1/messages`（多模态后端），Codex CLI 与 Claude Code 能直接接入。
  - 网关自己代取 `http(s)://` 媒体，归一思考开关与强度，对模型一律流式取结果，
    给流式客户端保活，客户端断开即取消生成。
- **v1.0.0**（2026-09）—— 首个发布版本。全链路已在真机跑通：
  下载 27.5 GB 模型 → 登记 → 起服务 → OpenAI 网关 → Cherry Studio → 流式推理。

---

## ⚡ 快速开始

### 前置

需要 Homebrew 的 Python **3.10+**。macOS 自带的 3.9.6 **不行**，理由见
[下文](#为什么不能用-macos-自带的-python)。

```bash
brew install python@3.13
```

### 安装

```bash
git clone https://github.com/<你>/mlx-toolkit.git && cd mlx-toolkit
bash deployer/install.sh
source ~/.zshrc          # 或者新开一个终端
mlx check                # 12 项检查，应该全过
```

安装器会建推理 venv 并装 `huggingface_hub`、`mlx`、`mlx-lm`、`mlx-vlm`（最后一个会带入 torch，
第一次装要多等一会儿）；还会生成两把密钥（`backend.api_key` 与 `api.token`），并提示网关默认监听全部网卡。

### 跑一个模型

```bash
# 下载 + 登记一步到位（约 27 GB，可续传，有进度）
mlx model install qwen38 \
  --repo=mlx-community/Qwen3.8-27B-8bit \
  --port=8100

mlx up qwen38
mlx chat qwen38 --prompt="用一句话说明什么是投机解码"
```

### 接入外部应用

```bash
mlx up api                          # OpenAI 与 Anthropic 兼容网关
mlx api info                        # 本机、虚拟机、Wi-Fi 设备各自该填的地址
mlx config get api.token --reveal   # 所有客户端都要带的令牌
```

| 客户端里填 | 值 |
|---|---|
| API 地址 | OpenAI 风格客户端：本机填 `http://127.0.0.1:8080/v1`；Anthropic 客户端（Claude Code）填同一地址但**不带** `/v1`。虚拟机或 Wi-Fi 设备填 `mlx api info` 里对应那行，两列都给了 |
| API 密钥 | 网关令牌 —— 所有来源都要，本机也不例外 |
| 模型 ID | `qwen38`（`mlx model ls` 里的 id，不是 HF 仓库名） |

各客户端（Cherry Studio、Claude Code、Codex CLI、Qwen Code）的具体填法见手册的[客户端一节](docs/USER-GUIDE.md#clients)。

> ⚠️ **`max_tokens` 要给到 2048 以上。** 推理模型会先把思考过程输出到单独的
> `reasoning` 字段，再产出 `content`。预算给小了会在思考阶段被截断，
> `content` 回来是空的 —— 客户端里显示为**空白回复且没有任何报错**。

---

## ✨ 功能特性

| 特性 | 实际意味着什么 |
|---|---|
| 🔋 **零依赖 CLI** | 所有面向用户的命令只 import 标准库。推理 venv 坏掉时，`mlx check` 照样能跑、照样告诉你怎么修。解释器按 venv → Homebrew → 系统三级回落，你和一份可用的诊断之间没有单点故障。 |
| 🗂️ **三根分离** | 代码、状态、权重放在三个互不相干的目录。重装工具只重写第一个 —— 28 GB 的下载和认识它的注册表都留着。三个位置都能用 `MLX_ROOT_*` 覆盖。 |
| 🧭 **按权重选后端** | 每次 `mlx up` 读模型的 `config.json`：有非空 `vision_config` 起 `mlx_vlm.server`，否则起 `mlx_lm.server`。单个模型可用 `modality` 强制走文本。 |
| 🔌 **一个网关接多种客户端** | `mlx up api` 把所有在跑的模型收敛到一个端点后面：`/v1/chat/completions`、`/v1/completions`（文本模型）、`/v1/responses` 与 Anthropic `/v1/messages`（多模态模型）、`/v1/models`，另有免鉴权的 `GET /healthz`。`/v1` 前缀可省。后端不支持的端点回 404，模型零请求。 |
| 🛡️ **暴露面写在明处** | 网关只放行回环、Wi-Fi / 有线网段与虚拟机网桥 —— VPN、雷雳网桥拒绝 —— 且每个请求都要令牌。TLS 可选（`api.tls_cert` / `api.tls_key`）。 |
| 🖼️ **媒体、思考、长输出** | `http(s)://` 图片、音频、视频由网关代取（远端来源不能借它访问放行网段之外的私网地址）。思考开关与强度按模型模板接受的取值归一。网关对模型一律流式取结果，给流式客户端发 `: keepalive`，客户端断开即取消生成。详见手册的[媒体](docs/USER-GUIDE.md#media)、[思考](docs/USER-GUIDE.md#thinking)与[流式](docs/USER-GUIDE.md#streaming)几节。 |
| 🛑 **先拒绝，而不是先破坏** | 结果带类型：`Done` / `Refused` / `Failed`，而 `Refused` **保证零资源已创建** —— 由静态守卫强制，不靠自觉。起一个内存装不下的模型会被拒绝并列出数字。`mlx restart` 停服务之前先查登记、权重、配置与后端；容量、后端包漂移与网关端口占用只能停了之后才查。 |
| 🎯 **三态探针** | `Ok(v)` / `Unknown(kind, reason)`。"判不出来"绝不静默塌成"否" —— 命令 fail-closed 并指名是哪个输入不确定。 |
| 📋 **表就是事实来源** | `payload/share/` 下七张 TSV 表定义命令、参数、全部面向用户的文案、配置键、后端、可挂的 drafter 与分发清单。解析器与 `mlx help` 都由它们生成。 |
| ♻️ **删除可恢复** | `model rm --weights` 把权重移进回收站。`model trash ls` / `restore` / `empty` —— 确认强度按**这个动作到底有多不可逆**分档。 |
| 🧪 **抓到过真缺陷的守卫** | 测试跑在沙箱里的真实安装产物上。多数守卫都带着注释，说明它当初该抓住上一版的哪个具体缺陷 —— 其中一条会把本仓库文档里的每一条 `mlx …` 示例过一遍解析器。 |

---

## 🖼️ 长什么样

**健康检查 —— 十二项，每一项失败时都给可执行的修法**

```console
$ mlx check
  ✓ code_root      ok
  ✓ data_root      ok
  ✓ weights_root   ok
  ✓ venv           ok
  ✓ tables         ok
  ✓ models_file    ok
  ✓ run_dir        ok
  ✓ ports          ok
  ✓ caps           ok
  ✓ gateway_nets   ok
  ✓ gateway_port   ok
  ✓ settings_mode  ok

  12 项，12 项通过
```

**状态 —— 登记了什么、在跑什么、吃多少内存**

```console
$ mlx status
…
模型（2）
     ID       模型                            状态  端口   后端     并发  PID    内存      体积
  ─  ───────  ──────────────────────────────  ────  ─────  ───────  ────  ─────  ────────  ────────
  ✓  qwen38   mlx-community/Qwen3.8-27B-8bit  就绪  :8100  mlx_vlm  8     43699  27788 MB  28164 MB
  ·  qwen05b  mlx-community/Qwen3-0.6B-4bit   就绪  :8150  —        —     —      —         335 MB

  2 个已登记，1 个在跑，共占 27788 MB 内存

网关
  ✓ api        在跑  :8080  pid 65853 · 绑定 0.0.0.0 · TLS 关 · 放行网段 3
```

**下载进度 —— 自己扫盘算出来的，不依赖下载器的输出**

```console
$ mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit --port=8100
  ↓ 下载 mlx-community/Qwen3.8-27B-8bit
   … 已下 23.3 GB  21.7 MB/s  84.8%  约剩 3 分
  installed  qwen38
  modality   auto
  path       ~/lab/models/mlx/hub/models--mlx-community--Qwen3.8-27B-8bit/snapshots/main
  state      就绪
```

**报错说清怎么修，而不只是说哪儿疼**

```console
$ mlx chat "如何查询手机sn号"
没有名为「如何查询手机sn号」的模型。已登记：qwen05b、qwen38
chat 的第一个参数是**模型 id**，问题用 --prompt= 传：
  mlx chat qwen38 --prompt="如何查询手机sn号"
不带 --prompt 则进入多轮对话：mlx chat qwen38
```

---

## 🗺️ 命令速查

| 分组 | 命令 |
|---|---|
| **工具自身** | `install` · `uninstall` · `status` · `check` · `version` · `help` |
| **服务** | `up` · `down` · `restart` |
| **模型** | `install` · `register` · `ls` · `info` · `rm` · `gc` · `repair` · `trash ls` · `trash restore` · `trash empty` |
| **配置** | `get` · `set` · `unset` · `ls` · `diff` · `export` · `import` · `validate` |
| **网关** | `api info` |
| **后端能力** | `caps refresh` · `caps show` |
| **密钥** | `bootstrap keys` · `bootstrap verify` |
| **其他** | `chat` · `bench` |

`mlx help` 列出全部命令；`mlx help <命令>` 看单条详解。

> **参数写法**：带值的写成 `--key=value`，不接受空格分隔。布尔参数
> （`--json`、`--dry-run`、`--all`、`--yes`、`--weights`、`--reveal`、`--ignore-fitness`）不带值。

---

## 📖 用户手册

[完整手册](docs/USER-GUIDE.md)按实际顺序讲：安装、找模型、下载、登记、起服务、使用、
接入外部工具（含虚拟机与 Wi-Fi 设备、TLS、媒体与流式）、卸载、排查。

### 为什么不能用 macOS 自带的 Python

`mlx` 从 `0.30.4` 起不再发 cp39 的 wheel，而跑当前的模型架构需要
`mlx-lm >= 0.31.0`，后者又依赖 `mlx >= 0.30.4`。用 macOS 自带的 3.9.6 建的
venv **永远装不上**能用的后端 —— 而 pip 报的是 `ResolutionImpossible`，
一个字都不提解释器版本，极难自查。安装器会挑 Homebrew 的解释器；
发现已有 venv 是 3.9 时就地重建它，权重与注册表不动。

### 关于网关令牌

**所有来源的每个请求都要令牌，本机也不例外。** 例外只有 CORS 预检请求与 `GET /healthz`。
没配令牌时 `mlx up api` 拒绝启动；安装器会生成一个。

网关默认绑定 `0.0.0.0`，但只放行回环、Wi-Fi / 有线网段与虚拟机网桥（`api.allow_cidrs` 可追加）。
不设 `api.tls_cert` 与 `api.tls_key` 时走明文 HTTP —— 同一网段上的设备能看到请求内容与令牌。
只给本机用：`mlx config set api.bind --value=127.0.0.1`，再 `mlx restart api`。
细节见手册的[远端接入一节](docs/USER-GUIDE.md#remote)。

---

## 🏗️ 架构

三层，而层与层的边界才是要点：

```
T0  deployer/install.sh      bash · 宿主机侧 · 不进载荷
T1  payload/lib/mlx_server   纯 Python 标准库 · 整个 CLI
T2  payload/gateway          纯 Python 标准库 · HTTP 网关
```

T1 的 import 面等于标准库，这正是 CLI 能在 venv 损坏时活下来的原因。网关同样只用标准库
（复用少量 T1 模块）；启动时若 venv 的 python3 能 import 它就用 venv（TLS 1.3），
否则用 `/usr/bin/python3`（最高 TLS 1.2）。`check.sh` 用真实的 3.9.6 解释器编译全部载荷文件。

T1 内部的分层由守卫强制，不靠约定 —— import 只能向下：

```
result → 原语 → 策略 → 资源 → 编排 → 解析 → 装配
```

| | |
|---|---|
| 运行依赖（CLI 与网关） | 无 —— 只用 Python 标准库 |
| 运行依赖（推理） | 由工具托管的 venv 里的 `mlx`、`mlx-lm`、`mlx-vlm` 与 `huggingface_hub` |
| 测试 | 单元 · 集成（L2）· 真实安装产物上的端到端（L3）· 设计台账层 |
| | 设计台账层断言「产品表与设计台账一致」。台账属于开发过程材料、不发布，所以从公开仓库克隆下来时整个 `tests/design` **带明确原因 skip**。 |

---

## 🧪 开发

```bash
bash check.sh     # ruff + pytest + 3.9.6 下限编译；最后一行应是「全绿 ✓」
```

测试分层。最上层把真实产物装进沙箱、驱动真实的 `mlx` 二进制 ——
断言与产物之间没有桩。每次提交前跑一遍；仓库不会替你装 git 钩子。

部分 L3 网关用例读这台机器的真实网络配置：需要 Wi-Fi / 有线 / 虚拟机网桥上有 IPv4 地址，
且有一个在线的 `utun*` VPN 接口。条件不满足时这些用例**按设计失败**，而不是跳过。

---

## 📝 参与贡献

欢迎 Issue 和 PR。动手前有两件事值得先知道：

1. **新命令先是一行表数据。** 加一个动词意味着往 `verbs.tsv` 加一行
   （参数加进 `cli.tsv`，文案加进 `messages.tsv`）。守卫会断言表与实现一致 ——
   实现了不登记、登记了不实现，两种都会让测试变红。
2. **CLI 与网关保持零第三方依赖。** T1 的 import 面由守卫强制；第三方代码属于推理 venv，经子进程调用。

---

## 🙏 致谢

构建在 [MLX](https://github.com/ml-explore/mlx)、[mlx-lm](https://github.com/ml-explore/mlx-lm)
与 [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) 之上，以及
[mlx-community](https://huggingface.co/mlx-community) 发布的模型转换成果。

---

## ⚖️ 许可协议

[MIT](LICENSE)
