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
    """冻结的启动规格。**九字段**（裁决 R4-1 增 `service`；spec §3.1 增 `route`、`secret_env`）。

    `secret_env`：env 里属于密钥的变量名。**渲染层据此打码**，spec 本身保留原值 ——
    spawn 要用原值，`--dry-run` 与日志只能看到 `****`。
    """

    __slots__ = ("service", "argv", "env", "cwd_rel", "model_arg",
                 "self_id_expected", "sources", "route", "secret_env")

    def __init__(self, service, argv, env, cwd_rel, model_arg, self_id_expected, sources,
                 route=None, secret_env=()):
        # type: (str, tuple, dict, str, object, object, dict, object, tuple) -> None
        self.service = service
        self.argv = tuple(argv)
        self.env = dict(env)
        self.cwd_rel = cwd_rel
        self.model_arg = model_arg
        self.self_id_expected = self_id_expected
        self.sources = dict(sources)
        self.route = route
        self.secret_env = tuple(secret_env)


def _fill(template, **kw):
    # type: (str, object) -> list
    """参数模板 → token 列表。**先按空白切分再逐个替换** —— 值里有空格也不会被切开。"""
    if template in ("", "-", None):
        return []
    return [t.format(**kw) for t in template.split()]


def spec(service, cfg, route):
    # type: (str, object, object) -> object
    """产出 argv 向量。**模型服务的 argv / env 全部来自 `route.row`（`backends.tsv`）**。

    G-SSOT-14：非表驱动分支的集合 **== {api}**。模型目标缺判定结论 ⇒ None
    （校验段据此 `Refused(5, svc.spec_unavailable)`）。
    态 A（INV-01）：`model_arg == 短 id`，**snapshot 绝对路径不出现在 argv 里**。
    """
    v = cfg.values

    if service == "api":                      # ← 唯一的非表驱动分支（G-SSOT-14）
        # 网关跑在**下限解释器**上，与 T1 同一个 —— 它零依赖（标准库 HTTP 服务器），
        # 这样 venv 坏了网关照样起得来。`cwd_rel` 为空：它不需要别名目录。
        env = {"MLX_GATEWAY_PORT": str(v["port"]),
               "MLX_GATEWAY_BIND": str(v.get("api.bind") or "0.0.0.0"),
               # 空值写空串而不是不设：网关进程据此知道「配置里明确为空」，
               # 不会从继承的环境里拿到一个陈旧值
               "MLX_GATEWAY_ALLOW_CIDRS": str(v.get("api.allow_cidrs") or ""),
               "MLX_GATEWAY_TLS_CERT": str(v.get("api.tls_cert") or ""),
               "MLX_GATEWAY_TLS_KEY": str(v.get("api.tls_key") or ""),
               "MLX_GATEWAY_MAX_BODY_MB": str(v.get("api.max_body_mb") or 256),
               "MLX_GATEWAY_MEDIA_MAX_MB": str(v.get("api.media_max_mb") or 128),
               "MLX_GATEWAY_MEDIA_MAX_PARTS": str(v.get("api.media_max_parts") or 32),
               "MLX_GATEWAY_INFLIGHT_MAX_MB": str(v.get("api.inflight_max_mb") or 2048)}
        secret = []
        # 密钥经环境变量交给网关进程（spec §4.8）：spawn 以 spec.env 覆盖继承的环境，
        # 所以这里设了就是它；值为空则不设——继承的同名变量由 `runtime.spawn`
        # 统一剔除，网关令牌若来自 shell 是经 ctx 折入 cfg 后才在这里放进 env 的
        for name, key in (("MLX_GATEWAY_TOKEN", "api.token"),
                          ("MLX_BACKEND_API_KEY", "backend.api_key")):
            if v.get(key):
                env[name] = str(v[key])
                secret.append(name)
        return LaunchSpec(
            service=service,
            # `-u`：网关的 stdout 被 supervisor 重定向到 `logs/api.log`（普通文件），
            # 不加 `-u` 时 CPython 对非 tty 的 stdout 是块缓冲（8 KB）——`log_event`
            # 从不 flush，`down` 的 SIGTERM 默认处理器直接终止进程、不走 atexit，
            # 缓冲区里的 `info`（含每条 http 访问事件）会原样丢失，泄漏扫描扫不到它们
            # （Task 11 controller ruling OS1）。选在这里加而不是改 `log_event` 里
            # flush：plan 3 Task 8 要重写 `log_event`，不在这提前占那处改动。
            argv=("/usr/bin/python3", "-u", "-m", "gateway"),
            env=env, cwd_rel="", model_arg=None, self_id_expected=None,
            sources=dict(cfg.sources), secret_env=secret,
        )

    row = getattr(route, "row", None)
    if not row:
        return None
    # **argv[0] 是可执行的解释器路径**，由 ctx 注入（INV-02：argv 只有这一个产出点）
    interp = cfg.venv_python or "python3"
    argv = [interp, "-m", row["module"],
            "--model", service,
            "--port", str(v["port"]),
            "--max-tokens", str(v["max_tokens"]),
            "--host", row["host"]]
    n_concurrency = v.get("max_concurrency", 8)
    argv += _fill(row["concurrency_args"], n=n_concurrency)
    # **投机解码（完整小模型 drafter）**：值是 `<id>-draft`，一个**相对 cwd 的别名**，
    # 不是路径 —— cwd 就是 `supervisor.materialize` 建的那个别名目录（BCR-2026-09-02）。
    if v.get("draft_model"):
        argv += _fill(row["draft_args"], alias=f"{service}-draft",
                      k=v.get("num_draft_tokens", 3))
    if row.get("thinking") == "template" and getattr(route, "thinking", False):
        argv.append("--enable-thinking")

    env, secret = {}, []
    if row.get("offline") == "1":
        env["HF_HUB_OFFLINE"] = "1"
    for raw_name in (row.get("env_clear") or "-").split(","):
        name = raw_name.strip()
        if name and name != "-":
            env[name] = ""                   # 置空中和父进程继承（上游以真值判断）
    key_env = row.get("key_env")
    if key_env not in ("", "-", None) and v.get("backend.api_key"):
        env[key_env] = str(v["backend.api_key"])
        secret.append(key_env)
    return LaunchSpec(
        service=service, argv=argv, env=env,
        cwd_rel=f"run/alias/{service}",
        model_arg=service, self_id_expected=service,
        sources=dict(cfg.sources), route=route, secret_env=secret,
    )


def with_interpreter(spec, path):
    # type: (object, str) -> object
    """同一份规格，只把 `argv[0]` 换成执行段判定出的解释器（spec D20）。

    argv 仍只有本模块产出（INV-02）：判定要跑子进程，只能发生在执行段，
    所以校验段产出的规格用系统 python 占位，执行段经这里替换。
    """
    return LaunchSpec(service=spec.service, argv=(path,) + tuple(spec.argv[1:]), env=spec.env,
                      cwd_rel=spec.cwd_rel, model_arg=spec.model_arg,
                      self_id_expected=spec.self_id_expected, sources=spec.sources,
                      route=spec.route, secret_env=spec.secret_env)
