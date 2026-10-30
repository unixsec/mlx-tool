"""发布物要能在**任何人的 Mac** 上照抄就跑。两类缺陷都是 2026-10-27 在真机上撞到的：

1. 文档的 bash 代码块里带 `#` 注释。zsh 交互模式默认不开 `interactivecomments`：
   行尾注释变成命令参数（`mlx check  # 12 项…` ⇒「check 不接位置参数，多给了 '#'」），
   整行注释报 `command not found: #`，带注释的函数定义报 parse error。
2. 作者自己的环境漏进发布物：权重根默认 `~/lab/models/mlx`（作者的目录规范）、
   文档里写死 `~/dev/projects/mlx-toolkit`、clone 地址是占位符 `<you>`。
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
USER_DOCS = [REPO / "README.md", REPO / "README_cn.md", *sorted((REPO / "docs").glob("*.md"))]
FENCE = re.compile(r"^```(bash|zsh|sh|shell)\n(.*?)^```", re.S | re.M)
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")


def _comment_at(line):
    """引号外、处于词首的 `#` 的位置；没有返回 -1。`${#x}`、`$#`、URL 片段都不在词首。"""
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
            continue
        if c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return i
    return -1


def _shell_lines(body):
    """跳过 heredoc 内容 —— 那是数据（Python 脚本、JSON），里面的 `#` 不归 shell 解析。"""
    end = None
    for i, ln in enumerate(body.splitlines()):
        if end is not None:
            if ln.strip() == end:
                end = None
            continue
        yield i, ln
        m = HEREDOC.search(ln)
        if m:
            end = m.group(1)


def test_shell_comment_detector_is_not_vacuous():
    assert _comment_at("mlx check  # 12 项") > 0
    assert _comment_at("# 整行") == 0
    assert _comment_at('echo "${#T} a#b"') == -1
    assert _comment_at("curl http://x/#frag") == -1
    assert [ln for _, ln in _shell_lines("cat <<'PY'\n# py\nPY\nls")] == ["cat <<'PY'", "ls"]


def test_doc_shell_blocks_have_no_comments():
    scanned, bad = 0, []
    for doc in USER_DOCS:
        text = doc.read_text()
        for m in FENCE.finditer(text):
            start = text[:m.start()].count("\n") + 2
            for off, ln in _shell_lines(m.group(2)):
                scanned += 1
                if _comment_at(ln) >= 0:
                    bad.append(f"{doc.relative_to(REPO)}:{start + off}  {ln.strip()}")
    assert scanned > 50, f"只扫到 {scanned} 行 —— 代码块正则失配，这条守卫在空转"
    assert not bad, ("bash 代码块里不能有 # 注释（zsh 默认交互模式下粘贴即报错），"
                     "说明移到代码块外：\n" + "\n".join(bad))


PERSONAL = re.compile(r"~/lab/|\$HOME/lab\b|~/dev/projects|/Users/[a-z][\w.-]*/|github\.com/<")


def _shipped_code():
    for p in sorted((REPO / "deployer").glob("*.sh")) + sorted((REPO / "payload").rglob("*")):
        if p.is_file() and p.suffix in (".sh", ".py", ".tsv", ".env", "") and "__pycache__" not in p.parts:
            yield p


@pytest.mark.parametrize("kind", ["docs", "code"])
def test_no_personal_environment_in_shipped_files(kind):
    """CHANGELOG 不扫（它记录的就是「从哪个旧值改成哪个新值」）；代码只扫非注释行。"""
    files = USER_DOCS if kind == "docs" else list(_shipped_code())
    if kind == "docs":
        # 点名而不是数个数：守卫是防「一个文件都没扫到」，文档增删不该让它失效或误报
        want = {"README.md", "README_cn.md", "docs/USER-GUIDE.md"}
        assert want <= {str(p.relative_to(REPO)) for p in files}, files
    else:
        assert len(files) > 20, files
    bad = []
    for p in files:
        try:
            lines = p.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        for n, ln in enumerate(lines, 1):
            live = ln.split("#", 1)[0] if kind == "code" and p.suffix != ".tsv" else ln
            if PERSONAL.search(live):
                bad.append(f"{p.relative_to(REPO)}:{n}  {ln.strip()[:120]}")
    assert not bad, "发布物里有作者个人环境的路径或占位地址：\n" + "\n".join(bad)
