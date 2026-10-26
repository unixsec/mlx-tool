"""文档与计划 3 的网关行为一致（spec §13 文档项：媒体规则、并发与输出长度、流式建议；§10 已知问题）。

只查照做就会出错或被误导的句子：错误类型、配置键、映射结果写错，用户对着手册排查就会走错路。
"""

import re
import sys
from pathlib import Path

_PAYLOAD = Path(__file__).resolve().parents[2] / "payload"
for _p in (_PAYLOAD, _PAYLOAD / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from gateway import effort, fetch, media, relay, server  # noqa: E402


def _read(repo, rel):
    return (repo / rel).read_text(encoding="utf-8")


def _schema(repo):
    rows = {}
    for ln in _read(repo, "payload/share/schema.tsv").splitlines():
        if ln.strip() and not ln.startswith("#"):
            c = ln.split("\t")
            rows[c[0]] = (c[2], c[3])  # 域、默认
    return rows


def _line(text, needle):
    hits = [ln for ln in text.splitlines() if needle in ln]
    assert hits, f"手册没有含「{needle}」的行"
    return hits[0]


def test_guide_covers_media_thinking_streaming(repo):
    g = _read(repo, "docs/USER-GUIDE.md")
    for s in (
        "{#media}",
        "{#thinking}",
        "{#streaming}",
        "media_fetch_failed",
        "address_denied",
        "too_large",
        "http_error",
        "media_source_rejected",
        "too_many_media_parts",
        "gateway_busy",
        "Retry-After",
        "media_storage_failed",
        "api.media_max_mb",
        "api.media_max_parts",
        "api.inflight_max_mb",
        "media_fetched",
        "max_completion_tokens",
        "thinking_budget_unsupported",
        ": keepalive",
        "client_disconnected",
        "`high` / `max`",
        # 审计 T9-GAP：适用范围、预算冲突、目标策略、默认值、落点、日志事件名
        "api.max_body_mb",
        "413",
        "upstream_aborted",
        "run/media/",
        "默认 81920",
        "调大 `api.inflight_max_mb`",
        "**任何公网地址一律放行**",
        "sse_truncated",
    ):
        assert s in g, f"手册缺「{s}」"
    for stale in (
        "本版暂不支持",
        "请传 `data:` URL",
        "超出模型模板接受的取值时后端报错",
        "服务端上限 81920",
        "客户端收到 403 / 401 / 415",
    ):
        assert stale not in g, f"手册仍有过时说法「{stale}」"


def test_known_issues_and_changelog(repo):
    issues = _read(repo, "docs/KNOWN-ISSUES.md")
    for s in (
        "非流式客户端无法保活",
        "TLS 下非流式请求的断连",
        "代取可以跨放行网段",
        "本机路径原样交给模型",
        "`model` 位置",
        "任何公网地址一律放行",
    ):
        assert s in issues, f"KNOWN-ISSUES 缺「{s}」"
    head = _read(repo, "CHANGELOG.md").split("## [1.0.0]")[0]
    for s in (
        "上游一律流式",
        "reasoning_content",
        "max_completion_tokens",
        "api.media_max_mb",
        "api.media_max_parts",
        "api.inflight_max_mb",
        "keepalive",
        "effort",
        "upstream_aborted",
        "sse_stream_aborted",
        "sse_truncated",
        "502",
    ):
        assert s in head, f"CHANGELOG Unreleased 缺「{s}」"


# ---- 以下断言的真值取自代码，不是手抄的字面串 ----


def test_guide_numbers_come_from_code(repo):
    g = _read(repo, "docs/USER-GUIDE.md")
    # 每个原因码都出现在 reason 表的行首
    for r in fetch.REASONS:
        assert re.search(rf"^\| .*`{r}`", g, re.M), f"reason 表缺 {r}"
    # 配置键的默认与域取自 schema.tsv，要出现在该键所在的行
    sch = _schema(repo)
    for key in (
        "api.media_max_mb",
        "api.media_max_parts",
        "api.inflight_max_mb",
        "api.max_body_mb",
    ):
        dom, default = sch[key]
        lo, hi = dom.split("-")
        row = _line(g, f"| `{key}` |")
        assert f"{default}（{lo}–{hi}）" in row, f"{key} 行与 schema.tsv 不符：{row}"
    dom, default = sch["max_tokens"]
    lo, hi = dom.split("-")
    assert f"`max_tokens` 默认 {default}（可改，域 {lo}–{hi}）" in g
    # 常量
    # **按结构位置比对值**，不查全文子串（终审 D G1/G2/G4）：全文任意一处命中就算过，
    # 另一处写错的值照样绿
    ka_line = _line(g, ": keepalive")
    ka, probe = int(relay.KEEPALIVE_SEC), int(relay.PROBE_SEC)
    assert f"上游静默满 {ka} 秒" in ka_line
    assert f"实际间隔在 {ka}～{ka + probe} 秒之间" in ka_line, f"保活实际间隔与常量不符：{ka_line}"
    assert f"{int(fetch.WALL_SEC)} 秒内没取完" in _line(g, "`timeout`")
    assert f"重定向最多 {fetch.MAX_REDIRECTS} 跳" in _line(g, "重定向最多"), "代取规则段的跳数"
    redirect_row = _line(g, "| `redirect_limit` |")
    assert f"超过 {fetch.MAX_REDIRECTS} 跳" in redirect_row, f"reason 表跳数与代码不符：{redirect_row}"
    busy_row = _line(g, "| `api.inflight_max_mb` |")
    assert f"`Retry-After: {server.RETRY_AFTER_SEC}`" in busy_row, f"配置表 Retry-After 值不符：{busy_row}"
    trouble_row = _line(g, "| 503 `gateway_busy` |")
    assert f"按 `Retry-After` {server.RETRY_AFTER_SEC} 秒" in trouble_row, f"排查表 Retry-After 值不符：{trouble_row}"
    assert f"超过 {int(media.SWEEP_AGE_SEC // 3600)} 小时的残留" in _line(g, "run/media/<uuid4>/")


def test_guide_effort_table_matches_map_effort(repo):
    g = _read(repo, "docs/USER-GUIDE.md")
    domain = ["low", "medium", "xhigh"]
    lines = g.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("| 你发的 | qwen38"))
    doc = {}
    for ln in lines[i + 2 :]:
        if not ln.startswith("|"):
            break
        left, right = [c.strip() for c in ln.strip("|").split("|")[:2]]
        for raw in left.split(" / "):
            tok = raw.strip("` ")
            doc[tok] = tok if right == "原样" else right.strip("` ")
    assert set(doc) == {"minimal", "low", "medium", "high", "max"}
    for k, v in doc.items():
        assert effort.map_effort(k, domain) == v, f"手册 {k} → {v} 与 map_effort 不符"
    # 域外的取值都要有一行（域内的 xhigh 原样，不必列）
    assert {k for k in effort.EFFORT_ORDER if k not in domain} <= set(doc)


def test_documented_log_events_exist_in_code(repo):
    src = "".join(p.read_text(encoding="utf-8") for p in (_PAYLOAD / "gateway").glob("*.py"))
    events = set(re.findall(r'log_event\("\w+",\s*"(\w+)"', src))
    g = _read(repo, "docs/USER-GUIDE.md")
    for ev in ("client_disconnected", "upstream_aborted", "sse_truncated", "media_fetched"):
        assert ev in events, f"代码里没有事件 {ev}"
        assert ev in g, f"手册没提事件 {ev}"
    assert "sse_stream_aborted" not in events
