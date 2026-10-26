"""P 层 · 配置解析与分层。**纯函数、零 I/O。**"""
import ipaddress
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
    "api.bind": "0.0.0.0",
    # 判档的三个**比例常量**（百分数整数）。设计逐字：「没有独立的绝对阈值键」——
    # 绝对值来自探针，阈值只表达比例，换机器不用改配置。
    "fit.reserve_pct": 20,     # 预留给系统的份额
    "fit.tight_pct": 75,       # 超过预算的这个比例 ⇒ TIGHT（能起，但挤）
    "fit.over_pct": 95,        # 超过这个比例 ⇒ OVER（拒绝启动）
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


MASK = "****"


def is_secret(key, defs):
    # type: (str, dict) -> bool
    """这个键是不是机密（`schema.tsv#secret`）。"""
    d = defs.get(key)
    return bool(d) and d.get("secret") == "yes"


def masked(values, defs):
    # type: (dict, dict) -> dict
    """secret 键的**非空**值替换为 `****`。

    空值原样返回：「未设置」本身不是秘密，而把它也打成 `****` 会让用户以为已经设过。
    """
    return {k: (MASK if is_secret(k, defs) and v not in (None, "") else v)
            for k, v in values.items()}


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


def domain_errors(cfg, defs):
    # type: (object, dict) -> dict
    """合成后配置的**值域判定，全项目唯一实现点** → `{键: 原因}`（空 ⇒ 全部合法）。

    `svc._v_config`（`up` 的求值点）与 `config validate` 共用：此前值域检查只在前者，
    validate 只在 `effective` 抛错时才报 failed —— 而那条分支对表内键不可达，于是
    `settings.json` 里 `max_tokens: 0` 时 validate 报 ok、`up` 报非法（终审 C I2）。
    出厂默认来源的键不判（出厂值由表保证）。
    """
    out = {}
    for k in sorted(cfg.values):
        if cfg.sources.get(k) == "default":
            continue
        bad = check_domain(k, cfg.values[k], defs)
        if bad:
            out[k] = bad
    return out


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
    if dom == "#ip":
        # IP 字面量（v4 或 v6）。主机名不收：绑定地址写主机名会随解析结果漂移
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return "不是 IP 地址字面量"
        return None
    if dom == "#cidrs":
        for raw_part in value.split(","):
            part = raw_part.strip()
            if not part:
                continue
            try:
                ipaddress.ip_network(part, strict=False)
            except ValueError:
                return f"不是网段：{part}"
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


def build_layers(defs, settings, is_model, rec_user=None, registered_port=None,
                 gateway_port=None, gateway_token_env="", runtime=True):
    # type: (dict, dict, bool, object, object, object, str, bool) -> tuple
    """`effective` 的输入层 —— **全项目唯一的层构造点**（纯函数，不读盘）。

    `svc._v_config`（`up` 的求值点）与 `config get/ls/validate/diff` 此前各有一份
    层构造：读侧只按「键在不在配置域」筛，缺登记端口、缺网关的 cli 层、不看
    `level` 列 —— 同一个键两个命令两个答案（`config get <模型> --key=port` 报
    8100，`up` 用登记端口）。两份实现必然漂移，所以合并成这一份。

    层序（低→高）：出厂默认 < machine < user < cli。
      · machine = settings 里 scope==user 且 level∈(global,both) 的键；
                  模型目标**不吃全局 `port`**，改用注册表登记端口
      · user    = 模型记录 user 段里 scope==user 且 level∈(model,both) 的键
      · cli     = 仅网关（`is_model` 假）且 `runtime`：`ctx.gateway_port`，
                  以及 shell 里显式给的 `MLX_GATEWAY_TOKEN`（→ `api.token`）。
                  `runtime=False` 给 set/unset 回读与 export：它们说的是**落盘状态**，
                  不该把进程环境当成用户设过的值。
    返回 `(layers, dropped)`；`dropped` = 被忽略的表外键（已退役的键）。
    """
    dropped = sorted(k for k in settings if k not in defs)
    machine = {k: v for k, v in settings.items()
               if k in defs and defs[k]["scope"] == "user"
               and defs[k]["level"] in ("global", "both")
               and not (is_model and k == "port")}
    layers = {}
    if not is_model:
        if runtime:
            layers["cli"] = {}
            if gateway_port is not None:
                layers["cli"]["port"] = gateway_port
            if gateway_token_env:
                layers["cli"]["api.token"] = gateway_token_env
    else:
        if registered_port is not None:
            machine["port"] = int(registered_port)
        user = {}
        for k in list(rec_user or {}):
            d = defs.get(k)
            if d is None:
                if k not in ("repo", "type"):       # 非配置字段（repo）与旧字段（type）
                    dropped.append(k)
                continue
            if d["scope"] == "user" and d["level"] in ("model", "both"):
                user[k] = rec_user[k]
        if user:
            layers["user"] = user
    if machine:
        layers["machine"] = machine
    return layers, sorted(set(dropped))


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
