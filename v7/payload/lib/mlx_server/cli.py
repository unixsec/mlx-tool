"""E 层 · argv 解析。**只解析，不判定** —— 值域校验在 P 层。

**表驱动**：动词面来自 `share/verbs.tsv`（PROD-08），不在本文件硬编码。
v6 有 11 处手写解析循环、4 份各自维护的命令集合，两条守卫会对同一个动词
给出相反结论；本模块与 `entry` 的分派、`help` 的渲染读的是**同一张表**。
"""
from .cmd.svc import Invocation

# **flag 面来自 `cli.tsv`，本文件不再持有第二份**（R-OBSERVE-32 的验收：
# 「解析器、补全、帮助三处均由该表生成，不存在手抄的第二份」）。


class ParseError(Exception):
    """argv 形态不合法。`code` 由 `entry` 映射成 rc。"""

    def __init__(self, message, code=2):
        # type: (str, int) -> None
        super().__init__(message)
        self.code = code


def _index(verb_rows):
    # type: (list) -> tuple
    """台账 → 两张索引：顶层动词、`(父动词 → {子动词: 行})`。

    列名按产物定义（`02` SSOT 表第 14 行）：`verb` 是动词名，
    `parent` 是父动词，顶层动词此列为空。
    """
    top, groups = {}, {}
    for r in verb_rows:
        if not r["parent"]:
            top[r["verb"]] = r
        else:
            groups.setdefault(r["parent"], {})[r["verb"]] = r
    return top, groups


def _flags_for(cli_rows, path):
    # type: (list, str) -> dict
    """该命令可用的 flag → 行。`owner_verb` 为 `*` 即对全部动词有效。"""
    out = {}
    for r in cli_rows or []:
        owners = [o.strip() for o in r["owner_verb"].split(",")]
        if "*" in owners or path in owners or (path.split()[0] if path else "") in owners:
            out[r["flag"]] = r
    return out


def usage(verb_rows, tables=None):
    # type: (list, object) -> str
    """一屏速查。**只渲染 `visibility == public` 的行**（BC-D-03）。"""
    top, groups = _index(verb_rows)
    pub_top = sorted(k for k, r in top.items() if r["visibility"] == "public")
    lines = ["usage: mlx <命令> [<参数>] [--flag]", "",
             "  工具自身  " + " · ".join(v for v in
                                     ["install", "uninstall", "status", "check",
                                      "version", "help"] if v in pub_top)]
    svc = [v for v in ["up", "down", "restart"] if v in pub_top]
    if svc:
        lines.append("  服务      " + " · ".join(svc) + " <id>")
    for g in sorted(groups):
        subs = sorted(s for s, r in groups[g].items() if r["visibility"] == "public")
        # internal 组整组不出现（BC-D-03：不进 help、不进补全、不进用户指南）
        if subs:
            lines.append(f"  {g:<9} " + " · ".join(subs))
    other = [v for v in pub_top if v not in
             ("install", "uninstall", "status", "check", "version", "help",
              "up", "down", "restart")]
    if other:
        lines.append("  其他      " + " · ".join(sorted(other)))
    # **带值的 flag 一律 `--k=v`**（解析器只认这一种，见 337-341 的缺值分支）。
    # 这句话此前全工具没有一个地方说过：顶层 help、man 页的用法行、未知参数
    # 提示三处都印裸 `--flag`，只有 `mlx help <命令>` 的参数段隐含着形态。
    # 于是用户按 GNU 习惯敲 `--repo x` 撞一次 rc 2 才学会 ——
    # 2026-09-02 Alex 就是这样撞的，而那条命令还是我给他的。
    lines += ["", "  带值的参数写成 --key=value（如 --repo=mlx-community/xxx）",
              "  mlx help <命令>   看单条详解"]
    return "\n".join(lines) + "\n"


def is_help_request(argv):
    # type: (list) -> bool
    """`mlx help [...]` 或任意位置的 `--help` / `-h`。

    **两个入口，一个渲染实现点**（`02` §4.1.2）：`--help` 是解析第一阶段短路，
    `help` 是登记在册的独立动词。它们不是别名关系 —— `help` 有自己的 rc 集合
    与空结果表现（未知动词 → rc 2 并列出全集），`--help` 恒 0。
    """
    rest = argv[1:]
    return bool(rest) and (rest[0] == "help" or "--help" in rest or "-h" in rest)


def render_help(argv, verb_rows, cli_rows=None, tables=None):
    # type: (list, list, list, object) -> tuple
    """→ `(文本, rc)`。**走 stdout，stderr 必须为空**（R-OBSERVE-34）。"""
    rest = [a for a in argv[1:] if not a.startswith("-")]
    if rest and rest[0] == "help":
        rest = rest[1:]
    if not rest:
        return usage(verb_rows), 0

    top, groups = _index(verb_rows)
    head = rest[0]
    sub = rest[1] if len(rest) > 1 else None

    if head in groups and sub:
        # **子命令长匹配**，与 `parse` 同一规则：动词名可含空格（`trash ls`）。
        # 不做长匹配的话 `mlx help model trash ls` 会拿 `trash` 去查表、
        # 查不到、报「没有这个命令」—— 而它明明在表里、也真的能跑。
        two = " ".join(rest[1:3]) if len(rest) > 2 else None
        row = (groups[head].get(two) if two and two in groups[head]
               else groups[head].get(sub))
    elif head in groups:
        subs = [r for s, r in sorted(groups[head].items())
                if r["visibility"] == "public"]
        body = [f"mlx {head} <子命令>", ""]
        body += [f"  {r['verb']:<10} {_summary(r, tables)}" for r in subs]
        return "\n".join(body) + "\n", 0
    else:
        row = top.get(head)

    if row is None or row["visibility"] != "public":
        # **空结果表现**：未知动词不是 0 —— 用户拼错时必须拿到非零码
        names = sorted([k for k, r in top.items() if r["visibility"] == "public"]
                       + [g for g, rows_ in groups.items()
                          if any(r["visibility"] == "public" for r in rows_.values())])
        return (f"没有这个命令：{' '.join(rest)!r}\n"
                f"可用：{' · '.join(names)}\n"), 2

    return _man_page(row, cli_rows, tables, verb_rows), 0


# 退出码词汇表（`02` §4.3）。help 的「退出码」段把登记的数字翻译成人话 ——
# 光给一串数字等于让用户去翻文档。
_RC_MEANING = {
    0: "成功", 1: "执行段失败（可能已有资源落地，看 stderr 的「已落地」行）",
    2: "参数错（未知命令 / 元数不符 / 未知 flag）",
    3: "外部依赖不可用（网络 / venv / 外部命令缺失 / 无法启动）",
    4: "有界等待走完仍未达成（就绪 / 终止确认）",
    5: "用户取消、确认串失配或中断（Ctrl-C）",
    6: "前置拒绝（校验段判定不通过，**零资源创建**）",
    7: "资源冲突（端口 / 唯一性 / 锁超时）",
    8: "供应链闸门阻断",
    9: "要执行动作时判定不确定 → 拒绝执行（fail-closed）",
    10: "服务启动后立即退出（进程在就绪判定成立前非零退出）",
    64: "聚合结果：含告警", 65: "聚合结果：含失败",
    70: "部署硬门 1", 71: "部署硬门 2", 72: "部署硬门 3",
    73: "部署硬门 4", 74: "部署硬门 5",
}


# 显示宽度只有**一份**实现（INV-23）。这里曾另写一份，理由是
# 「E 层的解析面不依赖 B 层的渲染原语」—— 那个理由不成立：
# INV-14 的层序是「跨层只向下」，而 E→B 正是向下。
# 代价是真实的：两份同样的东西，任一处漏掉全角判断就出现 CJK 列错位，
# 而那正是 v6 的老症状。
def _w(text):
    # type: (str) -> int
    from . import ui
    return ui.display_width(str(text))


def _pad(text, n):
    # type: (str, int) -> str
    from . import ui
    return ui.pad(str(text), n)


def _man_page(row, cli_rows, tables, verb_rows):
    # type: (dict, list, object, list) -> str
    """单条命令的详解，**man 的段落结构**：
    名称 / 用法 / 说明 / 参数 / 退出码 / 例子 / 参见。

    每一段的数据都来自已有的两张表与文案表，没有第四处事实：
    用法与元数来自 `verbs.tsv#positionals`，参数来自 `cli.tsv`，
    退出码来自 `verbs.tsv#exit_codes` 再经 §4.3 的词汇表翻译，
    说明与例子来自 `messages.tsv`（「help 文本里不出现裸文案字面量」）。
    """
    path = _path(row)
    arity = {"0": "", "?": " [<目标>]", "1": " <目标>", "+": " <目标>..."}[
        row["positionals"]]
    flags = _flags_for(cli_rows, path)

    out = ["名称", f"    mlx {path} —— {_summary(row, tables)}", "",
           "用法", f"    mlx {path}{arity}"
           + ("".join(f" [{f}]" for f in sorted(flags)) if flags else ""), ""]

    long = _msg(tables, f"help.{path.replace(' ', '.')}.long")
    if long:
        out += ["说明"] + [f"    {ln}" for ln in long.split("\n")] + [""]

    if flags:
        out.append("参数")
        w = max(_w(f + ("=" + flags[f]["value_domain"]
                        if flags[f]["takes_value"] == "yes" else "")) for f in flags)
        for name in sorted(flags):
            r = flags[name]
            head = name + (f"={r['value_domain']}" if r["takes_value"] == "yes" else "")
            out.append(f"    {_pad(head, w)}   {_flag_help(r, tables)}")
        out.append("")

    codes = [int(c) for c in row["exit_codes"].split(",") if c.strip().isdigit()]
    if codes:
        out.append("退出码")
        out += [f"    {c:<4}{_RC_MEANING.get(c, '（未登记含义）')}" for c in sorted(codes)]
        out.append("")

    ex = _msg(tables, f"help.{path.replace(' ', '.')}.example")
    if ex:
        out += ["例子"] + [f"    {ln}" for ln in ex.split("\n")] + [""]

    # 参见：同组的兄弟命令 + 顶层的相邻动词
    top, groups = _index(verb_rows or [])
    if row["parent"]:
        sib = sorted(f"{row['parent']} {v}" for v, r in groups[row["parent"]].items()
                     if r["visibility"] == "public" and v != row["verb"])
    else:
        sib = sorted(v for v, r in top.items()
                     if r["visibility"] == "public" and v != row["verb"])[:6]
    if sib:
        out += ["参见", "    " + " · ".join(f"mlx {x}" for x in sib), ""]

    if row.get("doc_anchor"):
        out.append(f"文档  {row['doc_anchor']}")
    return "\n".join(out).rstrip() + "\n"


def _msg(tables, key):
    # type: (object, str) -> str
    if tables is None:
        return ""
    try:
        return tables.text(tables.msg(key))
    except Exception:                              # noqa: BLE001
        return ""


def _summary(row, tables):
    # type: (dict, object) -> str
    """动词的一行说明。**取自 `messages.tsv`，不在表里存裸文案**
    （`02:1060` 对 help_key 的要求，动词说明同理）。"""
    key = "help." + _path(row).replace(" ", ".")
    if tables is not None:
        try:
            return tables.text(tables.msg(key))
        except Exception:                              # noqa: BLE001
            pass
    return row.get("stdout_主产物", "")[:60]


def _flag_help(row, tables):
    # type: (dict, object) -> str
    if tables is not None and row.get("help_key"):
        try:
            return tables.text(tables.msg(row["help_key"]))
        except Exception:                              # noqa: BLE001
            pass
    return row.get("intent_id", "")


# `cli.tsv#value_domain` 的**形态**校验。`#name` 形态引用一张表，
# `<name>` 形态是自由文本占位。此前这一列**零消费者** —— 于是
# `--port=abc` / `--port=-1` 一路 rc 0 落盘，`models.jsonl` 里躺着 `"port": "abc"`。
# 这里只做形态校验（是不是整数、非空）；集合归属留给 D 层（它才有 ctx）。
def _domain_error(domain, value):
    # type: (str, str) -> str
    if domain in ("", "-", None):
        return ""
    if value == "":
        return "不能为空"
    if domain == "#port_range":
        if not value.isdigit():
            return "端口必须是数字"
        if not (1 <= int(value) <= 65535):
            return "端口要在 1–65535 之间"
    return ""


def parse(argv, verb_rows, cli_rows=None):
    # type: (list, list, list) -> Invocation
    """`argv[1:]` → `Invocation`。未知动词与元数不符都抛 `ParseError(2)`。

    **消歧靠命令路径，不靠有无位置参数**：`install` 是顶层行、`model install`
    是 `model` 组下的行，两条路径不同，无歧义可消。写成「带位置参数就转
    model install」会让 `mlx install qwen` 静默开始下载模型。
    """
    top, groups = _index(verb_rows)
    args_in = list(argv[1:])
    if not args_in:
        raise ParseError("缺少命令。`mlx help` 看全部命令")
    head, rest = args_in[0], args_in[1:]

    if head not in top and head not in groups:
        raise ParseError(
            f"未知命令 {head!r}。`mlx help` 看全部命令")

    # ── flag 解析（在选组之前做，`mlx model ls --json` 的 --json 不是子动词）──
    dry_run = "--dry-run" in rest
    _by_flag = {r["flag"]: r for r in (cli_rows or [])}
    flags, unknown = {}, []
    missing_value, wrong_owner = [], []

    # **归属校验要用完整动词路径**，所以先算一次（选组在下面，这里只是预读）。
    # 与 `parse` 的选组同一规则：子命令长匹配（动词名可含空格，如 `trash ls`）。
    _pos = [a for a in rest if not a.startswith("--")]
    if head in groups and _pos:
        _two = " ".join(_pos[:2]) if len(_pos) >= 2 else None
        _sub = _two if (_two and _two in groups[head]) else _pos[0]
        _full = f"{head} {_sub}"
    else:
        _full = head

    def _owns(row):
        """这个 flag 归不归当前动词。`*` 表示对全部动词有效。"""
        if row["owner_verb"] == "*":
            return True
        owners = [x.strip() for x in row["owner_verb"].split(",")]
        # 顶层动词（`mlx up`）在表里可能只写 `up`；分组动词写全路径
        return _full in owners or head in owners
    for a in rest:
        if not a.startswith("--"):
            continue
        body = a[2:]
        # **`--k=v` 也必须查表。** 此前这一支直接入 dict、完全不校验 ——
        # 于是 `--live=1`、`--tail=50`、`--jsno=1` 全部 rc 0 被静默吞掉
        # （用户以为生效了），而撞上内部键名的 `--layers=x` 直接把解释器
        # 打出 traceback + rc 1。未知参数拒绝这一阶段对整个 `--k=v` 形态失效。
        name, has_eq, value = body.partition("=")
        row = _by_flag.get("--" + name)
        if row is None:
            unknown.append(a)
            continue
        if not _owns(row):
            # **归属校验**（`cli.tsv#owner_verb`）。此前这一列只用来渲染
            # 「该命令可用」的提示，不参与拒绝 —— 于是 `mlx version --repo=x`、
            # `mlx model ls --weights` 一律 rc 0 被静默接受。
            # 对 `--yes` 这类**削弱确认**的 flag，归属失守意味着
            # `mlx model trash empty --yes` 会跳过那个唯一不可恢复动作的确认。
            wrong_owner.append((a, row["owner_verb"]))
            continue
        takes = row["takes_value"] == "yes"
        if takes and not has_eq:
            # **缺值检查**：声明 takes_value=yes 的 flag 裸给是参数错。
            # 此前 `mlx config set backend`（没给 --value）返回 rc 0
            # 并往注册表里插了一条 id 为 `backend` 的幽灵模型。
            missing_value.append(a)
            continue
        if not takes:
            if has_eq:
                unknown.append(a + "（这个参数不接受值）")
                continue
            if name != "dry-run":
                flags[name.replace("-", "_")] = True
            continue
        bad = _domain_error(row["value_domain"], value)
        if bad:
            unknown.append(f"{a}（{bad}）")
            continue
        flags[name.replace("-", "_")] = value
    if wrong_owner:
        a, owners = wrong_owner[0]
        raise ParseError(f"{a} 不是 `mlx {_full}` 的参数（它属于：{owners}）")
    if missing_value:
        raise ParseError(
            f"{missing_value[0]} 需要一个值，写成 {missing_value[0]}=<值>")
    if unknown:
        # **直接列出该命令的合法 flag**，不让用户去 help 页做第二跳 ——
        # 那一跳曾经指向一个零参数的页面（cli.tsv 缺失时）。
        #
        # 匹配用**完整动词名**。此前是 `head in r["owner_verb"]` 的子串匹配：
        # head 是顶层动词 `model`，于是 `"model" in "model register,model install"`
        # 为真 —— `mlx model ls --live` 的提示会列出 `--repo --type --port --weights`，
        # 对 `model ls` 一个都不合法。这一跳存在的理由就是「第一跳给对」。
        _pos = [a for a in rest if not a.startswith("--")]
        full = f"{head} {_pos[0]}" if (head in groups and _pos) else head
        owners = lambda r: [x.strip() for x in r["owner_verb"].split(",")]   # noqa: E731
        avail = sorted(f for f, r in _by_flag.items()
                       if r["owner_verb"] == "*" or full in owners(r))
        tail = ("；该命令可用：" + " ".join(avail)) if avail else ""
        raise ParseError(f"未知参数 {unknown[0]}{tail}")
    positional = [a for a in rest if not a.startswith("--")]

    # ── 选行：分组动词吃掉第一个位置参数作为子动词 ──
    if head in groups:
        if not positional:
            subs = sorted(s for s, r in groups[head].items()
                          if r["visibility"] == "public")
            raise ParseError(f"{head} 需要一个子命令：{' · '.join(subs)}")
        # **子命令长匹配**：设计台账里有 `trash ls` / `trash restore` 这种
        # **含空格的动词名**（`03b-ledgers.md:196-198`，parent 仍是 `model`）。
        # 先试两个词，不中再退回一个词 —— 顺序反了的话 `trash` 会先命中
        # 一个不存在的单词动词，用户拿到「没有子命令 trash」。
        two = " ".join(positional[:2]) if len(positional) >= 2 else None
        if two and two in groups[head]:
            sub, positional = two, positional[2:]
        else:
            sub, positional = positional[0], positional[1:]
        row = groups[head].get(sub)
        if row is None:
            subs = sorted(s for s, r in groups[head].items()
                          if r["visibility"] == "public")
            raise ParseError(
                f"{head} 没有子命令 {sub!r}；可用：{' · '.join(subs)}")
        verb, group = sub, head
    else:
        row, verb, group = top[head], head, None

    # ── 元数校验。**这是校验，不是消歧** ──
    # 元数值域：`0` 不接 · `?` 可选（0 或 1）· `1` 必须一个 · `+` 一个以上
    arity = row["positionals"]
    if arity == "0" and positional:
        raise ParseError(_misuse_hint(head, positional[0]))
    if arity == "?" and len(positional) > 1:
        raise ParseError(f"{_path(row)} 最多接一个目标，多给了 {positional[1]!r}")
    if arity in ("1", "+") and not positional and not flags.get("all"):
        raise ParseError(f"{_path(row)} 需要一个目标（或 --all）")
    if arity == "1" and len(positional) > 1:
        # **元数 1 的上界**。不补这条，多给的 token 会被静默吞掉；
        # 而把 up/down 放宽到 `+` 之后，静默吞掉会变成「把它当第二个目标去启动」。
        raise ParseError(
            f"{_path(row)} 只接一个目标，多给了 {positional[1]!r}")

    target = positional[0] if positional else None
    targets = tuple(positional)
    if group:
        flags["_group"] = group
        if "key" not in flags and positional:
            flags["key"] = positional[0]
    return Invocation(verb, target, args=flags, dry_run=dry_run, targets=targets)


def _path(row):
    # type: (dict) -> str
    return row["verb"] if not row["parent"] \
        else f"{row['parent']} {row['verb']}"


def _misuse_hint(head, extra):
    # type: (str, str) -> str
    """元数为 0 却给了位置参数 —— **这类误用必须给出确切的正确写法**。

    `mlx install qwen` 与 `mlx uninstall qwen` 在英文语感上都像「对 qwen 做事」，
    而正确写法连动词都不同（`model install` / `model rm`），用户猜不到。
    """
    redirect = {"install": f"mlx model install {extra}",
                "uninstall": f"mlx model rm {extra}"}
    hint = redirect.get(head)
    if hint:
        return (f"`mlx {head}` 管的是**工具自身**，不接目标。"
                f"要对模型 {extra!r} 操作，用：{hint}")
    return f"{head} 不接位置参数，多给了 {extra!r}"
