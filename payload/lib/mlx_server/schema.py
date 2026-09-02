"""P 层 · 配置解析与分层。**纯函数、零 I/O。**"""


class ConfigError(Exception):
    """键缺失或值域非法。`kind` ∈ {missing, domain}。"""

    def __init__(self, message, kind, key):
        # type: (str, str, str) -> None
        super().__init__(message)
        self.kind = kind
        self.key = key


# 每个键的默认值与值域。写成表，不是散在代码里的 if —— 加一个键只改这里。
DEFAULTS = {
    "port": 8100,
    # **网关绑定地址**。设计把 `api.bind` 登记为 `gw.guard` 经 `schema.effective`
    # 读的键（暴露等级由它唯一决定，`R-API-25`），而它此前根本不在配置域里 ——
    # 用户手册教的 `mlx config set api.bind=0.0.0.0` 返回「未知配置键」。
    # **`api.token` 有意不进这里**：它是凭据，落进 settings.json 就是明文存盘，
    # 而本版没有 secret 列来标注「导出时排除」。它保持为环境变量
    # `MLX_GATEWAY_TOKEN`（进程生命周期内有效，不落盘），手册照此说明。
    "api.bind": "127.0.0.1",
    # 判档的三个**比例常量**（百分数整数）。设计逐字：「没有独立的绝对阈值键」——
    # 绝对值来自探针，阈值只表达比例，换机器不用改配置。
    "fit.reserve_pct": 20,     # 预留给系统的份额
    "fit.tight_pct": 75,       # 超过预算的这个比例 ⇒ TIGHT（能起，但挤）
    "fit.over_pct": 95,        # 超过这个比例 ⇒ OVER（拒绝启动）
    "backend": "mlx_lm",
    "max_tokens": 4096,
}


def keydefs(tables):
    # type: (object) -> dict
    """`key` → 定义（十一列）。**唯一读点**，来自 `schema.tsv`。"""
    return {r["key"]: r for r in tables.schema_keys()}


def defaults_from(tables):
    # type: (object) -> dict
    """出厂默认值。**类型按 `type` 列归一** —— 表里都是字符串。"""
    out = {}
    for r in tables.schema_keys():
        v = r["default"]
        if v == "-":
            # `-` = **没有出厂默认值**。machine 段字段（`revision` / `size_mb`）
            # 就是这样：它们的值只能来自磁盘事实，不存在「出厂值」。
            # 此前这里对它们做 `int("-")` ⇒ ValueError，而这条路径只有在
            # 有人调 `defaults_from` 时才走到，所以一直没暴露。
            continue
        out[r["key"]] = int(v) if r["type"] == "int" else v
    return out


def writable(key, defs):
    # type: (str, dict) -> bool
    """这个键**能不能被 `config set` 写**。

    `P-SCH-15` 是双向差集：`{k : writable(k)}` **==** `{k ∈ defs : scope == "user"}`。
    此前没有这个函数，也没有 `scope` 列 —— 那条守卫**没有求值对象**，
    而它要拦的正是 `mlx config set 27b revision abc`（往 machine 段写磁盘事实）。
    """
    d = defs.get(key)
    return bool(d) and d.get("scope") == "user"


def exportable(key, defs):
    # type: (str, dict) -> bool
    """`config export` 的**肯定白名单**：`exportable == yes` 且 `secret == no`。

    两个条件缺一不可 —— 机密即使标了 exportable 也不导出。
    """
    d = defs.get(key)
    return bool(d) and d.get("exportable") == "yes" and d.get("secret") != "yes"


def coerce(key, raw):
    # type: (str, object) -> object
    """把 CLI 来的字符串按键的**出厂类型**归一。**唯一实现点**。

    不归一的后果不是「显示成字符串」这么轻：`ports.conflict_check` 比的是
    `machine["port"] == ctx.gateway_port`，一边是 `"8390"` 一边是 `8390`，
    **恒为假** —— 网关端口互斥这道检查因此从不报警，`mlx check` 的 ports 项
    也一直是绿的。类型错误在这个项目里是以「检查恒不命中」的形态出现的。
    """
    if key not in DEFAULTS or raw is None or not isinstance(raw, str):
        return raw
    want = type(DEFAULTS[key])
    # 本版配置域里没有 bool 键。设计要求 schema 提供「唯一的 parse_bool」
    # （`04-interfaces.md` schema 行），但零消费者的实现正是这轮审查批的东西 ——
    # 等真有 bool 键时连同它的读取点一起加。
    if want is int:
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"{key} 需要整数，实得 {raw!r}", kind="domain", key=key) from None
    return raw


class ServiceConfig:
    """一个服务的生效配置 + 每项的来源层（`--dry-run` 逐项打印它）。"""

    __slots__ = ("service", "values", "sources", "venv_python")

    def __init__(self, service, values, sources, venv_python=""):
        # type: (str, dict, dict, str) -> None
        # `venv_python` 由 **ctx 注入**（合稿 `04-interfaces.md` 的 ServiceConfig
        # 逐字：「MLX_VENV 指向的解释器绝对路径，由 ctx 注入，非本模块读取」）。
        # 有了它，`spec()` 才能直接产出**可执行的** argv —— 否则 `runtime.spawn`
        # 得在启动前把 argv[0] 从包名换成解释器路径，那就是 argv 的第二个产出点，
        # 而 `--dry-run` 恰好看不见它：用户照着打印出来的 `["mlx_lm", ...]`
        # 手工复现必然失败，因为 `mlx_lm` 是包名不是可执行文件（INV-02）。
        self.service = service
        self.values = values
        self.venv_python = venv_python
        self.sources = sources

    def __repr__(self):
        # type: () -> str
        return f"ServiceConfig({self.service!r}, {self.values!r})"


def effective(service, layers, venv_python="", defaults=None):
    # type: (str, dict, str, dict) -> ServiceConfig
    """把分层配置合成生效值。**纯函数**：同样入参任意次调用返回逐字节相同。

    层序（低→高）：出厂默认 < machine < user < cli。
    每项记来源层，`--dry-run` 要逐项打印它（`BCR-R-27`）。

    `defaults` 由调用方注入（来自 `schema.tsv`）—— **纯函数不自己读表**。
    不注入时回落到本模块的 `DEFAULTS`：那是引导期与表损坏时的兜底，
    不是「第二份定义」（表在就以表为准）。

    此前这里锁死在硬编码的 `DEFAULTS` 上，于是 `schema.tsv` 建好之后
    新加的键（`draft_model` / `fit.*` / `api.bind`）在这里一律判「未知配置键」——
    `config set` 写得进去、`config get` 读不回来，而 `config set` 的效果回读
    正好走这条路径 ⇒ `KeyError`。建了表却没让求值器用表，等于表只有一半在生效。
    """
    base = defaults if defaults is not None else DEFAULTS
    values, sources = {}, {}
    for k, v in base.items():
        values[k], sources[k] = v, "default"
    for layer in ("machine", "user", "cli"):
        for k, v in (layers.get(layer) or {}).items():
            if k not in base:
                raise ConfigError(f"未知配置键 {k}（层 {layer}）", kind="domain", key=k)
            values[k], sources[k] = v, layer
    return ServiceConfig(service, values, sources, venv_python)
