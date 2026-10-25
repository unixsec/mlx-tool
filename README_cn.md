<div align="center">

# 🧰 MLX Toolkit

**在 Apple Silicon 上跑本地大模型 —— 并接进任何 OpenAI 兼容的应用。**

[![Version](https://img.shields.io/badge/version-1.0.0-brightgreen)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-black)](https://support.apple.com/zh-cn/116943)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Backend](https://img.shields.io/badge/backend-mlx--lm-orange)](https://github.com/ml-explore/mlx-lm)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-158%20passing-success)](tests)

[English](README.md) · 🌐 **简体中文**

📖 [用户手册](docs/USER-GUIDE.md) · 🐛 [已知缺口](docs/KNOWN-ISSUES.md) · 📝 [变更记录](CHANGELOG.md)

</div>

---

## 🚀 最新进展

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
git clone https://github.com/<你>/mlx-tool.git && cd mlx-tool
bash deployer/install.sh
source ~/.zshrc          # 或者新开一个终端
mlx check                # 12 项检查，应该全过
```

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
mlx up api               # OpenAI 兼容网关
mlx api info             # 本机、虚拟机、Wi-Fi 设备各自该填的地址
mlx config get api.token --reveal   # 所有客户端都要带的令牌
```

| 客户端里填 | 值 |
|---|---|
| API 地址 | 本机：`http://127.0.0.1:8080/v1`；虚拟机或 Wi-Fi 设备：`mlx api info` 里对应那行 |
| API 密钥 | 网关令牌 —— 所有来源都要，本机也不例外 |
| 模型 ID | `qwen38`（`mlx model ls` 里的 id，不是 HF 仓库名） |

> ⚠️ **`max_tokens` 要给到 2048 以上。** 推理模型会先把思考过程输出到单独的
> `reasoning` 字段，再产出 `content`。预算给小了会在思考阶段被截断，
> `content` 回来是空的 —— 客户端里显示为**空白回复且没有任何报错**。

---

## ✨ 功能特性

| 特性 | 实际意味着什么 |
|---|---|
| 🔋 **零依赖 CLI** | 所有面向用户的命令只 import 标准库。推理 venv 坏掉时，`mlx check` 照样能跑、照样告诉你怎么修。解释器按 venv → Homebrew → 系统三级回落，你和一份可用的诊断之间没有单点故障。 |
| 🗂️ **三根分离** | 代码、状态、权重放在三个互不相干的目录。重装工具只重写第一个 —— 28 GB 的下载和认识它的注册表都留着。三个位置都能用 `MLX_ROOT_*` 覆盖。 |
| 🔌 **一个网关接所有客户端** | `mlx up api` 把所有在跑的模型收敛到一个 OpenAI 兼容端点后面（`/v1/models`、`/v1/chat/completions`，含流式）。Cherry Studio、Open WebUI、或者裸 `curl`，都只要三个值，不用适配器。 |
| 🛑 **先拒绝，而不是先破坏** | 结果带类型：`Done` / `Refused` / `Failed`，而 `Refused` **保证零资源已创建** —— 由静态守卫强制，不靠自觉。起一个内存装不下的模型会被拒绝并列出数字，而不是起起来、然后让机器换页到卡死。 |
| 🎯 **三态探针** | `Ok(v)` / `Unknown(kind, reason)`。"判不出来"绝不静默塌成"否" —— 命令 fail-closed 并指名是哪个输入不确定。 |
| 📋 **表就是事实来源** | 六张 TSV 表定义命令面、参数、全部面向用户的文案、配置键、模型类型、分发清单。解析器、`mlx help`、shell 补全全部由它们生成。 |
| ♻️ **删除可恢复** | `model rm --weights` 把权重移进回收站。`model trash ls` / `restore` / `empty` —— 确认强度按**这个动作到底有多不可逆**分档。 |
| 🧪 **抓到过真缺陷的守卫** | 158 个测试函数、435 处断言，跑在沙箱里的真实安装产物上。多数守卫都带着注释，说明它当初该抓住上一版的哪个具体缺陷。 |

---

## 🖼️ 长什么样

**健康检查 —— 十二项，每一项失败时都给可执行的修法**

```console
$ mlx check
  ✓ code_root     ok
  ✓ data_root     ok
  ✓ weights_root  ok
  ✓ venv          ok
  ✓ tables        ok
  ✓ models_file   ok
  ✓ run_dir       ok
  ✓ ports         ok
  ✓ caps          ok
  ✓ gateway_nets   ok
  ✓ gateway_port   ok
  ✓ settings_mode  ok

  12 项，12 项通过
```

**状态 —— 登记了什么、在跑什么、吃多少内存**

```console
$ mlx status
模型（2）
     ID        模型                                状态  端口   后端     并发  PID    内存      体积
  ─  ────────  ──────────────────────────────────  ────  ─────  ───────  ────  ─────  ────────  ────────
  ✓  qwen38    mlx-community/Qwen3.8-27B-8bit      就绪  :8100  mlx_vlm  8     43699  27788 MB  28164 MB
  ·  qwen38-d  mlx-community/Qwen3.8-27B-MTP-4bit  就绪  :8101  —        —     —      —         253 MB

  2 个已登记，1 个在跑，共占 27788 MB 内存

网关
  ✓ api        在跑  :8080  pid 65853
```

**下载进度 —— 自己扫盘算出来的，不依赖下载器的输出**

```console
$ mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit --port=8100
  ↓ 下载 mlx-community/Qwen3.8-27B-8bit
   … 已下 23.3 GB  21.7 MB/s  84.8%  约剩 3 分
  installed  qwen38
  path       ~/lab/models/mlx/hub/models--mlx-community--Qwen3.8-27B-8bit/snapshots/main
  state      就绪
```

**报错说清怎么修，而不只是说哪儿疼**

```console
$ mlx chat "如何查询手机sn号"
没有名为「如何查询手机sn号」的模型。已登记：qwen38、qwen38-d
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
| **模型** | `install` · `register` · `ls` · `info` · `rm` · `gc` · `repair` · `trash {ls,restore,empty}` |
| **配置** | `get` · `set` · `unset` · `ls` · `diff` · `export` · `import` · `validate` |
| **网关** | `api info` |
| **后端能力** | `caps refresh` · `caps show` |
| **其他** | `chat` · `bench` · `bootstrap verify` |

33 条公开命令。任何一条都可以 `mlx help <命令>` 看详解。

> **参数写法**：带值的写成 `--key=value`，不接受空格分隔。布尔参数
> （`--json`、`--dry-run`、`--yes`、`--weights`）不带值。

---

## 📖 用户手册

[完整手册](docs/USER-GUIDE.md)有 15 章，里面每个 `$` 开头的块**都是从真实
运行里粘出来的** —— 安装、找模型、下载、登记、起服务、使用、接入外部工具、
卸载、排查。

### 为什么不能用 macOS 自带的 Python

`mlx` 从 `0.30.4` 起不再发 cp39 的 wheel，而跑当前的模型架构需要
`mlx-lm >= 0.31.0`，后者又依赖 `mlx >= 0.30.4`。用 macOS 自带的 3.9.6 建的
venv **永远装不上**能用的后端 —— 而 pip 报的是 `ResolutionImpossible`，
一个字都不提解释器版本，极难自查。安装器会挑 Homebrew 的解释器；
发现已有 venv 是 3.9 时就地重建它，权重与注册表不动。

### 关于网关令牌

网关不做鉴权，而且它自己就把理由说了出来：

> 同机场景下模型端口本就在回环上无鉴权监听；网关令牌提供零额外阻挡，
> 真正的边界是绑定地址。

`api.bind` 默认 `127.0.0.1`。改成 `0.0.0.0` 就是把一个**无鉴权端点**
暴露到局域网 —— 那种场景请放在做鉴权的反向代理后面。

---

## 🏗️ 架构

三层，而层与层的边界才是要点：

```
T0  deployer/install.sh      bash · 宿主机侧 · 不进载荷
T1  payload/lib/mlx_server   纯 Python 标准库 · 整个 CLI
T2  payload/gateway          跑在 venv 里 · 可以用第三方库
```

T1 的 import 面等于标准库，这正是 CLI 能在 venv 损坏时活下来的原因。
T1 内部的分层由守卫强制，不靠约定 —— import 只能向下：

```
result → 原语 → 策略 → 资源 → 编排 → 解析 → 装配
```

| | |
|---|---|
| 产品代码 | 7.2k 行，50 个文件 |
| 测试 | 5.9k 行，158 个函数，435 处断言 |
| | 其中 18 条断言「产品表与设计台账一致」。台账属于开发过程材料、不发布，所以它们在本仓库里**带明确原因 skip** —— `140 passed, 18 skipped` 就是预期结果。 |
| 运行依赖（CLI） | 无 |
| 运行依赖（推理） | 由工具托管的 venv 里的 `mlx` + `mlx-lm` |

---

## 🧪 开发

```bash
cd v7 && bash check.sh     # ruff + pytest + 3.9.6 下限编译
```

测试分四层。最上层把真实产物装进沙箱、驱动真实的 `mlx` 二进制 ——
断言与产物之间没有桩。pre-commit 钩子跑的是同一个脚本。

---

## 📝 参与贡献

欢迎 Issue 和 PR。动手前有两件事值得先知道：

1. **新命令先是一行表数据。** 加一个动词意味着往 `verbs.tsv` 加一行
   （参数加进 `cli.tsv`，文案加进 `messages.tsv`）。守卫会断言表与实现一致 ——
   实现了不登记、登记了不实现，两种都会让测试变红。
2. **T1 保持零第三方依赖。** import 面由守卫强制。需要第三方库的东西属于 T2。

---

## 🙏 致谢

构建在 Apple 机器学习研究团队的 [MLX](https://github.com/ml-explore/mlx) 与
[mlx-lm](https://github.com/ml-explore/mlx-lm) 之上，以及
[mlx-community](https://huggingface.co/mlx-community) 发布的模型转换成果。

---

## ⚖️ 许可协议

[MIT](LICENSE)
