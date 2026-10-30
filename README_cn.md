<div align="center">

# 🧰 MLX Toolkit

**把 Apple Silicon Mac 变成一台私有的大模型服务器：模型在本机跑，提示词与输出不经过任何第三方，
OpenAI 兼容的应用都能接入，多模态模型还提供 Anthropic 接口。**

[![Version](https://img.shields.io/badge/version-1.2.0-brightgreen)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-black)](https://support.apple.com/zh-cn/116943)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Backend](https://img.shields.io/badge/backend-mlx--lm%20%7C%20mlx--vlm-orange)](https://github.com/ml-explore/mlx-lm)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[English](README.md) · 🌐 **简体中文**

📖 [用户手册](docs/USER-GUIDE.md) · 📝 [变更记录](CHANGELOG.md)

</div>

---

## 🎯 解决什么问题

在 Mac 上本地跑模型，难的不是推理本身，而是推理之外的事：

| 常见做法 | 问题 | MLX Toolkit |
|---|---|---|
| 每个模型单独起一个端口 | 客户端要逐个配，端口和参数各不相同 | **一个网关、一个地址、一把令牌**，接所有在跑的模型 |
| 让虚拟机、手机能用，就把模型端口开到局域网 | 不鉴权、不限来源，同网段的任何设备都能调用 | 模型只听 `127.0.0.1`；对外只有网关，**每个请求都要令牌**，只放行本机、局域网与虚拟机网段 |
| 手工下载、选后端、估内存 | 选错后端起不来，内存不够把机器拖死，报错只有 traceback | 一条命令下载并登记；**按权重自动选后端**；启动前按空闲内存估算，装不下直接拒绝 |
| 重装、升级时手工迁移 | 配置丢失，几十 GB 权重被误删 | 代码、数据、权重分三处；**重装只换代码**，配置自动备份，安装器可以放心重跑 |

## 💎 核心价值

| 价值 | 简介 |
|---|---|
| 🔒 **数据不出本机** | 推理全部在本机完成，提示词与输出不经过任何第三方服务。工具自身只在安装依赖、下载权重、按请求代取远程媒体时联网 |
| 🔌 **一个入口接所有客户端** | 同一地址、同一令牌接所有在跑的模型；Cherry Studio、Qwen Code 等 OpenAI 风格客户端直接接入，Claude Code、Codex CLI 接多模态模型 |
| 🛡️ **默认安全** | 安装即生成令牌，所有来源都要鉴权，模型进程只听回环；暴露面用 `mlx api info` 一查便知 |
| 🧯 **不破坏现场** | 校验先于动手，被拒的操作不留下文件或进程，`restart` 被拒时旧服务照常在跑；内存装不下的模型不启动；删掉的权重可以从回收站恢复 |
| 🩺 **可自助排障** | 12 项内置检查，失败项给出带真实路径的修法；`mlx logs` 直接看服务日志；每次安装都留日志 |

---

## ✨ 功能特性

| 特性 | 简介 |
|---|---|
| 📦 **一条命令装模型** | `mlx model install` 下载并登记，可续传，进度按实际落盘的字节计算 |
| 🧭 **按权重自动选后端** | 每次 `mlx up` 读模型的 `config.json`：多模态走 `mlx_vlm`，纯文本走 `mlx_lm`；可用 `modality` 强制 |
| 📏 **启动前容量评估** | 按物理内存（含已在跑的模型）与当前空闲内存两道估算，各先预留 20%，装不下直接拒绝并给出数字；确实要试可加 `--ignore-fitness`；`--dry-run` 先看用哪个后端、依据是什么 |
| ✅ **起来就能用** | `mlx up` 等模型真正可用才返回：mlx_lm 模型会先发一次 1 token 补全确认，加载失败当场报错并停掉进程（`--no-wait` 跳过） |
| 🌐 **统一网关** | `/v1/chat/completions`、`/v1/completions`、`/v1/responses`、Anthropic `/v1/messages`、`/v1/models`，`/v1` 前缀可省；`/v1/completions` 只有纯文本模型提供，`/v1/responses` 与 `/v1/messages` 只有多模态模型提供 |
| 🖥️ **多来源接入** | 本机、虚拟机（Parallels / VMware 共享网络）、同一 Wi-Fi / 以太网网段的设备；`mlx api info` 给出各自的地址 |
| 🧠 **思考归一** | 思考强度按各模型模板接受的取值归一；思考开关与强度按后端要求的格式传给模型 |
| 🖼️ **多模态媒体** | `http(s)://` 图片、音频、视频由网关取回再交给模型；视频落临时文件，请求结束即删 |
| 📡 **流式与断连取消** | 流式请求在排队与模型暂无输出时发保活帧；客户端断开即取消生成，明文与 HTTPS 都一样，不白占算力 |
| ♾️ **可反复运行的安装器** | 什么都没变时几秒结束、不停服务；有变化才重装，重装前自动备份配置与模型登记表；升级时清掉旧版残留文件 |
| 🗂️ **代码、数据、权重分离** | 三处互不相干，重装只替换代码；几十 GB 的权重和配置不受影响 |
| ♻️ **回收站** | `mlx model rm <模型> --weights` 把权重移进回收站，`mlx model trash restore <ID>` 恢复 |
| 🔋 **零依赖 CLI** | CLI 与网关只用 Python 标准库：推理环境坏了，`mlx check` 照样能跑、照样告诉你怎么修 |

---

## 🛡️ 安全特性

### 接入控制

| 特性 | 简介 |
|---|---|
| **全来源令牌鉴权** | 每个请求都要网关令牌，本机也不例外；只有 CORS 预检与 `GET /healthz` 免鉴权。没有令牌时网关拒绝启动；令牌须为 32–128 个字符（字母、数字、`-`、`_`） |
| **来源网段放行** | 只放行回环、Wi-Fi / 以太网网段与虚拟机网桥；VPN、雷雳网桥、USB 设备模式链路一律拒绝；USB 坞等有线网卡用 `api.allow_ifaces` 显式放行，额外网段用 `api.allow_cidrs` 追加；网段随网络变化自动重算。来源检查在连接建立时就做，被拒的来源一个字节都不读 |
| **端点白名单** | 白名单之外一律 404，请求不会到达模型；`POST` 必须是 `application/json` |
| **模型进程只听回环、各要后端密钥** | 后端显式绑定 `127.0.0.1`，对外只有网关一个端口；两种后端都只认独立的后端密钥（`mlx_lm` 上游没有鉴权，由工具的鉴权包装补上），本机其他进程绕不过网关 |
| **可选 TLS** | 配置 `api.tls_cert` / `api.tls_key` 即启用，最低 TLS 1.2 |

### 网关协议加固

| 特性 | 简介 |
|---|---|
| **防请求走私** | 拒绝分块编码的请求体、畸形或超长的 `Content-Length`、带换行续行的请求头；拒绝后关闭连接，残留字节不会被当成下一条请求 |
| **先鉴权后读体** | 鉴权与端点检查在读取请求体之前完成；`Expect: 100-continue` 也先鉴权、先预留预算 |
| **流式错误可见** | 上游在流中途断开时，按 OpenAI / Responses / Anthropic 各自的格式补一个错误事件；排队超过 15 秒先发响应头并保活，客户端不会因自己的超时断开 |
| **错误回程净化** | 回给虚拟机、Wi-Fi 设备的上游错误按白名单重建：只保留像参数校验说明、又不含实现细节的消息，与短标识符形式的 `type` / `code` / `param`，其余换成通用文案；后端 5xx 与鉴权失败对所有来源只回通用的 502；本机来源的 4xx 保留原文便于排查 |

### 防滥用与资源保护

| 特性 | 简介 |
|---|---|
| **媒体代取防 SSRF** | 远端来源（虚拟机、Wi-Fi 设备）要求代取时：回环、链路本地、组播、保留地址与本机自身地址一律拒绝，放行网段之外的私网地址取不到；重定向逐跳校验；IDN 主机名拒绝。本机来源不受这些限制 |
| **慢速请求防护** | 请求头（含 keep-alive 的空闲等待）30 秒内必须收齐；请求体的读取时限随实际到货量增长，滴灌式上传回 408 并退还预算 |
| **连接数上限** | 远端连接全局最多 256 条、每个来源最多 32 条，本机另有独立额度；超出的连接立刻关闭，不占线程 |
| **在途预算** | 同时在处理的请求体与媒体有内存上限，超出回 503 并带 `Retry-After`；每个远端来源最多占四分之一，读体超时的来源 30 秒内减半；代取与内联的媒体都受单件大小与数量上限 |
| **容量拒绝** | 启动前按物理内存与当前空闲内存两道检查，装不下就拒绝启动并给出数字，避免一上来就把整机拖进交换；运行中上下文变长仍可能换页 |

### 数据与密钥保护

| 特性 | 简介 |
|---|---|
| **密钥落盘 0600** | `settings.json` 与运行记录以 0600 原子写入；聊天记录创建即为 0600 |
| **显示一律打码** | `status`、`api info` 只显示密钥是否已配置；`config ls`、`config get` 显示为 `****`，`config get --reveal` 才显示原值；`mlx bootstrap keys` 只补空值、从不输出值；`config set --value-stdin` 设密钥，值不进进程列表与 shell 历史 |
| **导出不带机密** | `config export` 一律排除机密字段，配置可以放心拷到别的机器 |
| **子进程环境净化** | 起模型与网关时，从继承的环境里去掉令牌与后端密钥变量，只传工具算出的值。注意：shell 里导出的 `MLX_GATEWAY_TOKEN` 会被当作网关令牌（优先于配置文件），`config ls` 中来源显示为 `env` |
| **模板渲染进沙箱** | 判定模型模板时，渲染子进程跑在 jinja2 沙箱与 macOS `sandbox-exec` 两层里：不能联网、不能写文件、读不到 home 与数据根（含 `settings.json`），内存与时长有上限 |
| **日志与输出不泄密** | 测试跑一组会碰密钥的命令，扫描三处目录与这些命令的输出，断言没有密钥出现；安装输出里的路径把 home 显示成 `~`，贴出来求助不带用户名 |

### 本机进程与文件安全

| 特性 | 简介 |
|---|---|
| **只停自己起的进程** | 按运行记录（pid 与启动时间）认领进程，并按进程组核对端口归属，不会误杀别人的进程 |
| **拒绝与外来进程共用端口** | 启动前用 `lsof` 与连接探测检查网关端口，其他用户的监听者也能发现；绑通配地址时网关先占住 `127.0.0.1` / `::1`，同用户的进程抢不走本机流量 |
| **先检查，后动手** | 校验全部通过才创建文件、起进程或停服务，由静态检查守护（停服务、删文件也算副作用）；`restart` 的检查全部在停旧服务之前；`--dry-run` 不改状态、不留进程；在跑的模型不能 `rm` |
| **删除受限** | 不可恢复的递归删除只允许作用在工具自己的几个目录上；删权重先进回收站 |

### 信任边界

```text
客户端（本机 / 虚拟机 / Wi-Fi 设备）
   │  HTTP 或 HTTPS + 网关令牌
   ▼
网关 :8080        唯一对外的端口：来源放行 → 令牌鉴权 → 端点白名单 → 媒体代取
   │  127.0.0.1 + 后端密钥
   ▼
模型进程          只绑回环；以离线模式运行，远程媒体由网关取回再交给它
```

### 默认不防的

部署前请确认以下几点可以接受：

- **不开 TLS 时，同一网段可以旁听请求内容与令牌。** 不需要远程访问时，执行 `mlx config set api.bind --value=127.0.0.1` 只给本机用；需要时开启 TLS。
- **令牌持有者视为受信任方。** 他能看到全部在跑的模型，也能让网关去取任意公网地址；本机的令牌持有者还能让网关代取回环与内网地址、让模型读取本机文件路径。令牌泄露时换一把即可切断。
- **本机的反向代理与隧道会被当成本机来源**（ssh -R、cloudflared、ngrok 等）：经它们进来的远端请求享有本机来源的权限。需要对外暴露时，用网关自己的局域网监听与 TLS，不要在本机再套一层转发。
- 同用户的进程仍能抢绑网关的**局域网 IP**（回环已被网关占住）；防这类进程要靠 TLS。
- 用 `--value` 设密钥时值会短暂出现在进程列表里；改用 `mlx config set api.token --value-stdin`。
- 模型进程运行的是上游推理代码，加载模型时会读权重目录里的文件；只加载可信来源的模型。

---

## ⚡ 快速开始

### 前置条件

- **Apple Silicon（M 系列芯片）的 Mac。** 在 Intel Mac 上安装器会直接停下，不改动任何东西。
- **Homebrew 的 Python 3.10 及以上。** macOS 自带的 3.9.6 装不上当前的 `mlx`（0.29.3 之后不再提供 3.9 的安装包，而 mlx-lm 0.31 要求 mlx ≥ 0.30.4）。
- 足够的磁盘空间：小模型几百 MB，大模型几十 GB。

```bash
brew install python@3.13
```

### 安装

```bash
git clone https://github.com/unixsec/mlx-tool.git
cd mlx-tool
bash deployer/install.sh
```

安装器会创建推理环境、生成两把密钥、把 `mlx` 加进 `~/.zshrc` 的 PATH，最后自己跑一遍 12 项检查。
**只看结尾的「安装结果」：** 全是 ✓ 就照它给的「下一步」做，通常是新开一个终端。

```console
== 安装结果
  ✓ 版本      1.2.0（已安装）
  ✓ 自检      12 项，12 项通过
  ✓ 密钥      已就绪（查看：mlx config get api.token --reveal）
  · 网关      http://127.0.0.1:8080/v1，也对局域网与虚拟机开放、均需令牌（启动：mlx up api）
  · 模型      已登记 0 个
  → 下一步：新开一个终端（或 source ~/.zshrc），然后 mlx model install <短名> --repo=<仓库>
```

### 跑一个模型

先用一个 0.6B 的小模型（约 335 MB）把整条链路走通：

```bash
mlx model install qwen06b --repo=mlx-community/Qwen3-0.6B-4bit --port=8150
mlx up qwen06b
mlx chat qwen06b --prompt="用一句话介绍你自己"
```

再换大模型。例如 27B 多模态模型约 28 GB，启动时需要约 37 GB 以上的空闲内存，一般要 48 GB 及以上的 Mac：

```bash
mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit --port=8100
mlx up qwen38
```

### 接入应用

```bash
mlx up api
mlx api info
mlx config get api.token --reveal
```

| 客户端里填 | 值 |
|---|---|
| API 地址 | OpenAI 风格客户端：本机填 `http://127.0.0.1:8080/v1`；Anthropic 客户端（Claude Code）填同一地址但**不带** `/v1`。虚拟机与 Wi-Fi 设备填 `mlx api info` 里对应的那一行 |
| API 密钥 | 网关令牌（上面第三条命令的输出） |
| 模型 ID | 登记时起的短名，例如 `qwen06b`。Claude Code、Codex CLI 要用的 `/v1/messages`、`/v1/responses` 只有多模态模型提供，接它们时填多模态模型，例如 `qwen38` |

各客户端的具体填法见手册的[客户端一节](docs/USER-GUIDE.md#clients)。

> ⚠️ **`max_tokens` 要给到 2048 以上。** 推理模型先输出思考过程，再输出正文；预算太小会在思考阶段截断，
> 客户端里显示为空白回复，而且没有任何报错。

```console
$ mlx status
模型（2）
     ID       模型                            权重    端口   后端     并发  PID    内存      体积
  ─  ───────  ──────────────────────────────  ──────  ─────  ───────  ────  ─────  ────────  ────────
  ✓  qwen38   mlx-community/Qwen3.8-27B-8bit  已下载  :8100  mlx_vlm  8     43699  27788 MB  28164 MB
  ·  qwen06b  mlx-community/Qwen3-0.6B-4bit   已下载  :8150  —        —     —      —         335 MB

网关
  ✓ api        在跑  :8080  pid 65853 · 绑定 0.0.0.0 · TLS 关 · 放行网段 3
```

### 升级与卸载

升级：拉取新代码后重跑安装器。配置、模型登记表与权重都保留，安装结果会列出重装前停掉的服务。
2026-10-30 之前克隆的仓库：git 历史重写过一次，`git pull` 会报分叉，改用 `git fetch origin --tags --force && git reset --hard origin/main`。

```bash
cd mlx-tool
git pull
bash deployer/install.sh
```

卸载：`mlx uninstall` 删除工具本体与 PATH 设置，**保留**配置与权重，需要在终端键入 `yes` 确认。
详见手册的[卸载一节](docs/USER-GUIDE.md#uninstall)。

---

## 🗺️ 命令速查

| 分组 | 命令 |
|---|---|
| 工具 | `install` · `uninstall` · `status` · `check` · `version` · `help` |
| 服务 | `up` · `down` · `restart` · `logs` |
| 模型 | `model install` · `register` · `ls` · `info` · `rm` · `gc` · `repair` · `trash ls / restore / empty` |
| 配置 | `config get` · `set` · `unset` · `ls` · `diff` · `export` · `import` · `validate` |
| 网关与密钥 | `api info` · `bootstrap keys` · `bootstrap verify` · `caps refresh` · `caps show` |
| 其他 | `chat` · `bench` |

`mlx help <命令>` 查看单条命令的详细说明。带值的参数一律写成 `--key=value`。

| 目录 | 默认位置 | 内容 |
|---|---|---|
| 代码 | `~/.local/share/mlx_server` | 工具本体与推理环境，重装只替换这里 |
| 数据 | `~/.local/state/mlx_server` | 配置、模型登记表、日志、备份 |
| 权重 | `~/.local/share/mlx_models` | 下载的模型权重 |

三处都可以在安装时用 `MLX_ROOT_CODE` / `MLX_ROOT_DATA` / `MLX_ROOT_WEIGHTS` 改。

---

## 📖 文档

- [用户手册](docs/USER-GUIDE.md)：安装、找模型、起服务、接入客户端（含虚拟机、Wi-Fi 设备与 TLS）、[媒体](docs/USER-GUIDE.md#media)、[思考](docs/USER-GUIDE.md#thinking)、[流式](docs/USER-GUIDE.md#streaming)、[远端接入](docs/USER-GUIDE.md#remote)、排查
- [变更记录](CHANGELOG.md)：各版本改动与升级须知

---

## 🧪 开发与贡献

```bash
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
bash check.sh
```

`check.sh` 依次运行 ruff、pytest，并用真实的 Python 3.9.6 编译全部产品文件，最后一行应为「全绿 ✓」。
最上层的测试把真实安装产物装进沙箱，驱动真实的 `mlx` 命令。设计台账不随仓库发布，所以 `tests/design` 会带原因跳过。

结构上有两条硬约束，都由测试强制：

1. **CLI 与网关零第三方依赖**，只用 Python 标准库；第三方代码只在推理环境里，通过子进程调用。
2. **命令、参数、文案、配置键都定义在 `payload/share/` 的表里**，加命令先加一行表数据；表与实现不一致时测试变红。

欢迎提 Issue 和 PR。

---

## 🙏 致谢

构建在 [MLX](https://github.com/ml-explore/mlx)、[mlx-lm](https://github.com/ml-explore/mlx-lm)
与 [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) 之上，并使用 [mlx-community](https://huggingface.co/mlx-community) 转换的模型。

## ⚖️ 许可协议

[MIT](LICENSE)
