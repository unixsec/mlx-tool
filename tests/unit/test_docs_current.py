"""文档不得继续教已退役的做法（spec §6、§10）。只查**会让用户照做就出错或被误导**的句子。"""


def _read(repo, rel):
    return (repo / rel).read_text(encoding="utf-8")


def test_docs_no_longer_teach_retired_behaviour(repo):
    guide = _read(repo, "docs/USER-GUIDE.md")
    assert "{#backend}" in guide, "手册缺「自动选择后端」一节"
    assert "#2033" in guide, "手册没说明 MTP drafter 为什么不支持"
    assert "令牌**有意不进配置文件**" not in guide, "api.token 已是配置项，手册仍说不进配置文件"
    assert "mlx config get api.token --reveal" in guide
    assert "--key=api.token" not in guide, "config get 的全局键是位置参数；--key= 缺位置参数时 rc 2"
    for rel in ("docs/USER-GUIDE.md", "README.md", "README_cn.md"):
        txt = _read(repo, rel)
        assert "--type=text" not in txt, f"{rel} 仍教 --type=text（登记不再需要类型）"
        assert "Qwen3.8-27B-MTP-4bit --type" not in txt

    issues = _read(repo, "docs/KNOWN-ISSUES.md")
    assert "`--type vision` 起不来时不说原因" not in issues
    assert "在 mlx_lm 上用不了" not in issues
    assert "#2033" in issues
    assert "模板渲染沙箱的边界是 jinja2，不是进程隔离" in issues

    log = _read(repo, "CHANGELOG.md")
    head = log.split("## [1.0.0]")[0]
    for s in ("## [1.1.0]", "81920", "mlx model repair", "types.tsv", "mlx-vlm"):
        assert s in head, f"CHANGELOG 的 1.1.0 段缺「{s}」"
