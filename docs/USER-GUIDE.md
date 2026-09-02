# MLX Toolkit 使用指南

> macOS + Apple Silicon 上管理 MLX 本地大模型的命令行工具。
> 版本 1.0.0 · 带 `$` 提示符的段落**都是从真实产物上粘贴的**。
> 其余是说明文字。审查发现过三处「照设计意图写、没跑过」的段落
> （局域网、升级、`model info` 打路径），已全部改为实测结果。

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
| 用模型对话 / 跑基准 | ✅ 可用 | 第六节 |
| 起网关 | ✅ 可用 | 第七节 |
| 接入 Cherry Studio | ✅ 可用 | 第七节。三项配置填完即可对话 |
| 停用服务 | ✅ 可用 | 第八节 |
| 卸载模型登记 | ✅ 可用 | 第九节 |

整条链已在真机上跑通：下载 `mlx-community/Qwen2.5-0.5B-Instruct-4bit` →
登记 → `mlx up` → `mlx chat` 拿到回答 → `mlx up api` → Cherry Studio 经网关对话。

**还没做的**：`mlx logs`、`mlx model convert / quantize / upgrade / rollback`、
`mlx trash ls / restore / empty` 尚未实现，敲了会告诉你未实现，不会假装成功。
`--live` / `--brief` / `--only` / `--tail` 几个 flag 能解析但还没有读取方。
完整清单与每条的落点见 [KNOWN-ISSUES.md](KNOWN-ISSUES.md)。

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

**分工要分清**（`00-inheritance` §0.4.1/§0.4.2）：工具本体（`check`、`config`、
`model ls`、`help` 这些）是纯标准库的，**系统 python3 也跑得动**，这是故意的 ——
venv 坏掉时你还得能用 `mlx check` 诊断它。Homebrew 的 Python 是给 venv 用的，
venv 只服务**下载权重**与**跑推理**这两件事。

### 1.2 装

```
$ bash ~/dev/projects/mlx-tool/deployer/install.sh
== 建三根
== 拷载荷
== 写 constants.env（三根的兜底来源，裁决 R4-3 断口②）
== 校验产物（按 manifest.tsv）
== 挂 PATH
   ✓ ~/.zshrc（备份 ~/.local/state/mlx_server/backups/zshrc.20260831-183129）
== 完成。**新开一个终端**，或先跑：source ~/.zshrc
```

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

### 1.4 验证

**新开一个终端**（PATH 要重新加载），然后：

```
$ mlx status
工具状态

  ✓ 代码根   ~/.local/share/mlx_server
  ✓ 数据根   ~/.local/state/mlx_server
  ✓ 权重根   ~/lab/models/mlx
  · 工具版本 1.0.0

  ✓ PATH 生效  ~/.local/share/mlx_server/bin
  ✓ 分发清单  53 项，完整

模型（2）
     ID       模型                            状态  端口   PID  内存  体积
  ─  ───────  ──────────────────────────────  ────  ─────  ───  ────  ─────
  ·  llama8b  mlx-community/Llama-3-8B        就绪  :8101  —    —     18 MB
  ·  qwen27b  mlx-community/Qwen3.8-27B-8bit  就绪  :8100  —    —     42 MB

  2 个已登记，0 个在跑

网关
  ✓ api        在跑  :8340  pid 71835
```

`PATH 生效` 那行打 ✗ 就是终端没重载 —— 新开一个，或 `source ~/.zshrc`。

---

### 1.5 命令形态（**先看这条，能省一次报错**）

`mlx` 的**带值参数一律写成 `--key=value`**，不接受空格分隔：

```bash
mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit   # ✓
mlx model install qwen38 --repo mlx-community/Qwen3.8-27B-8bit   # ✗ rc 2
                               ↑ 报「--repo 需要一个值，写成 --repo=<值>」
```

布尔参数（`--json` / `--dry-run` / `--all` / `--yes` / `--weights` / `--force`）
不带值，直接写：`mlx model rm qwen38 --weights --yes`。

**为什么跟 GNU 习惯不同**：`--k v` 要靠"下一个 token 是值还是位置参数"来消歧，
而本工具的位置参数判据是 `not startswith("--")`（`cli.py:375`）——
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
model 没有子命令 'search'；可用：gc · info · install · ls · register · repair · rm
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
   └─ 默认 ~/lab/models/mlx，装到别处时看 `mlx status` 的「权重根」那行
```

举例，仓库 `mlx-community/Qwen2.5-0.5B-Instruct-4bit` 的权重完整路径是：

```
~/lab/models/mlx/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main/
```

这是 HuggingFace 的缓存布局，工具按它解析。**放错地方的后果是静默的**：
登记能成功，但状态一直停在「已登记未下载」，启动时才报错。

不确定权重根在哪就先问工具：

```bash
mlx status | grep 权重根
```

### 3.2 路子一：让工具自己下（推荐）

一条命令同时做完下载和登记：

```bash
mlx model install qwen05b \
    --repo=mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --type=text --port=8100
```

```
  evidence   CLI_EXPLICIT
  installed  qwen05b
  path       ~/lab/models/mlx/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main
  type       text
```

`path` 那行就是它落下去的地方 —— 和 3.1 的规划目录一致，不需要你算。

内部走的是 venv 里的 `hf`（安装器已经装好）。它**先下到临时目录再原子改名**，
所以中途断网、Ctrl-C 都不会在规划目录里留下半个模型 —— 要么完整要么没有。

下载失败时它**不会假装成功**：

```
下载器不可用 —— venv 里没有 hf 命令。<repo> 未下载，记录已登记为「已登记未下载」
  已落地（需收敛）：models.jsonl:qwen05b
  rc=3
```

rc=3、指名原因、并告诉你记录停在哪 —— 补下完权重后跑 `mlx model repair` 就能对上。

### 3.3 路子二：HuggingFace 手工下

想自己控制下载过程（断点续传、只下部分文件、用镜像站）时用这条。
**关键是 `--local-dir` 要指向 3.1 算出来的完整路径**：

```bash
hf download mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --local-dir ~/lab/models/mlx/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main
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
    --local-dir ~/lab/models/mlx/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main
```

### 3.4 路子三：魔搭社区（ModelScope）手工下

国内下载通常比 HuggingFace 快很多。魔搭上的 MLX 模型主要在 `mlx-community`
镜像组织下，仓库名一般与 HuggingFace 一致。

```bash
pip install -U modelscope

modelscope download \
    --model mlx-community/Qwen2.5-0.5B-Instruct-4bit \
    --local_dir ~/lab/models/mlx/hub/models--mlx-community--Qwen2.5-0.5B-Instruct-4bit/snapshots/main
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
    --repo=mlx-community/Qwen2.5-0.5B-Instruct-4bit --type=text --port=8100
mlx model repair          # 用磁盘事实校对注册表
mlx model ls              # 状态应该从「已登记未下载」变成「就绪」
```

**状态还是「已登记未下载」** = 路径算错了。用这条对一下工具到底在找哪里：

```bash
mlx model info qwen05b
```

它会打印期望的权重路径，跟你 `hf`/`modelscope` 实际写的目录逐字比。

---

## 四 · 登记模型

下载好的权重要**登记**进注册表，工具才认得它。登记时给它一个**短名**——
之后所有命令都用这个短名，不用敲长长的仓库名。

```
$ mlx model register qwen27b --repo=mlx-community/Qwen3.8-27B-8bit --type=text --port=8100
  evidence    CLI_EXPLICIT
  registered  qwen27b
  type        text
```

三个参数的含义：

| 参数 | 说明 |
|---|---|
| `<短名>` | 你自己起的 id，之后 `mlx up <短名>` 用它 |
| `--repo` | HuggingFace 仓库名，要和你下载时用的**一致** |
| `--type` | 后端类型。文本模型填 `text` |
| `--port` | 这个模型的监听端口。每个模型一个，不能重复 |

登记完看一眼：

```
$ mlx model ls
  ID                       类型  端口  状态  
  ───────────────────────  ────  ────  ──────
  mlx-community/NoSuch-7B  text  8199  已登记
  qwen27b                  text  8100  就绪  

  共 2 个
```

**状态列的含义**：

- `就绪` —— 权重在盘上，记录也完整
- `已登记` —— 记录有了，但 `machine.revision` 还没回填（权重找不到，或刚 register 还没 repair）
- `已登记未下载` —— 记录只有 user 段（极少见；正常路径不会停在这里）

如果状态是「已登记未下载」而你确信权重下好了，多半是目录布局不对。跑一次：

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

## 五 · 启动模型

```bash
mlx up qwen05b
```

```
  ✓ qwen05b   已启动   port 8150   pid 90731
```

端口取的是**登记时给的那个**，不是默认值。想先看会跑什么命令，用 `--dry-run`：

```
$ mlx up qwen05b --dry-run
  backend: default
  max_tokens: default
  port: machine
["mlx_lm", "-m", "mlx_lm.server", "--model", "qwen05b", "--port", "8150", "--max-tokens", "4096"]
```

上面三行是**每个配置项的来源层**（`default` = 内置默认值，`machine` = 注册表里登记的）。
`port: machine` 说明端口来自你登记的 8150，而不是内置的 8100。

### 它会等到真的就绪才返回

`up` 起完进程**不会立刻报成功**，而是等端口监听上。进程若在就绪之前退出
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
mlx config get fit.reserve_pct     # 预留给系统的份额，默认 20
mlx config get fit.tight_pct       # 超过这个比例算「挤」，默认 75
mlx config get fit.over_pct        # 超过这个比例拒绝启动，默认 95
```

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

### 起多个 · 重启

```bash
mlx up qwen05b llama8b        # 每个目标一行结论
mlx restart qwen05b           # 停了再起
```

同时起多个时端口必须各不相同 —— 登记那一步就会拦住重复端口（rc 7）。

---

### 5.4 投机解码（speculative decoding） {#draft}

用一个**小模型**先猜若干 token，大模型一次性校验——吞吐能明显提升，
输出与不用它时**逐字节一致**（校验不通过就回退，不是近似）。

两个模型都要登记，然后把小的配给大的：

```bash
mlx model install qwen38 \
    --repo=mlx-community/Qwen3.8-27B-8bit --type=text --port=8100
mlx model install qwen38-d \
    --repo=mlx-community/Qwen3.8-27B-MTP-4bit --type=text --port=8199

mlx config set qwen38 --key=draft_model --value=qwen38-d
mlx up qwen38
```

`mlx up qwen38 --dry-run` 可以确认参数拼上了：

```
[".../python3", "-m", "mlx_lm.server", "--model", "qwen38", "--port", "8100",
 "--max-tokens", "4096", "--draft-model", "qwen38-draft", "--num-draft-tokens", "3"]
```

猜多少个可调（默认 3，范围 1–16）：

```bash
mlx config set qwen38 --key=num_draft_tokens --value=4
```

不用了就撤销：`mlx config unset qwen38 --key=draft_model`

**几点说明**：

- drafter 那条记录**占一个端口号但永远不 `up`** —— 端口只是注册表的占位。
  它不是独立服务，是主模型启动时的一个参数。
- **两个模型同时驻留内存**，容量判档会把它们的体积加在一起算。
  26.6 GB + 0.27 GB 在 128 GB 机器上仍是「宽裕」。
- drafter 的权重**必须先在盘上**。不在的话 `mlx up` 拒绝并指名
  （rc 6），而不是让后端起来之后自己去下载——那会在启动路径上引入一次
  没有上界的网络等待。

---

## 六 · 使用模型

模型起来之后有两条用法：命令行对话、跑一次基准。

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

接着上次的会话继续聊，把 id 带上：`mlx chat qwen05b --session=<id>`。

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
连不上后端 qwen05b。先确认它在跑：mlx status qwen05b
  rc=3
```

rc 3 = 外部依赖不可用。先 `mlx status` 看那一行有没有 PID。

---

## 七 · 接入 Cherry Studio 等外部工具

网关提供 OpenAI 兼容接口，把已经起来的模型统一转发出去。

```
$ mlx up api
  ✓ api   已启动   port 8080   pid 48057
```

```
$ mlx api info
  ✓ 网关   在跑
    地址   http://127.0.0.1:8080/v1
    端口   8080   pid 48057
    可用   qwen38
    未起   qwen38-d（已登记，但网关转发不到）

  同机场景下模型端口本就在回环上无鉴权监听；网关令牌提供零额外阻挡，真正的边界是绑定地址
```

**网关端口默认 8080**，与模型端口（`mlx model ls` 里那一列）是两回事 ——
客户端要连的是网关，不是模型。

### 三项配置

Cherry Studio / OpenWebUI / 任何 OpenAI 兼容客户端，填这三项：

| 配置项 | 填什么 | 从哪来 |
|---|---|---|
| **API 地址 / Base URL** | `http://127.0.0.1:8080/v1` | `mlx api info` 的「地址」行 |
| **API 密钥 / API Key** | 任意非空字符串，例如 `local` | 网关不校验，但多数客户端不让留空 |
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

### 网关只转发正在跑的模型

`/v1/models` 只列出 `mlx up` 起来的那些。`mlx api info` 会把两类分开显示
（「可用」/「未起」）。要用别的模型先 `mlx up <id>`。

### 重装工具会停掉服务

`bash deployer/install.sh` 的第一步是 `mlx down --all`（重装前必须收敛服务）。
装完客户端就连不上了 —— 安装器会在结尾列出它停掉的服务和起回来的命令。

### 先用 curl 验一遍

接进客户端之前，先确认接口自己是通的：

```bash
# 1. 列模型 —— 应当能看到你起的那个
curl -s http://127.0.0.1:8080/v1/models
```

```json
{"object": "list", "data": [{"id": "qwen38", "object": "model", "owned_by": "mlx-toolkit"}]}
```

```bash
# 2. 发一次对话。max_tokens 给够，否则推理模型只会返回 reasoning 没有 content
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38","messages":[{"role":"user","content":"1+1=?"}],"max_tokens":2048}'
```

```bash
# 3. 流式（多数客户端默认走这条），结尾应当有 data: [DONE]
curl -sN http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen38","messages":[{"role":"user","content":"数到三"}],"max_tokens":2048,"stream":true}'
```

`/v1/models` 列的是**正在跑的**模型。列表是空的说明模型没起 —— 回第五节，
先 `mlx up <id>`，再刷新客户端的模型列表。

### 让局域网其它机器连

默认只绑回环（`127.0.0.1`），别的机器连不上。要开放：

```bash
mlx config set api.bind --value=0.0.0.0     # 绑全部网卡
mlx restart api
mlx api info                                 # 「地址」行会变成 0.0.0.0，确认一下
```

**令牌用环境变量给，不落盘**：

```bash
MLX_GATEWAY_TOKEN='<你自己生成的长随机串>' mlx up api
```

客户端那边把 API Key 填成同一个串。

**非本地绑定而没给令牌时，网关会起来，但每个请求都返回 503**
（`/healthz` 除外，它只回状态、不泄漏装了哪些模型）。
这是刻意的 —— 不给「开了外网又忘了鉴权」留口子。

几点说明：

- 令牌**有意不进配置文件**：本版还没有「导出时排除机密」的机制，
  存进 `settings.json` 就是明文落盘。它只在启动网关那个进程的环境里存在。
- 回环绑定时**不校验令牌**，填什么都行 —— 同机场景下模型端口本就在回环上
  无鉴权监听，真正的边界是绑定地址。`mlx api info` 最后一行就是这么写的。
- 改回只给本机：`mlx config set api.bind --value=127.0.0.1 && mlx restart api`。

---

## 八 · 停用模型

```
$ mlx down --all
  ✓ api   已停止
```

`down` 是**幂等**的：停一个没在跑的服务返回 0，不是错误。

```bash
mlx down qwen27b        # 停一个
mlx down api qwen27b    # 停多个
mlx down --all          # 停掉全部有运行记录的
mlx restart qwen27b     # 先停再起
```

---

## 九 · 卸载模型

`model rm` 删的是**登记**，默认**不动权重**：

```bash
mlx model rm qwen27b              # 只删登记，几十 GB 的权重原样保留
mlx model rm qwen27b --weights    # 连权重一起移入回收站
```

**确认的强度按可恢复性分档** —— 三个删除动作的代价差着数量级：

| 动作 | 代价 | 要确认吗 |
|---|---|---|
| `mlx model rm <id>` | 登记少一行，`model register` 一条命令就回来 | **不问** |
| `mlx model rm <id> --weights` | 权重进回收站，`trash restore` 能恢复 | 键入 id；脚本里用 `--yes` |
| `mlx model trash empty` | **真正不可恢复** | 键入 `empty`，**没有跳过的办法** |

带 `--weights` 时会要求你**键入模型 id** 确认（比 yes 更防手滑）：

```
$ mlx model rm qwen27b
确认删除 qwen27b？此操作移入回收站
键入 qwen27b 确认: qwen27b
  ✓ qwen27b   已删除登记
    未加 --weights，权重原样保留
```

不带 tty 的场景（脚本、CI）下确认读不到输入，按设计**视为拒绝**，零删除。

回收孤儿权重（磁盘上有、但没有任何登记引用的）：

```bash
mlx model gc --dry-run  # 先看可回收多少（这一步不动任何文件）
mlx model gc            # 确认无误后再真回收
```

---

### 9.3 回收站：看 · 恢复 · 清空 {#trash}

`model rm --weights` 与 `model gc` 都是**移入回收站**，不是直接删。

```bash
mlx model trash ls                    # 看里面有什么
```

```
  ID                                  体积      原路径
  ─────────────────────────────────   ────────  ────────────────────────────
  models--org--demo.1788226602363     2.0 MB    ~/lab/models/mlx/hub/models--org--demo

  共 1 项，2.0 MB（`mlx model trash restore <ID>` 恢复到原路径）
```

```bash
mlx model trash restore models--org--demo.1788226602363
```

```
  ✓ 已恢复  models--org--demo.1788226602363
    恢复到  ~/lab/models/mlx/hub/models--org--demo
```

恢复的目标是**台账里记着的原路径**。原路径如果已经被占用，**拒绝**而不是覆盖 ——
那会让一次恢复变成一次覆盖删除。

```bash
mlx model trash empty                 # 永久删除，会要求键入 empty 确认
```

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
  权重根  ~/lab/models/mlx
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

**配置不会被重置**：网关端口、rc 文件面这些会从现有的 `constants.env` 继承。
（这一条以前不成立 —— 重装会把它们悄悄打回出厂值，旧 rc 文件里的 PATH 块
就此成为卸载器扫不到的孤儿。）

### 10.2 装新版本的代码

新代码在源码树里，代码根内的副本是旧的，所以**必须从源码树跑安装器**：

```bash
cd ~/dev/projects/mlx-toolkit && git pull

# 装到默认位置：
bash deployer/install.sh

# 装在自定义根上的（`mlx status` 能看到你的三根），要带上原来的根，
# 否则会在默认位置装出**第二套**，而你的 PATH 还指着旧的那套：
MLX_ROOT_CODE=<你的代码根> \
MLX_ROOT_DATA=<你的数据根> \
MLX_ROOT_WEIGHTS=<你的权重根> \
  bash deployer/install.sh
```

**数据不会丢**：升级只替换代码根，注册表、会话、权重都在另外两个根里，原样不动。

升级后跑一次 `mlx check`，九项应该全绿。

---

## 十一 · 卸载工具

```bash
mlx uninstall
```

**它会先要你键入 `yes` 确认**：

```
将删除：~/.local/share/mlx_server（代码根）
保留：  ~/.local/state/mlx_server（数据根）
       ~/lab/models/mlx（权重根）
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
   ~/lab/models/mlx（权重根）
（没有正在运行的服务）
  卸载完成
  已删除  ~/.local/share/mlx_server
  保留    ~/.local/state/mlx_server（数据根）
  保留    ~/lab/models/mlx（权重根）
```

### 保留什么

**数据根与权重根不删。** 注册表、会话、几十 GB 的权重原地不动，重装后接着用。
要彻底清干净，卸载后手动删这两个目录（路径就在上面的输出里）。

### 有服务停不掉时会拒绝

若某个服务无法停止，卸载**拒绝且零删除**，并逐个列出仍在跑的服务。
理由很直接：工具被删掉之后你就失去停止它们的手段了。

---

## 十二 · 命令速查

```
usage: mlx <命令> [<参数>] [--flag]

  工具自身  install · uninstall · status · check · version · help
  服务      up · down · restart <id>
  api       info
  bootstrap render · verify
  caps      refresh · show
  config    diff · export · get · import · ls · set · unset · validate
  model     gc · info · install · ls · register · repair · rm
  其他      bench · chat

  mlx help <命令>   看单条详解
```

### 每条命令都有详解

`mlx help <命令>` 是 man 的段落结构：**名称 / 用法 / 说明 / 参数 / 退出码 / 例子 / 参见**。

```
$ mlx help model rm
名称
    mlx model rm —— 删除一个模型的登记；--weights 连权重一起回收

用法
    mlx model rm <目标> [--dry-run] [--json] [--weights]

说明
    删除一个模型的**登记**。默认**不动权重** —— 加 --weights 才把权重
    移入回收站（可恢复）。删除前会要求你键入模型 id 确认。

参数
    --dry-run   只打印将要做什么，不真的做（零外部动作）
    --json      输出机读 JSON（默认输出是给人看的）
    --weights   连同权重文件一起回收（默认只删登记）

退出码
    0   成功
    1   执行段失败（可能已有资源落地，看 stderr 的「已落地」行）
    2   参数错（未知命令 / 元数不符 / 未知 flag）
    5   用户取消、确认串失配或中断（Ctrl-C）
    6   前置拒绝（校验段判定不通过，**零资源创建**）

例子
    mlx model rm qwen27b             # 只删登记，权重保留（不问确认）
    mlx model rm qwen27b --weights   # 连权重一起回收（要确认）
mlx model rm qwen27b --weights --yes   # 脚本 / CI 里跳过确认

参见
    mlx model gc · mlx model info · mlx model install · mlx model ls · mlx model register · mlx model repair

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

### 默认给人看，`--json` 才给机器看

```
$ mlx check
有检查项未通过（见上）。每一项的下一步写在它自己那行
  ✓ code_root     ok
  ✓ data_root     ok
  ✓ weights_root  ok
  ✗ venv          failed   推理 venv 未建 —— 只影响真实推理，工具本身可用
  ✓ tables        ok
  ✓ models_file   ok
  ✓ run_dir       ok
  ✓ ports         ok
  ✓ caps          ok

  9 项，8 项通过，1 项未通过：venv
```

失败项的 detail 是**可操作的下一步**，不是一个裸的 `False`。
要机读就加 `--json`；`kind` 字段是判别字段，消费者据此分派。

---

## 十二之二 · 配置 {#config}

配置有**三层**，高层覆盖低层：出厂默认 < 全局（`settings.json`）< 单个模型。

```bash
mlx config ls                              # 全部键：当前值 + 来自哪一层
mlx config get port                        # 单个键
mlx config set port --value=8200           # 写全局层
mlx config set qwen05b --key=port --value=8177   # 写这个模型（覆盖全局）
mlx config unset port                      # 撤销，回落到下一层
mlx config diff                            # 只看你改过的
```

```
$ mlx config diff
  键           当前值      来自      出厂值
  ──────────   ──────────  ────────  ────────
  max_tokens   2048        machine   4096
  port         8200        machine   8100
```

### 搬到另一台机器

```bash
mlx config export --json > mlx-config.json      # 只导出改过的
mlx config import --from=mlx-config.json --dry-run   # 先看计划
mlx config import --from=mlx-config.json
```

`export` **只导出改过的键**（出厂值换台机器仍是出厂值），且**跳过机密**。
`import` **不假装原子** —— 跨文件事务做不到一次全成，所以它逐条报告：

```
$ mlx config import --from=bad.json
  ✓ 已导入  port
  ✗ 拒绝    nosuchkey：未知键
  ✗ 拒绝    revision：不可写（scope != user）
```

**前面成功的保持生效**，不回滚。

### 有些字段改不了

```
$ mlx config set qwen05b --key=revision --value=abc
revision 不能用 `config set` 改（scope=machine）—— 它是磁盘事实，由工具自己填，不是配置
  rc=6
```

`revision` / `size_mb` 这类是**工具从磁盘读出来的事实**，不是你的设置。
要改它们只有一条路：改磁盘上的东西，然后 `mlx model repair`。

---

## 十三 · 排查

### `mlx: command not found`

```bash
ls -l ~/.local/share/mlx_server/bin/mlx     # 文件在吗
grep -n 'mlx_server' ~/.zshrc               # PATH 写进去了吗
```

两个都在就是当前终端没加载新 PATH —— **新开一个终端**或 `source ~/.zshrc`。

### `caps.not_collected`

首次使用，能力还没采过。跑 `mlx caps refresh`。

### 模型状态一直是「已登记未下载」

权重目录布局不对，或权重根路径不对（第 3.1 节讲了规划目录长什么样）。
用 `mlx model install` 下载的不会出这个问题，手工下载的要对一下路径：

```bash
mlx status                                       # 看权重根在哪
ls ~/lab/models/mlx/hub/                         # 看目录名对不对
mlx model repair                                 # 让工具按磁盘事实重建
```

### `venv` 检查项失败

安装器会建 venv 并装 `huggingface_hub` / `mlx` / `mlx-lm`。这一项失败有**两种**成因，
先看 `mlx check` 那行的提示语区分：

- **「缺失」** —— 建 venv 那步没成。工具本身仍可用（垫片会回落 Homebrew 或系统
  python3），但下载与推理用不了。
- **「解释器太旧（要 ≥ 3.10）」** —— venv 在，但它是拿 3.9 建的，**装不上
  `mlx ≥ 0.30.4`**。先 `brew install python@3.13` 再重跑安装器，它会**就地重建**
  venv（权重与注册表不动）。

两种都是重跑一次安装器：

```bash
mlx install            # 幂等，会补建 venv
mlx version            # 看「venv 解释器」和「依赖三包」两行

> `mlx install` 跑的是**代码根里那份**安装器（`INV-20`：`deployer/*.sh` 是部署的
> 唯一实现点，`mlx install` 只是转调它）。**安装器自身升级过**时它还是旧的 ——
> 那种情况要从源码跑一次 `bash <源码>/deployer/install.sh`，跑完新 `deployer/`
> 就被拷进代码根，之后 `mlx install` 又等价了。
```

`依赖三包` 那行列的是**真正装上的**包。缺哪个就手工补：

```bash
~/.local/share/mlx_server/.venv/bin/pip install -U mlx-lm
mlx caps refresh       # 重新采集，否则 `up` 会因能力漂移拒绝启动
```

### 服务在跑，但 `chat` / `bench` 说连不上

先确认端口对得上：

```bash
mlx status                     # 看那一行的端口和 PID
curl -s http://127.0.0.1:<那个端口>/v1/models
```

curl 通而 `mlx chat` 不通，说明注册表里登记的端口与实际监听的不是同一个 ——
`mlx model repair` 后重启服务。

### 命令返回 rc=1 且提到「已落地（需收敛）」

有资源已创建但事务没走完。**它会指名收敛动作**，照做即可 ——
这类失败不会让你去猜要清理什么。

### 退出码

| rc | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 执行段失败，可能有资源已落地 |
| 2 | 参数错 |
| 3 | 外部依赖不可用（网络 / venv / 外部命令缺失） |
| 4 | 有界等待走完仍未达成（就绪 / 终止确认） |
| 5 | 用户取消或中断 |
| 6 | 前置条件不满足（**零资源创建**） |
| 7 | 资源冲突（端口 / 锁） |
| 9 | 要动手时判定不确定 → 拒绝（fail-closed） |
| 10 | 服务启动后立即退出 |
| 64 / 65 | 聚合结果：含告警 / 含失败 |
| 70–74 | 部署硬门五项，各自独立 |

`mlx help <命令>` 会列出该命令**可能返回的码**并逐个翻译。

---

## 十四 · 目录与文件

```
~/.local/share/mlx_server/      代码根 —— 卸载时删这个
├── bin/mlx                     T0 垫片（3 行 exec）
├── lib/mlx_server/             T1 主体（纯标准库；venv → brew → 系统，三级回落）
├── gateway/                    T2 网关
├── deployer/                   部署器（源码树删了也能卸载）
├── payload/                    载荷副本（mlx install 重装的源）
├── .venv/                      推理用的虚拟环境（hf / mlx / mlx-lm 装在这里）
└── share/
    ├── constants.env           三根 · 端口 · 标记串（安装器产出）
    ├── verbs.tsv               动词台账 —— 解析/分派/help 都读它
    ├── cli.tsv                 参数面 —— 解析/补全/help 都读它
    ├── manifest.tsv            分发清单
    └── messages.tsv            文案

~/.local/state/mlx_server/      数据根 —— 卸载保留
├── models.jsonl                模型注册表 + 状态
├── settings.json               全局配置（`mlx config set <键>` 写这里）
├── caps.json                   能力采集缓存
├── sessions/                   `mlx chat` 的会话记录
├── trash/                      回收站（`model rm --weights` / `model gc` 移到这里）
│   └── origins.tsv             每份被回收物的原始路径
├── run/<service>.json          运行记录（pid/pgid/port/started_at）
├── logs/<service>.log
└── backups/                    改动你 shell rc 前的备份

~/lab/models/mlx/               权重根 —— 卸载保留
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
cd ~/dev/projects/mlx-toolkit/v7
bash check.sh           # ruff + pytest + 3.9.6 真解释器编译
```

三步全绿才算过。**提交会被 pre-commit hook 拦** —— `check.sh` 不绿提交不进去。

### 测试的纪律

每条断言都要能回答：**错误的实现能通过它吗？** 答不上来的断言等于没有。

本项目吃过这个亏：上一版 665 条断言全绿，同时有 9 个用户可见故障 ——
因为断言求值的是源码和合成夹具，而用户运行的是**安装产物**。

因此 L3 层一律跑真实安装器 + 真实安装产物，且命令跑在 `env -i`
（只有 PATH 和 HOME）的干净环境里 —— 测试环境每多一个便利设置，
就多一块正对着真实用户的掩盖面。
