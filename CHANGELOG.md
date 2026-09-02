# 变更记录 · Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [1.0.0] — 2026-09-02

首个发布版本。全链路已在真机（Apple Silicon / macOS 26.6）跑通并验证：
下载 27.5 GB 模型 → 登记 → 起服务 → 网关 → Cherry Studio 接入 → 流式推理。

### 能做什么

- **模型生命周期**：`model install` / `register` / `ls` / `info` / `rm` / `gc` /
  `repair`，权重删除进回收站可恢复（`model trash {ls,restore,empty}`）
- **服务管理**：`up` / `down` / `restart`，支持多目标与 `--all`，
  启动前做容量判档，不让内存装不下的模型起来
- **OpenAI 兼容网关**：`up api` + `api info`，`/v1/models` 与
  `/v1/chat/completions`，支持流式
- **配置**：`config get/set/unset/ls/diff/export/import/validate`，
  用户段与机器段按 `schema.tsv` 的 scope 分离
- **诊断**：`check` 九项健康检查、`status` 双语义、`version` 含两个解释器与
  后端包指纹、`caps refresh/show`
- **交互**：`chat` 多轮 REPL、`bench` 单次压测

### 设计要点

- **T1 零第三方依赖**：整个 CLI 只 import 标准库，venv 损坏时仍能自我诊断。
  解释器按 venv → Homebrew → 系统三级回落
- **三根分离**：代码 / 状态 / 权重三个独立目录，重装只重写代码根
- **六张 TSV 表为 SSOT**：解析器、`help`、补全均由表生成
- **类型化结果**：`Done` / `Refused` / `Failed`，`Refused` 保证零资源已创建
  （静态守卫强制）
- **三态探针**：`Ok` / `Unknown`，「判不出来」不塌成「否」

### 已知缺口

见 [KNOWN-ISSUES.md](v7/docs/KNOWN-ISSUES.md)。主要是：`mlx up` 报「已启动」
时后端可能仍在加载模型；`model rm --dry-run` 等若干动词尚未实现预览；
`logs` / `model convert` / `model upgrade` 未实现。

### 环境要求

macOS on Apple Silicon，Homebrew Python ≥ 3.10。**不能用 macOS 自带的
3.9.6** —— `mlx ≥ 0.30.4` 只发 cp310+ 的 wheel，而跑当前模型架构需要
`mlx-lm ≥ 0.31.0`（它依赖前者）。
