"""文档与文案里写出的每一条 `mlx …` 命令都必须能过 CLI 解析器（docs-refresh X1 的类级守卫）。

立在一次现行犯上：`mlx config get --key=api.token --reveal` 写进了两份 README、手册七处、
CHANGELOG，还写进了产品自己的输出（`bootstrap keys` 收尾提示、安装器、`help bootstrap keys`）。
`config get` 的位置参数元数是 `1`，缺了它 rc 2 —— **用户照抄我们印出来的命令就报错**。
守卫当时反而在保它：`test_docs_current.py` 断言手册含这个字面串，L3 用例跑了这条命令却不查 rc。

逐条修字面串只修得了这一个实例；这里守的是整个类：「印给用户照抄的 `mlx` 命令」。

**提取面**（`_collect()`，来源清单见 `_sources()`）：
- 两份 README、`docs/*.md`、CHANGELOG 里的代码块与行内代码；
- `messages.tsv` 的**全部正文**，按纯文本抽（不只反引号片段：`先起它：mlx up {service}` 这种也是照抄对象）；
- `deployer/*.sh` 里 `say` / `echo` 印给用户的文本（安装器的收尾提示就在这里）；
- `payload/**/*.py` 的字符串字面量（`ast` 取常量与 f-string，跳过 docstring）中，
  `mlx` 后紧跟**已登记动词**（取自 `verbs.tsv`）的片段 —— 渲染器、提示文案里拼出来的命令。
每个来源都断言抽到了东西（`test_every_source_contributes`），提取面悄悄失效会红。
**求值面**：只走解析器 —— `cli.parse`（`entry.main` 解析段的同一个入口；`help` 走
`cli.render_help`，与 `entry.main` 的短路一致）。不执行动词。经下限解释器调起
（`floor.run_floor`），不在测试自己的 venv 里 import 产品模块。

**跳过的东西与理由**（每一类都显式计数，见 `test_doc_command_examples_parse`）：
- 裸 `mlx`（只是工具名，不是命令）；
- **不带任何 flag** 且只缺目标 / 子命令的（行文里「`mlx up` 起完进程……」、help「参见」行、
  目录图注释里的 `mlx chat`，都是在指称动词，不是让人照敲）。带了 flag 的一律不豁免 ——
  X1 那条正是「带 flag、缺目标」；
- help 的「用法」行（含 `[--`）：方括号是语法记号；
- 第一个词不以小写 ASCII 字母开头的（`mlx ≥ 0.30.4` 说的是 mlx 这个 Python 包；`mlx 找不到` 是中文行文）；
- 文案里动词本身是模板占位的（`mlx {verb}`）；
- `_COUNTEREXAMPLES`：文档**有意**写出的错误命令（教学反例、未实现的命令）。
  它们反过来必须**解析失败** —— 哪天变得能解析了，名单就过时了，守卫同样红。
占位符（`<模型>`、`{id}` 等）按 `_PLACEHOLDERS` 显式替换；**不在表里的占位符直接红**，
逼新占位符的作者在这里做一次决定，而不是被静默放过。
"""

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cardinality import Cardinality  # noqa: E402
from floor import run_floor  # noqa: E402

# 占位符 → 解析器眼里等价的样例值。值只需满足 `cli.tsv#value_domain` 的形态（端口要是数字）。
_PLACEHOLDERS = {
    "<id>": "qwen38",
    "<ID>": "models--org--demo.1788226602363",
    "<模型>": "qwen38",
    "<模型id>": "qwen38",
    "<短名>": "qwen38",
    "<别的模型>": "llama8b",
    "<服务>": "qwen38",
    "<键>": "port",
    "<键名>": "api.token",
    "<值>": "8200",
    "<新值>": "abcdefghijklmnopqrstuvwxyz012345",
    "<新端口>": "8177",
    "<端口>": "8177",
    "<网段>": "192.168.8.0/24",
    "<证书>": "/tmp/gateway.crt",
    "<私钥>": "/tmp/gateway.key",
    "<命令>": "model",
    "<command>": "model",
    "<路径>": "./mlx-config.json",
    "<repo>": "mlx-community/Qwen3-0.6B-4bit",
    "<同一家族、同一词表的小模型仓库>": "mlx-community/Qwen3-0.6B-4bit",
    "<32 位以上的串>": "abcdefghijklmnopqrstuvwxyz012345",
    "{id}": "qwen38",
    "{model}": "qwen38",
    "{service}": "qwen38",
    "{draft}": "qwen38-small",
    "{repo}": "mlx-community/Qwen3-0.6B-4bit",
    "{key}": "port",
    "{example}": "qwen38",
    "{known_first}": "qwen38",
    "{extra}": "qwen",
    "{svc}": "qwen38",
    "{t}": "qwen38",
    "{s}": "qwen38",
    "{mid}": "qwen38",
    "{target}": "qwen38",
    "{inv.target}": "qwen38",
    "$s": "qwen38",
    "$PWD": "/tmp/certs",
}
# 文案模板里动词本身是占位：解析不出任何有意义的东西
_TEMPLATED_VERB = ("{verb}", "{action}", "{head}")

# 有意写出的错误命令（argv 去掉开头的 `mlx`，空格连接）。**必须解析失败**。
_COUNTEREXAMPLES = {
    # 教学反例：等号形态、误用顶层 install/uninstall、旧写法、不提供的搜索
    "model install qwen38 --repo mlx-community/Qwen3.8-27B-8bit",
    "install qwen",
    "uninstall qwen",
    "ls",
    "model search qwen",
    "model search",
    # KNOWN-ISSUES「未实现的命令」
    "logs",
    "bootstrap render",
    "model convert / quantize",
    "model upgrade / rollback",
}

_REPO = Path(__file__).resolve().parents[2]
_PAYLOAD = _REPO / "payload"
_CMD = re.compile(r"(?<![\w./~-])mlx(?=\s|$)")
_SPAN = re.compile(r"`([^`\n]+)`")
_OPS = set("();<>|&")


def _sources():
    """→ (markdown 文件, messages.tsv, deployer 脚本, 产品 .py)。"""
    md = [_REPO / "README.md", _REPO / "README_cn.md", _REPO / "CHANGELOG.md"]
    md += sorted((_REPO / "docs").glob("*.md"))
    sh = sorted((_REPO / "deployer").glob("*.sh"))
    py = sorted(p for p in _PAYLOAD.rglob("*.py") if "__pycache__" not in p.parts)
    return md, _PAYLOAD / "share" / "messages.tsv", sh, py


def _verbs():
    """`mlx` 后面能跟的第一个词：顶层动词与动词组名（`internal` 组不算）。"""
    out = set()
    for ln in (_PAYLOAD / "share" / "verbs.tsv").read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        c = ln.split("\t")
        if c[13] != "public":
            continue
        out.add(c[1] or c[0])
    return out


def _py_strings(path):
    """→ [(行号, 文本)]：字符串常量与 f-string（`{表达式}` 原样留作占位符），跳过 docstring。"""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            skip.add(id(body[0].value))
        if isinstance(node, ast.JoinedStr):
            skip.update(id(v) for v in node.values)
    out = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            text = "".join(v.value if isinstance(v, ast.Constant)
                           else "{" + ast.unparse(v.value) + "}" for v in node.values)
            out.append((node.lineno, text))
    return out


def _logical_lines(text):
    """反斜杠续行并成一行（缩进压成一个空格）。"""
    return re.sub(r"\\\n\s*", " ", text).splitlines()


def _commands(text):
    """一段文字里的每条 `mlx` 命令 → 原文片段（不含开头的 `mlx`）。

    在这些地方截断：反引号（代码块里粘贴的产品输出自带行内代码）、`·`（help 的「参见」行把
    几条命令连写）、`——`（help 的「名称」行）、三个以上连续空格（命令与行尾说明之间的对齐空白）、
    引号外的中文标点（见 `_cut_prose`）。
    注释与 shell 操作符留给 `_tokens` 截。
    """
    out = []
    for m in _CMD.finditer(text):
        tail = re.split(r"`|·|——|\s{3,}", text[m.end():], maxsplit=1)[0]
        out.append(_cut_prose(tail))
    return out


def _cut_prose(tail):
    """引号外遇到中文标点就截断：粘贴的产品输出里命令后面常紧跟「）；」（`查看：mlx … --reveal）；VPN…`）。
    引号与 `<占位符>` 内不截 —— `--prompt="…，…"` 的值里可以有中文标点。"""
    quote = None
    for i, ch in enumerate(tail):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "<":
            quote = ">"                       # 占位符 `<同一家族、同一词表…>` 里的标点同样不截
        elif ch in "（）；。，、」":
            return tail[:i]
    return tail


def _tokens(tail):
    lex = shlex.shlex(tail, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = "#"
    argv = []
    for tok in lex:
        if tok and set(tok) <= _OPS:
            break
        argv.append(tok)
    return argv


def _collect():
    """→ [(位置, 种类, 原文)]。种类：block（代码块 / help 例子）或 span（行内代码）。"""
    md, messages, sh, py = _sources()
    found = []
    for f in md:
        in_block = False
        block = []
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                if in_block:
                    for j, raw in _numbered_block(block):
                        for tail in _commands(re.sub(r"^\s*\$\s+", "", raw)):
                            found.append((f"{f.name}:{j}", "block", tail))
                    block = []
                in_block = not in_block
                continue
            if in_block:
                block.append((i, line))
                continue
            for span in _SPAN.findall(line):
                for tail in _commands(span):
                    found.append((f"{f.name}:{i}", "span", tail))
    for i, line in enumerate(messages.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("#") or "\t" not in line:
            continue
        key, text = line.split("\t", 1)
        for ln in _logical_lines(text.replace("\\n", "\n")):
            for tail in _commands(ln):
                found.append((f"messages.tsv:{i}({key})", "text", tail))
    for f in sh:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r'\b(?:say|echo)\s+"(.*)"', line)
            if m:
                for tail in _commands(m.group(1)):
                    found.append((f"deployer/{f.name}:{i}", "text", tail))
    verbs = _verbs()
    for f in py:
        rel = f.relative_to(_REPO)
        for i, text in _py_strings(f):
            for ln in _logical_lines(text):
                for tail in _commands(ln):
                    first = tail.split()[0] if tail.split() else ""
                    if first in verbs:
                        found.append((f"{rel}:{i}", "text", tail))
    return found


def _numbered_block(block):
    """代码块内的续行合并，保留每条逻辑行的起始行号。"""
    out, buf, start = [], "", None
    for i, line in block:
        if start is None:
            start = i
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        out.append((start, buf + (line.strip() if buf else line)))
        buf, start = "", None
    if buf:
        out.append((start, buf))
    return out


def _substitute(tail):
    """→ (替换后的原文, 未登记的占位符列表)。**先替换再分词**：`<` `>` 在 shell 里是重定向。"""
    for ph, val in _PLACEHOLDERS.items():
        tail = tail.replace(ph, val)
    return tail, re.findall(r"<[^<>\s]+>|\{[^{}\s]+\}|\$\{?\w+\}?", tail)


def _parse_all(payload, sys_py, tmp_path, cases):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return run_floor(payload, sys_py, """
        import json
        from mlx_server import cli
        from mlx_server.tables import TablesView
        tv = TablesView("@PAYLOAD@/share")
        verbs, rows = tv.verbs(), tv.cli()
        out = []
        with open("@CASES@", encoding="utf-8") as fh:
            cases = json.load(fh)
        for argv in cases:
            full = ["mlx"] + argv
            if cli.is_help_request(full):
                _text, rc = cli.render_help(full, verbs, rows, tv)
                out.append([rc == 0, "" if rc == 0 else "help rc %d" % rc])
                continue
            try:
                cli.parse(full, verbs, rows)
                out.append([True, ""])
            except cli.ParseError as e:
                out.append([False, str(e)])
        print(json.dumps(out, ensure_ascii=False))
    """, CASES=path)


def test_doc_command_examples_parse(payload, sys_py, tmp_path):
    found = _collect()
    Cardinality.require_nonempty("文档里的 mlx 命令示例", found,
                                 "提取面失效：一条 `mlx …` 都没抽到")
    anchor = [t for _, _, t in found if t.strip() == "config get api.token --reveal"]
    assert anchor, "提取面失效：手册里看密钥那条命令没被抽到"

    bad, cases, meta = [], [], []
    skipped = {"bare": 0, "templated": 0, "synopsis": 0, "not_cli": 0, "reference": 0}
    for where, kind, tail in found:
        if any(v in tail for v in _TEMPLATED_VERB):
            skipped["templated"] += 1
            continue
        if "[--" in tail or "[<" in tail:
            skipped["synopsis"] += 1          # help 的「用法」行：方括号是语法记号，不是参数
            continue
        sub, unknown = _substitute(tail)
        if unknown:
            bad.append(f"{where} `mlx {tail}` 有未登记的占位符 {unknown}（加进 _PLACEHOLDERS）")
            continue
        try:
            argv = _tokens(sub)
        except ValueError as e:
            bad.append(f"{where} `mlx {tail}` 分不了词：{e}")
            continue
        if not argv:
            skipped["bare"] += 1              # 只是工具名
            continue
        if not re.match(r"[a-z]", argv[0]):
            # `mlx ≥ 0.30.4`、`mlx / mlx-lm` 说的是 mlx 这个包；`mlx 找不到` 是中文行文。动词都是小写 ASCII
            skipped["not_cli"] += 1
            continue
        cases.append(argv)
        meta.append((where, kind, tail))

    results = _parse_all(payload, sys_py, tmp_path, cases)
    seen_counter = set()
    for (where, _kind, tail), argv, (ok, msg) in zip(meta, cases, results):
        key = " ".join(argv)
        if key in _COUNTEREXAMPLES:
            seen_counter.add(key)
            if ok:
                bad.append(f"{where} `mlx {key}` 在反例名单里却能解析 —— 名单过时了")
            continue
        if ok:
            continue
        no_flags = not any(a.startswith("--") for a in argv)
        if no_flags and ("需要一个目标" in msg or "需要一个子命令" in msg):
            skipped["reference"] += 1
            continue
        bad.append(f"{where} `mlx {tail.strip()}` → {msg}")

    Cardinality.check("解析失败的 mlx 命令示例", sorted(set(bad)), rel="empty")
    # 名单里每一条都要真在文档里出现过：删了反例却留着名单，名单就成了摆设
    Cardinality.check("反例名单里不再出现在文档中的条目",
                      sorted(_COUNTEREXAMPLES - seen_counter), rel="empty")
    assert len(cases) > 100, f"只抽到 {len(cases)} 条可解析示例，提取面可能失效（跳过：{skipped}）"


def _group(where):
    if where.startswith("payload/"):
        return "payload/**/*.py"
    if where.startswith("deployer/"):
        return where.split(":")[0]
    return where.split(":")[0].split("(")[0]


def test_every_source_contributes():
    """按来源逐个断言非空：某个来源的提取面悄悄失效（改了文件结构、正则不再命中），
    这条守卫会把它当成「这个来源里零个坏命令」而一直绿 —— 闭集断言的空真。"""
    md, _messages, sh, _py = _sources()
    got = {_group(w) for w, _, _ in _collect()}
    want = {f.name for f in md} | {"messages.tsv", "payload/**/*.py", "deployer/install.sh"}
    Cardinality.check("没有贡献任何 mlx 命令的来源", sorted(want - got), rel="empty")
    assert sh, "deployer/*.sh 一个都没找到"
    # 锚点：X1 现行犯所在的三类产品文本都要被抽到
    tails = {(_group(w), t.strip()) for w, _, t in _collect()}
    for src, cmd in (("deployer/install.sh", "config get api.token --reveal"),
                     ("payload/**/*.py", "config get <键名> --reveal"),
                     ("messages.tsv", "config get api.token --reveal")):
        assert (src, cmd) in tails, f"提取面失效：{src} 里的 `mlx {cmd}` 没被抽到"


def test_help_doc_anchors_exist_in_guide():
    """`mlx help <命令>` 末行印 `文档  USER-GUIDE.md#<锚点>`（`verbs.tsv#doc_anchor`）。
    锚点在手册里不存在，用户点过去落在文件开头。"""
    guide = (_REPO / "docs" / "USER-GUIDE.md").read_text(encoding="utf-8")
    have = set(re.findall(r"\{#([\w-]+)\}", guide)) | set(re.findall(r'<a id="([\w-]+)"', guide))
    want = set()
    for ln in (_PAYLOAD / "share" / "verbs.tsv").read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        c = ln.split("\t")
        if c[13] == "public" and "#" in c[14]:
            want.add(c[14].split("#", 1)[1])
    Cardinality.require_nonempty("help 引用的手册锚点", want, "verbs.tsv 的 doc_anchor 列没读到")
    Cardinality.check("help 引用但手册里没有的锚点", sorted(want - have), rel="empty")
