"""文档与 1.1.0 网关行为一致（spec §13 文档项）。只查照做就会出错或被误导的句子。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from floor import run_floor  # noqa: E402


def _read(repo, rel):
    return (repo / rel).read_text(encoding="utf-8")


def test_guide_covers_multi_source_access(repo):
    g = _read(repo, "docs/USER-GUIDE.md")
    for s in ("{#remote}", "{#clients}", "api.allow_cidrs", "api.tls_cert", "ANTHROPIC_BASE_URL",
              "ANTHROPIC_AUTH_TOKEN", 'wire_api = "responses"', "OPENAI_BASE_URL",
              "Authorization: Bearer", "peer_not_allowed", "endpoint_not_supported_by_backend"):
        assert s in g, f"手册缺「{s}」"
    for stale in ("回环绑定时任意非空串", "回环绑定时**不校验令牌**", "默认只绑回环", "九项应该全绿",
                  "9 项，8 项通过"):
        assert stale not in g, f"手册仍有过时说法「{stale}」"


def test_readmes_and_changelog(repo):
    for rel in ("README.md", "README_cn.md"):
        txt = _read(repo, rel)
        assert "9 checks" not in txt and "9 项检查" not in txt and "9 项，9 项通过" not in txt, rel
        assert "mlx api info" in txt, f"{rel} 没告诉读者去哪看访问地址"
    head = _read(repo, "CHANGELOG.md").split("## [1.0.0]")[0]
    for s in ("api.bind", "0.0.0.0", "所有来源都要令牌", "404", "/v1/responses", "/v1/messages", "TLS"):
        assert s in head, f"CHANGELOG Unreleased 缺「{s}」"
    issues = _read(repo, "docs/KNOWN-ISSUES.md")
    for s in ("TLS 关闭时", "networksetup", "端口复用"):
        assert s in issues, f"KNOWN-ISSUES 缺「{s}」"


def test_check_count_matches_product(repo, payload, sys_py):
    """docs 与 README 里的检查项数不是抄来的常量 —— 必须等于 cmd.check.ITEMS 的真实长度。

    这样下一次新增/删除检查项时，文档不会静默漂移：先红后改（G-SSOT）。
    经 `run_floor` 在下限解释器里读，不在测试自己的 venv 里 import 产品模块。
    """
    got = run_floor(payload, sys_py, """
        import json
        from mlx_server.cmd import check
        print(json.dumps({"n": len(check.ITEMS)}))
    """)
    n = got["n"]
    assert n == 12, f"ITEMS 长度变了（现在 {n}），本测试与本任务的文案数字需要一起复核"

    guide = _read(repo, "docs/USER-GUIDE.md")
    assert f"{n} 项，{n - 1} 项通过，1 项未通过：venv" in guide, \
        f"USER-GUIDE 的示例检查输出没有对上 ITEMS 的真实数量（{n}）"
    assert "九项" not in guide, "手册仍有旧的「九项」说法"

    for rel in ("README.md", "README_cn.md"):
        txt = _read(repo, rel)
        assert f"{n} 项，{n} 项通过" in txt, f"{rel} 的示例检查输出没有对上 ITEMS 的真实数量（{n}）"
        assert f"{n} checks" in txt or f"{n} 项检查" in txt, f"{rel} 缺检查总数（{n}）的说明"
