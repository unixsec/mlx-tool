"""P 层 · 服务启动参数与 argv 向量的**唯一产出点**（INV-02 / `G-SSOT-03`）。

**纯函数、零 I/O**：函数体内不得读文件、进程、网络、时钟、环境变量。
`--dry-run` 打印的与真启动用的是**同一个对象**（`F-12` 步 3 与步 5）。
"""


class SpecRefusal(Exception):
    """用户显式设置的参数其能力状态未知 ⇒ 指名参数并拒绝（code 9）。"""

    def __init__(self, message, code, param):
        # type: (str, int, str) -> None
        super().__init__(message)
        self.code = code
        self.param = param


class LaunchSpec:
    """冻结的启动规格。**七字段**（裁决 R4-1 增 `service`）。"""

    __slots__ = ("service", "argv", "env", "cwd_rel", "model_arg",
                 "self_id_expected", "sources")

    def __init__(self, service, argv, env, cwd_rel, model_arg, self_id_expected, sources):
        # type: (str, tuple, dict, str, object, object, dict) -> None
        self.service = service
        self.argv = tuple(argv)
        self.env = dict(env)
        self.cwd_rel = cwd_rel
        self.model_arg = model_arg
        self.self_id_expected = self_id_expected
        self.sources = dict(sources)


def spec(service, cfg, caps):
    # type: (str, object, dict) -> LaunchSpec
    """产出 argv 向量。`G-SSOT-14`：非表驱动分支的集合 **== {api}**（相等断言）。

    态 A（INV-01）：`model_arg == 短 id`，**snapshot 绝对路径不出现在 argv 里**。
    """
    v = cfg.values

    if service == "api":                      # ← 唯一的非表驱动分支（G-SSOT-14）
        # 网关跑在**下限解释器**上，与 T1 同一个 —— 它零依赖（标准库 HTTP 服务器），
        # 这样 venv 坏了网关照样起得来。`cwd_rel` 为空：它不需要别名目录。
        return LaunchSpec(
            service=service,
            argv=("/usr/bin/python3", "-m", "gateway"),
            # `MLX_GATEWAY_BIND` 从配置来，不再只有一个写死的默认值。
            # `MLX_GATEWAY_TOKEN` 不在这里 —— 它由用户在环境里给，
            # `runtime.spawn` 把父进程环境整体传下去（见那里的注释）。
            env={"MLX_GATEWAY_PORT": str(v["port"]),
                 "MLX_GATEWAY_BIND": str(v.get("api.bind", "127.0.0.1"))},
            cwd_rel="", model_arg=None, self_id_expected=None,
            sources=dict(cfg.sources),
        )

    # 表驱动：模型服务。model_arg 是短 id，不是路径（INV-01 态 A）
    # **argv[0] 是可执行的解释器路径**，不是包名。`backend` 声明的是「用哪个
    # 后端模块」（`mlx_lm`），跑它需要一个解释器 —— 那个解释器由 ctx 注入。
    # 此前这里直接把 `mlx_lm` 放进 argv[0]，`runtime.spawn` 在启动前再换掉，
    # 于是 argv 有了第二个产出点，而 `--dry-run` 看不见它（INV-02）。
    interp = cfg.venv_python or "python3"
    argv = [
        interp, "-m", f"{v['backend']}.server",
        "--model", service,
        "--port", str(v["port"]),
        "--max-tokens", str(v["max_tokens"]),
    ]
    # **投机解码**：值是 `<id>-draft`，一个**相对 cwd 的别名**，不是路径 ——
    # cwd 就是 `supervisor.materialize` 建的那个别名目录，里面有同名链接。
    # 传绝对路径会违反 INV-01 态 A；传 HF repo id 则解析不到
    # （`mlx_lm` 用 `snapshot_download(local_files_only=True)`，要标准缓存布局，
    # 而本工具的下载器产出 `snapshots/main/` 无 refs —— 实测 LocalEntryNotFoundError）。
    if v.get("draft_model"):
        argv += ["--draft-model", f"{service}-draft"]
        if v.get("num_draft_tokens"):
            argv += ["--num-draft-tokens", str(v["num_draft_tokens"])]
    argv = tuple(argv)
    return LaunchSpec(
        service=service, argv=argv, env={},
        cwd_rel=f"run/alias/{service}",
        model_arg=service, self_id_expected=service,
        sources=dict(cfg.sources),
    )
