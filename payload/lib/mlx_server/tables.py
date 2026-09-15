"""B 层 · `share/*.tsv` 的**唯一读取者**与只读访问器。表根经 `ctx` 注入。

`msg("KEY") -> MessageKey` 是 `MessageKey` 的**唯一构造点**（INV-24 的类型强制）。
"""
import os


class MessageKey(str):
    """文案键。`ui.*` 只接受它，传裸 str 是类型错误。"""

    __slots__ = ()


class TableError(Exception):
    """表缺失或行格式非法。"""


def _read_tsv(path, columns):
    # type: (str, tuple) -> list
    if not os.path.exists(path):
        raise TableError(f"表不存在：{path}")
    rows = []
    with open(path) as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            cells = line.split("\t")
            if len(cells) != len(columns):
                raise TableError(
                    f"{path}:{lineno} 列数 {len(cells)} ≠ 表头 {len(columns)}：{line[:60]}"
                )
            rows.append(dict(zip(columns, cells)))
    return rows


class TablesView:
    """只读访问器。构造一次，之后不再碰盘。"""

    __slots__ = ("_root", "_messages", "_manifest", "_verbs", "_cli",
                 "_schema", "_drafters", "_backends")

    COLUMNS = {
        "messages": ("key", "text"),
        "manifest": ("path", "kind", "on_uninstall"),
        # 16 列，逐字按 `02-architecture.md` SSOT 表第 14 行
        "verbs": ("verb", "parent", "impl", "aliases", "positionals",
                  "stage_split", "exit_codes", "stdout_主产物",
                  "machine_readable", "empty_result", "stderr_过程流",
                  "creates_resource", "effect_read_path", "visibility",
                  "doc_anchor", "completion"),
        # 8 列，逐字按 SSOT 表第 13 行（PROD-07）
        "cli": ("flag", "owner_verb", "takes_value", "value_domain",
                "mutex_group", "default", "intent_id", "help_key"),
        # 11 列，逐字按 `04-iface-P.md:86` 的 `KeyDef`
        "schema": ("key", "type", "domain", "default", "scope", "level",
                   "restart", "exportable", "secret", "group", "readers"),
        "drafters": ("model_type",),
        "backends": ("backend", "module", "host", "offline", "concurrency_args",
                     "draft_args", "thinking", "ready", "key_env", "env_clear"),
    }

    def __init__(self, tables_root):
        # type: (str) -> None
        self._root = tables_root
        self._messages = None
        self._verbs = None
        self._cli = None
        self._manifest = None
        self._schema = None
        self._drafters = None
        self._backends = None

    def _load(self, name):
        p = os.path.join(self._root, f"{name}.tsv")
        return _read_tsv(p, self.COLUMNS[name])

    def msg(self, key):
        # type: (str) -> MessageKey
        """**`MessageKey` 的唯一构造点**。键不在表里即抛 —— 不返回键本身。"""
        if self._messages is None:
            self._messages = {r["key"]: r["text"] for r in self._load("messages")}
        if key not in self._messages:
            raise TableError(f"messages.tsv 里没有键 {key!r}")
        return MessageKey(key)

    def text(self, message_key, **args):
        # type: (MessageKey, object) -> str
        """把键渲染成文案。**渲染是这里的职责，`ui` 只负责写流。**

        第一个形参**不能叫 `key`**：文案的参数里就有叫 `key` 的
        （`config.unknown_key` 的 `{key}`），同名会撞成
        `TypeError: got multiple values for argument 'key'` ⇒
        那条文案永远渲染不出来，用户看到的是裸键。实测踩过。
        """
        if self._messages is None:
            self._messages = {r["key"]: r["text"] for r in self._load("messages")}
        # TSV 是**逐行**格式，多行文案只能写成字面的 `\n` 再在此还原 ——
        # help 的「说明」「例子」两段需要真换行（man 的段落结构）。
        text = self._messages[str(message_key)].replace("\\n", "\n")
        return text.format(**args)

    def verbs(self):
        # type: () -> list
        """动词台账（PROD-08）。**解析 / 分派 / help 三处共用这一份。**

        每行是 dict：`command` / `subcommand` / `impl` / `positionals` / `visibility`。
        顶层动词的 `subcommand` 是 `-`。
        """
        if self._verbs is None:
            self._verbs = self._load("verbs")
        return self._verbs

    def cli(self):
        # type: () -> list
        """参数面声明表（PROD-07）。**解析器、help、补全三处都从它生成。**

        设计（R-OBSERVE-32，P0）：「每个命令的参数面用声明表表达
        （flag 名 / 是否带值 / 互斥组 / 默认值 / 一行说明），解析器、补全、
        帮助三者都从该表生成」，验收是「三处均由该表生成，**不存在手抄的第二份**」。
        """
        if self._cli is None:
            self._cli = self._load("cli")
        return self._cli

    def manifest(self):
        # type: () -> list
        if self._manifest is None:
            self._manifest = self._load("manifest")
        return list(self._manifest)

    def schema_keys(self):
        # type: () -> list
        """配置键定义（`schema.tsv` 的十一列）。**唯一读点。**"""
        if self._schema is None:
            self._schema = self._load("schema")
        return self._schema

    def drafters(self):
        # type: () -> list
        """附属 drafter 的 model_type 集合（`drafters.tsv`，spec §2.3）。"""
        if self._drafters is None:
            self._drafters = self._load("drafters")
        return self._drafters

    def backends(self):
        # type: () -> list
        """后端定义（`backends.tsv`，spec §3.1）。`backendarg` 的 argv 全部来自这里。"""
        if self._backends is None:
            self._backends = self._load("backends")
        return self._backends


def open_tables(ctx):
    # type: (object) -> TablesView
    """装配入口。表根经 `ctx` 注入 —— `tables` 自己不决定去哪读。"""
    from .ctx import share_of
    return TablesView(share_of(ctx))
