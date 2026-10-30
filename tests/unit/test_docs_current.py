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

    # 已知问题清单是作者的本地记录，不随仓库发布；面向用户的边界写在 README「默认不防的」
    assert not (repo / "docs" / "KNOWN-ISSUES.md").exists(), "已知问题清单不该放在 docs/ 下发布"
    for rel in ("README.md", "README_cn.md", "CHANGELOG.md", "docs/USER-GUIDE.md"):
        assert "KNOWN-ISSUES" not in _read(repo, rel), f"{rel} 仍链接已不发布的 KNOWN-ISSUES"
    assert "jinja2 沙箱" in _read(repo, "README_cn.md"), "README 没说明模板渲染沙箱的边界"
    assert "jinja2 sandbox" in _read(repo, "README.md")

    log = _read(repo, "CHANGELOG.md")
    head = log.split("## [1.0.0]")[0]
    for s in ("## [1.1.0]", "81920", "mlx model repair", "types.tsv", "mlx-vlm"):
        assert s in head, f"CHANGELOG 的 1.1.0 段缺「{s}」"
