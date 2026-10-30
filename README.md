<div align="center">

# 🧰 MLX Toolkit

**Turn an Apple Silicon Mac into a private LLM server: models run locally, prompts and outputs never pass
through a third party. Any OpenAI-compatible app can connect, and multimodal models also speak the Anthropic API.**

[![Version](https://img.shields.io/badge/version-1.2.0-brightgreen)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Apple%20Silicon-black)](https://support.apple.com/en-us/116943)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Backend](https://img.shields.io/badge/backend-mlx--lm%20%7C%20mlx--vlm-orange)](https://github.com/ml-explore/mlx-lm)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

🌐 **English** · [简体中文](README_cn.md)

📖 [User guide](docs/USER-GUIDE.md) (Chinese) · 📝 [Changelog](CHANGELOG.md)

</div>

---

## 🎯 The problem it solves

Running models locally on a Mac is easy. Everything around the inference is not:

| Common approach | Problem | MLX Toolkit |
|---|---|---|
| One port per model | Every client is configured per model, each with its own port and quirks | **One gateway, one address, one token** in front of every running model |
| Expose the model port to the LAN so a VM or phone can use it | No auth, no source filtering; anyone on the network can call it | Models listen on `127.0.0.1` only. The gateway is the only exposed port: **every request needs a token**, and only this Mac, the LAN and VM networks are allowed |
| Download, pick a backend and size memory by hand | Wrong backend won't start, oversized models freeze the machine, errors are tracebacks | One command downloads and registers; **the backend is picked from the weights**; free memory is checked before start, and a model that won't fit is refused |
| Migrate by hand on reinstall or upgrade | Lost settings, tens of GB of weights deleted by accident | Code, data and weights live apart; **a reinstall replaces only the code**, settings are backed up, the installer is safe to re-run |

## 💎 Why it matters

| Value | In short |
|---|---|
| 🔒 **Your data stays on your Mac** | Inference runs entirely locally; prompts and outputs never pass through a third party. The tool itself only goes online to install dependencies, download weights, and fetch remote media a request asks for |
| 🔌 **One endpoint for every client** | One address and one token in front of every running model; OpenAI-style clients such as Cherry Studio and Qwen Code connect directly, Claude Code and Codex CLI connect to multimodal models |
| 🛡️ **Secure by default** | A token is generated at install and required from every source; model processes listen on loopback only; `mlx api info` shows the attack surface |
| 🧯 **Nothing broken by accident** | Checks run before any change, so a refused operation leaves no file or process behind, and a refused `restart` leaves the old service running; a model that won't fit in memory doesn't start; deleted weights can be restored from the trash |
| 🩺 **Self-service troubleshooting** | 12 built-in checks; failures come with a fix that names the real path; `mlx logs` shows a service's log; every install leaves a log |

---

## ✨ Features

| Feature | In short |
|---|---|
| 📦 **One-command model install** | `mlx model install` downloads and registers, resumable, with progress measured from bytes on disk |
| 🧭 **Backend picked from the weights** | On every `mlx up` the model's `config.json` decides: multimodal goes to `mlx_vlm`, text-only to `mlx_lm`; override with `modality` |
| 📏 **Capacity check before start** | Two checks, each holding back 20%: physical memory (including running models) and currently free memory; a model that won't fit is refused with the numbers, `--ignore-fitness` forces it; `--dry-run` shows the backend and why |
| ✅ **Ready means usable** | `mlx up` returns only once the model can answer: for mlx_lm models it sends one 1-token completion first, and a load failure is reported at once and the process stopped (`--no-wait` skips this) |
| 🌐 **Unified gateway** | `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, Anthropic `/v1/messages`, `/v1/models`; the `/v1` prefix is optional. `/v1/completions` is served by text-only models, `/v1/responses` and `/v1/messages` by multimodal models only |
| 🖥️ **Multiple sources** | This Mac, VMs (Parallels / VMware shared networking), devices on the same Wi-Fi / Ethernet network; `mlx api info` gives each its address |
| 🧠 **Thinking normalization** | Effort is normalized to the values each model's template accepts; the thinking switch and effort are written in the backend's format |
| 🖼️ **Multimodal media** | `http(s)://` images, audio and video are fetched by the gateway and handed to the model; videos go to a temp file deleted when the request ends |
| 📡 **Streaming and cancel on disconnect** | Streaming requests get keepalive frames while queued and while the model is silent; generation is cancelled when the client disconnects, over plain HTTP and HTTPS alike, so no compute is wasted |
| ♾️ **Re-runnable installer** | If nothing changed it finishes in seconds without stopping services; otherwise it backs up settings and the registry, then reinstalls; upgrades remove files the old version left behind |
| 🗂️ **Code, data and weights kept apart** | Three independent directories; a reinstall replaces only the code, tens of GB of weights and your settings stay put |
| ♻️ **Trash** | `mlx model rm <id> --weights` moves weights to a trash; `mlx model trash restore <id>` brings them back |
| 🔋 **Zero-dependency CLI** | The CLI and gateway use only the Python standard library: when the inference environment breaks, `mlx check` still runs and tells you how to fix it |

---

## 🛡️ Security features

### Access control

| Feature | In short |
|---|---|
| **Token auth for every source** | Every request needs the gateway token, local ones included; only CORS preflight and `GET /healthz` are exempt. The gateway won't start without a token; tokens must be 32–128 characters (letters, digits, `-`, `_`) |
| **Source network filtering** | Only loopback, Wi-Fi / Ethernet networks and VM bridges are allowed; VPN, Thunderbolt bridge and USB device-mode links are refused; USB docks and other wired adapters are opted in with `api.allow_ifaces`, extra networks with `api.allow_cidrs`; networks are recomputed as they change. The source is checked as soon as a connection is accepted, so a refused source gets nothing read |
| **Endpoint allowlist** | Anything off the list is a 404 and never reaches a model; `POST` must be `application/json` |
| **Models on loopback, each with a backend key** | Backends bind to `127.0.0.1` explicitly, so the gateway is the only exposed port; both backends accept only their own backend key (`mlx_lm` has no auth upstream, the tool adds it with an auth wrapper), so other local processes can't bypass the gateway |
| **Optional TLS** | Set `api.tls_cert` / `api.tls_key` to enable it; TLS 1.2 minimum |

### Protocol hardening

| Feature | In short |
|---|---|
| **Request smuggling defense** | Chunked request bodies, malformed or oversized `Content-Length` and folded header lines are refused, and the connection is closed so leftover bytes are never parsed as a second request |
| **Auth before body** | Auth and endpoint checks happen before the request body is read; `Expect: 100-continue` is authenticated and budgeted first |
| **Visible stream errors** | If the upstream drops mid-stream, a native error event is sent in the OpenAI, Responses or Anthropic format; after 15 s in the queue the response headers and keepalives go out first, so clients don't time out |
| **Error sanitization** | Upstream errors returned to VMs and Wi-Fi devices are rebuilt from an allowlist: only messages that read like parameter validation and carry no implementation details, plus `type` / `code` / `param` when they are short identifiers, are kept; everything else becomes a generic message; backend 5xx and auth failures become a generic 502 for every source; local clients see 4xx errors unchanged for debugging |

### Abuse and resource protection

| Feature | In short |
|---|---|
| **SSRF-safe media fetch** | For fetches requested by remote sources (VMs, Wi-Fi devices): loopback, link-local, multicast, reserved and this Mac's own addresses are refused, and private addresses outside the allowed networks are unreachable; every redirect hop is re-checked; IDN hostnames are refused. Local sources are not restricted |
| **Slow-request defense** | Request headers (including keep-alive idle time) must arrive within 30 s; the body read deadline grows with the bytes actually received; drip-fed uploads get 408 and their budget back |
| **Connection caps** | At most 256 remote connections in total and 32 per source, with a separate allowance for local clients; extra connections are closed at once and use no thread |
| **In-flight budget** | Request bodies and media being processed share a memory cap; over it, 503 with `Retry-After`; one remote source can hold at most a quarter, halved for 30 s after a body timeout; fetched and inline media both have per-part size and count limits |
| **Capacity refusal** | Physical and currently free memory are both checked before start; a model that won't fit is refused with the numbers instead of pushing the machine straight into swap; a growing context can still cause paging later |

### Data and secret protection

| Feature | In short |
|---|---|
| **Secrets on disk at 0600** | `settings.json` and run records are written atomically at 0600; chat history files are created at 0600 |
| **Masked everywhere** | `status` and `api info` only say whether a secret is set; `config ls` and `config get` show `****`, and only `config get --reveal` shows the value; `mlx bootstrap keys` only fills empty values and never prints them; `config set --value-stdin` sets a secret without it reaching the process list or shell history |
| **Export without secrets** | `config export` always leaves secret fields out, so a config is safe to copy to another machine |
| **Clean child environments** | Token and backend-key variables are removed from the environment model and gateway processes inherit; they get only the values the tool computes. Note: a `MLX_GATEWAY_TOKEN` exported in your shell is used as the gateway token (it beats the config file) and shows as source `env` in `config ls` |
| **Sandboxed template rendering** | Model chat templates are rendered in a jinja2 sandbox inside a macOS `sandbox-exec` profile: no network, no file writes, no reads of home or the data root (including `settings.json`), with memory and time limits |
| **No secrets in logs or output** | A test runs a set of commands that touch the keys, then scans all three directories and those commands' output for them; install output shows your home as `~`, so pasted logs don't carry your username |

### Local process and file safety

| Feature | In short |
|---|---|
| **Stops only its own processes** | Processes are claimed from the run record (pid and start time) and port ownership is checked by process group, so other people's processes are never killed |
| **No port sharing with strangers** | Before start, the gateway port is checked with `lsof` plus a connect probe, so listeners of other users are found too; with a wildcard bind the gateway first claims `127.0.0.1` / `::1`, so a same-user process can't take the local traffic |
| **Check first, then act** | Files, processes and service stops happen only after every check passes, guarded by static checks (stopping a service and deleting files count as side effects); all `restart` checks run before the old service stops; `--dry-run` changes no state and leaves no process; a running model can't be `rm`'d |
| **Bounded deletion** | Irreversible recursive deletes are allowed only on the tool's own directories; deleted weights go to the trash first |

### Trust boundary

```text
Client (this Mac / VM / Wi-Fi device)
   │  HTTP or HTTPS + gateway token
   ▼
Gateway :8080     the only exposed port: source filter → token auth → endpoint allowlist → media fetch
   │  127.0.0.1 + backend key
   ▼
Model process     loopback only; runs in offline mode, remote media is fetched by the gateway and handed over
```

### Not covered by default

Before deploying, make sure these are acceptable:

- **Without TLS, devices on the same network can read requests and the token.** If you don't need remote access, run `mlx config set api.bind --value=127.0.0.1` to keep it local; otherwise enable TLS.
- **Token holders are trusted.** They can see every running model and make the gateway fetch any public address; a token holder on this Mac can also make it fetch loopback and LAN addresses and have the model read local file paths. Rotate the token to cut someone off.
- **Local reverse proxies and tunnels count as local sources** (ssh -R, cloudflared, ngrok and similar): remote requests that come through them get local-source rights. To expose the gateway, use its own LAN listener and TLS rather than another forwarding layer on this Mac.
- A same-user process can still bind the gateway's **LAN IP** (loopback is already claimed); TLS is the defence against such processes.
- Setting a secret with `--value` briefly puts it in the process list; use `mlx config set api.token --value-stdin` instead.
- The model process runs upstream inference code and reads files in the weights directory when loading; load models from trusted sources only.

---

## ⚡ Quick start

### Requirements

- **A Mac with Apple Silicon (M-series).** On an Intel Mac the installer stops without changing anything.
- **Homebrew Python 3.10 or newer.** The macOS system Python 3.9.6 can't install current `mlx` (no 3.9 wheels after 0.29.3, and mlx-lm 0.31 needs mlx ≥ 0.30.4).
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
  ✓ 版本      1.2.0（已安装）
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

Then move to a large one. A 27B multimodal model is about 28 GB and needs roughly 37 GB of free memory at start, which usually means a Mac with 48 GB or more:

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
| Model ID | The short name you registered, e.g. `qwen06b`. Claude Code and Codex CLI need `/v1/messages` / `/v1/responses`, which only multimodal models serve, so give them a multimodal model such as `qwen38` |

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
Clones made before 2026-10-30: the git history was rewritten once, so `git pull` reports diverged branches; run `git fetch origin --tags --force && git reset --hard origin/main` instead.

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
| Services | `up` · `down` · `restart` · `logs` |
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
