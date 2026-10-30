<div align="center">

# 🧰 MLX Toolkit

**Run local LLMs on Apple Silicon — and plug them into any OpenAI- or Anthropic-compatible app.**

[![Version](https://img.shields.io/badge/version-1.1.0-brightgreen)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-black)](https://support.apple.com/en-us/116943)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Backend](https://img.shields.io/badge/backend-mlx--lm%20%7C%20mlx--vlm-orange)](https://github.com/ml-explore/mlx-lm)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-bash%20check.sh-success)](tests)

🌐 **English** · [简体中文](README_cn.md)

📖 [User Guide](docs/USER-GUIDE.md) · 🐛 [Known Issues](docs/KNOWN-ISSUES.md) · 📝 [Changelog](CHANGELOG.md)

</div>

---

## 🚀 What's New

- **v1.1.0** (2026-10) — see the `[1.1.0]` section of the
  [Changelog](CHANGELOG.md) for the full list and the upgrade notes.
  - The backend is picked from the weights on every `mlx up`: multimodal models
    run on `mlx_vlm`, text models on `mlx_lm`.
  - The gateway serves this Mac, VMs (Parallels / VMware shared network) and
    devices on the same Wi-Fi. **Every source needs the gateway token**, TLS is
    optional, and `api.bind` now defaults to `0.0.0.0`.
  - New endpoints: `/v1/responses` and Anthropic `/v1/messages` (multimodal
    backend), so Codex CLI and Claude Code can connect.
  - The gateway fetches `http(s)://` media itself, normalises thinking switches
    and effort levels, always streams from the model, keeps streaming clients
    alive and cancels generation when the client disconnects.
  - The installer is safe to re-run: when nothing changed it finishes in seconds
    without stopping your services, and it ends with an install summary that
    tells you what to do next. The weights root now defaults to
    `~/.local/share/mlx_models` (existing installs keep their location).
- **v1.0.0** (2026-09) — First release. The full chain is verified on real
  hardware: download a 27.5 GB model → register → serve → OpenAI gateway →
  Cherry Studio → streaming inference.

---

## ⚡ Quick Start

### Prerequisites

- **A Mac with Apple Silicon (M-series chip).** The MLX backend is arm64-only;
  on an Intel Mac the installer stops before changing anything.
- **Homebrew Python 3.10+.** The system Python (3.9.6) will not work — see
  [Why](#why-not-the-system-python) below.
- **git**, plus disk space for models (a few hundred MB for small ones, tens of GB for large ones).

```bash
brew install python@3.13
```

### Install

```bash
git clone https://github.com/unixsec/mlx-tool.git
cd mlx-tool
bash deployer/install.sh
```

The installer builds the inference venv and installs `huggingface_hub`, `mlx`,
`mlx-lm` and `mlx-vlm` (the last one pulls in torch, so the first install takes
a few minutes), generates the two secrets (`backend.api_key` and `api.token`),
adds `mlx` to the PATH in `~/.zshrc`, and finally runs the 12 checks
(`mlx check`) itself. **Only the "安装结果" (install result) summary at the end
matters**: if every line is ✓, follow its next step — usually **open a new
terminal** so PATH takes effect.

Where things go (three independent directories, each overridable with
`MLX_ROOT_CODE` / `MLX_ROOT_DATA` / `MLX_ROOT_WEIGHTS`):

| Root | Default | Holds |
|---|---|---|
| Code | `~/.local/share/mlx_server` | the tool and its inference venv — the only thing a reinstall replaces |
| Data | `~/.local/state/mlx_server` | settings, model registry, logs, backups |
| Weights | `~/.local/share/mlx_models` | downloaded model weights |

**Safe to re-run**: when nothing changed it finishes in seconds without stopping
your services; otherwise it backs up your settings and model registry before
reinstalling. Pass `--force` to reinstall anyway.

### Run a model

Start with a 0.6B model (~335 MB) to check the whole chain. Download and
registration happen in one step, resumable, with progress:

```bash
mlx model install qwen06b --repo=mlx-community/Qwen3-0.6B-4bit --port=8150
mlx up qwen06b
mlx chat qwen06b --prompt="Introduce yourself in one sentence"
```

Then move to a bigger model. The 27B multimodal model below is ~28 GB and needs
36 GB or more of unified memory; if it does not fit, `mlx up` refuses with the
numbers instead of grinding the machine to a halt:

```bash
mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit --port=8100
mlx up qwen38
```

Where to find models and how to size them against your memory: see "找模型"
(finding models) in the [user guide](docs/USER-GUIDE.md).

### Connect an external app

```bash
mlx up api
mlx api info
mlx config get api.token --reveal
```

- `mlx up api`: OpenAI- and Anthropic-compatible gateway
- `mlx api info`: base URLs for this Mac, VMs and Wi-Fi devices
- `mlx config get api.token --reveal`: the token every client must send

| Field in your client | Value |
|---|---|
| Base URL | OpenAI-style clients: `http://127.0.0.1:8080/v1` on this Mac; Anthropic clients (Claude Code): the same address **without** `/v1`. A VM or Wi-Fi device uses the matching row from `mlx api info`, which prints both columns |
| API Key | the gateway token — every source needs it, this Mac included |
| Model ID | the id you registered, e.g. `qwen06b` (the id from `mlx model ls`, not the HF repo name) |

Per-client settings (Cherry Studio, Claude Code, Codex CLI, Qwen Code) are in the
guide's [client section](docs/USER-GUIDE.md#clients).

> ⚠️ **Set `max_tokens` to 2048 or higher.** Reasoning models emit thinking into
> a separate `reasoning` field before producing `content`. A small budget
> truncates mid-reasoning, `content` returns empty, and your client renders a
> **blank reply with no error**.

### Upgrade and uninstall

Upgrade: pull the new code in the source directory and run the installer again.
Settings, the model registry and weights are left alone; the install summary
lists any services it had to stop and how to start them again.

```bash
cd mlx-tool
git pull
bash deployer/install.sh
```

Uninstall: `mlx uninstall` removes the code root and the PATH lines, and
**keeps** the data and weights roots (tens of GB of models are never deleted as a
side effect). It asks you to type `yes` in the terminal. See the guide's
[uninstall section](docs/USER-GUIDE.md#uninstall).

---

## ✨ Features

| Feature | What it means in practice |
|---|---|
| 🔋 **Zero-dependency CLI** | Every user-facing command imports only the Python standard library. When the inference venv breaks, `mlx check` still runs and still tells you how to fix it. The interpreter falls back venv → Homebrew → system, so there is no single point of failure between you and a working diagnostic. |
| 🗂️ **Three separate roots** | Code, state, and weights live in three independent directories. Reinstalling the tool rewrites only the first — a 28 GB download and the registry that knows about it both survive. All three are overridable via `MLX_ROOT_*`, and a reinstall keeps whatever locations you used last time. |
| ♾️ **An installer you can re-run** | When the code, config and PATH are unchanged and the self-check passes, it skips the reinstall and leaves your services running; otherwise it reinstalls after backing up settings and the model registry (no duplicate backups when nothing changed). Every run is logged, and on screen your home directory shows as `~`, so pasting the output for help does not leak your username. |
| 🧭 **Backend picked from the weights** | Every `mlx up` reads the model's `config.json`: a non-empty `vision_config` starts `mlx_vlm.server`, anything else `mlx_lm.server`. Force text mode per model with `modality`. |
| 🔌 **One gateway, many clients** | `mlx up api` puts every running model behind one endpoint: `/v1/chat/completions`, `/v1/completions` (text models), `/v1/responses` and Anthropic `/v1/messages` (multimodal models), `/v1/models`, plus an unauthenticated `GET /healthz`. The `/v1` prefix is optional. Endpoints a backend can't serve return 404 without touching the model. |
| 🛡️ **Explicit exposure** | The gateway accepts loopback, Wi-Fi / Ethernet subnets and VM bridges — VPN and Thunderbolt bridges are rejected — and every request needs the token. TLS is optional (`api.tls_cert` / `api.tls_key`). |
| 🖼️ **Media, thinking, long outputs** | `http(s)://` images, audio and video are fetched by the gateway (remote sources can't make it reach private addresses outside the allowed subnets). Thinking switches and effort levels are mapped to what the model's template accepts. The gateway always streams from the model, sends `: keepalive` to streaming clients and cancels generation on disconnect. See the guide's [media](docs/USER-GUIDE.md#media), [thinking](docs/USER-GUIDE.md#thinking) and [streaming](docs/USER-GUIDE.md#streaming) sections. |
| 🛑 **Refuses before it breaks things** | Outcomes are typed `Done` / `Refused` / `Failed`, and a `Refused` is *guaranteed* to have created zero resources — enforced by a static guard, not by convention. Starting a model that won't fit in RAM is refused with the numbers shown. `mlx restart` checks the registry, weights, config and backend before stopping anything; capacity, backend-package drift and gateway port conflicts can only be checked after the stop. |
| 🎯 **Three-state probes** | `Ok(v)` / `Unknown(kind, reason)`. "Can't tell" never silently collapses into "no" — the command fails closed and names which input was undeterminable. |
| 📋 **Tables are the source of truth** | Seven TSV tables under `payload/share/` define the commands, flags, every user-facing string, config keys, backends, attachable drafters and the distribution manifest. The parser and `mlx help` are generated from them. |
| ♻️ **Recoverable deletes** | `model rm --weights` moves weights to a trash directory. `model trash ls` / `restore` / `empty` — with confirmation strength tiered by how recoverable the action actually is. |
| 🧪 **Guards that have caught real defects** | Tests run against a real installed product in a sandbox. Most guards carry a comment naming the specific defect that got past the previous version — including one that parses every `mlx …` example in these docs. |

---

## 🖼️ Showcase

**Install summary — the only part of the install output you need to read**

```console
$ bash deployer/install.sh
…
== 安装结果
  ✓ 版本      1.1.0（已安装）
  ✓ 自检      12 项，12 项通过
  ✓ 密钥      已就绪（查看：mlx config get api.token --reveal）
  · 网关      http://127.0.0.1:8080/v1，也对局域网与虚拟机开放、均需令牌（启动：mlx up api）
  · 模型      已登记 0 个
  → 下一步：新开一个终端（或 source ~/.zshrc），然后 mlx model install <短名> --repo=<仓库>
            <仓库> 在 huggingface.co/mlx-community 挑，例如 mlx-community/Qwen3-0.6B-4bit
  · 日志      ~/.local/state/mlx_server/logs/install/20261027-103045.log
```

The tool's messages are in Chinese. Each line reads: version (installed), self-check
(12 of 12 passed), secrets ready, gateway address, registered models, the next
command to run, and the log file.

**Health check — twelve items, every one actionable when it fails**

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

**Status — what's registered, what's running, what it costs**

```console
$ mlx status
…
模型（2）
     ID       模型                            权重    端口   后端     并发  PID    内存      体积
  ─  ───────  ──────────────────────────────  ──────  ─────  ───────  ────  ─────  ────────  ────────
  ✓  qwen38   mlx-community/Qwen3.8-27B-8bit  已下载  :8100  mlx_vlm  8     43699  27788 MB  28164 MB
  ·  qwen05b  mlx-community/Qwen3-0.6B-4bit   已下载  :8150  —        —     —      —         335 MB

  2 个已登记，1 个在跑，共占 27788 MB 内存

网关
  ✓ api        在跑  :8080  pid 65853 · 绑定 0.0.0.0 · TLS 关 · 放行网段 3
```

**Download progress — computed from disk, not scraped from the downloader**

```console
$ mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit --port=8100
  ↓ 下载 mlx-community/Qwen3.8-27B-8bit
   … 已下 23.3 GB  21.7 MB/s  84.8%  约剩 3 分
  installed  qwen38
  modality   auto
  path       ~/.local/share/mlx_models/hub/models--mlx-community--Qwen3.8-27B-8bit/snapshots/main
  state      已下载
```

**Errors that name the fix, not just the symptom**

```console
$ mlx chat "how do I check a phone's serial number"
没有名为「how do I check a phone's serial number」的模型。已登记：qwen05b、qwen38
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
| **Model** | `install` · `register` · `ls` · `info` · `rm` · `gc` · `repair` · `trash ls` · `trash restore` · `trash empty` |
| **Config** | `get` · `set` · `unset` · `ls` · `diff` · `export` · `import` · `validate` |
| **Gateway** | `api info` |
| **Backend** | `caps refresh` · `caps show` |
| **Secrets** | `bootstrap keys` · `bootstrap verify` |
| **Other** | `chat` · `bench` |

`mlx help` lists them all; `mlx help <command>` explains one.

> **Flag syntax**: values are `--key=value`, not `--key value`. Boolean flags
> (`--json`, `--dry-run`, `--all`, `--yes`, `--weights`, `--reveal`,
> `--ignore-fitness`) take no value.

---

## 📖 User Guide

The [full guide](docs/USER-GUIDE.md) walks through install, finding models,
download, register, serve, use, connecting external tools (including VMs, Wi-Fi
devices, TLS, media and streaming), upgrade, uninstall and troubleshooting.

### Why not the system Python?

`mlx` stopped shipping cp39 wheels at `0.30.4`, and current model architectures
need `mlx-lm >= 0.31.0`, which requires `mlx >= 0.30.4`. A venv built on macOS's
own 3.9.6 can **never** install a working backend — and pip reports this as
`ResolutionImpossible`, never mentioning the interpreter version, which makes it
very hard to diagnose. The installer picks a Homebrew interpreter and rebuilds
an existing 3.9 venv in place, leaving weights and registry untouched.

### About the gateway token

**Every request needs the token, from every source — this Mac included.** The
only exceptions are CORS preflight requests and `GET /healthz`. Without a token
configured, `mlx up api` refuses to start; the installer generates one.

The gateway binds `0.0.0.0` by default, but only accepts loopback, Wi-Fi /
Ethernet subnets and VM bridges (`api.allow_cidrs` adds more). Traffic is plain
HTTP unless you set `api.tls_cert` and `api.tls_key` — without TLS, devices on
the same network segment can read requests and the token. For this Mac only:
`mlx config set api.bind --value=127.0.0.1`, then `mlx restart api`. Details in
the guide's [remote access section](docs/USER-GUIDE.md#remote).

---

## 🏗️ Architecture

Three tiers, and the boundary between them is the point:

```
T0  deployer/install.sh      bash · host side · never shipped into the payload
T1  payload/lib/mlx_server   Python stdlib only · the entire CLI
T2  payload/gateway          Python stdlib only · the HTTP gateway
```

T1's import surface being the standard library is exactly what lets the CLI
survive a broken venv. The gateway is stdlib-only too (it reuses a few T1
modules); at start-up it runs on the venv's python3 if that interpreter can
import it (TLS 1.3), otherwise on `/usr/bin/python3` (TLS 1.2 at most).
`check.sh` compiles every payload file with the real 3.9.6 interpreter.

Layering inside T1 is enforced by a guard, not a convention — imports only
travel downward:

```
result → primitives → policy → resources → orchestration → parsing → composition root
```

| | |
|---|---|
| Runtime deps (CLI and gateway) | none — Python standard library |
| Runtime deps (inference) | `mlx`, `mlx-lm`, `mlx-vlm` and `huggingface_hub` in a managed venv |
| Tests | unit · integration (L2) · e2e against a real install (L3) · design-ledger layer |
| | The design-ledger tests assert the product tables match the design ledger. That ledger is development material and is not published, so in a public clone the whole `tests/design` layer **skips** with an explicit reason. |

---

## 🧪 Development

First create a development venv in the source directory (test tools only —
unrelated to the tool's own inference venv):

```bash
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Run `check.sh` before every commit. It runs ruff, pytest, and a compile of every
payload file with the real 3.9.6 interpreter; the last line must be 全绿 ✓ (all green):

```bash
bash check.sh
```

Tests run in layers. The top layer installs the real product into a sandbox
and drives the real `mlx` binary — no mocks between the assertion and the
artifact. The repository does not install a git hook for you.

Some L3 gateway tests read this machine's real network configuration: they
need an IPv4 address on Wi-Fi / Ethernet / a VM bridge and an active `utun*`
VPN interface. Without them those tests **fail on purpose** rather than skip.

---

## 📝 Contributing

Issues and pull requests are welcome. Two things worth knowing before you start:

1. **New commands are table rows first.** Adding a verb means adding a row to
   `verbs.tsv` (and flags to `cli.tsv`, strings to `messages.tsv`). A guard
   asserts the tables and the implementation agree — implementing without
   registering, or registering without implementing, both turn the suite red.
2. **The CLI and the gateway stay stdlib-only.** A guard enforces the T1
   import surface; third-party code belongs in the inference venv, behind a
   subprocess.

---

## 🙏 Acknowledgements

Built on [MLX](https://github.com/ml-explore/mlx),
[mlx-lm](https://github.com/ml-explore/mlx-lm) and
[mlx-vlm](https://github.com/Blaizzy/mlx-vlm), and on the model conversions
published by [mlx-community](https://huggingface.co/mlx-community).

---

## ⚖️ License

[MIT](LICENSE)
