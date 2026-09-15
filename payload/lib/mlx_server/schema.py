"""P 层 · 配置解析与分层。**纯函数、零 I/O。**"""
import re


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
    # **两个密钥键进了配置域**（spec §5）：此前 `api.token` 有意不进这里，理由是
    # 「落进 settings.json 就是明文存盘，而本版没有 secret 列」。现在 `schema.tsv`
    # 有 secret 列（打码、不导出），settings.json 以 0600 写出（atomic.replace mode）。
    "api.bind": "127.0.0.1",
    # 判档的三个**比例常量**（百分数整数）。设计逐字：「没有独立的绝对阈值键」——
    # 绝对值来自探针，阈值只表达比例，换机器不用改配置。
    "fit.reserve_pct": 20,     # 预留给系统的份额
    "fit.tight_pct": 75,       # 超过预算的这个比例 ⇒ TIGHT（能起，但挤）
    "fit.over_pct": 95,        # 超过这个比例 ⇒ OVER（拒绝启动）
    "backend": "mlx_lm",
    "max_tokens": 81920,
    "modality": "auto",
    "max_concurrency": 8,
    "backend.api_key": "",
    "api.token": "",
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


def coerce(key, raw, defs=None):
    # type: (str, object, dict) -> object
    """把 CLI 来的字符串按键的类型归一。**唯一实现点**。

    类型优先取 `schema.tsv` 的 `type` 列（`defs` 由调用方注入），表里没有再回落
    `DEFAULTS`。此前只看 `DEFAULTS`，于是表里有而 DEFAULTS 没有的 int 键
    （`num_draft_tokens`）以字符串落盘 —— 值域校验一接上就会把它判成非法。

    不归一的后果不是「显示成字符串」这么轻：`ports.conflict_check` 比的是
    `machine["port"] == ctx.gateway_port`，一边是 `"8390"` 一边是 `8390`，
    **恒为假** —— 类型错误在这个项目里是以「检查恒不命中」的形态出现的。
    """
    if raw is None or not isinstance(raw, str):
        return raw
    d = (defs or {}).get(key)
    if d is not None:
        want_int = d.get("type") == "int"
    elif key in DEFAULTS:
        want_int = isinstance(DEFAULTS[key], int) and not isinstance(DEFAULTS[key], bool)
    else:
        return raw
    if want_int:
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(f"{key} 需要整数，实得 {raw!r}", kind="domain", key=key) from None
    return raw


def check_domain(key, value, defs):
    # type: (str, object, dict) -> object
    """值域校验的**唯一实现点**（spec §2.2）。合法返回 None，非法返回原因文本。

    `config set`、`config import`、`model register --type`、校验段「生效配置」四处共用。
    值域列的形态：`lo-hi` / `lo-`（整数区间）· `a|b|c`（枚举）· `^…`（正则全匹配）·
    `-` / `<占位>` / 空（不约束）。str 键的空串恒合法 —— 它表示「未设置」。
    """
    d = defs.get(key)
    if not d:
        return "未知配置键"
    dom = (d.get("domain") or "").strip()
    if dom in ("", "-") or dom.startswith("<"):
        return None
    if d.get("type") == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"需要整数，实得 {value!r}"
        lo, _, hi = dom.partition("-")
        if lo and value < int(lo):
            return f"不得小于 {lo}"
        if hi and value > int(hi):
            return f"不得大于 {hi}"
        return None
    if not isinstance(value, str):
        return f"需要字符串，实得 {value!r}"
    if value == "":
        return None
    if dom.startswith("^"):
        return None if re.fullmatch(dom, value) else "格式不符"
    allowed = dom.split("|")
    return None if value in allowed else "取值须为 " + "|".join(allowed) + " 之一"


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
