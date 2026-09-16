<div align="center">

# 🧰 MLX Toolkit

**Run local LLMs on Apple Silicon — and plug them into any OpenAI-compatible app.**

[![Version](https://img.shields.io/badge/version-1.0.0-brightgreen)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-black)](https://support.apple.com/en-us/116943)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Backend](https://img.shields.io/badge/backend-mlx--lm-orange)](https://github.com/ml-explore/mlx-lm)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-158%20passing-success)](tests)

🌐 **English** · [简体中文](README_cn.md)

📖 [User Guide](docs/USER-GUIDE.md) · 🐛 [Known Issues](docs/KNOWN-ISSUES.md) · 📝 [Changelog](CHANGELOG.md)

</div>

---

## 🚀 What's New

- **v1.0.0** (2026-09) — First release. The full chain is verified on real
  hardware: download a 27.5 GB model → register → serve → OpenAI gateway →
  Cherry Studio → streaming inference.

---

## ⚡ Quick Start

### Prerequisites

Homebrew Python **3.10+**. The system Python (3.9.6) will not work — see
[Why](#why-not-the-system-python) below.

```bash
brew install python@3.13
```

### Install

```bash
git clone https://github.com/<you>/mlx-tool.git && cd mlx-tool
bash deployer/install.sh
source ~/.zshrc          # or open a new terminal
mlx check                # 12 checks — all should pass
```

### Run a model

```bash
# Download + register in one step (~27 GB, resumable, shows progress)
mlx model install qwen38 \
  --repo=mlx-community/Qwen3.8-27B-8bit \
  --port=8100

mlx up qwen38
mlx chat qwen38 --prompt="Explain speculative decoding in one sentence"
```

### Connect an external app

```bash
mlx up api               # OpenAI-compatible gateway
mlx api info             # base URLs for this Mac, VMs and Wi-Fi devices
mlx config get --key=api.token --reveal   # the token every client must send
```

| Field in your client | Value |
|---|---|
| Base URL | this Mac: `http://127.0.0.1:8080/v1`; a VM or Wi-Fi device: the matching row from `mlx api info` |
| API Key | the gateway token — every source needs it, this Mac included |
| Model ID | `qwen38` (the id from `mlx model ls`, not the HF repo name) |

> ⚠️ **Set `max_tokens` to 2048 or higher.** Reasoning models emit thinking into
> a separate `reasoning` field before producing `content`. A small budget
> truncates mid-reasoning, `content` returns empty, and your client renders a
> **blank reply with no error**.

---

## ✨ Features

| Feature | What it means in practice |
|---|---|
| 🔋 **Zero-dependency CLI** | Every user-facing command imports only the Python standard library. When the inference venv breaks, `mlx check` still runs and still tells you how to fix it. The interpreter falls back venv → Homebrew → system, so there is no single point of failure between you and a working diagnostic. |
| 🗂️ **Three separate roots** | Code, state, and weights live in three independent directories. Reinstalling the tool rewrites only the first — a 28 GB download and the registry that knows about it both survive. All three are overridable via `MLX_ROOT_*`. |
| 🔌 **One gateway, any client** | `mlx up api` puts every running model behind a single OpenAI-compatible endpoint (`/v1/models`, `/v1/chat/completions`, streaming included). Cherry Studio, Open WebUI, or plain `curl` need three values and no adapter. |
| 🛑 **Refuses before it breaks things** | Outcomes are typed `Done` / `Refused` / `Failed`, and a `Refused` is *guaranteed* to have created zero resources — enforced by a static guard, not by convention. Starting a model that won't fit in RAM is refused with the numbers shown, instead of started and left to swap the machine to death. |
| 🎯 **Three-state probes** | `Ok(v)` / `Unknown(kind, reason)`. "Can't tell" never silently collapses into "no" — the command fails closed and names which input was undeterminable. |
| 📋 **Tables are the source of truth** | Six TSV tables define the command surface, flags, every user-facing string, config keys, model types, and the distribution manifest. The parser, `mlx help`, and shell completion are all generated from them. |
| ♻️ **Recoverable deletes** | `model rm --weights` moves weights to a trash directory. `model trash ls` / `restore` / `empty` — with confirmation strength tiered by how recoverable the action actually is. |
| 🧪 **Guards that have caught real defects** | 158 test functions, 435 assertions, run against a real installed product in a sandbox. Most guards carry a comment naming the specific defect that got past the previous version. |

---

## 🖼️ Showcase

**Health check — twelve items, every one actionable when it fails**

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

**Status — what's registered, what's running, what it costs**

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

**Download progress — computed from disk, not scraped from the downloader**

```console
$ mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit --port=8100
  ↓ 下载 mlx-community/Qwen3.8-27B-8bit
   … 已下 23.3 GB  21.7 MB/s  84.8%  约剩 3 分
  installed  qwen38
  path       ~/lab/models/mlx/hub/models--mlx-community--Qwen3.8-27B-8bit/snapshots/main
  state      就绪
```

**Errors that name the fix, not just the symptom**

```console
$ mlx chat "how do I check a phone's serial number"
没有名为「how do I check a phone's serial number」的模型。已登记：qwen38、qwen38-d
chat 的第一个参数是**模型 id**，问题用 --prompt= 传：
  mlx chat qwen38 --prompt="how do I check a phone's serial number"
不带 --prompt 则进入多轮对话：mlx chat qwen38
```

---

## 🗺️ Command Reference

| Group | Commands |
|---|---|
| **Tool** | `install` · `uninstall` · `status` · `check` · `version` · `help` |
| **Service** | `up` · `down` · `restart` |
| **Model** | `install` · `register` · `ls` · `info` · `rm` · `gc` · `repair` · `trash {ls,restore,empty}` |
| **Config** | `get` · `set` · `unset` · `ls` · `diff` · `export` · `import` · `validate` |
| **Gateway** | `api info` |
| **Backend** | `caps refresh` · `caps show` |
| **Other** | `chat` · `bench` · `bootstrap verify` |

33 public commands. `mlx help <command>` for any of them.

> **Flag syntax**: values are `--key=value`, not `--key value`. Boolean flags
> (`--json`, `--dry-run`, `--yes`, `--weights`) take no value.

---

## 📖 User Guide

The [full guide](docs/USER-GUIDE.md) has 15 chapters, and every `$` block in
it is pasted from a real run — install, find models, download, register, serve,
use, connect external tools, uninstall, troubleshoot.

### Why not the system Python?

`mlx` stopped shipping cp39 wheels at `0.30.4`, and current model architectures
need `mlx-lm >= 0.31.0`, which requires `mlx >= 0.30.4`. A venv built on macOS's
own 3.9.6 can **never** install a working backend — and pip reports this as
`ResolutionImpossible`, never mentioning the interpreter version, which makes it
very hard to diagnose. The installer picks a Homebrew interpreter and rebuilds
an existing 3.9 venv in place, leaving weights and registry untouched.

### About the gateway token

The gateway does not authenticate, and says so itself:

> On a single machine the model ports are already listening on loopback with no
> auth; a gateway token adds zero real obstacle. The actual boundary is the bind
> address.

`api.bind` defaults to `127.0.0.1`. Changing it to `0.0.0.0` exposes an
unauthenticated endpoint to your network — put it behind an authenticating
reverse proxy if you do.

---

## 🏗️ Architecture

Three tiers, and the boundary between them is the point:

```
T0  deployer/install.sh      bash · host side · never shipped into the payload
T1  payload/lib/mlx_server   Python stdlib only · the entire CLI
T2  payload/gateway          runs in the venv · may use third-party libraries
```

T1's import surface being the standard library is exactly what lets the CLI
survive a broken venv. Layering inside T1 is enforced by a guard, not a
convention — imports only travel downward:

```
result → primitives → policy → resources → orchestration → parsing → composition root
```

| | |
|---|---|
| Product code | 7.2k lines, 50 files |
| Tests | 5.9k lines, 158 functions, 435 assertions |
| | 18 of those assert the product tables match the design ledger. That ledger is development material and is not published, so they **skip** here with an explicit reason — `140 passed, 18 skipped` is the expected result. |
| Runtime deps (CLI) | none |
| Runtime deps (inference) | `mlx` + `mlx-lm` in a managed venv |

---

## 🧪 Development

```bash
cd v7 && bash check.sh     # ruff + pytest + 3.9.6 floor compile
```

Tests run in four layers. The top layer installs the real product into a sandbox
and drives the real `mlx` binary — no mocks between the assertion and the
artifact. A pre-commit hook runs the same script.

---

## 📝 Contributing

Issues and pull requests are welcome. Two things worth knowing before you start:

1. **New commands are table rows first.** Adding a verb means adding a row to
   `verbs.tsv` (and flags to `cli.tsv`, strings to `messages.tsv`). A guard
   asserts the tables and the implementation agree — implementing without
   registering, or registering without implementing, both turn the suite red.
2. **T1 stays stdlib-only.** A guard enforces the import surface. Anything
   needing third-party libraries belongs in T2.

---

## 🙏 Acknowledgements

Built on [MLX](https://github.com/ml-explore/mlx) and
[mlx-lm](https://github.com/ml-explore/mlx-lm) from Apple's machine learning
research team, and on the model conversions published by
[mlx-community](https://huggingface.co/mlx-community).

---

## ⚖️ License

[MIT](LICENSE)
