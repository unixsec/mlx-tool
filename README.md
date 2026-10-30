<div align="center">

# 🧰 MLX Toolkit

**Turn an Apple Silicon Mac into a private LLM server: models run locally, prompts and outputs never pass
through a third party, and any OpenAI- or Anthropic-compatible app can connect.**

[![Version](https://img.shields.io/badge/version-1.1.1-brightgreen)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-black)](https://support.apple.com/en-us/116943)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Backend](https://img.shields.io/badge/backend-mlx--lm%20%7C%20mlx--vlm-orange)](https://github.com/ml-explore/mlx-lm)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

🌐 **English** · [简体中文](README_cn.md)

📖 [User guide](docs/USER-GUIDE.md) (Chinese) · 🐛 [Known issues](docs/KNOWN-ISSUES.md) · 📝 [Changelog](CHANGELOG.md)

</div>

---

## 🎯 The problem it solves

Running models locally on a Mac is easy. Everything around the inference is not:

| Common approach | Problem | MLX Toolkit |
|---|---|---|
| One port per model | Every client is configured per model, each with its own port and quirks | **One gateway, one address, one token** in front of every running model |
| Expose the model port to the LAN so a VM or phone can use it | No auth, no source filtering; anyone on the network can call it | Models listen on `127.0.0.1` only. The gateway is the only exposed port: **every request needs a token**, and only this Mac, the LAN and VM networks are allowed |
| Download, pick a backend and size memory by hand | Wrong backend won't start, oversized models freeze the machine, errors are tracebacks | One command downloads and registers; **the backend is picked from the weights**; a model that won't fit is refused with the numbers |
| Migrate by hand on reinstall or upgrade | Lost settings, tens of GB of weights deleted by accident | Code, data and weights live apart; **a reinstall replaces only the code**, settings are backed up, the installer is safe to re-run |

## 💎 Why it matters

1. **Your data stays on your Mac.** Inference runs entirely locally; prompts and outputs never pass through a third party.
   The tool itself only goes online to install dependencies, download weights, and fetch remote media a request asks for.
2. **One endpoint for every client.** OpenAI Chat Completions / Responses and Anthropic Messages side by side:
   Cherry Studio, Claude Code, Codex CLI and Qwen Code connect directly.
3. **Secure by default, with a visible attack surface.** A token is generated at install and required from every source;
   `mlx api info` shows who can connect and at which address.
4. **Check first, act second.** When an operation should be refused, it is refused before anything is created.
   Deleted weights go to a trash you can restore from.
5. **Problems you can diagnose yourself.** 12 built-in checks (`mlx check`), each with a fix you can run as-is;
   every install leaves a log.

---

## ✨ Features

**Models and inference**
- `mlx model install`: download and register in one step, resumable, with progress measured from bytes on disk
- On every `mlx up` the model's `config.json` decides: multimodal goes to `mlx_vlm`, text-only to `mlx_lm`; override per model with `modality`
- Memory is estimated before start and an oversized model is refused; `--dry-run` shows the backend and why
- Thinking switch and effort are normalized to the values each model's template accepts, then written in the backend's format

**Gateway and clients**
- Endpoints: `/v1/chat/completions`, `/v1/completions` (text models), `/v1/responses` and Anthropic `/v1/messages` (multimodal models), `/v1/models`; the `/v1` prefix is optional
- Three kinds of source: this Mac, VMs (Parallels / VMware shared networking), devices on the same Wi-Fi / wired network; `mlx api info` gives each one its address
- Streaming requests get keepalive frames while the model is silent, and generation is cancelled when the client disconnects; `http(s)://` images, audio and video are fetched by the gateway

**Operations**
- The installer is safe to re-run: if nothing changed it finishes in seconds without stopping services; otherwise it backs up, then reinstalls
- `mlx status` shows it all on one screen: registered models, what's running, memory, backend
- `mlx model rm <id> --weights` moves weights to a trash; `mlx model trash restore` brings them back
- The CLI uses only the Python standard library: when the inference environment breaks, `mlx check` still runs and still tells you how to fix it

---

## 🛡️ Security design

### Trust boundary

```text
Client (this Mac / VM / Wi-Fi device)
   │  HTTP or HTTPS + gateway token
   ▼
Gateway :8080     the only exposed port: source filter → token auth → endpoint allowlist → media fetch
   │  127.0.0.1 + backend key (mlx_vlm)
   ▼
Model process     loopback only; never goes online, remote media is fetched by the gateway and handed over
```

### Controls

| Control | How |
|---|---|
| Authentication | Every request needs the token, local ones included (only CORS preflight and `GET /healthz` are exempt); the gateway refuses to start without one |
| Source filtering | Only loopback, Wi-Fi / wired networks and VM bridges; VPN and Thunderbolt bridge interfaces are refused; add networks with `api.allow_cidrs` |
| Endpoint allowlist | Anything off the list is a 404 and never reaches a model; `POST` must be `application/json` |
| Media fetch | Every redirect hop is re-checked; loopback, link-local, multicast and similar addresses are always refused, and remote sources can't reach private addresses outside the allowed networks |
| Resource limits | A total deadline for reading request bodies (slow drip gets 408), an in-flight memory budget (503 with `Retry-After` when full), media size and count limits |
| Secrets | `settings.json` and run records are mode 0600; secrets are masked on screen and shown only with `--reveal`; model processes don't inherit keys from your shell |
| Transport | Optional TLS (`api.tls_cert` / `api.tls_key`), TLS 1.2 minimum |

### Not covered by default

The full list, with reasons, is in [Known issues](docs/KNOWN-ISSUES.md). Before deploying, make sure these are acceptable:

- **Without TLS, devices on the same network can read requests and the token.** If you don't need remote access, run `mlx config set api.bind --value=127.0.0.1` to keep it local; otherwise enable TLS.
- **Token holders are trusted.** They can see every running model, fill the in-flight budget, and make the gateway fetch any public address. Rotate the token to cut someone off.
- The `mlx_lm` backend port has no authentication of its own (upstream has none); it binds to `127.0.0.1` only.
- Model templates render in a jinja2 sandbox, not in an isolated process; load models from trusted sources only.

---

## ⚡ Quick start

### Requirements

- **A Mac with Apple Silicon (M-series).** On an Intel Mac the installer stops without changing anything.
- **Homebrew Python 3.10 or newer.** The macOS system Python 3.9.6 can't install current `mlx` (no 3.9 wheels since 0.30.4).
- Disk space for models: hundreds of MB for small ones, tens of GB for large ones.

```bash
brew install python@3.13
```

### Install

```bash
git clone https://github.com/unixsec/mlx-tool.git
cd mlx-tool
bash deployer/install.sh
```

The installer sets up the inference environment, generates two keys, adds `mlx` to the PATH in `~/.zshrc`,
and finally runs the 12 checks itself. **Only the install result at the end matters:** if every line is ✓,
follow its next step, which is usually to open a new terminal.

```console
== 安装结果
  ✓ 版本      1.1.1（已安装）
  ✓ 自检      12 项，12 项通过
  ✓ 密钥      已就绪（查看：mlx config get api.token --reveal）
  · 网关      http://127.0.0.1:8080/v1，也对局域网与虚拟机开放、均需令牌（启动：mlx up api）
  · 模型      已登记 0 个
  → 下一步：新开一个终端（或 source ~/.zshrc），然后 mlx model install <短名> --repo=<仓库>
```

The tool's output is in Chinese.

### Run a model

Walk the whole chain with a 0.6B model first (about 335 MB):

```bash
mlx model install qwen06b --repo=mlx-community/Qwen3-0.6B-4bit --port=8150
mlx up qwen06b
mlx chat qwen06b --prompt="Introduce yourself in one sentence"
```

Then move to a large one. A 27B multimodal model is about 28 GB and needs 36 GB or more of unified memory:

```bash
mlx model install qwen38 --repo=mlx-community/Qwen3.8-27B-8bit --port=8100
mlx up qwen38
```

### Connect an app

```bash
mlx up api
mlx api info
mlx config get api.token --reveal
```

| In the client | Value |
|---|---|
| Base URL | OpenAI-style clients: `http://127.0.0.1:8080/v1` on this Mac; Anthropic clients (Claude Code): the same address **without** `/v1`. VMs and Wi-Fi devices use their line from `mlx api info` |
| API key | The gateway token (output of the third command above) |
| Model ID | The short name you registered, e.g. `qwen06b` |

Per-client settings are in the guide's [client section](docs/USER-GUIDE.md#clients).

> ⚠️ **Set `max_tokens` to 2048 or more.** Reasoning models write their thinking first, then the answer. Too small a budget
> cuts them off while thinking, and the client shows an empty reply with no error.

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

### Upgrade and uninstall

Upgrade: pull the new code and re-run the installer. Settings, the model registry and weights are kept;
the install result lists any services it stopped.

```bash
cd mlx-tool
git pull
bash deployer/install.sh
```

Uninstall: `mlx uninstall` removes the tool and its PATH lines but **keeps** settings and weights. You confirm by typing `yes`.
See the guide's [uninstall section](docs/USER-GUIDE.md#uninstall).

---

## 🗺️ Command reference

| Group | Commands |
|---|---|
| Tool | `install` · `uninstall` · `status` · `check` · `version` · `help` |
| Services | `up` · `down` · `restart` |
| Models | `model install` · `register` · `ls` · `info` · `rm` · `gc` · `repair` · `trash ls / restore / empty` |
| Config | `config get` · `set` · `unset` · `ls` · `diff` · `export` · `import` · `validate` |
| Gateway and keys | `api info` · `bootstrap keys` · `bootstrap verify` · `caps refresh` · `caps show` |
| Other | `chat` · `bench` |

`mlx help <command>` explains a single command. Flags that take a value are always written `--key=value`.

| Directory | Default | Contents |
|---|---|---|
| Code | `~/.local/share/mlx_server` | The tool and its inference environment; a reinstall replaces only this |
| Data | `~/.local/state/mlx_server` | Settings, model registry, logs, backups |
| Weights | `~/.local/share/mlx_models` | Downloaded model weights |

All three can be changed at install time with `MLX_ROOT_CODE` / `MLX_ROOT_DATA` / `MLX_ROOT_WEIGHTS`.

---

## 📖 Documentation

- [User guide](docs/USER-GUIDE.md) (Chinese): install, finding models, services, clients (VMs, Wi-Fi devices, TLS), [media](docs/USER-GUIDE.md#media), [thinking](docs/USER-GUIDE.md#thinking), [streaming](docs/USER-GUIDE.md#streaming), [remote access](docs/USER-GUIDE.md#remote), troubleshooting
- [Known issues](docs/KNOWN-ISSUES.md): open defects and deliberate limits
- [Changelog](CHANGELOG.md): changes and upgrade notes for each release

---

## 🧪 Development and contributing

```bash
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
bash check.sh
```

`check.sh` runs ruff, pytest, and compiles every product file with a real Python 3.9.6; the last line should read 「全绿 ✓」.
The top test layer installs the real artifacts into a sandbox and drives the real `mlx` binary. The design ledgers are not
published, so `tests/design` skips with a stated reason.

Two structural rules, both enforced by tests:

1. **The CLI and gateway have zero third-party dependencies**, standard library only; third-party code lives in the inference environment and is called as a subprocess.
2. **Commands, flags, messages and config keys are defined in tables under `payload/share/`.** A new command starts as a table row; tests fail when tables and code disagree.

Issues and PRs are welcome.

---

## 🙏 Acknowledgements

Built on [MLX](https://github.com/ml-explore/mlx), [mlx-lm](https://github.com/ml-explore/mlx-lm)
and [mlx-vlm](https://github.com/Blaizzy/mlx-vlm), using models converted by [mlx-community](https://huggingface.co/mlx-community).

## ⚖️ License

[MIT](LICENSE)
