# MLX Toolkit 使用指南

> macOS + Apple Silicon 上管理 MLX 本地大模型的命令行工具。
> 工具版本 1.1.1（改动见 [CHANGELOG](../CHANGELOG.md) 的 `[1.1.1]` 段）。
> 带 `$` 提示符的段落取自真实运行，路径、pid、地址等换成了示意值，输出的形状与本版一致。

本文按**你实际会走的顺序**编排：先把工具装上，再找模型、下载、登记、启动、使用，
最后是停用、卸载与升级。每一节都写清楚**现在能做到什么程度** ——
本版有几处确实还做不到，那些地方给了可用的替代路径，不会让你走到一半卡死。

---

## 〇 · 先看这张表：现在到底能做什么

| 你想做的 | 现在 | 怎么做 |
|---|---|---|
| 装 / 卸 / 升级工具 | ✅ 可用 | 第一、十、十一节 |
| 找模型（搜索） | ❌ 不提供 | 去 HuggingFace 或魔搭网站找，见第二节 |
| 下载权重 | ✅ 可用 | 第三节。工具自己下，直接落到规划目录 |
| 登记模型 | ✅ 可用 | 第四节 |
| 启动模型服务 | ✅ 可用 | 第五节 |
| 启动多模态模型（图像输入） | ✅ 可用 | 第五节「自动选择后端」。按权重自动选 mlx_vlm |
| 用模型对话 / 跑基准 | ✅ 可用 | 第六节 |
| 起网关 | ✅ 可用 | 第七节 |
| 接入 Cherry Studio / OpenWebUI | ✅ 可用 | 第七节。三项配置填完即可对话 |
| 从虚拟机、同一 Wi-Fi 的设备连网关 | ✅ 可用 | 第七节「[从虚拟机、同一 Wi-Fi 的设备连](#remote)」。所有来源都要令牌，TLS 可选 |
| 接入 Claude Code / Codex CLI | ✅ 可用（仅多模态模型） | 第七节「[接入各类客户端](#clients)」 |
| 发图片、音频、视频；控制思考 | ✅ 可用 | 第七节「[媒体](#media)」「[思考与输出长度](#thinking)」 |
| 停用服务 | ✅ 可用 | 第八节 |
| 卸载模型登记 | ✅ 可用 | 第九节 |
| 改配置、管密钥 | ✅ 可用 | 第十二之二节 |
| 自检 | ✅ 可用 | `mlx check`（12 项），见第十二节 |

整条链已在真机上跑通：下载 `mlx-community/Qwen2.5-0.5B-Instruct-4bit` →
登记 → `mlx up` → `mlx chat` 拿到回答 → `mlx up api` → Cherry Studio 经网关对话。

**还没做的**：查看日志的命令、模型格式转换 / 量化 / 升降级都还没有，敲了会报「未知命令」或
「没有子命令」（rc 2），不会假装成功。日志直接看数据根下的 `logs/<服务>.log`。
安全上默认不防的几点见 README 的「默认不防的」一节。

---

## 一 · 装工具

### 1.1 前置

**要装 Homebrew 的 Python**（≥ 3.10，推荐 3.13）：

```
$ brew install python@3.13
$ /opt/homebrew/bin/python3 -V
Python 3.13.x            # 有这一行就够了，安装器会自己挑
```

不需要先建 venv —— 安装器会用它挑到的解释器建。

**为什么不能用 macOS 自带的 `/usr/bin/python3`（3.9.6）**：`mlx` 从 `0.30.4`
起只发 cp310+ 的 wheel，而跑 Qwen3.8 这类新架构要 `mlx-lm ≥ 0.31.0`，后者又要
`mlx ≥ 0.30.4`。3.9 上**永远装不上**能跑新模型的后端，且 `pip` 只会报
`ResolutionImpossible: 依赖冲突`，一个字都不提解释器版本 —— 极难自查。

**分工要分清**：工具本体（`check`、`config`、
`model ls`、`help` 这些）是纯标准库的，**系统 python3 也跑得动**，这是故意的 ——
venv 坏掉时你还得能用 `mlx check` 诊断它。Homebrew 的 Python 是给 venv 用的，
venv 只服务**下载权重**与**跑推理**这两件事。

<a id="install"></a>

### 1.2 装

在源码目录（`git clone` 下来的 `mlx-tool`）里跑：

```
$ bash deployer/install.sh
== 备份状态文件
   · 首次安装，没有要备份的
== 建三根
== 拷载荷
== 写 constants.env
== 建 venv
   ✓ ~/.local/share/mlx_server/.venv  Python 3.13.x
== 装推理依赖（失败不影响工具本体）
   ✓ huggingface_hub
   ✓ mlx
   ✓ mlx-lm
   ✓ mlx-vlm
== 采集后端能力
   ✓ caps.json
== 生成密钥（只补空值，已有的不动）
     ✓ 已生成  api.token
     ✓ 已生成  backend.api_key
     查看原值：mlx config get <键名> --reveal
== 网关暴露面
   !! 网关默认绑定全部网卡（0.0.0.0）：本机、Parallels 虚拟机、同一 Wi-Fi / 有线网段都能连。
      所有来源都要令牌（查看：mlx config get api.token --reveal）；VPN、雷雳网桥等接口一律拒绝。
      只给本机用：mlx config set api.bind --value=127.0.0.1
== 校验产物（按 manifest.tsv）
== 挂 PATH
   ✓ ~/.zshrc（备份 ~/.local/state/mlx_server/backups/.zshrc.20261026-071807）
== 自检

== 安装结果
  ✓ 版本      1.1.1（已安装）
  ✓ 自检      12 项，12 项通过
  ✓ 密钥      已就绪（查看：mlx config get api.token --reveal）
  · 网关      http://127.0.0.1:8080/v1，也对局域网与虚拟机开放、均需令牌（启动：mlx up api）
  · 模型      已登记 0 个
  → 下一步：新开一个终端（或 source ~/.zshrc），然后 mlx model install <短名> --repo=<仓库>
            <仓库> 在 huggingface.co/mlx-community 挑，例如 mlx-community/Qwen3-0.6B-4bit
  · 日志      ~/.local/state/mlx_server/logs/install/20261026-071807.log
```

**只看最后的「安装结果」就够了**：全是 ✓ 就照「下一步」做。有 ✗ 时那一行会写怎么补；
自检有未通过的项时，它们会逐项列在「== 自检」下面，每项附下一步。屏幕上的路径把你的 home 显示成 `~`，
完整输出（含 `mlx version` 与 `mlx check` 全文）在「日志」那行的文件里，求助时贴它就行。

安装器做的事比看上去多三件，第一次装之前知道比较好：

- **装 `mlx-vlm`**：多模态模型的后端，会带入 torch 等依赖，第一次装要多等几分钟。
  某个包没装上时打印 `! <包> 未装上` 和补装命令，工具本体不受影响。
- **生成两把密钥**：`backend.api_key`（访问 mlx_vlm 后端）与 `api.token`（客户端访问网关），只补空值，
  输出只列键名不打印值（见[密钥](#keys)）。
- **网关默认对局域网开放**：上面那段 `!!` 警告就是在说这件事；只给本机用就照它给的命令改。

开头有两道检查：不是 Apple Silicon（M 系列芯片）的 Mac 直接停下（rc 70，什么都不改，推理后端 mlx 只支持 arm64）；
没找到 3.10 以上的 Homebrew Python 时先提醒一句再继续（见上一节）。

**可以放心重跑**：

- 代码、配置、PATH 都没变且自检通过时，打印「已是当前版本」并直接给出安装结果，**不停服务、不动文件**，几秒结束；
  要强制重装：`bash deployer/install.sh --force`。
- 有变化时才重装。重装第一步是 `== 收敛正在运行的服务`，先停掉在跑的服务，安装结果里会列出停了谁、怎么起回来。
- 动盘之前把 `settings.json`、`models.jsonl` 备份进数据根的 `backups/`（和最近一份内容相同就不再复制）；
  密钥只补空值；PATH 块只留一份；每次安装的输出另存到 `logs/install/<时间>.log`。

**PATH 是自动写入的**，不用你手动贴。安装器用 `# >>> mlx_server:mlx >>>` 标记块
包住它写的行，重装时先删旧块再写新块（永远只有一份），改动前把你的 rc 备份到数据根。

### 1.3 装到别处

三个根都能在**安装时**覆盖，装完写进 `constants.env`，之后不用再设：

```bash
MLX_ROOT_CODE=/opt/mlx \
MLX_ROOT_DATA=/opt/mlx-data \
MLX_ROOT_WEIGHTS=/Volumes/ext/mlx \
  mlx install
```

不想让它动你的 rc：加 `MLX_RC_FILES=/dev/null`，然后自己把 `<代码根>/bin` 加进 PATH。

<a id="status"></a>

### 1.4 验证

**新开一个终端**（PATH 要重新加载），然后：

```
$ mlx status
工具状态

  ✓ 代码根   ~/.local/share/mlx_server
  ✓ 数据根   ~/.local/state/mlx_server
  ✓ 权重根   ~/.local/share/mlx_models
  · 工具版本 1.1.1

  ✓ PATH 生效  ~/.local/share/mlx_server/bin
  ✓ 分发清单  73 项，完整

模型（2）
     ID       模型                            权重    端口   后端     并发  PID    内存      体积
  ─  ───────  ──────────────────────────────  ──────  ─────  ───────  ────  ─────  ────────  ────────
  ·  qwen05b  mlx-community/Qwen3-0.6B-4bit   已下载  :8150  —        —     —      —         335 MB
  ✓  qwen38   mlx-community/Qwen3.8-27B-8bit  已下载  :8100  mlx_vlm  8     43699  27788 MB  28164 MB

  2 个已登记，1 个在跑，共占 27788 MB 内存

网关
  ✓ api        在跑  :8080  pid 65853 · 绑定 0.0.0.0 · TLS 关 · 放行网段 3
```

- `PATH 生效` 那行打 ✗ 就是终端没重载 —— 新开一个，或 `source ~/.zshrc`。
- `分发清单` 的项数来自安装产物的清单（`share/manifest.tsv`），缺文件时列出缺的前几个；`mlx bootstrap verify` 逐项校验。
- 模型表的「后端」「并发」只对在跑的模型有值。挂了完整 drafter 的 mlx_lm 并发列显示「串行」（见[投机解码](#draft)）。
- 网关行在跑时带绑定地址、TLS 开关与放行网段个数。运行记录读不出来时下面多一行 `└ 运行记录读不出来…`，
  照它说的确认进程后删掉数据根的 `run/api.json` 再 `mlx up api`。
- 改过密钥而服务还没重启时，末尾有一行 `⚠ 密钥已变更，需重启：mlx restart …`。

只看一个服务：`mlx status qwen38`；看所有有运行记录的服务：`mlx status --all`；给脚本用：`mlx status --json`。

---

### 1.5 命令形态（**先看这条，能省一次报错**）

`mlx` 的**带值参数一律写成 `--key=value`**，不接受空格分隔：

```text
mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit   ✓
mlx model install qwen38 --repo mlx-community/Qwen3.8-27B-8bit   ✗ rc 2
                               ↑ 报「--repo 需要一个值，写成 --repo=<值>」
```

布尔参数（`--json` / `--dry-run` / `--all` / `--yes` / `--weights` / `--reveal` / `--ignore-fitness`）
不带值，直接写：`mlx model rm qwen38 --weights --yes`。每个参数只属于特定命令，
用错了会报「`--x` 不是 `mlx …` 的参数（它属于：…）」，`mlx help <命令>` 列出该命令能用的。

**为什么跟 GNU 习惯不同**：`--k v` 要靠"下一个 token 是值还是位置参数"来消歧，
而本工具把凡是不以 `--` 开头的词都当位置参数 ——
两者会打架。锁死等号形态后，`--repo` 缺值是**当场报错**而不是把下一个词
悄悄吞掉。代价是要记一条规矩，换来的是不会静默做错事。

> 注意区分：`hf`、`mlx_lm.server` 这些**外部命令**用的是 GNU 风格空格形态。
> 本节只管 `mlx` 自己的 flag。

---

## 二 · 找模型

**本工具不提供搜索。** 这是设计时的决定（`mlx model search` 全套约 700 行，
判断「哪个模型适合我的机器」这件事移到了 `model install` 的确认环节）。

```
$ mlx model search qwen
model 没有子命令 'search'；可用：gc · info · install · ls · register · repair · rm · trash empty · trash ls · trash restore
```

**怎么找**：去 <https://huggingface.co/mlx-community> 挑。这个组织专门放
已经转成 MLX 格式的模型，名字形如 `mlx-community/Qwen3.8-27B-8bit`。
挑的时候看两件事：

- **量化位数**：`8bit` / `4bit` 后缀。位数越低越省内存，质量略降。
- **体积 vs 你的内存**：模型文件总大小要能装进统一内存，且要留出余量。
  27B-8bit 约 28 GB，在 32 GB 的机器上会很紧张；64 GB 才舒服。

记下那个 `组织/模型名` 字符串，下一节要用。

---

## 三 · 下载权重

权重**必须落在工具的规划目录里**，工具才解析得到。这一节先说规划目录长什么样，
再给三条把权重放进去的路子：工具自己下（最省事）、HuggingFace 手工下、
魔搭社区手工下（国内快）。

### 3.1 规划目录：权重该放在哪

```
<权重根>/hub/models--<组织>--<模型名>/snapshots/<revision>/
   │                 │                              └─ 版本，默认 main
   │                 └─ 仓库名里的 `/` 换成 `--`，前缀固定 `models--`
   └─ 默认 ~/.local/share/mlx_models，装到别处时看 `mlx status` 的「权重根」那行
```

举例，仓库 `mlx-community/Qwen2.5-0.5B-Instruct-4bit` 的权重完整路径是：

```
~/.local/share/mlx_models/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main/
```

这是 HuggingFace 的缓存布局，工具按它解析。**放错地方的后果是静默的**：
登记能成功，但权重列一直是「未下载」，启动时才报错。

不确定权重根在哪就先问工具：

```bash
mlx status | grep 权重根
```

### 3.2 路子一：让工具自己下（推荐）

一条命令同时做完下载和登记：

```bash
mlx model install qwen05b \
    --repo=mlx-community/Qwen2.5-0.5B-Instruct-4bit --port=8100
```

```
  installed  qwen05b
  modality   auto
  path       ~/.local/share/mlx_models/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main
  state      已下载
```

`path` 那行就是它落下去的地方 —— 和 3.1 的规划目录一致，不需要你算。

内部走的是 venv 里的 `hf`（安装器已经装好）。它**先下到临时目录再原子改名**，
所以中途断网、Ctrl-C 都不会在规划目录里留下半个模型 —— 要么完整要么没有。

下载失败时它**不会假装成功**：

```
下载器不可用 —— venv 里没有 hf 命令。<repo> 未下载，登记已写入但没有权重（`mlx model ls` 的权重列显示「未下载」）
  已落地（需收敛）：models.jsonl:qwen05b
  rc=3
```

rc=3、指名原因、并告诉你记录停在哪 —— 补下完权重后跑 `mlx model repair` 就能对上。

### 3.3 路子二：HuggingFace 手工下

想自己控制下载过程（断点续传、只下部分文件、用镜像站）时用这条。
**关键是 `--local-dir` 要指向 3.1 算出来的完整路径**：

```bash
hf download mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --local-dir ~/.local/share/mlx_models/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main
```

`hf` 命令来自 `huggingface_hub`。安装器已经把它装进工具的 venv 了，
不想再装一遍就直接用那个：

```bash
~/.local/share/mlx_server/.venv/bin/hf download ...
```

或者装到你自己的环境：`pip install -U huggingface_hub`。

`hf download` 的几个常用参数（**注意它是 GNU 风格，值用空格分隔**；
`mlx` 自己的 flag 相反，一律 `--k=v`，见 §1.5）：

| `hf download` 的参数 | 作用 |
|---|---|
| `--revision <分支或 commit>` | 下指定版本；目录名里的 `snapshots/<revision>` 要跟着改 |
| `--include "*.safetensors"` | 只下权重文件，跳过示例和文档 |
| `--exclude "*.bin"` | 排除老格式的 pytorch 权重，MLX 用不上 |

**国内网络慢的话**用官方镜像端点，命令本身不变：

```bash
HF_ENDPOINT=https://hf-mirror.com hf download mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --local-dir ~/.local/share/mlx_models/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main
```

### 3.4 路子三：魔搭社区（ModelScope）手工下

国内下载通常比 HuggingFace 快很多。魔搭上的 MLX 模型主要在 `mlx-community`
镜像组织下，仓库名一般与 HuggingFace 一致。

```bash
pip install -U modelscope

modelscope download \
    --model mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --local_dir ~/.local/share/mlx_models/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main
```

**注意两处与 `hf` 不同**，抄错了会报参数错：

| | HuggingFace | 魔搭 |
|---|---|---|
| 仓库怎么给 | 位置参数，直接跟在 `download` 后 | 要加 `--model` |
| 目录参数 | `--local-dir`（**连字符**） | `--local_dir`（**下划线**） |

目录**仍然按 3.1 的规划路径算**，用 HuggingFace 的 `models--组织--模型名` 布局 ——
工具只认这一种布局，跟你从哪个站下的无关。

### 3.5 手工下完之后：登记 + 校对

手工下载不会自己登记，要补一步（详见第四节）：

```bash
mlx model register qwen05b \
    --repo=mlx-community/Qwen2.5-0.5B-Instruct-4bit --port=8100
mlx model repair
mlx model ls
```

- `mlx model repair`：用磁盘事实校对注册表
- `mlx model ls`：权重列应该是「已下载」

**权重列还是「未下载」** = 路径算错了。用这条对一下工具到底在找哪里：

```bash
mlx model info qwen05b
```

它会打印「期望路径」与「实际找到」两行，跟你 `hf`/`modelscope` 实际写的目录逐字比。

---

<a id="register"></a><a id="ls"></a>

## 四 · 登记模型

下载好的权重要**登记**进注册表，工具才认得它。登记时给它一个**短名**——
之后所有命令都用这个短名，不用敲长长的仓库名。

```
$ mlx model register qwen27b --repo=mlx-community/Qwen3.8-27B-8bit --port=8100
  ✓ 已登记  qwen27b
    模态    auto（auto = 每次 up 按权重判定）
```

参数的含义（`model install` 与 `model register` 相同）：

| 参数 | 说明 |
|---|---|
| `<短名>` | 你自己起的 id，之后 `mlx up <短名>` 用它 |
| `--repo` | HuggingFace 仓库名，要和你下载时用的**一致** |
| `--port` | 这个模型的监听端口。每个模型一个，不能重复 |
| `--type` | 可选。模态覆盖：`text` / `vision`，写进这个模型的 `modality`。不给则每次 `mlx up` 按权重自动判定（见[自动选择后端](#backend)） |
| `--revision` | 可选。仓库的分支、标签或 commit，对应目录里的 `snapshots/<revision>`；不给是 `main` |

登记成功那行的「模态」显示的是登记下来的值，括号里的说明是固定文字（给了 `--type=vision` 也照样印「auto = 每次 up 按权重判定」），以 `mlx model info <短名>` 为准。

登记完看一眼：

```
$ mlx model ls
  ID                       模态    端口  权重  
  ───────────────────────  ──────  ────  ──────
  mlx-community/NoSuch-7B  vision  8199  未下载
  qwen27b                  vision  8100  已下载

  共 2 个
```

**权重列的含义**（只说权重在不在盘上，不说服务在不在跑 —— 在跑看 `mlx status` 的 ✓ 与 PID）：

- `已下载` —— 权重在盘上，记录也完整
- `未下载` —— 记录有了，但权重的版本（`revision`）还没回填：权重找不到，或刚登记还没 `repair`；没给 `--port` 时端口列也是 `—`

`--json` 里的 `state` 字段保持原值（`就绪` / `已登记` / `已登记未下载`），脚本照旧按它分派。

<a id="repair"></a>

如果权重列是「未下载」而你确信权重下好了，多半是目录布局不对。跑一次：

```
$ mlx model repair
  重建了 1 个字段：
    qwen27b.size_mb  （未设置） → 42.0
  扫描到磁盘上 1 个仓库，注册表 2 条

  已登记但磁盘上没有：mlx-community/NoSuch-7B
    → 权重未下载或已被移走；`mlx model rm <id>` 清理登记
```

`model repair` 按磁盘事实重建记录，**只填空不覆盖**，并告诉你两边对不上的地方。

---

<a id="up"></a>

## 五 · 启动模型

```
$ mlx up qwen05b
  ✓ qwen05b 已启动 · mlx_lm · 并发 8 · port 8150
```

成功行带上判定出的后端与并发上限；挂了完整 drafter 的 mlx_lm 显示「串行」而不是「并发 N」（它实际逐个处理请求）。
端口取的是**登记时给的那个**，不是默认值。想先看会跑什么命令，用 `--dry-run`：

```
$ mlx up qwen05b --dry-run
["/…/.venv/bin/python3", "-m", "mlx_lm.server", "--model", "qwen05b", "--port", "8150",
 "--max-tokens", "81920", "--host", "127.0.0.1", "--decode-concurrency", "8", "--prompt-concurrency", "8"]
  port: machine
  max_tokens: default
  …（每个配置项一行来源）
  env   HF_HUB_OFFLINE=1
  判定  R4 · mlx_lm · 依据 config.json 无非空 vision_config · thinking=关 · effort=（不传）
  就绪  bind · 最多等 20 秒 · 并发上限 8
```

来源层：`default` = 内置默认值，`machine` = 全局设置或注册表里登记的，`user` = 这个模型自己的设置，`install` = 安装时写定的（网关端口），`env` = 当前 shell 的环境变量（`MLX_GATEWAY_TOKEN`）。`config ls` 末尾会对出现过的层逐个说明。
`port: machine` 说明端口来自你登记的 8150，而不是内置的 8100。

### 它会等到真的就绪才返回

`up` 起完进程**不会立刻报成功**，而是等端口监听上（mlx_lm 最多等 20 秒；mlx_vlm 加载完权重才监听，
最多等 `20 + 3 × 体积 GB` 秒）。注意 mlx_lm **先监听、首次请求才加载模型**，所以「已启动」不保证模型能加载成功
（加载失败要到第一次请求时才暴露，看 `logs/<服务>.log`）。进程若在就绪之前退出
（最常见是端口被别人占了），返回 **rc 10** 并附日志尾部：

```
qwen05b 起来后立刻退出了（端口 8150）。最常见的原因是端口已被占用；日志尾部：
  ...
  已落地（需收敛）：run/qwen05b.json
  rc=10
```

这样你不会拿到「rc=0 + 已启动」，而下一条 `mlx status` 却说「未运行」。

### 装不下的模型会被拒绝

```
$ mlx up huge
huge 装不下：它的体积加上已在跑的模型，超过了这台机器的可分配内存预算。
先 `mlx down <别的模型>` 腾出内存，或换一个更小/量化更狠的版本。
确实要试就加 --ignore-fitness（起完可能开始换页，机器会很慢）
  rc=6
```

判据是「模型体积 + 已在跑的模型占用」对「可分配内存 × (100 − 预留比例)」。
三个比例可调：

```bash
mlx config get fit.reserve_pct
mlx config get fit.tight_pct
mlx config get fit.over_pct
```

- `mlx config get fit.reserve_pct`：预留给系统的份额，默认 20
- `mlx config get fit.tight_pct`：超过这个比例算「挤」，默认 75
- `mlx config get fit.over_pct`：超过这个比例拒绝启动，默认 95

**内存探不出来时也拒绝**（rc 9），并说明是哪个输入不确定 ——
这条闸门保护的正是「容量不明」那种情形，塌成「大概够」等于没有它。

### 权重不在盘上会被提前拒绝

```
$ mlx up qwen05b
qwen05b 的权重不在盘上（仓库 mlx-community/Qwen2.5-0.5B-Instruct-4bit）。
先下载：`mlx model install qwen05b --repo=...`，或跑 `mlx model repair` 让工具重新扫盘
  rc=6
```

rc 6 意味着**零资源创建** —— 没起进程、没写 run 记录，没有要你收敛的东西。

<a id="restart"></a>

### 起多个 · 重启

```bash
mlx up qwen05b llama8b
mlx restart qwen05b
```

- `mlx up qwen05b llama8b`：每个目标一行结论
- `mlx restart qwen05b`：先校验，校验全过才停，再起

同时起多个时端口必须各不相同 —— 登记那一步就会拦住重复端口（rc 7）；用 `config set <模型> --key=port` 给模型自设端口时同样检查（与其它模型的端口、网关端口都不能相同）。

**`restart` 分两段检查**：

- **停之前**查的：目标与登记、权重在不在、生效配置、后端判定（后端包在不在、密钥、drafter、网关的令牌与 TLS 配置）、登记端口有没有冲突。
  这些有一条不过就原样返回拒绝，在跑的服务保持原状。
- **停之后**才查的：容量够不够（在跑的这个模型自己就占着内存，只能停了再算）、后端能力有没有漂移
  （比如升级了后端包却没 `mlx caps refresh`）、网关端口有没有被别的进程占着。这几条被拒时，**服务已经停了**，
  照拒绝文案处理后再 `mlx up <id>`。

`--ignore-fitness` 对 `restart` 同样有效。

**多个目标的结论**：每个目标一行。全部成功 rc 0；失败的目标全是「拒绝」（零资源创建）时，整体也是拒绝，
rc 取其中最差的那个（例如 6 = 模型不存在）；只要有目标真的落地了东西（起了进程又失败），整体是失败，
并逐个列出要收敛的运行记录。

---

<a id="backend"></a>
### 自动选择后端 {#backend}

每次 `mlx up` 都读一遍权重目录里的 `config.json` 现场判定，不在登记时定死：

| 权重 | 后端 | 说明 |
|---|---|---|
| 带非空 `vision_config`（如 Qwen3.8-27B） | `mlx_vlm.server` | 能收图像；加载完权重才监听，所以 `up` 会先打印「最多等 N 秒」 |
| 其他 | `mlx_lm.server` | 纯文本 |

- **强制走文本**（放弃视觉，换 mlx_lm 的并发与投机解码）：`mlx config set qwen38 --key=modality --value=text`；撤销：`mlx config unset qwen38 --key=modality`。
- **思考开关跟随模型模板的默认**：模板默认开的（Qwen3.8）就带 `--enable-thinking` 起；`mlx model info <id>` 能看到「思考默认」与模板接受的 effort 值域。
- **并发上限**默认 8（`max_concurrency`，1–256）；**输出上限**默认 81920 token（`max_tokens`）。两者都能按模型或全局改，改完 `mlx restart <id>`。
- mlx_vlm 后端**要求密钥**：安装器已生成 `backend.api_key`（见[密钥](#keys)），`mlx up` 自动带上，`mlx chat` / `mlx bench` 与网关转发也会自动带。

判定不下来时**拒绝启动而不是猜**，文案会给出下一步：

| 提示 | 原因 | 怎么办 |
|---|---|---|
| 判不出是否多模态 | `config.json` 读不到或字段自相矛盾 | `mlx config set <id> --key=modality --value=text`（或 `vision`） |
| 需要 mlx_vlm 后端，但 venv 里没有 | 装包失败或旧安装 | 重跑 `bash deployer/install.sh`，再 `mlx caps refresh` |
| 需要 backend.api_key，但它是空的 | 密钥没生成 | `mlx bootstrap keys` |
| 是附属 drafter，不能单独启动 | 你 `up` 的是 MTP 预测头 | 它只能配给主模型，而本版不支持（见[投机解码](#draft)） |

---

<a id="draft"></a>
### 投机解码（speculative decoding） {#draft}

用一个**小模型**先猜若干 token，大模型一次性校验 —— 吞吐能明显提升，
输出与不用它时**逐字节一致**（校验不通过就回退，不是近似）。

**本版只支持「完整的小模型」当 drafter，且只在文本后端（mlx_lm）上**：

```bash
mlx model install qwen38-small --repo=<同一家族、同一词表的小模型仓库> --port=8199
mlx config set qwen38 --key=modality --value=text
mlx config set qwen38 --key=draft_model --value=qwen38-small
mlx up qwen38 --dry-run
```

- `mlx config set qwen38 --key=modality --value=text`：多模态模型要先放弃视觉
- `mlx up qwen38 --dry-run`：argv 里应出现 --draft-model qwen38-draft --num-draft-tokens 3

猜多少个可调（默认 3，范围 1–16）：`mlx config set qwen38 --key=num_draft_tokens --value=4`。
不用了就撤销：`mlx config unset qwen38 --key=draft_model`。

**MTP 预测头（如 `Qwen3.8-27B-MTP-4bit`）本版不支持**，配上会被拒绝：
它只能跑在 mlx_vlm 上，而 mlx_vlm 在**并发请求**下会静默输出损坏的内容
（上游 mlx-vlm #2033，2026-09 实测复现：两路并发即出现 `!!!!` 退化输出）。
研究与 AI coding 这类多子 agent 场景恰好是并发的，所以宁可不提供。
上游修复并经本机并发逐字比对复验之后再开放。

- drafter 那条记录**占一个端口号但永远不 `up`** —— 端口只是注册表的占位。
- 多模态模型挂 drafter 会被拒绝：drafter 只能在 mlx_lm 上用，而 mlx_lm 处理不了图像。

---

## 六 · 使用模型

模型起来之后有两条用法：命令行对话、跑一次基准。

<a id="chat"></a>

### 对话

**多轮**（不给 `--prompt` 就进这个模式，一行一句，`/exit` 或 Ctrl-D 结束）：

```
$ mlx chat qwen05b
你 > 1+1=?
1+1 = 2
你 > 刚才我问的是什么？
我刚才的问题是"1+1=?"。这个问题的答案是 2。
你 > /exit
  2 轮 · 会话 qwen05b-1788226997617（`--session=qwen05b-1788226997617` 接着聊）
```

每一轮都会存进会话，中途断了不丢。
**回复正文走 stdout，提示符和汇总走 stderr** —— 所以
`mlx chat qwen05b > out.txt` 存下来的是纯回复，没有夹杂提示符。

**单轮**（脚本里常用）：

```bash
mlx chat qwen05b --prompt="用一句话说明 MLX 是什么"
```

```
  reply    MLX 是苹果为自家芯片做的机器学习框架，用统一内存跑推理和训练。
  session  qwen05b-1788188855174
  turns    2
```

`session` 是会话 id，`turns` 是这个会话累计的消息条数（一问一答算两条）。
接着上一次的会话继续聊，把 id 带上：

```bash
mlx chat qwen05b --session=qwen05b-1788188855174 --prompt="再展开讲讲统一内存"
```

不带 `--prompt` 而带 `--session=<id>`，就是接着那个会话进入多轮模式：`mlx chat qwen05b --session=<id>`。

<a id="bench"></a>

### 跑基准

```bash
mlx bench qwen05b --prompt="用一句话介绍 MLX"
```

```
  chars               94
  chars_per_sec       241.0
  elapsed_sec         0.39
  port                8150
  prompt_fingerprint  662bf5bd2b818db3
  service             qwen05b
```

`prompt_fingerprint` 是提示词的哈希 —— 换了提示词再比吞吐就不是同一件事了，
这一列让你事后能对上。基准结果**不落盘**，要留就自己重定向。

### 服务没起来时

```
$ mlx bench qwen05b
qwen05b 没在跑。先起它：mlx up qwen05b
  rc=3
```

进程在跑、端口却连不上时是另一句：

```
qwen05b 的进程在跑，但连不上它的端口 8150 —— 多半是模型没加载成功。看日志：~/.local/state/mlx_server/logs/qwen05b.log
```

这种情况 `mlx status` 会说「在跑」（mlx_lm 先监听再加载，加载失败进程也不退出），**真因只在日志里**。
`mlx chat` 给的是同样两句。rc 3 = 外部依赖不可用。

---

<a id="info"></a>

## 七 · 接入 Cherry Studio 等外部工具

网关提供 OpenAI 兼容与 Anthropic 兼容接口，把已经起来的模型统一转发出去。

```
$ mlx up api
  ✓ api 已启动 · 绑定 0.0.0.0 · TLS 关 · port 8080
```

```
$ mlx api info
  ✓ 网关   在跑   pid 65853
    地址（OpenAI 兼容 base URL / Anthropic base URL）
      本机            http://127.0.0.1:8080/v1   http://127.0.0.1:8080
      Wi-Fi           http://192.168.2.11:8080/v1   http://192.168.2.11:8080
      虚拟机共享网络  http://10.211.55.2:8080/v1   http://10.211.55.2:8080
    绑定   0.0.0.0   TLS 关
    解释器 ~/.local/share/mlx_server/.venv/bin/python3 3.13.5
    令牌   已配置   来源 config   与运行中一致
    放行网段  127.0.0.0/8、::1/128、192.168.2.0/24、10.211.55.0/24
    排除接口  bridge0（Thunderbolt Bridge）、utun4（隧道接口（VPN 等））
    可用   qwen38
    未起   qwen05b（已登记，但网关转发不到）

  ⚠ TLS 未开启：同一网段上的其他设备能看到请求内容与令牌。需要时设 api.tls_cert 与 api.tls_key 后 `mlx restart api`

  所有来源都要令牌（本机也不例外）；只有上面列出的网段能连进来，VPN、雷雳网桥等其余接口一律拒绝
```

逐行看：

- **地址**：每类来源一行，左列给 OpenAI 风格客户端（带 `/v1`），右列给 Anthropic 客户端（不带 `/v1`）。网关没在跑时只有一行本机地址。
- **绑定 / TLS**：TLS 开着时显示 `TLS 开（最高 TLS1.3）` 之类，括号里是解释器支持的最高版本。
- **解释器**：网关启动那一刻判定 —— venv 的 python3 能 import 网关就用它（TLS 1.3），否则用 `/usr/bin/python3`（最高 TLS 1.2）。
- **令牌**：「来源 config」是从配置读的，「来源 env」是启动时用 `MLX_GATEWAY_TOKEN` 覆盖的；改了令牌没重启时显示「已变更，需 `mlx restart api`」。
- **放行网段 / 排除接口**：哪些来源能连、哪些网卡被排除以及原因（见[从虚拟机、同一 Wi-Fi 的设备连](#remote)）。
- **可用 / 未起**：网关转发得到的模型与已登记但没起的模型。
- 有远端地址且 TLS 关时，末尾多一段 `⚠ TLS 未开启` 提示。
- 运行记录（数据根的 `run/api.json`）读不出来时，第二行是 `⚠ 网关运行记录读不出来…`，下面只显示已知的部分。
  这不等于网关没在跑：确认进程后删掉 `run/api.json`，再 `mlx up api`。`mlx status` 的网关行同样会提示这件事。

**网关端口默认 8080**，与模型端口（`mlx model ls` 里那一列）是两回事 ——
客户端要连的是网关，不是模型。网关自己的日志在数据根的 `logs/api.log`（启动、请求访问记录带来源地址、
代取、断连等事件都在这里），排查网关问题先看它。

### `mlx up api` 被拒绝时

网关起来之前会先检查下面几件事，任何一条不过都**拒绝启动、零资源创建**（令牌与 TLS 几条 rc 6，端口被占 rc 7，判不出 rc 9），文案里给了修法：

| 提示 | 原因 | 怎么办 |
|---|---|---|
| 网关令牌 api.token 是空的 | 没生成令牌 | `mlx bootstrap keys` |
| TLS 只配了证书或私钥中的一个 | `api.tls_cert` / `api.tls_key` 只设了一个 | 两个都设，或两个都 `mlx config unset` |
| TLS 文件读不到 | 路径不对或没有读权限 | 改对路径（要绝对路径） |
| 私钥 … 的权限是 …，宽于 0600 | 私钥对同机其他用户可读 | `chmod 600 <私钥>` |
| 证书或私钥读得到、权限也对，但加载不动 | 证书与私钥不配对，或不是 PEM 格式 | 重新生成一对 |
| 端口 … 上已有别的进程在监听 | 网关端口被别的程序占着 | 停掉它，或换网关端口 |
| 端口 … 的占用状态不可判定 | 查端口占用要用 `/usr/sbin/lsof`，它不可用或输出异常 | 确认 `/usr/sbin/lsof` 存在且能运行；判定不了时网关宁可不启动，不冒险与外来进程共用端口 |

令牌与 TLS 这几条，`mlx restart api` 在停网关之前就查，不过就不会停掉正在跑的网关；端口占用要等旧网关停了才查得准。

### 三项配置

Cherry Studio / OpenWebUI / 任何 OpenAI 兼容客户端，填这三项：

| 配置项 | 填什么 | 从哪来 |
|---|---|---|
| **API 地址 / Base URL** | 本机填 `http://127.0.0.1:8080/v1`；虚拟机或 Wi-Fi 上的设备填对应那行 | `mlx api info` 的「地址」段 |
| **API 密钥 / API Key** | 网关令牌 —— **所有来源都要**，本机也不例外 | `mlx config get api.token --reveal` |
| **模型 ID / Model** | `qwen38` | `mlx api info` 的「可用」行，或 `mlx model ls` |

在 Cherry Studio 里的位置：**设置 → 模型服务 → 添加提供商 → 选「OpenAI 兼容」**，
把上面三项填进去，然后在模型列表里手动添加一个 id 为 `qwen38` 的模型。

**填 Base URL 时注意结尾**：要带 `/v1`，不要带 `/v1/`（多一条斜杠有些客户端会拼成
`//v1/chat/completions`）。填完点「检测」应当立刻通过。

### ⚠️ `max_tokens` 给小了会看到空回复

推理模型（Qwen3.8 这类）**先把思考过程输出到 `reasoning` 字段，再产出 `content`**。
预算给小了，响应在思考阶段就被截断，`content` 回来是空的：

```
$ curl ... -d '{"model":"qwen38","messages":[...],"max_tokens":20}'
  finish_reason: length
  message 的字段: ['role', 'reasoning']      ← 没有 content
```

给到 600 就正常：

```
  finish_reason: stop
  message 的字段: ['role', 'content', 'reasoning']
  content: '\n\n没问题'
```

在客户端里，前一种显示为**空白回复且没有任何报错** —— OpenAI 兼容格式里
`content` 为空是合法响应，客户端无从判断出了什么事。**建议设 2048 以上。**

服务端 `max_tokens` 的默认值已经是 81920（见[思考与输出长度](#thinking)）；这里说的是**客户端请求里自己带的** `max_tokens`。

### 网关只转发正在跑的模型

`/v1/models` 只列出 `mlx up` 起来的那些。`mlx api info` 会把两类分开显示
（「可用」/「未起」）。要用别的模型先 `mlx up <id>`。

### 重装工具会停掉服务

`bash deployer/install.sh` 的第一步是 `mlx down --all`（重装前必须收敛服务）。
装完客户端就连不上了 —— 安装器会在结尾列出它停掉的服务和起回来的命令。

### 先用 curl 验一遍

接进客户端之前，先确认接口自己是通的：

`mlx config get api.token --reveal` 给人看（键 / 值 / 来源三行）；脚本里取裸值用 `--json`：

```bash
TOKEN=$(mlx config get api.token --reveal --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["value"])')
```

1. 列模型 —— 应当能看到你起的那个

```bash
curl -s http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer $TOKEN"
```

```json
{"object": "list", "data": [{"id": "qwen38", "object": "model", "owned_by": "mlx-toolkit"}]}
```

2. 发一次对话。max_tokens 给够，否则推理模型只会返回 reasoning 没有 content

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38","messages":[{"role":"user","content":"1+1=?"}],"max_tokens":2048}'
```

3. 流式（多数客户端默认走这条），结尾应当有 data: [DONE]

```bash
curl -sN http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38","messages":[{"role":"user","content":"数到三"}],"max_tokens":2048,"stream":true}'
```

`/v1/models` 列的是**正在跑的**模型。列表是空的说明模型没起 —— 回第五节，
先 `mlx up <id>`，再刷新客户端的模型列表。

<a id="remote"></a>
### 从虚拟机、同一 Wi-Fi 的设备连 {#remote}

网关默认绑定全部网卡（`api.bind` = `0.0.0.0`），但**只放行这几类来源**，其余一律回 403 `peer_not_allowed`：

| 来源 | 放行依据 |
|---|---|
| 本机 | 回环地址 |
| 同一 Wi-Fi / 有线网段的设备 | 硬件端口类型为 Wi-Fi 或以太网的网卡所在网段 |
| Parallels / VMware 虚拟机（共享网络） | 成员含 `vmenet` / `vnic` 的网桥所在网段 |
| 你额外指定的网段 | `mlx config set api.allow_cidrs --value=192.168.8.0/24,fd00:1::/64` |

VPN（`utun*`）、雷雳网桥、AirDrop 等接口**不放行** —— VPN 对端不是你的局域网。网卡换网（换 Wi-Fi、开热点）后
网关 30 秒内自动重算；不在放行网段的地址连进来时，网关会先重算一次再判（两次重算至少隔 2 秒，连串的陌生请求不会每次都重算）。

被拒的来源收到 403 `peer_not_allowed`，**响应体里不含它的地址与网段**（不给探测者信息）；地址记在 `logs/api.log` 的 `peer_denied` 事件里。

`mlx api info` 列出每类来源该填的地址：

```
$ mlx api info
  ✓ 网关   在跑   pid 65853
    地址（OpenAI 兼容 base URL / Anthropic base URL）
      本机            http://127.0.0.1:8080/v1   http://127.0.0.1:8080
      Wi-Fi           http://192.168.2.11:8080/v1   http://192.168.2.11:8080
      虚拟机共享网络  http://10.211.55.2:8080/v1   http://10.211.55.2:8080
    …
```

**令牌**：所有来源都要。客户端填 `Authorization: Bearer <令牌>` 或 `x-api-key: <令牌>`（Anthropic 客户端用后者）。

```bash
mlx config get api.token --reveal
mlx config set api.token --value=<新值>
mlx restart api
```

- `mlx config get api.token --reveal`：看原值
- `mlx config set api.token --value=<新值>`：轮换（32–128 位字母、数字、_ 或 -）

临时换一个令牌起网关：`MLX_GATEWAY_TOKEN='<32 位以上的串>' mlx up api`（环境变量覆盖配置，只对这次启动有效）。

**只给本机用**：`mlx config set api.bind --value=127.0.0.1 && mlx restart api`。

**TLS（可选，默认关）**：TLS 关闭时同一网段上的设备能看到请求内容与令牌。需要时：

```bash
cd ~/.local/state/mlx_server
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout gateway.key -out gateway.crt \
  -subj "/CN=mlx-gateway" -addext "subjectAltName=IP:127.0.0.1,IP:192.168.2.11,IP:10.211.55.2"
chmod 600 gateway.key
mlx config set api.tls_cert --value="$PWD/gateway.crt"
mlx config set api.tls_key --value="$PWD/gateway.key"
mlx restart api
```

`subjectAltName` 里写 `mlx api info` 列出的地址。客户端要信任这张证书（把 `gateway.crt` 导入，或在 curl 里用 `--cacert gateway.crt`）。
私钥权限宽于 0600 时 `mlx up api` 会拒绝启动。系统 python 最高支持 TLS 1.2，装了推理 venv 时网关自动用 venv 的解释器（TLS 1.3）。

<a id="clients"></a>
### 接入各类客户端 {#clients}

| 客户端 | 配置 |
|---|---|
| Cherry Studio / OpenWebUI | 「OpenAI 兼容」提供商：Base URL 填 `…/v1`，API Key 填令牌，模型 id 填 `mlx api info` 的「可用」 |
| Claude Code | `ANTHROPIC_BASE_URL=http://<地址>:8080` `ANTHROPIC_AUTH_TOKEN=<令牌>` `ANTHROPIC_MODEL=qwen38` |
| Qwen Code 等 OpenAI 兼容 CLI | `OPENAI_BASE_URL=http://<地址>:8080/v1` `OPENAI_API_KEY=<令牌>` `OPENAI_MODEL=qwen38` |
| Codex CLI | `~/.codex/config.toml` 见下 |

```toml
model = "qwen38"
model_provider = "mlx"

[model_providers.mlx]
name = "mlx-toolkit"
base_url = "http://127.0.0.1:8080/v1"
env_key = "MLX_GATEWAY_TOKEN"          # 在 shell 里 export MLX_GATEWAY_TOKEN=<令牌>
wire_api = "responses"
```

Codex 启动时会打一行 `ERROR codex_models_manager::manager: failed to refresh available models: … missing field `models``，
**可以忽略**，对话照常进行。原因是 Codex 用自定义 provider 时，会按它自家模型目录的私有格式（`{"models": [...]}`）去解析 `GET /v1/models`，
而网关按 OpenAI 标准回 `{"object": "list", "data": [...]}`。解析失败后 Codex 改用内置的模型元数据。
网关不去迎合这个格式：它没有公开的版本承诺，照抄的话，Codex 一升级就可能变成别的报错（2026-10 在 Codex 0.149 上确认）。

**端点按后端能力开放**（后端由权重自动判定，见[自动选择后端](#backend)）：

| 端点 | 文本模型（mlx_lm） | 多模态模型（mlx_vlm） |
|---|---|---|
| `POST /v1/chat/completions` | ✓ | ✓ |
| `POST /v1/completions` | ✓ | 404 `endpoint_not_supported_by_backend` |
| `POST /v1/responses`、`POST /v1/responses/input_tokens` | 404 `endpoint_not_supported_by_backend` | ✓ |
| `GET` / `DELETE /v1/responses/{id}`、`POST /v1/responses/{id}/cancel`、`GET /v1/responses/{id}/input_items` | 不问它 | ✓（见下） |
| `POST /v1/messages`、`POST /v1/messages/count_tokens`（Anthropic） | 404 `endpoint_not_supported_by_backend` | ✓ |
| `GET /v1/models` | 网关自己回答：列出正在跑的模型 | 同左 |
| `GET /healthz` | 网关自己回答，**免令牌**，只回路由状态 | 同左 |
| 其他路径（embeddings、rerank、images、audio、realtime，以及后端的管理端点如 `/unload`、`/v1/settings`） | 404 `endpoint_not_supported`，不转发 | 同左 |

路径带不带 `/v1` 都行。按请求体 `model` 字段路由的端点，发给不支持它的后端时直接回 404，后端收不到请求。
按 id 的 responses 端点请求里没有 `model`：网关并发去问所有在跑的多模态模型（最多等 10 秒），第一个认得这个 id 的作答；
都不认得回 404 `not_found`，等满 10 秒还有没回话的回 504 `upstream_timeout`。
Codex 与 Claude Code 分别只用 responses 与 messages，所以只能配多模态后端的模型。

**浏览器里的 Web 客户端**：网关支持 CORS。预检请求（`OPTIONS` 带 `Access-Control-Request-Method`）在鉴权之前回 204，
允许 `GET, POST, DELETE, OPTIONS` 与 `Authorization`、`x-api-key`、`Content-Type`、`anthropic-version`、`anthropic-beta`、`openai-beta` 这几个请求头；
响应带 `Access-Control-Allow-Origin: *`，不带 `Allow-Credentials`（令牌走请求头）。真正的请求仍要令牌，
且 `POST` 必须是 `Content-Type: application/json`（浏览器的 `text/plain` 简单请求会被 415 挡掉）。

<a id="media"></a>
### 图片、音频、视频 {#media}

| 你发的 | 本机来源 | 虚拟机 / Wi-Fi 设备 |
|---|---|---|
| `data:image/…`、`data:audio/…;base64,…` | 原样交给模型 | 同左 |
| `input_audio.data` 里的裸 base64 串（严格 base64，不带 `data:` 前缀） | 原样交给模型 | 同左 |
| Anthropic `source.type=base64`（`media_type` 为 `image/*` 或不填） | 原样交给模型 | 同左 |
| `data:video/…;base64,…` | 网关解码成临时文件交给模型，请求结束即删；base64 解不开 → 400 `media_source_rejected` | 同左 |
| `http(s)://` 图片、音频（含 Anthropic `source.type=url`） | 网关取回，改写成 `data:` 交给模型 | 同左，目标地址受限（见下） |
| `http(s)://` 视频 | 网关取回存成临时文件，请求结束即删 | 同左，目标地址受限 |
| 本机文件路径、`file://`，以及模型后端会当成路径读的其他写法（不带 `;base64` 的 `data:audio`、`media_type` 不是图片的 Anthropic base64 等） | 原样交给模型 | 400 `media_source_rejected` |
| `file_id`、Anthropic `source.type=file` | 400 `media_source_rejected` | 同左 |

网关只改写媒体部件，其余字段原样转发。判定按后端实际怎么读这个值来：后端会当成本机路径去读的，远端来源一律拒。

模型进程自己不对外连接，`http(s)://` 媒体一律由网关代取。**目标地址规则**（本机来源不受限，以下只针对虚拟机与 Wi-Fi 设备）：

- 回环、链路本地、组播、未指定（`0.0.0.0`）、保留、广播地址与 Teredo **无条件拒绝**，放行网段也豁免不了；私网与 CGNAT 地址只有落在 `mlx api info` 列出的**放行网段**内才放行，否则拒绝；
- **任何公网地址一律放行**。因此经 VPN 可达的公网段地址，远端来源也能让网关去取（已知限制）；
- 目标等于本机自身的地址（包括公网形态）拒绝；IPv4-mapped、NAT64、6to4 先还原成 IPv4 再判断；
- 重定向最多 3 跳，每一跳都重新检查；主机名写成数字形式（如 `2130706433`）按 `address_denied` 处理。

代取失败返回 400 `media_fetch_failed`，`error.reason` 说明原因：

| `reason` | 含义 |
|---|---|
| `address_denied` | 目标地址不允许，或主机名写成了数字形式 |
| `dns_failed` / `connect_failed` / `timeout` | 解析不了、连不上、30 秒内没取完 |
| `redirect_limit` | 重定向超过 3 跳 |
| `too_large` | 超过 `api.media_max_mb`（看 `Content-Length` 或边读边数） |
| `bad_content_type` | 响应的 `Content-Type` 大类与这个部件要的不一致：图片部件要 `image/*`、音频要 `audio/*`、视频要 `video/*`（图片部件取回一个 `video/mp4` 也算） |
| `encoding_rejected` | 对方坚持压缩传输 |
| `http_error` | 对方没回 200：任何其他状态码（含 204、206 与 4xx / 5xx），重定向却没给 `Location`，或正文比 `Content-Length` 短（被截断） |

| 配置键 | 默认（域） | 管什么，超出时 |
|---|---|---|
| `api.media_max_mb` | 128（1–2048） | **只限网关代取的字节**：单个媒体超出 → 400 `media_fetch_failed`（`too_large`）。请求里内联的 `data:` 媒体不受它管 |
| `api.max_body_mb` | 256（1–2048） | 整个请求体（内联 `data:` 媒体算在里面）：超出 → 413 `invalid_request`，在读请求体之前就拒 |
| `api.media_max_parts` | 32（1–256） | 一个请求里的媒体个数：超出 → 400 `too_many_media_parts`，一个都不代取 |
| `api.inflight_max_mb` | 2048（64–65536） | 全网关同时在处理的请求体、代取内容与临时文件之和：超出 → 503 `gateway_busy`，带 `Retry-After: 5` |

`api.inflight_max_mb` 不能小于 `api.media_max_mb`：**每一次代取**（本机来源也一样）都先按 `media_max_mb` 整额预留，取完再退回多出的部分，
预留不下就拒。两个键各自的取值域允许 `inflight_max_mb` 比 `media_max_mb` 小，配置时**不做交叉校验** ——
这样配了之后**所有代取都会 503 `gateway_busy`**。遇到这种情况调大 `api.inflight_max_mb`（或调小 `api.media_max_mb`）。
改完 `mlx restart api` 生效。

网关日志 `api.log` 的 `media_fetched` 事件记主机名、原因与字节数，**不记完整 URL**（查询串里常有签名）。
视频临时文件落在 `<数据根>/run/media/<uuid4>/`（目录权限 0700），请求结束即删；网关崩溃留下的目录，启动时清扫一次、运行中随路由刷新（约每 5 秒）再查，删掉超过 1 小时的残留。
写不下时返回 507 `media_storage_failed`。

<a id="thinking"></a>
### 思考与输出长度 {#thinking}

**开关思考**（chat / completions 端点，任选一种写法）：

- 关：`"enable_thinking": false`、`"chat_template_kwargs": {"enable_thinking": false}`、`"thinking": {"type": "disabled"}`、
  `"reasoning_effort": "none"` 或 `"reasoning": {"effort": "none"}`（`off` / `disabled` / `false` / `0` 与 `none` 同义）。
- 开：`"enable_thinking": true`、`"chat_template_kwargs": {"enable_thinking": true}`、`"thinking": {"type": "enabled"}` 或 `{"type": "adaptive"}`。
  `"thinking": {"type": "auto"}` 等于没表态，用模型模板的默认。
- 几种写法同时出现时按这个顺序取第一个出现的：`chat_template_kwargs.enable_thinking` → `enable_thinking` → `thinking.type` → 取值为禁用的 `reasoning_effort` / `reasoning.effort`。
  开关与强度分开算：`"enable_thinking": true` 加 `"reasoning_effort": "low"` 是「开，低强度」。
多模态模型的后端（mlx_vlm）不读 `chat_template_kwargs`，网关把 `enable_thinking` / `reasoning_effort` 写到请求顶层再转给它。
responses 与 messages 端点上，网关不归一开关，交后端按原生字段（`reasoning`、`thinking`）处理，只做下面的强度映射。

**思考强度**：chat 认 `reasoning_effort`（也认 `chat_template_kwargs.reasoning_effort`、`reasoning.effort`），
responses 与 messages 认 `reasoning.effort`（顶层 `reasoning_effort` 同样映射）。模型模板只接受部分取值时，网关换成**不低于它的最小可用值**，没有就用最大值：

| 你发的 | qwen38 实际用（模板接受 `low` / `medium` / `xhigh`） |
|---|---|
| `minimal` | `low` |
| `low` / `medium` | 原样 |
| `high` / `max` | `xhigh` |

`mlx model info <id>` 显示模板接受的取值；模板不认强度的模型，网关不下发这个字段。

**输出长度**：`max_tokens` 默认 81920（可改，域 1–1048576），不是上限；请求里自己带的值照常生效。
`max_tokens` 与 `max_completion_tokens` 都认 —— 多模态模型的后端只读 `max_tokens`，网关自动换过去。
`thinking_budget` 只有多模态模型支持，发给文本模型返回 400 `thinking_budget_unsupported`。
请求里的 `adapters`、`adapter_path`、`draft_model` 会被网关剥离，客户端不能让模型加载别的权重。

<a id="streaming"></a>
### 流式与长任务 {#streaming}

网关对模型**一律用流式**取结果：

- **流式请求**：边生成边转发。收到模型的 200 响应头之后，上游静默满 15 秒（排队、读长上下文、长时间思考），网关发一行 `: keepalive`（SSE 注释，客户端会忽略），连接不会被中间设备当成空闲掐断。网关每秒检查一次，所以实际间隔在 15～16 秒之间。收到响应头之前模型不可达或出错，客户端拿到的是一个正常的 JSON 错误响应。
- **非流式请求**：网关收齐后拼成一个完整 JSON 返回。这期间没有字节可发，**无法保活，客户端自己的 HTTP 超时可能先到** —— 长输出、Agent 类任务请用流式（Claude Code、Codex、Cherry Studio 默认都是流式）。
- **客户端断开**（关页面、Ctrl-C、超时）：网关关闭到模型的连接，模型停止生成、腾出并发位；`api.log` 记 `client_disconnected`。
  - **明文连接**：网关每秒探测一次客户端是否还在（流式、非流式都一样，等响应头期间也探测），断开后约 1 秒内发现。
  - **TLS 连接无法探测**，断开只能在网关往客户端写失败时发现。流式请求：上游响应头到达之前（mlx_lm 排队可能数分钟）发现不了，模型照常生成；
    头到达之后，靠转发帧或保活写入发现，静默段最长约 16 秒，且对端已关时第一次写可能仍然成功。
    非流式请求：没有保活，要等模型生成完、网关写回响应时才发现，这次生成会跑完。
- **模型一侧出问题**：连不上模型或上游传输异常，`api.log` 记 `upstream_aborted`（与 `client_disconnected` 是两回事）；响应头还没发出时客户端收到 502 `upstream_error`（1.0.0 是 504）。非流式聚合失败同样记 `upstream_aborted` 并回 502。
  这条说的是按请求体 `model` 路由的端点；按 id 查询 / 删除 / 取消 responses 的扇出端点，后端连不上或回 5xx 是 502，等满 10 秒仍有后端没回话是 504 `upstream_timeout`。
- **流已经开始后模型断了**：`api.log` 记 `sse_truncated`（流结束时没见到终止帧：半帧、断在帧边界，或上游发了错误帧后不发终止帧就关连接）或 `upstream_aborted`（上游传输异常）。响应头已发出，客户端只看到流结束、没有错误帧；OpenAI 风格客户端可能把缺 `[DONE]` 的流当成正常结束，遇到回答突然截断时看 `api.log`。
- 同时请求超过模型的 `max_concurrency`（默认 8）时排队。mlx_vlm 排队期间保活照常；mlx_lm 排队时连响应头都不回，**此时没有保活**。挂了完整 drafter 的 mlx_lm 实际串行处理，`max_concurrency` 不生效（`mlx status` 并发列显示「串行」）。
- 没在请求里写 `stream_options.include_usage` 的流式客户端收不到 usage 帧（网关内部为聚合打开了它，但不下发）。
- 文本模型的流式帧只带 `reasoning`，网关补上多数客户端读的 `reasoning_content`。

---

<a id="down"></a>

## 八 · 停用模型

```
$ mlx down --all
  ✓ api   已停止
```

`down` 是**幂等**的：停一个没在跑的服务返回 0，不是错误。

```bash
mlx down qwen27b
mlx down api qwen27b
mlx down --all
mlx restart qwen27b
```

- `mlx down qwen27b`：停一个
- `mlx down api qwen27b`：停多个
- `mlx down --all`：停掉全部有运行记录的
- `mlx restart qwen27b`：先校验，通过了才停、再起

想先看会停掉谁：`mlx down qwen27b --dry-run`（`down` / `restart` 都接 `--dry-run`，只打印不动手）。
`down --all --dry-run` 同样只预览，列出有运行记录、会被停掉的全部服务。

---

<a id="rm"></a>

## 九 · 卸载模型

`model rm` 删的是**登记**，默认**不动权重**：

```bash
mlx model rm qwen27b
mlx model rm qwen27b --weights
```

- `mlx model rm qwen27b`：只删登记，几十 GB 的权重原样保留
- `mlx model rm qwen27b --weights`：连权重一起移入回收站

**确认的强度按可恢复性分档** —— 三个删除动作的代价差着数量级：

| 动作 | 代价 | 要确认吗 |
|---|---|---|
| `mlx model rm <id>` | 登记少一行，`model register` 一条命令就回来 | **不问** |
| `mlx model rm <id> --weights` | 权重进回收站，`trash restore` 能恢复 | 键入 id；脚本里用 `--yes` |
| `mlx model trash empty` | **真正不可恢复** | 键入 `empty`，**没有跳过的办法** |

带 `--weights` 时会要求你**键入模型 id** 确认（比 yes 更防手滑）：

```
$ mlx model rm qwen27b --weights
确认删除 qwen27b？此操作移入回收站
键入 qwen27b 确认: qwen27b
  ✓ qwen27b   已删除登记
    权重已移入回收站  ~/.local/state/mlx_server/trash/models--mlx-community--Qwen3.8-27B-8bit.1788226602363
```

不带 `--weights` 不问，直接删登记，并说明权重原样保留。`model rm` 没有 `--dry-run`。

不带 tty 的场景（脚本、CI）下确认读不到输入，按设计**视为拒绝**，零删除。

<a id="gc"></a>

回收孤儿权重（磁盘上有、但没有任何登记引用的）：

```bash
mlx model gc --dry-run
mlx model gc
```

- `mlx model gc --dry-run`：先看可回收多少（这一步不动任何文件）
- `mlx model gc`：确认无误后再真回收

---

<a id="trash"></a>
### 回收站：看 · 恢复 · 清空 {#trash}

`model rm --weights` 与 `model gc` 都是**移入回收站**，不是直接删。

```bash
mlx model trash ls
```

- `mlx model trash ls`：看里面有什么

```
  ID                                  体积      原路径
  ─────────────────────────────────   ────────  ────────────────────────────
  models--org--demo.1788226602363     2.0 MB    ~/.local/share/mlx_models/hub/models--org--demo

  共 1 项，2.0 MB（`mlx model trash restore <ID>` 恢复到原路径）
```

```bash
mlx model trash restore models--org--demo.1788226602363
```

```
  ✓ 已恢复  models--org--demo.1788226602363
    恢复到  ~/.local/share/mlx_models/hub/models--org--demo
```

恢复的目标是**台账里记着的原路径**。原路径如果已经被占用，**拒绝**而不是覆盖 ——
那会让一次恢复变成一次覆盖删除。

```bash
mlx model trash empty
```

- `mlx model trash empty`：永久删除，会要求键入 empty 确认

回收站里的东西**不占注册表**，但占磁盘。`mlx model trash ls` 的最后一行给了总量。

---

## 十 · 升级工具

**两种升级，用途不同，别搞混。**

### 10.1 原地重装（修复产物，不换版本）

```
$ mlx install --dry-run
  将执行  ~/.local/share/mlx_server/deployer/install.sh
  代码根  ~/.local/share/mlx_server
  数据根  ~/.local/state/mlx_server
  权重根  ~/.local/share/mlx_models
```

去掉 `--dry-run` 就真的执行。它用的是**代码根内的载荷副本**，
所以装的还是当前这一版 —— 用途是修复被误删的文件、重建 venv、重挂 PATH。

```
$ mlx install
== 收敛正在运行的服务（重装前置）
== 建三根
== 拷载荷
   · 源即目标（自升级），跳过 deployer/payload 自拷
...
  安装完成
```

**它会先停掉所有在跑的服务**，装完要自己重新 `mlx up`。

**配置不会被重置**：网关端口、rc 文件面、数据根、权重根会从现有的 `constants.env` 继承。
（这一条以前不成立 —— 重装会把它们悄悄打回出厂值，旧 rc 文件里的 PATH 块
就此成为卸载器扫不到的孤儿。）

### 10.2 装新版本的代码

新代码在源码树里，代码根内的副本是旧的，所以**必须从源码树跑安装器**。
下面的 `<源码目录>` 指当初 `git clone` 下来的那个目录（默认叫 `mlx-tool`）：

```bash
cd <源码目录>
git pull
bash deployer/install.sh
```

代码根装在自定义位置的（`mlx status` 能看到你的三根），要带上原来的代码根，否则会在默认位置装出**第二套**，
而你的 PATH 还指着旧的那套。数据根和权重根不用带，安装器会从代码根里的 `constants.env` 读出来：

```bash
MLX_ROOT_CODE=<你的代码根> bash deployer/install.sh
```

没有任何变化时，安装器几秒就结束，不停服务；要强制重装加 `--force`。

**数据不会丢**：升级只替换代码根，注册表、会话、权重都在另外两个根里，原样不动。

安装结果清单里的「自检」一行就是 `mlx check` 的结论，应为「12 项，12 项通过」。

**从 1.0.0 升到 1.1.0** 还有几件事要做，[CHANGELOG](../CHANGELOG.md) 的「升级须知」逐条列了。要点：

- 重跑 `bash deployer/install.sh`：会装 mlx-vlm、生成两把密钥、删除退役的表；
- 跑一次 `mlx model repair`，清掉 1.0.0 的旧字段与退役配置键；
- 网关现在默认对局域网与虚拟机开放、**所有来源都要令牌**：客户端都要填令牌，只给本机用就 `mlx config set api.bind --value=127.0.0.1`；
- 配过 MTP drafter 的要撤掉（`mlx config unset <id> --key=draft_model`），本版不支持；
- 后端密钥不再从 shell 的 `MLX_BACKEND_API_KEY` 继承，用 `mlx config set backend.api_key --value=…` 或 `mlx bootstrap keys`。

---

<a id="uninstall"></a>

## 十一 · 卸载工具

```bash
mlx uninstall
```

**它会先要你键入 `yes` 确认**：

```
将删除：~/.local/share/mlx_server（代码根）
保留：  ~/.local/state/mlx_server（数据根）
       ~/.local/share/mlx_models（权重根）
键入 yes 确认: yes
```

确认只从 `/dev/tty` 读，所以**管道喂不进去**（`echo yes | mlx uninstall` 会被拒），
无 tty 的场景（CI、脚本）也一样拒绝、零删除：

```
键入 yes 确认: 已取消，未删除任何东西
要继续，重跑 `mlx uninstall` 并在提示处键入 yes
```

确认之后：停掉在跑的服务 → 清 shell rc 里的标记块 → 按分发清单逐文件移除 →
告诉你**保留了什么**。

```
== 收敛正在运行的服务
== 清 shell rc 的标记块
   ✓ ~/.zshrc
== 按分发清单移除
== 清 venv
== 完成。以下两处保留：
   ~/.local/state/mlx_server（数据根）
   ~/.local/share/mlx_models（权重根）
（没有正在运行的服务）
  卸载完成
  已删除  ~/.local/share/mlx_server
  保留    ~/.local/state/mlx_server（数据根）
  保留    ~/.local/share/mlx_models（权重根）
```

### 保留什么

**数据根与权重根不删。** 注册表、会话、几十 GB 的权重原地不动，重装后接着用。
要彻底清干净，卸载后手动删这两个目录（路径就在上面的输出里）。

### 有服务停不掉时会拒绝

若某个服务无法停止，卸载**拒绝且零删除**，并逐个列出仍在跑的服务。
理由很直接：工具被删掉之后你就失去停止它们的手段了。

---

<a id="help"></a><a id="version"></a><a id="show"></a><a id="refresh"></a><a id="verify"></a>

## 十二 · 命令速查

```
$ mlx help
usage: mlx <命令> [<参数>] [--flag]

  工具自身  install · uninstall · status · check · version · help
  服务      up · down · restart <id>
  api       info
  bootstrap keys · verify
  caps      refresh · show
  config    diff · export · get · import · ls · set · unset · validate
  model     gc · info · install · ls · register · repair · rm · trash empty · trash ls · trash restore
  其他      bench · chat

  带值的参数写成 --key=value（如 --repo=mlx-community/xxx）
  mlx help <命令>   看单条详解
```

几条手册别处没细讲的：

| 命令 | 做什么 |
|---|---|
| `mlx version` | 工具版本、当前解释器、venv 解释器、venv 里的后端包与能力采集时间 |
| `mlx caps refresh` / `mlx caps show` | 重新采集 / 查看 venv 里装了哪些后端包（`up` 按它判断后端可不可用，装完包要 refresh） |
| `mlx bootstrap keys` | 只补空的密钥，输出只列键名（见[密钥](#keys)） |
| `mlx bootstrap verify` | 按分发清单逐项校验安装产物：齐全时 `verified` 是清单项数；缺文件时 rc 6 并列出缺的。输出里的 `extra` 目前恒为空（本版不检查清单外多出来的文件） |
| `mlx config validate` | 每个配置键一行：当前值与来源，并按 `up` 同一套值域检查：**非法的键逐个标 failed**，detail 写原因（rc 65） |
| `mlx status <id>` / `mlx status --all` | 只看一个服务 / 看所有有运行记录的服务 |
| `mlx check --json` | 机读的检查结果 |

### 每条命令都有详解

`mlx help <命令>` 是 man 的段落结构：**名称 / 用法 / 说明 / 参数 / 退出码 / 例子 / 参见**。

```
$ mlx help model rm
名称
    mlx model rm —— 删除一个模型的登记；--weights 连权重一起回收

用法
    mlx model rm <目标> [--json] [--weights] [--yes]

说明
    删除一个模型的**登记**。默认**不动权重** —— 加 --weights 才把权重
    移入回收站（可恢复）。不带 --weights 只删登记，**不询问确认**；带 --weights 时才要求键入模型 id 确认（加 --yes 跳过）。

参数
    --json      输出机读 JSON（默认输出是给人看的）
    --weights   连同权重文件一起回收（默认只删登记）
    --yes       跳过确认（**只对 `model rm --weights` 有效**；`model trash empty` 不可跳过，它是唯一不可恢复的动作）

退出码
    0   成功
    1   执行段失败（可能已有资源落地，看 stderr 的「已落地」行）
    2   参数错（未知命令 / 元数不符 / 未知 flag）
    5   用户取消、确认串失配或中断（Ctrl-C）
    6   前置拒绝（校验段判定不通过，**零资源创建**）

例子
    mlx model rm qwen27b             # 只删登记，权重保留
    mlx model rm qwen27b --weights   # 连权重一起回收

参见
    mlx model gc · mlx model info · mlx model install · mlx model ls · mlx model register · mlx model repair · mlx model trash empty · mlx model trash ls · mlx model trash restore

文档  USER-GUIDE.md#rm
```

退出码那段是**翻译过的** —— 光给一串数字等于让你去翻文档。

### 三个容易敲错的地方

```
$ mlx install qwen
`mlx install` 管的是**工具自身**，不接目标。要对模型 'qwen' 操作，用：mlx model install qwen

$ mlx uninstall qwen
… 用：mlx model rm qwen

$ mlx ls
未知命令 'ls'。`mlx help` 看全部命令        # 旧写法 → mlx model ls
```

它们都会给出**确切的正确命令**，不是干巴巴的「未知命令」。

<a id="check"></a>

### 默认给人看，`--json` 才给机器看

```
$ mlx check
有检查项未通过（见上）。每一项的下一步写在它自己那行
  ✓ code_root      ok
  ✓ data_root      ok
  ✓ weights_root   ok
  ✗ venv           failed   推理 venv 缺失或解释器太旧（要 ≥ 3.10）—— `mlx up` 会起不来新模型。`mlx version` 看当前版本；修：`brew install python@3.13` 后重跑安装器 `bash <源码目录>/deployer/install.sh`（它会用新解释器重建 venv，权重与注册表不动）
  ✓ tables         ok
  ✓ models_file    ok
  ✓ run_dir        ok
  ✓ ports          ok
  ✓ caps           ok
  ✓ gateway_nets   ok
  ✓ gateway_port   ok
  ✓ settings_mode  ok

  12 项，11 项通过，1 项未通过：venv
```

失败项的 detail 是**可操作的下一步**，不是一个裸的 `False`。
要机读就加 `--json`；`kind` 字段是判别字段，消费者据此分派。

---

<a id="get"></a><a id="set"></a><a id="validate"></a>

<a id="config"></a>
## 十二之二 · 配置 {#config}

配置有**三层**，高层覆盖低层：出厂默认 < 全局（`settings.json`）< 单个模型。

```bash
mlx config ls
mlx config get max_tokens
mlx config set max_tokens --value=4096
mlx config set qwen05b --key=max_tokens --value=2048
mlx config unset max_tokens
mlx config diff
```

- `mlx config ls`：全部键：当前值 + 来自哪一层
- `mlx config get max_tokens`：单个键
- `mlx config set max_tokens --value=4096`：写全局层（对所有模型生效）
- `mlx config set qwen05b --key=max_tokens --value=2048`：写这个模型（覆盖全局）
- `mlx config unset max_tokens`：撤销，回落到下一层
- `mlx config diff`：只看你改过的

**`get` / `set` / `unset` 的目标有两种写法**，别混：

- **全局键**：键名本身就是位置参数 —— `mlx config get api.token --reveal`、`mlx config set max_tokens --value=4096`、`mlx config unset max_tokens`。
- **某个模型**：位置参数是模型 id，键用 `--key=` 给 —— `mlx config get qwen05b --key=max_tokens`、`mlx config unset qwen05b --key=modality`。

只写 `--key=` 而不给位置参数（`config get` 后面直接跟 `--key=…`）会报「config get 需要一个目标」，rc 2。

`get` 默认给人看（键 / 值 / 来源三行），密钥打码为 `****`；`--reveal` 显示原值，`--json` 给脚本用。

`mlx config get <模型> --key=port` 显示的就是 `up` 会用的端口：模型自己设过的值（来源 user），否则是登记时给的端口（来源 machine）。

`config get port`（不带模型、即网关）显示的是安装时写进 `constants.env` 的**网关端口**，来源 `install`。
`port` 是模型级键，`mlx config set port --value=…`（全局）会被拒绝；旧版留下的全局 `port` 用 `mlx config unset port` 清掉。

每个键有**作用范围**（见下表）：作用范围为「全局」的键（`api.*`、`backend.api_key`、`fit.*`）
只能写全局，为「模型」的键（`port`、`modality`、`draft_model`、`num_draft_tokens`）只能写在某个模型上，「全局或模型」的两处都行。
写错层会被拒绝（rc 6，`config.level_mismatch`），因为写错层的值读侧与 `up` 都会忽略。
`mlx config import` 同理：模型级键会列入「被拒」。读不存在的模型 id（`config get <拼错的id> --key=port`）报「模型不存在」。

全部配置键（`mlx config ls` 列出同一批；默认与取值域来自 `share/schema.tsv`）：

| 键 | 默认（域） | 作用范围 | 管什么 | 改完重启 |
|---|---|---|---|---|
| `port` | 8100（1–65535） | 模型 | 模型监听端口：模型自己设过的值 > 登记时的 `--port` > 出厂 8100。**不能设在全局**（`mlx config set port --value=…` 被拒，rc 6）；设在模型上时不能与其它模型或网关的端口相同（rc 7） | 该模型 |
| `max_tokens` | 81920（1–1048576） | 全局或模型 | 后端默认输出长度（请求里自带的值照常生效） | 该模型 |
| `max_concurrency` | 8（1–256） | 全局或模型 | 后端同时处理的请求数 | 该模型 |
| `modality` | auto（auto / text / vision） | 模型 | 模态覆盖，见[自动选择后端](#backend) | 该模型 |
| `draft_model` | 空（模型短名） | 模型 | 投机解码的 drafter，见[投机解码](#draft) | 该模型 |
| `num_draft_tokens` | 3（1–16） | 模型 | drafter 每次猜几个 token | 该模型 |
| `backend.api_key` | 空（32–128 位 `[A-Za-z0-9_-]`） | 全局 | mlx_vlm 后端的密钥，见[密钥](#keys) | 多模态模型与网关 |
| `api.token` | 空（同上） | 全局 | 网关令牌 | 网关 |
| `api.bind` | 0.0.0.0（IP 地址） | 全局 | 网关绑定地址 | 网关 |
| `api.allow_cidrs` | 空（逗号分隔的网段） | 全局 | 额外放行的网段，见[远端接入](#remote) | 网关 |
| `api.tls_cert` / `api.tls_key` | 空（文件路径） | 全局 | 网关 TLS 证书与私钥，两个都设才开 TLS | 网关 |
| `api.max_body_mb` | 256（1–2048） | 全局 | 请求体上限，超出 413 | 网关 |
| `api.media_max_mb` | 128（1–2048） | 全局 | 单个代取媒体的上限，见[媒体](#media) | 网关 |
| `api.media_max_parts` | 32（1–256） | 全局 | 一个请求里的媒体个数上限 | 网关 |
| `api.inflight_max_mb` | 2048（64–65536） | 全局 | 全网关在途字节上限；**不得小于 `api.media_max_mb`**，否则所有代取 503（配置时不交叉校验） | 网关 |
| `fit.reserve_pct` | 20（0–99） | 全局 | 容量闸门给系统预留的内存份额 | 不用 |
| `fit.tight_pct` | 75（1–100） | 全局 | 超过这个比例算「挤」 | 不用 |
| `fit.over_pct` | 95（1–100） | 全局 | 超过这个比例拒绝启动 | 不用 |

`revision`、`size_mb` 也在表里，但它们是工具从磁盘读出的事实，不能 `config set`（见下）。

```
$ mlx config diff
  键           当前值      来自      出厂值
  ──────────   ──────────  ────────  ────────
  max_tokens   4096        machine   81920
```

`diff` 只列你改过的：来自安装或环境的值（来源 `install` / `env`，如网关端口）不列。带模型时（`config diff <模型>`）登记端口会作为一行出现（来源 machine）。

### 搬到另一台机器

```bash
mlx config export --json > mlx-config.json
mlx config import --from=mlx-config.json --dry-run
mlx config import --from=mlx-config.json
```

- `mlx config export --json > mlx-config.json`：只导出改过的
- `mlx config import --from=mlx-config.json --dry-run`：先看计划

`export` **只导出改过的键**（出厂值换台机器仍是出厂值），且**跳过机密**（两把密钥要在新机器上重新生成或设置）。
`import` 只写全局层：带模型导出（`config export <模型> --json`）的包里，模型级键（`port`、`modality` 等）导入时列入「被拒」，在新机器上用 `mlx config set <模型id> --key=…` 手工设。
`import` **不假装原子** —— 跨文件事务做不到一次全成，所以它逐条报告：

```
$ mlx config import --from=bad.json
  ✓ 已导入  max_tokens
  ✗ 拒绝    nosuchkey：未知键
  ✗ 拒绝    revision：不可写（scope != user）
```

**前面成功的保持生效**，不回滚。

<a id="keys"></a>
### 密钥 {#keys}

工具有两把密钥，都存在数据根的 `settings.json`（权限 0600），显示时一律打码为 `****`：

| 键 | 用途 |
|---|---|
| `backend.api_key` | 网关、`mlx chat`、`mlx bench` 访问 mlx_vlm 后端时携带；未设置时多模态模型拒绝启动 |
| `api.token` | 客户端访问网关时携带 |

安装器会自动生成缺失的密钥。手动补：

```bash
mlx bootstrap keys
mlx config get api.token --reveal
mlx config set api.token --value=<新值>
```

- `mlx bootstrap keys`：只补空值，输出只列键名
- `mlx config get api.token --reveal`：查看原值
- `mlx config set api.token --value=<新值>`：手动换（值会短暂出现在进程列表里）

### 有些字段改不了

```
$ mlx config set qwen05b --key=revision --value=abc
revision 不能用 `config set` 改（scope=machine）—— 它是磁盘事实，由工具自己填，不是配置
  rc=6
```

`revision` / `size_mb` 这类是**工具从磁盘读出来的事实**，不是你的设置。
要改它们只有一条路：改磁盘上的东西，然后 `mlx model repair`。

**已退役的键**（例如 1.0.0 的 `backend`）不再生效，`mlx config ls` 与 `mlx up <id> --dry-run` 会列出它们；
跑一次 `mlx model repair` 清掉。

---

## 十三 · 排查

### `mlx: command not found`

```bash
ls -l ~/.local/share/mlx_server/bin/mlx
grep -n 'mlx_server' ~/.zshrc
```

- `ls -l ~/.local/share/mlx_server/bin/mlx`：文件在吗
- `grep -n 'mlx_server' ~/.zshrc`：PATH 写进去了吗

两个都在就是当前终端没加载新 PATH —— **新开一个终端**或 `source ~/.zshrc`。

### `caps.not_collected`

首次使用，能力还没采过。跑 `mlx caps refresh`。

### 权重列一直是「未下载」

权重目录布局不对，或权重根路径不对（第 3.1 节讲了规划目录长什么样）。
用 `mlx model install` 下载的不会出这个问题，手工下载的要对一下路径：

```bash
mlx model info qwen05b
ls ~/.local/share/mlx_models/hub/
mlx model repair
```

- `mlx model info qwen05b`：「期望路径」与「实际找到」两行
- `ls ~/.local/share/mlx_models/hub/`：看目录名对不对
- `mlx model repair`：让工具按磁盘事实重建

`model install` 下载失败留下的记录权重列显示「未下载」：补下完权重后同样跑 `mlx model repair`。

### `venv` 检查项失败

安装器会建 venv 并装 `huggingface_hub` / `mlx` / `mlx-lm` / `mlx-vlm`。这一项失败的提示是
「推理 venv 缺失或解释器太旧（要 ≥ 3.10）」，对应**两种**成因，用 `mlx version` 的「venv 解释器」一行区分：

- **venv 不存在** —— 建 venv 那步没成。工具本身仍可用（垫片会回落 Homebrew 或系统
  python3），但下载与推理用不了。
- **venv 是 3.9 建的** —— **装不上 `mlx ≥ 0.30.4`**。先 `brew install python@3.13` 再重跑安装器，
  它会**就地重建** venv（权重与注册表不动）。

两种都是重跑一次安装器：

```bash
mlx install
mlx version
```

- `mlx install`：幂等，会补建 venv
- `mlx version`：看「venv 解释器」和「后端包」两行

> `mlx install` 跑的是**代码根里那份**安装器（`mlx install` 只是转调它）。**安装器自身升级过**时它还是旧的 ——
> 那种情况要从源码跑一次 `bash <源码>/deployer/install.sh`，跑完新 `deployer/`
> 就被拷进代码根，之后 `mlx install` 又等价了。

「后端包」那行列的是 venv 里**真正装上的**后端包（以最近一次能力采集为准）。缺哪个就手工补：

```bash
~/.local/share/mlx_server/.venv/bin/pip install -U mlx-lm
mlx caps refresh
```

- `mlx caps refresh`：重新采集，否则 `up` 会因能力漂移拒绝启动

### 服务在跑，但 `chat` / `bench` 说连不上

报错是「进程在跑，但连不上它的端口 … —— 多半是模型没加载成功」时，**先看那个服务的日志**：

```bash
tail -n 50 ~/.local/state/mlx_server/logs/qwen05b.log
```

mlx_lm 是先监听端口、首次请求才加载模型的，加载失败（不支持的架构、配了它跑不了的 drafter）进程也不退出，
所以 `mlx status` 会一直说「在跑」—— 真因只在日志里（常见是 `ValueError: Model type … not supported`）。
修好原因后 `mlx restart <id>`。日志里看不出问题，再对一下端口：`mlx model ls` 的端口列与日志里的监听端口是否一致。

### 命令返回 rc=1 且提到「已落地（需收敛）」

有资源已创建但事务没走完。**它会指名收敛动作**，照做即可 ——
这类失败不会让你去猜要清理什么。

### 多模态模型 `up` 等了很久后报「就绪超时」

mlx_vlm 加载完权重才开始监听，28 GB 的模型冷启动要一两分钟。超时按体积算（`20 + 3 × 体积 GB` 秒），
报错里会说明「可能只是还没加载完」—— 先 `mlx status <id>` 看它是否已在跑，不要重复 `up`。

### 客户端收到 4xx / 5xx

错误响应体都是 `{"error": {"type": …, "message": …}}`；网关自己的日志在数据根的 `logs/api.log`，模型的在 `logs/<模型>.log`。

| 状态码 | 含义 | 怎么办 |
|---|---|---|
| 403 `peer_not_allowed` | 来源地址不在放行网段（常见：经 VPN 连、网桥不是虚拟机网桥）。响应里不含地址，地址记在 `api.log` 的 `peer_denied` | `mlx api info` 看放行网段；确需放行：`mlx config set api.allow_cidrs --value=<网段>` 后 `mlx restart api` |
| 401 `auth` | 没带令牌或令牌不对（本机也要） | `mlx config get api.token --reveal`；改过令牌后网关要 `mlx restart api` |
| 415 | 请求头不是 `Content-Type: application/json` | 客户端改请求头 |
| 405 | `OPTIONS` 请求没带 `Access-Control-Request-Method`（不是合法的 CORS 预检） | 客户端问题，正常浏览器不会这样发 |
| 404 `model_not_found` | 请求里的 `model` 没在网关的路由表里，`message` 列出当前可用的 id。最常见：模型没 `up` | `mlx up <id>`；id 用短名（`mlx api info` 的「可用」），不是仓库名 |
| 404 `endpoint_not_supported_by_backend` | 该模型的后端不提供这个端点 | 看[端点表](#clients) |
| 404 `endpoint_not_supported` | 这个路径网关根本不转发（embeddings、images、管理端点等） | 看[端点表](#clients) |
| 404 `not_found` | 按 id 查询 / 删除 / 取消 responses 时，没有在跑的后端认得这个 id | id 是不是另一次会话、或模型重启前的 |
| 400 `media_fetch_failed` | 网关没取到 URL 里的媒体，`error.reason` 说明原因 | 看[媒体](#media)的原因表 |
| 400 `media_source_rejected` | 虚拟机或 Wi-Fi 设备发了本机路径 / `file://`，或用了 `file_id` | 改用 `data:` 或 `http(s)://` |
| 400 `too_many_media_parts` | 一个请求里的媒体超过 `api.media_max_parts` | 拆成多个请求，或调大该键 |
| 400 `thinking_budget_unsupported` | 文本模型不支持 `thinking_budget` | 去掉该字段，改用 `reasoning_effort` |
| 400 `invalid_request` | 缺 `model` 字段；请求体不是 JSON 对象（数组、字符串）或不是合法 JSON；用了分块传输（`Transfer-Encoding`，不支持）；`Content-Length` 非法 | 客户端改请求 |
| 413 `invalid_request` | 请求体超过 `api.max_body_mb`（默认 256 MB，内联 `data:` 媒体也算） | 改用 `http(s)://` 让网关代取，或调大该键后 `mlx restart api` |
| 502 `upstream_error` | 连不上模型 / 上游传输异常（`api.log` 记 `upstream_aborted`；流已开始后断开则是 200 加 `sse_truncated`，见[流式](#streaming)），后端回了 5xx，或按 id 查询 / 删除 / 取消时后端对 2xx 回了非 JSON | 看 `logs/<模型>.log`，`mlx status` 看它是否还在跑 |
| 502 `upstream_auth_failed` | 后端拒绝了网关带的 `backend.api_key`：多半是改了密钥，网关或模型没重启 | `mlx status` 末尾的「密钥已变更，需重启」那行列出要重启的服务 |
| 后端的 4xx | 后端认为请求参数不对（如 400 / 422），状态码原样透传；虚拟机与 Wi-Fi 来源看到的错误信息里，本机路径与异常栈换成了 `<redacted>` | 按 `message` 改请求参数 |
| 503 `gateway_busy` | 同时在处理的媒体太多（`api.inflight_max_mb`），或该键小于 `api.media_max_mb` | 按 `Retry-After` 5 秒后重试；或调大该键后 `mlx restart api` |
| 503 `auth` | 网关没有令牌（正常情况下 `mlx up api` 会拒绝这样起） | `mlx bootstrap keys` 后 `mlx restart api` |
| 503 `route_lost` | 请求路由到的模型在这一刻停了 | `mlx up <id>` 后重试 |
| 503 `unavailable` | `GET /v1/models` 时网关刚起、路由表还在建立（不回空列表，免得客户端以为没有模型） | 等几秒重试 |
| 504 `upstream_timeout` | 按 id 的 responses 端点，10 秒内有后端没回话 | 看该模型是否卡住（`logs/<模型>.log`） |
| 507 `media_storage_failed` | 视频临时文件写不下 | 看数据根所在磁盘的剩余空间 |

### 退出码

| rc | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 执行段失败，可能有资源已落地 |
| 2 | 参数错 |
| 3 | 外部依赖不可用（网络 / venv / 外部命令缺失 / 无法启动） |
| 4 | 有界等待走完仍未达成（就绪 / 终止确认） |
| 5 | 用户取消、确认串失配或中断 |
| 6 | 前置条件不满足（**零资源创建**） |
| 7 | 资源冲突（端口 / 唯一性 / 锁超时） |
| 8 | 供应链闸门阻断（登记在 `model install` / `register`、`config export` / `import` 的码集里） |
| 9 | 要动手时判定不确定 → 拒绝（fail-closed） |
| 10 | 服务启动后立即退出 |
| 64 / 65 | 聚合结果：含告警 / 含失败 |
| 70–74 | 部署硬门五项，各自独立 |

`mlx help <命令>` 会列出该命令**可能返回的码**并逐个翻译。

---

## 十四 · 目录与文件

```
~/.local/share/mlx_server/      代码根 —— 卸载时删这个
├── bin/mlx                     T0 垫片：找解释器（venv → Homebrew → 系统三级回落）后 exec
├── lib/mlx_server/             T1 主体（纯标准库）
├── gateway/                    T2 网关（纯标准库）
├── deployer/                   部署器（源码树删了也能卸载）
├── payload/                    载荷副本（`mlx install` 重装的源）
├── .venv/                      推理用的虚拟环境（huggingface_hub、mlx、mlx-lm、mlx-vlm 装在这里）
└── share/
    ├── constants.env           三根 · 端口 · 标记串（安装器产出）
    ├── verbs.tsv               动词台账 —— 解析/分派/help 都读它
    ├── cli.tsv                 参数面 —— 解析/help 都读它
    ├── schema.tsv              配置键：类型、取值域、默认值、改完重启什么
    ├── backends.tsv            两个后端（mlx_lm / mlx_vlm）的启动参数与端点
    ├── drafters.tsv            附属 drafter 的 model_type 集合
    ├── manifest.tsv            分发清单
    ├── messages.tsv            文案
    └── caps.json               后端能力采集结果（`mlx caps refresh` 写这里）

~/.local/state/mlx_server/      数据根 —— 卸载保留
├── models.jsonl                模型注册表 + 状态
├── settings.json               全局配置与两把密钥（权限 0600；`mlx config set <键>` 写这里）
├── sessions/                   `mlx chat` 的会话记录
├── trash/                      回收站（`model rm --weights` / `model gc` 移到这里）
│   └── index.jsonl             每份被回收物的原始路径
├── run/<服务>.json             运行记录（pid/pgid/port/started_at；网关的是 run/api.json）
├── run/media/<uuid4>/          网关代取的视频临时文件（0700，请求结束即删；超过 1 小时的残留在网关启动时与运行中清扫）
├── logs/<服务>.log             各模型的后端日志；网关的是 logs/api.log
└── backups/                    改动你 shell rc 前的备份

~/.local/share/mlx_models/               权重根 —— 卸载保留
└── hub/models--<组织>--<模型>/snapshots/<revision>/
```

三个根分开的理由是备份策略：代码可以随时重装，数据值得备份，
权重几十 GB 且能重新下载。Time Machine 排除权重根即可。

### `constants.env` —— 出问题先看它

```bash
cat ~/.local/share/mlx_server/share/constants.env
```

三个根的最终取值都在里面。**产品只从这里读，不猜默认值** ——
键缺失时它会明确报出缺哪个键，而不是静默用一个可能错的路径。

---

## 十五 · 给开发者

```bash
cd <源码目录>
bash check.sh
```

- `bash check.sh`：ruff + pytest + 3.9.6 真解释器编译

三步全绿（最后一行 `全绿 ✓`）才算过。仓库不替你装 git 钩子，提交前自己跑；想强制就把它挂进 `.git/hooks/pre-commit`。

部分 L3 网关用例读这台机器的真实网络配置：需要 Wi-Fi / 有线 / 虚拟机网桥上有 IPv4 地址，且有一个在线的 `utun*` VPN 接口。
条件不满足时这些用例**按设计失败**而不是跳过 —— 跳过会让「排除 VPN」这条规则在真实输出上没人验。
设计台账层（`tests/design`）在没有台账的克隆里整层带原因跳过，这是预期的。

### 测试的纪律

每条断言都要能回答：**错误的实现能通过它吗？** 答不上来的断言等于没有。

本项目吃过这个亏：上一代实现（v6）断言全绿，同时有 9 个用户可见故障 ——
因为断言求值的是源码和合成夹具，而用户运行的是**安装产物**。

因此 L3 层一律跑真实安装器 + 真实安装产物，且命令跑在 `env -i`
（只有 PATH 和 HOME）的干净环境里 —— 测试环境每多一个便利设置，
就多一块正对着真实用户的掩盖面。
