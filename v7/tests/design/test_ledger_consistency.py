"""LS-DESIGN · 设计台账的对账断言，**第一次绑上退出码**。

`03b-ledgers.md` §2.1 写了 8 条对账断言，全部以自然语言陈述、以人工核对为求值方式。
其中断言 1 自阶段 2 定稿以来**从未成立过、也从未被求值过**（实测差 8 恒定）——
「一个恒不触发的守卫和没有守卫在报告里长得一样」，而这句话是本项目自己写下的。

本文件把这些断言变成**可执行的**：设计文档是真实产物，pytest 的退出码是出口。
"""
import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cardinality import Cardinality  # noqa: E402

DOCS = Path(__file__).resolve().parents[3] / "docs" / "design" / "v7"
LEDGERS = DOCS / "03b-ledgers.md"
ARCH = DOCS / "02-architecture.md"

pytestmark = pytest.mark.design


def _section(text, start, end):
    return text[text.index(start):text.index(end)]


def _assert_contiguous(nums, what):
    """行号集合必须是 1..N 的连续区间。

    **这条自检是本项目花了一次误报换来的**：检查器的行号正则不认加粗数字，
    只解析到 `{1..20}` 而实际有 29 行，断裂处没有任何东西报警，
    于是后 9 行被当作「不存在」，凭空产出四个缺口编号并写进了台账。
    """
    if not nums:
        raise AssertionError(f"{what}: 一行都没解析到 —— 几乎必然是正则与表格形态不匹配")
    want = list(range(1, max(nums) + 1))
    missing = sorted(set(want) - set(nums))
    assert not missing, (
        f"{what}: 行号不连续，缺 {missing}（解析到 {len(nums)} 行，最大 {max(nums)}）——"
        f"通常意味着这些行的格式与正则不匹配，而不是它们真的不存在")


def _verbs_rows():
    """verbs.tsv 表体，返回 [(行号, command, subcommand, impl, visibility, creates), ...]"""
    sec = _section(LEDGERS.read_text(), "# §2 `verbs.tsv`", "## 2.1 本表的对账断言")
    out = []
    for line in sec.splitlines():
        # 行号可能被加粗（`| **23** |`）—— 不容忍强调符号会让整段行静默消失。
        # 本项目实测过这个后果：漏看 T3 第 21 行起的 9 行，**捏造**出四个不存在的缺口。
        if not re.match(r"^\| *\*{0,2}\d+\*{0,2} *\|", line):
            continue
        f = [c.strip() for c in line.split("|")]
        out.append((int(f[1].strip("*")), f[2].strip("`*"), f[3].strip("`*"),
                    f[4].strip("`*"), f[5].strip("*"), f[6]))
    _assert_contiguous([r[0] for r in out], "verbs.tsv 表体")
    return out


def _full_path(cmd, sub):
    """动词全路径：`model` + `install <repo>` → `model install`。"""
    if sub in ("—", "") or sub.startswith("["):
        return cmd          # `[<verb>]` 是可选位置参数，不是子命令
    # 去掉参数占位后保留**全部**动词 token —— 三级动词（`trash ls`）不能被截成两级
    toks = [t for t in re.sub(r"<[^>]*>|\[[^\]]*\]|=", " ", sub).split() if t]
    return " ".join([cmd] + toks)


def test_ls_design_01_row_count_matches_declared_baseline():
    """§2.1 断言 1 前半：表体行数 == §2 抬头声明的行数基准。

    错误实现能否通过：加一行忘了改抬头 ⇒ 红。
    """
    rows = _verbs_rows()
    head = _section(LEDGERS.read_text(), "# §2 `verbs.tsv`", "| # | command |")
    m = re.search(r"\*\*(\d+) 行 = (\d+) `public` \+ (\d+) `internal`\*\*", head)
    assert m, "§2 抬头找不到行数基准（形如「45 行 = 42 public + 3 internal」）"
    total, pub, intl = (int(g) for g in m.groups())

    assert len(rows) == total, f"表体 {len(rows)} 行，抬头声明 {total} 行"
    real_pub = sum(1 for r in rows if r[4] == "public")
    real_intl = sum(1 for r in rows if r[4] == "internal")
    assert (real_pub, real_intl) == (pub, intl), (
        f"实测 public={real_pub} internal={real_intl}，声明 public={pub} internal={intl}")
    assert real_pub + real_intl == total, "public + internal 之和不等于总行数"


def test_ls_design_03_impl_domain_is_D_layer_plus_cli():
    """§2.1 断言 3（BCR-2026-08-30 订正后）：impl ⊆ D 层 ∪ {cli}，且取 cli 的恰为两行。

    右侧钉死为 `{--help, help}`：任何第三个动词想借 cli 逃出 D 层编排，当场变红。
    """
    text = LEDGERS.read_text()
    mods = _section(text, "# §1 `modules.tsv`", "# §2 `verbs.tsv`")
    d_layer = {m.group(1) for m in re.finditer(r"^\| `([\w.]+)` \| D \|", mods, re.M)}
    assert d_layer, "modules.tsv 里没解析到 D 层模块"

    rows = _verbs_rows()
    for n, cmd, sub, impl, _vis, _ in rows:
        assert impl in d_layer | {"cli"}, (
            f"第 {n} 行 `{cmd} {sub}` 的 impl={impl!r} 既不在 D 层也不是 cli；"
            f"D 层有 {sorted(d_layer)}")

    cli_rows = {_full_path(c, s) for n, c, s, impl, _, _ in rows if impl == "cli"}
    assert cli_rows == {"任意动词 --help", "help"}, (
        f"取 cli 的行集合是 {cli_rows}，判据要求恰好是 {{--help, help}}")


def test_ls_design_01b_appendix_a_command_set_equals_public_verbs():
    """§2.1 断言 1 后半（订正版）：附录 A 展开后的命令集合 == public 行的动词全路径集合。

    这条自阶段 2 定稿以来从未被求值。原措辞是「行集合相等」，
    而附录 A 对同契约子命令合并渲染（`config get / ls / diff` 一行），
    行数恒差 8 ⇒ 字面上永不成立。订正为命令集合后本条才有意义。
    """
    rows = _verbs_rows()
    want = {_full_path(c, s) for _, c, s, _, vis, _ in rows if vis == "public"}

    a = ARCH.read_text()
    st = a.index("# 附录 A ·")
    nxt = a.find("\n# ", st + 10)
    appx = a[st: nxt if nxt != -1 else len(a)]

    got = set()
    for line in appx.splitlines():
        if not line.startswith("|") or line.startswith("| 命令") or set(line) <= set("|- "):
            continue
        cell = line.split("|")[1].strip()
        if not cell:
            continue
        # 去掉 flag、位置参数占位与省略号，只留动词骨架
        cell = re.sub(r"\[[^\]]*\]|<[^>]*>|\.\.\.", " ", cell)
        cell = cell.replace("`", "").replace("*", "").strip()
        if not cell:
            continue
        # 合并行：首段带组名，后续段省略组名并继承之（断言 1b 的配套约束）
        parts = [p.strip() for p in re.split(r"\s*/\s*", cell) if p.strip()]
        head = parts[0].split()
        # 三级动词（`model trash ls/restore/empty`）的组名是前两个 token
        assert len(head) <= 3, (
            f"附录 A 的命令格首段有 {len(head)} 个记号，超出三级动词上限: {parts[0]!r}。"
            f"静默截断会让多余记号不参与集合比较 ⇒ 写错也变绿（实证漏网）")
        depth = min(len(head), 3) - 1 if len(head) >= 2 else 0
        group = " ".join(head[:depth]) if depth else head[0]
        got.add(" ".join(head[:depth + 1]) if depth else head[0])
        for p in parts[1:]:
            tok = p.split()
            if not tok:
                continue
            got.add(f"{group} {tok[0]}" if depth else tok[0])

    missing, extra = want - got, got - want
    assert not missing and not extra, (
        f"\n  附录 A 缺少: {sorted(missing)}\n  附录 A 多出: {sorted(extra)}")


def test_ls_design_04_creates_resource_verbs_have_T3_row():
    """§2.1 断言 4：creates_resource ∈ {yes, cond:*} 的动词都要能映射到 T3 的某一行。

    共用映射在断言 4 的判据格里逐条登记，本测试只验证「登记过」而非猜测。
    """
    rows = _verbs_rows()
    def _is_creator(cell):
        # 值域是 `yes | no | cond:*`（BC-B-03）。格里可能带强调符号与说明文字，
        # 先剥掉 `*` 与反引号再取**首个记号**——用 `"yes" in cell` 会把说明文字里
        # 出现的 "yes" 误判成 creator（实证：该写法让加粗的 `cond:` 行恒假、
        # 让含 "yes" 说明的 no 行恒真，两个方向同时错）。
        v = cell.strip().lstrip("*").lstrip("`").strip()
        return v.startswith("yes") or v.startswith("cond:")

    creators = {_full_path(c, s) for _, c, s, _, vis, cr in rows
                if vis == "public" and _is_creator(cr)}
    text = LEDGERS.read_text()
    # **必须在 §2.1 内取**：全文有多张表都以 `| 4 |` 起行，取第一个会拿到别的表
    sec21 = text[text.index("## 2.1 本表的对账断言"):]
    if "\n---" in sec21:
        sec21 = sec21[:sec21.index("\n---")]
    a4 = [l for l in sec21.splitlines() if l.startswith("| 4 |")]
    assert len(a4) == 1, f"§2.1 内的断言 4 应恰好一条，实得 {len(a4)}"
    shared = a4[0]

    arch = ARCH.read_text()
    t3_start = arch.index("### 5.4.3 矩阵")
    t3_end = arch.find("\n### 5.4.4", t3_start)
    t3_block = arch[t3_start: t3_end if t3_end != -1 else len(arch)]

    # **逐行解析，只看每行的「操作」格**。整块文本搜索会命中散文里的交叉引用
    # （第 1 行提到「第 3 行 `down` 的四支」、第 4 行提到「`model repair <id>` 为收敛动作」），
    # 于是删掉 `down` 或 `model repair` 的整行之后，断言照样绿 —— 实证漏网。
    t3_rows, t3_nums = [], []
    for line in t3_block.splitlines():
        m = re.match(r"^\| *\*{0,2}(\d+)\*{0,2} *\| *([VRB]) *\| *(.*?) *\|", line)
        if not m:
            continue
        t3_nums.append(int(m.group(1)))
        t3_rows.append(m.group(3))
    _assert_contiguous(t3_nums, "T3 矩阵")
    t3 = "\n".join(t3_rows)          # 只含各行的操作格，不含结局散文

    # **只认明确的登记格式** `` `<动词>` → T3 第 <N> 行 ``。
    # 原先用宽松正则收割本格全部反引号项（连 `yes` 都收进去），于是往判据格里
    # 加一句提到某动词的散文，就能让它的真缺口从红变绿 —— 实证漏网。
    registered = set(re.findall(r"`([a-z][a-z. ]*?)`\s*→\s*T3 第 \d+ 行", shared))
    unmapped = []
    for v in sorted(creators):
        tail = v.split()[-1]
        # T3 的动词格可能带 flag（`model rm --weights`）⇒ 前缀匹配，不要精确匹配
        if re.search(rf"`{re.escape(v)}[ `]", t3):
            continue
        if re.search(rf"`{re.escape(tail)}[ `]", t3):
            continue
        if v in registered or tail in registered:
            continue                     # 组合展开 / 共用映射已在断言 4 里登记
        unmapped.append(v)

    # **受控缺口清单硬编在这里，不从被审文档里正则收割。**
    # 实证：从文档收割时，往断言 4 的判据格里加一句 `` `model zap` `` 就能让
    # 一个真缺陷从红变绿 —— 那等于把求值者交还给作者（`no-evaluator-but-author`）。
    # 要放宽它必须改这一行，进 diff，被评审看见。
    KNOWN_GAPS = frozenset()        # 当前无受控缺口（GAP-T3-28..31 系误报，已撤销）
    surprises = [v for v in unmapped if v not in KNOWN_GAPS]
    assert not surprises, (
        f"这些动词会创建资源，T3 里没有行，也不在受控缺口清单里: {surprises}\n"
        f"（受控缺口清单是本文件的 KNOWN_GAPS，当前为空）")


def test_ls_design_impl_modules_exist_in_arch_member_table():
    """verbs.tsv 的 impl 值域 ⊆ `02` §1.6 的成员表 —— §4.1.2 末尾那条接缝的机械化。

    原文：「必须同时出现在 modules.tsv 的 D 层与 §1.6 的成员表，否则 G-CLI-02 差集非空即红。」
    在此之前，这个「即红」没有任何东西会真的变红。
    """
    impls = {r[3] for r in _verbs_rows()} - {"cli"}
    arch = ARCH.read_text()
    # **必须限定到 §1.6 成员表**：全文子串匹配时 `cmd.self` 在 §4.1.2 的动词表里
    # 也以 `| `cmd.self` |` 的形状出现 ⇒ 把成员表整行删掉照样绿（实证漏网）。
    st = arch.index("## 1.6 T1-D")
    en = arch.index("## 1.7", st)
    members = arch[st:en]
    missing = [m for m in sorted(impls) if f"| `{m}` |" not in members]
    assert not missing, f"这些 impl 模块不在 `02` 的成员表里: {missing}"


# ── 以下三条是「产物表 ↔ 设计 ↔ 实现」的对账。
# 在两张表补齐列之前，它们**写在设计里却无从跑起** ——
# 而我这两轮手工修的 20 多条缺陷里，至少 8 条本该由它们自动抓到。

def _product_verbs():
    import csv
    root = Path(__file__).resolve().parents[2] / "payload" / "share"
    cols = ["verb", "parent", "impl", "aliases", "positionals", "stage_split",
            "exit_codes", "stdout_主产物", "machine_readable", "empty_result",
            "stderr_过程流", "creates_resource", "effect_read_path",
            "visibility", "doc_anchor", "completion"]
    return list(csv.DictReader(
        (l for l in (root / "verbs.tsv").read_text().splitlines()
         if l.strip() and not l.startswith("#")),
        fieldnames=cols, delimiter="\t"))


def _product_cli():
    import csv
    root = Path(__file__).resolve().parents[2] / "payload" / "share"
    cols = ["flag", "owner_verb", "takes_value", "value_domain",
            "mutex_group", "default", "intent_id", "help_key"]
    return list(csv.DictReader(
        (l for l in (root / "cli.tsv").read_text().splitlines()
         if l.strip() and not l.startswith("#")),
        fieldnames=cols, delimiter="\t"))


def test_g_ssot_09_product_table_matches_design_ledger():
    """**`G-SSOT-09`**：产物 `verbs.tsv` 的每一行，其 `exit_codes` 必须与
    设计台账（`03b` §2）逐字相同。

    设计把附录 A 定义为「本表最后五列的渲染段，`G-SSOT-09` 逐字节对账」
    （`02-architecture.md:1099`）。在产物表只有 6 列时，这条守卫**没有求值面** ——
    「某个命令的退出码集合写错了」只能靠人眼发现，而人眼这两轮漏了 8 条。
    """
    sec = _section(LEDGERS.read_text(), "# §2 `verbs.tsv`", "## 2.1 本表的对账断言")
    design = {}
    for line in sec.splitlines():
        if not re.match(r"^\| *\*{0,2}\d+\*{0,2} *\|", line):
            continue
        f = [c.strip() for c in line.split("|")]
        if len(f) < 12:
            continue
        cmd, sub = f[2].strip("`*"), f[3].strip("`*")
        # `[<service>]` / `[<verb>]` 是**可选位置参数**，不是子命令
        top = sub in ("—", "") or sub.startswith("[")
        # **动词名可以是多个词**（`trash ls`），而 sub 列同时还写着位置参数
        # （`rm <id> [--weights]`）。判据：参数以 `<` 或 `[` 开头，其余是名字。
        # 此前无条件 `split()[0]`，于是 `trash ls` 被截成 `trash` ——
        # 产物表里三行合法的动词被判成「设计台账里没有这一行」。
        def _verb_name(text):
            out = []
            for w in text.split():
                if w.startswith(("<", "[")):
                    break
                out.append(w)
            return " ".join(out) or text
        verb = cmd if top else _verb_name(sub)
        parent = "" if top else cmd
        raw = f[7].split("⚠")[0].split("〔")[0].replace("**", "")
        # **「（阶段 2 未定义）」不是一组码。** 从它里面正则抠数字会得到
        # 「阶段 **2**」的那个 2 —— 坏的提取不只是漏，它会**造出假数据**，
        # 而假数据带着权威的样子进入对账（本项目在加粗行号上栽过同一跤）。
        design[(verb, parent)] = (None if "未定义" in raw
                                  else ",".join(re.findall(r"\d+", raw)))

    rows = _product_verbs()
    Cardinality.require_nonempty("产物 verbs.tsv", rows, "表读不到 ⇒ 空真")

    drift, undefined = [], []
    for r in rows:
        key = (r["verb"], r["parent"])
        if key not in design:
            drift.append(f"{r['parent']} {r['verb']}".strip() + "：设计台账里没有这一行")
            continue
        want = design[key]
        if want is None:
            # 台账明写「阶段 2 未定义」⇒ 设计侧缺口，不是实现漂移。
            # 列出来，不静默放过。
            undefined.append(f"{r['parent']} {r['verb']}".strip())
        elif want != r["exit_codes"]:
            drift.append(f"{r['parent']} {r['verb']}".strip()
                         + f"：产物 {r['exit_codes']} ≠ 台账 {want}")
    Cardinality.check("产物表与设计台账的退出码漂移", drift, rel="empty")
    # 设计侧尚未定义退出码的动词 —— 受控清单，新增的会当场可见
    assert set(undefined) <= {"internal confirm"}, (
        f"新出现设计未定义退出码的动词：{sorted(set(undefined) - {'internal confirm'})}")


def test_g_cli_03_code_literals_subset_of_registered():
    """**`G-CLI-03`**：实现中出现的退出码字面量 ⊆ 该动词登记的集合。

    坏样本（本轮真实发生过两次）：`check` 返回 `Failed(3)/(4)` 而登记集合是
    `{0,2,64,65}`；`mlx install` 会透传 70–74 而登记集合里没有。
    两次都是人眼在跑命令时偶然发现的 —— 这条守卫存在的意义就是不靠偶然。
    """
    rows = _product_verbs()
    reg = {}
    for r in rows:
        path = f"{r['parent']} {r['verb']}".strip()
        reg[path] = {int(c) for c in r["exit_codes"].split(",") if c.strip().isdigit()}
    Cardinality.require_nonempty("登记的退出码", reg, "一个都没解析到 ⇒ 空真")

    # 动词 → 实现模块；再从模块源码里提取 Refused/Failed/Passthrough/Aggregate 的码
    import ast
    lib = Path(__file__).resolve().parents[2] / "payload" / "lib" / "mlx_server"
    mod_paths = {}
    for r in rows:
        impl = r["impl"]
        if impl == "cli":
            continue
        rel = impl.replace("cmd.", "cmd/").replace(".", "_") + ".py"
        cand = lib / rel
        if not cand.exists():                       # cmd.model.acquire → cmd/model_acquire.py
            cand = lib / ("cmd/" + impl.split(".", 1)[1].replace(".", "_") + ".py")
        if cand.exists():
            mod_paths.setdefault(impl, cand)

    Cardinality.require_nonempty("能定位到源码的 impl", mod_paths,
                                 "一个模块都没定位到 ⇒ 空真")
    over = []
    for r in rows:
        src = mod_paths.get(r["impl"])
        if src is None:
            continue
        path = f"{r['parent']} {r['verb']}".strip()
        used = set()
        for n in ast.walk(ast.parse(src.read_text())):
            if not isinstance(n, ast.Call):
                continue
            fn = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if fn in ("Refused", "Failed", "Passthrough", "Aggregate") and n.args:
                a = n.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, int):
                    used.add(a.value)
        # 一个模块常实现多个动词，故按模块聚合后与该模块全部动词的并集比较
        pool = set()
        for rr in rows:
            if rr["impl"] == r["impl"]:
                p2 = f"{rr['parent']} {rr['verb']}".strip()
                pool |= reg.get(p2, set())
        extra = used - pool - {0}
        if extra:
            over.append(f"{src.name} 用了 {sorted(extra)}，"
                        f"而 {r['impl']} 名下动词登记的并集是 {sorted(pool)}")
    Cardinality.check("实现中超出登记集合的退出码", sorted(set(over)), rel="empty")


def test_g_cli_01_flags_match_read_sites():
    """**`G-CLI-01`**：`cli.tsv` 的 flag 集合 == 实现中的读取点集合。

    设计原话（SSOT 表第 13 行）：「挡 v6『收了 `--trust-remote-code` 但不转发』」。
    登记了却没人读 = 骗用户；读了却没登记 = help 和补全里查无此项。
    """
    rows = _product_cli()
    Cardinality.require_nonempty("cli.tsv", rows, "表读不到 ⇒ 空真")
    declared = {r["flag"].lstrip("-").replace("-", "_") for r in rows}

    lib = Path(__file__).resolve().parents[2] / "payload" / "lib" / "mlx_server"
    read = set()
    for f in lib.rglob("*.py"):
        src = f.read_text()
        read |= set(re.findall(r'(?:args|flags|inv\.args)[^)\n]{0,40}?'
                               r'\.get\(\s*"([a-z_]+)"', src))
        read |= set(re.findall(r'\(inv\.args or \{\}\)\.get\("([a-z_]+)"', src))
    # **内部注入键**：这些不来自命令行，由 deps / 上层构造塞进 args。
    # 它们不该出现在 cli.tsv 里 —— 用户从命令行传它们必须被拒
    # （`--layers=x` 曾经因此把解释器打出 traceback）。
    # 排除表要逐个说得出理由，否则它就成了守卫的后门：
    # `key` / `prompt` / `expect` 一度躺在这里，于是它们「有读取点却没登记」
    # 长期查不出来，用户在 help 和补全里查不到 `--prompt`，
    # 而它又碰巧能用（`--k=v` 那条不查表的路径）。
    internal = {
        "_group",   # cli.parse 自己塞的分组名
        "detail",   # 文案参数，不是 flag
        "now",      # 时间注入点（测试用）
        "layers",   # 配置分层，由 D 层构造
        "meta",     # 模型元数据，由下载侧产出
        "files",    # 权重文件名列表，同上
    }
    read -= internal

    unread = sorted(declared - read - {"dry_run"})   # dry_run 由 parse 直接消费
    Cardinality.check("登记了但实现从不读的 flag", unread, rel="empty")

    # **反方向同样要守**。此前只查了一半，于是 `--revision` / `--prompt` /
    # `--session` / `--value` 有读取点却不在表里 —— help 不列、补全没有，
    # 用户查不到它们，而它们「碰巧能用」。这半边缺失时，
    # 「不存在手抄的第二份」这条验收实际上只兑现了一个方向。
    unregistered = sorted(read - declared)
    Cardinality.check("有读取点但没登记的 flag", unregistered, rel="empty")


def test_every_public_verb_has_a_man_page():
    """**每个 public 动词都要有详解**，且详解的每一段都有内容。

    Alex 的要求：「mlx help 要对每条命令都进行解释用途，
    类似 linux 里面的 man 命令输出结果一样有详细的命令使用介绍」。

    这条守卫盯的是「新增动词却忘了写说明」——那会让 help 页只剩一行 summary，
    而用户看 help 正是因为他不知道这条命令干什么。
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "payload" / "lib"))
    from mlx_server import cli
    from mlx_server.tables import TablesView

    share = Path(__file__).resolve().parents[2] / "payload" / "share"
    tv = TablesView(str(share))
    verbs, clis = tv.verbs(), tv.cli()

    missing = []
    for r in verbs:
        if r["visibility"] != "public" or r["verb"] == "任意动词":
            continue
        path = f"{r['parent']} {r['verb']}".strip()
        text, rc = cli.render_help(["mlx", "help"] + path.split(), verbs, clis, tv)
        if rc != 0:
            missing.append(f"{path}：help 返回 rc {rc}")
            continue
        for seg in ("名称", "用法", "说明", "退出码"):
            if seg not in text:
                missing.append(f"{path}：详解缺「{seg}」段")
        # 说明段必须有实质内容，不能只是把 summary 抄一遍
        long_key = "help." + path.replace(" ", ".") + ".long"
        try:
            tv.text(tv.msg(long_key))
        except Exception:                          # noqa: BLE001
            missing.append(f"{path}：没有 {long_key}（说明段会退化成空）")
    Cardinality.check("缺详解的 public 动词", sorted(set(missing)), rel="empty")


def test_message_templates_consume_every_arg_passed_at_construction():
    """**构造点传的参数，模板里必须有对应占位符**（否则参数静默蒸发）。

    坏样本（本次真实发生）：`svc.died_before_ready` 的模板以「日志尾部：」结尾，
    **没有 `{detail}`**，而 `cmd/svc.py` 确实传了 `detail=日志尾`。
    `str.format` 对多余的关键字参数不报错，于是用户看到的是
    「…日志尾部：」后面直接换行 —— 而真实原因（`No module named 'mlx_lm'`）
    就在那个被丢掉的参数里。同一屏还留着一句错误的猜测「最常见的原因是端口已被占用」。

    这条查的是「传了但没被消费」的方向；反方向（模板要占位符而构造点没传）
    由 `test_message_args_present_at_construction` 覆盖。
    """
    import re as _re
    root = Path(__file__).resolve().parents[2] / "payload"
    tmpl = {}
    for ln in (root / "share" / "messages.tsv").read_text().splitlines():
        if not ln.strip() or ln.startswith("#") or "\t" not in ln:
            continue
        k, _, body = ln.partition("\t")
        tmpl[k] = set(_re.findall(r"\{([a-z_0-9]+)\}", body))   # 含数字：`rc_from_t0` 这类键名会被 [a-z_]+ 漏掉

    dropped = []
    for f in sorted((root / "lib" / "mlx_server").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) in ("Refused", "Failed", "Aggregate")):
                continue
            key = next((a.value for a in node.args
                        if isinstance(a, ast.Constant) and isinstance(a.value, str)
                        and "." in a.value), None)
            if key is None or key not in tmpl:
                continue
            passed = {kw.arg for kw in node.keywords if kw.arg}
            # `id` / `service` 这类常被用在多个模板里；只报**这个模板**没用上的
            # `Refused/Failed` 的关键字有两种用途：**文案参数**与**payload 数据**。
            # 后者由 `ui` 渲染成逐行结论，模板里本就不该有它的占位符。
            # 这个豁免集要逐个说得出理由 —— 它不是「让守卫闭嘴」的后门。
            payload_only = {
                "results",   # `up/down` 的逐目标结论，ui._svc_change 渲染
                "missing",   # bootstrap verify 的缺失清单，逐项列出
                "allowed",   # 值域拒绝时的允许集合，ui 排版成列表
            }
            extra = passed - tmpl[key] - payload_only
            if extra:
                dropped.append(f"{f.name}:{node.lineno} {key} 传了 {sorted(extra)}"
                               f"，而模板只有 {sorted(tmpl[key])}")
    Cardinality.check("构造时传了却被模板丢弃的参数", sorted(dropped), rel="empty")


def test_g_res_11_config_reads_environ_only_in_ctx():
    """**INV-19（BCR-2026-09-01 订正后）：配置读取点 == 1**（`ctx` 构造）。

    原措辞是「`os.environ` 的读取点 == 1」，它把三类不同的事混为一谈，
    于是**从来无法求值** —— 实际有 5 个读取点，而守卫一直是绿的（不存在）。
    订正后三类登记豁免：
      ① **引导**：`entry` 找 `share/` 那次 —— `ctx` 尚未存在，
         读 `constants.env` 得先知道它在哪，塞进 `ctx` 就是循环依赖；
      ② **子进程环境转发**：`runtime.spawn` 的 `dict(os.environ)`、
         `cmd/svc.py` 的 `PATH` —— 不读配置，只把环境交给别的进程；
      ③ **网关进程自己的那一次** —— 独立进程，有自己的 `ctx` 构造。

    豁免集写成 `(文件, 理由)` 而不是一个文件名列表：**每一条都要说得出理由**，
    否则它就是让守卫闭嘴的后门（本轮已经在 `G-CLI-01` 的排除表上踩过一次）。
    """
    root = Path(__file__).resolve().parents[2] / "payload"
    exempt = {
        "lib/mlx_server/ctx.py": "唯一合法的配置读取点",
        "lib/mlx_server/entry.py": "① 引导：找 share/，ctx 尚未存在",
        "lib/mlx_server/runtime.py": "② 子进程环境转发：spawn 的 dict(os.environ)",
        "lib/mlx_server/cmd/svc.py": "② 子进程环境转发：PATH",
        "lib/mlx_server/cmd/self_deploy.py": "② 子进程环境转发：部署器的 _t0_env",
        "gateway/__main__.py": "③ 网关进程自己的 ctx 构造",
    }
    hits = {}
    for f in sorted(root.rglob("*.py")):
        rel = str(f.relative_to(root))
        for node in ast.walk(ast.parse(f.read_text())):
            attr = getattr(node, "attr", None) or getattr(node, "id", None)
            is_env = (attr in ("environ", "getenv")
                      and isinstance(node, (ast.Attribute, ast.Name)))
            if is_env:
                hits.setdefault(rel, []).append(node.lineno)

    Cardinality.require_nonempty("os.environ 读取点", sorted(hits), "一处都没有 ⇒ 空真")
    unexpected = sorted(set(hits) - set(exempt))
    assert unexpected == [], (
        "这些文件读了 os.environ，而它们不在 INV-19 的三类登记豁免里 —— "
        f"要么改成从 ctx 取，要么给出理由后登记进豁免集：{unexpected}")


# **模块层序台账**。设计要求的 `modules.tsv` 尚未建立（KNOWN-ISSUES 第 2 条：
# 建表要先于改代码，而建表本身要后于「谁消费它」的确定）。在它落地之前，
# 这张表就是层归属的事实来源 —— 写在守卫里而不是注释里，是为了让它**可求值**。
#
# 两个模块的层归属是 2026-09-01 定的，理由记在这里：
#   · `runtime` = **装配层**（composition root）。它是 `build_deps` 的所在，
#     装配点天然要认识所有层 —— 那不是「违反层序」，是它的职责。
#     标错成 B 层时，`runtime → supervisor`(C) 与 `runtime → cmd.caps`(D)
#     被判成两条反向边，而它们本来就该合法。
#   · `caps` = **C 层**（拥有 `share/caps.json`）。此前它在 `cmd/caps.py`，
#     于是 `checks`(B) 与 `cmd.meta`(D) 要读能力视图只能反向 import 一个
#     D 层编排器 —— 另外三条反向边的共同根因。
_LAYER = {
    # A 层 · 纯类型与原语，不依赖任何东西
    "result": 1, "cleanup": 1,
    # B 层 · 单一资源的原语
    "atomic": 2, "proc": 2, "probe": 2, "wire": 2, "tables": 2, "ui": 2, "ctx": 2,
    # P 层 · 纯策略（不碰 I/O，探针值由调用方注入）
    "schema": 3, "backendarg": 3, "fitness": 3,
    # C 层 · 资源所有者
    "store": 4, "ports": 4, "weights": 4, "vault": 4, "supervisor": 4,
    "sessions": 4, "caps": 4, "checks": 4,
    # D 层 · 编排（cmd/*），零自有资源
    # E 层 · 解析
    "cli": 6,
    # 装配层 · composition root
    "runtime": 7,
    # 入口 · 把解析、装配、编排接起来
    "entry": 8,
    "__main__": 9,     # `python -m mlx_server` 的薄壳，只调 entry.main
}


def test_g_dep_02_no_reverse_layer_edges():
    """**INV-14：import 图跨层只向下**（`G-DEP-02` 的双向差集）。

    坏样本（本轮真实存在过五条）：`checks`(B)→`store`,`ports`(C)、
    `checks`(B)→`cmd.caps`(D)、`runtime`(B)→`supervisor`(C)、
    `runtime`(B)→`cmd.caps`(D)、`cmd.meta`(D)→`cmd.caps`(D)。

    根因不是「代码违反了设计」，是**设计里少了两个模块** ——
    `checks` 与 `runtime` 不在任何模块清单里，因此没有层归属，
    而 `modules.tsv` 不存在，`G-DEP-01`/`G-DEP-02` 一直没有求值面。

    延迟 import（把 `from . import X` 挪进函数体）**不改变依赖**，
    只是让守卫看不见 —— 所以这里扫全部 `ImportFrom`，不分位置。
    """
    root = Path(__file__).resolve().parents[2] / "payload" / "lib" / "mlx_server"
    bad = []
    for f in sorted(root.rglob("*.py")):
        if f.name == "__init__.py":
            continue
        in_cmd = f.parent.name == "cmd"
        src = 5 if in_cmd else _LAYER.get(f.stem)
        if src is None:
            bad.append(f"{f.stem} 没有层归属 —— 新模块要先登记进 _LAYER 并写明理由")
            continue
        for n in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(n, ast.ImportFrom) and n.level):
                continue
            targets = ([n.module.split(".")[-1]] if n.module
                       else [a.name for a in n.names])
            for t in targets:
                tgt = 5 if (t == "cmd" or n.module == "cmd") else _LAYER.get(t)
                if tgt is None:
                    continue
                if tgt > src:
                    bad.append(f"{f.stem}({src}) → {t}({tgt}) 第 {n.lineno} 行")
                elif tgt == src == 5:
                    bad.append(f"D 层内边：{f.stem} → {t} 第 {n.lineno} 行"
                               f"（契约：D 层内边集合 == ∅，退出码与清理栈归属会串）")
    Cardinality.check("层序反向边", sorted(set(bad)), rel="empty")


def test_share_root_has_exactly_one_derivation():
    """**`share/` 的落点只有一处推导**（`ctx.share_root`）。

    此前有三处各自 `root_code + "share"`：垫片、`entry._share_root`、
    `runtime._tables` / `tables.open_tables`。三者今天一致**只因为**安装器与
    测试都显式设了 `MLX_SHARE_ROOT` —— 用户自己设一个别的值，
    表从 A 读、`caps.json` 写到 B，而没有任何东西会红。

    判据是「拼 `"share"` 这个字面量的地方」，不是函数名。
    `ctx.py`（唯一来源）与 `entry.py`（引导，`ctx` 尚未存在）是登记豁免。
    """
    root = Path(__file__).resolve().parents[2] / "payload" / "lib" / "mlx_server"
    exempt = {"ctx.py": "唯一来源", "entry.py": "引导：ctx 尚未存在"}
    hits = []
    for f in sorted(root.rglob("*.py")):
        if f.name in exempt:
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "join"):
                continue
            if any(isinstance(a, ast.Constant) and a.value == "share"
                   for a in node.args):
                hits.append(f"{f.name}:{node.lineno}")
    assert hits == [], (
        "这些地方自己拼 share/ 路径，而落点的唯一来源是 `ctx.share_root` —— "
        f"多一处推导就多一次分叉的机会：{hits}")


def test_p_sch_15_writable_equals_user_scope():
    """**`P-SCH-15`：`{k : writable(k)}` == `{k ∈ defs : scope == "user"}`**（双向差集）。

    设计点名的坏样本是 `mlx config set 27b revision abc` —— 往 machine 段写
    磁盘事实。它此前**没有求值对象**：`schema.tsv` 不存在，没有 `scope` 列，
    也没有 `writable()`；`config set` 只查「键在不在 DEFAULTS 里」，
    而 `revision` 不在 ⇒ 报「未知键」，看着像拦住了，理由却是错的。
    理由错在这里是有代价的：用户明明在 `model info` 里见过 `revision`，
    被告知「未知键」只会更困惑。

    **两个方向都要查**。只查一边的话，把 `writable` 写成 `return True`
    仍能让「可写键 ⊆ user 键」这一半通过。
    """
    from mlx_server import schema  # noqa: PLC0415
    from mlx_server.tables import TablesView  # noqa: PLC0415

    tv = TablesView(str(Path(__file__).resolve().parents[2] / "payload" / "share"))
    defs = schema.keydefs(tv)
    Cardinality.require_nonempty("schema.tsv 的键", sorted(defs), "表读不到 ⇒ 空真")

    writable = {k for k in defs if schema.writable(k, defs)}
    user_scope = {k for k, d in defs.items() if d["scope"] == "user"}
    Cardinality.check("可写键 ↔ scope==user", sorted(writable), sorted(user_scope),
                      rel="equal")

    # machine 段字段**必须登记在表里**（否则报的是「未知键」而不是「不可写」）
    assert "revision" in defs, (
        "`revision` 不在 schema.tsv 里 —— `config set <模型> --key=revision` "
        "会被报成「未知键」，而它明明出现在 `model info` 的输出里")
    assert defs["revision"]["scope"] == "machine", "revision 是磁盘事实，不是配置"


def test_every_schema_key_has_a_reader():
    """**空 `readers` 是缺陷** —— 一个没有消费者的配置键是死旋钮。

    v6 的 `gen.top_p` 就这么活了一整版：用户设了它，什么都不会发生。
    `readers` 列的存在就是为了让这件事可求值。

    这里只查列非空（真正的「读取点集合 == readers」要等 `guards.tsv`，
    见 KNOWN-ISSUES）。列都空着的话，那条更强的断言也无从谈起。
    """
    from mlx_server.tables import TablesView  # noqa: PLC0415
    tv = TablesView(str(Path(__file__).resolve().parents[2] / "payload" / "share"))
    empty = [r["key"] for r in tv.schema_keys() if not r["readers"].strip("-")]
    assert empty == [], f"这些配置键没有任何消费者，是死旋钮：{empty}"


def test_renderers_live_with_their_commands():
    """**渲染器随 `cmd.*` 走**（设计：留在 `ui` 会让它认识每一个命令的结果类型，
    那是「一个模块多个变更理由」）。

    `ui` 只留：原语（`pad` / `display_width` / `_w` / `_pad` / `_kv` / `_flat` /
    `_text` / `_mark`）、分派（`human` / `_renderers` / `register`）、
    回落（`_generic`），以及**三个落不下去的**渲染器。

    那三个各有理由，写在这里而不是注释里，是为了让豁免可求值：
      · `repair` —— payload 由 `store.rebuild_machine`（**C 层**）构造
      · `trash_restore` —— 由 `vault.restore`（**C 层**）构造
      · `checks` —— 被 `cmd.check` 与 `cmd.config` **两个** D 模块共用
    硬搬这三个会让 C 层的产物去依赖某个 D 模块，比现在的耦合更糟。

    **`_model_table` 这类辅助不得有第二份**：搬迁时它一度在 `ui` 与
    `render_svc` 各留一份 —— 与「显示宽度三份实现」（INV-23）同型。
    """
    root = Path(__file__).resolve().parents[2] / "payload" / "lib" / "mlx_server"
    ui_src = (root / "ui.py").read_text()

    # ① `ui` 里剩下的渲染器 == 三个登记豁免
    import re as _re
    i = ui_src.index("_BUILTIN = {")
    builtin = set(_re.findall(r'"([a-z_]+)":', ui_src[i:ui_src.index("}", i)]))
    Cardinality.check("留在 ui 的渲染器", sorted(builtin),
                      ["checks", "repair", "trash_restore"], rel="equal")

    # ② 没有一个函数在两处都定义（辅助复制是这次搬迁最容易犯的错）
    defs = {}
    for f in [root / "ui.py", *sorted((root / "cmd").glob("render_*.py"))]:
        for m in _re.finditer(r"^def (_?[a-z_]+)\(", f.read_text(), _re.M):
            defs.setdefault(m.group(1), []).append(f.name)
    dup = {k: v for k, v in defs.items() if len(v) > 1}
    assert dup == {}, f"这些函数有两份实现（与 INV-23 的教训同型）：{dup}"

    # ③ 每个 render_* 都自我登记，否则它的 kind 永远分派不到
    for f in sorted((root / "cmd").glob("render_*.py")):
        assert "ui.register(" in f.read_text(), \
            f"{f.name} 没调 ui.register() —— 它的渲染器永远不会被分派到"


def test_config_domain_comes_from_table_not_hardcoded():
    """**配置域的来源是 `schema.tsv`，不是 `schema.DEFAULTS`。**

    坏样本（本次真实发生，同一根因三处）：表建好了，但求值器仍锁在硬编码的
    `DEFAULTS` 上 —— `effective` 判「未知配置键」、`_layers` 静默滤掉、
    `run` 把新键误判成模型 id。症状是 `config set draft_model` 写得进去、
    读回来说「来自 default」，而 `config set` 的效果回读正好走这条路径 ⇒ KeyError。

    **建了表却没让求值器用表，等于表只有一半在生效** —— 而那一半的症状
    （写入成功）看起来完全正常。

    判据：`cmd/config.py` 里不得再出现 `schema.DEFAULTS` 作为**筛选依据**。
    它只允许作为 `_factory` / `_domain` 里的**兜底回落**（表读不到时）。
    """
    f = (Path(__file__).resolve().parents[2] / "payload" / "lib" / "mlx_server"
         / "cmd" / "config.py")
    # **在 AST 上查**，不扫文本 —— 文档串里提到 `schema.DEFAULTS` 是说明，
    # `dict(schema.DEFAULTS)` 是兜底回落，两者都不是「拿它当配置域用」。
    # 文本扫描把这三类混在一起，报的是假阳性（本轮第 N 次同一形态）。
    bad = []
    for node in ast.walk(ast.parse(f.read_text())):
        if not (isinstance(node, ast.Attribute) and node.attr == "DEFAULTS"
                and getattr(node.value, "id", None) == "schema"):
            continue
        # 允许：`dict(schema.DEFAULTS)` / `set(schema.DEFAULTS)`（兜底构造副本）
        parent_is_copy = False
        for p2 in ast.walk(ast.parse(f.read_text())):
            if (isinstance(p2, ast.Call)
                    and getattr(p2.func, "id", None) in ("dict", "set")
                    and p2.args and getattr(p2.args[0], "attr", None) == "DEFAULTS"
                    and p2.args[0].lineno == node.lineno):
                parent_is_copy = True
        if not parent_is_copy:
            bad.append(f"第 {node.lineno} 行")
    assert bad == [], (
        "这些地方拿硬编码 DEFAULTS 当配置域用，表里新加的键会被静默滤掉：\n  "
        + "\n  ".join(bad))


def test_draft_model_value_is_an_alias_not_a_path():
    """**`--draft-model` 的值是别名短名，不是路径**（INV-01 态 A）。

    三条路只有一条走得通，这里钉住走通的那条：
      · 绝对路径 ⇒ 违反 INV-01（「snapshot 绝对路径不出现在 argv 里」）
      · HF repo id ⇒ `mlx_lm` 用 `snapshot_download(local_files_only=True)` 解析，
        它要标准 HF 缓存布局（`refs/main` + `snapshots/<commit-hash>/`），
        而本工具的下载器产出 `snapshots/main/`（无 refs）⇒ LocalEntryNotFoundError（实测）
      · **别名短名** ⇒ cwd 是 `run/alias/<id>/<nonce>/`，里面有 `<id>-draft` 链接 ✓
    """
    # 单独跑这一条时 sys.path 里未必有产品包 —— 自己补上，
    # 否则守卫会以 ModuleNotFoundError 失败，那看起来像「守卫红了」
    # 而实际它一个字节都没测到（本轮踩过：坏样本注进去了却没红）。
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "payload" / "lib"))
    from mlx_server import backendarg, schema  # noqa: PLC0415
    from mlx_server.tables import TablesView  # noqa: PLC0415

    tv = TablesView(str(Path(__file__).resolve().parents[2] / "payload" / "share"))
    v = schema.defaults_from(tv)
    v.update({"draft_model": "d", "num_draft_tokens": 4})
    argv = backendarg.spec("m", schema.ServiceConfig("m", v, {}, "/py"), {}).argv

    assert "--draft-model" in argv, f"配了 draft_model 却没拼出参数：{argv}"
    val = argv[argv.index("--draft-model") + 1]
    assert val == "m-draft", f"值应是别名 `<id>-draft`，实得 {val!r}"
    assert not val.startswith("/"), "绝对路径进了 argv —— 违反 INV-01 态 A"
    assert "/" not in val, f"值里有斜杠，不是别名而是路径：{val!r}"
