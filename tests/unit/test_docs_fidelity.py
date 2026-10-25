"""文档与计划 3 的网关行为一致（spec §13 文档项：媒体规则、并发与输出长度、流式建议；§10 已知问题）。

只查照做就会出错或被误导的句子：错误类型、配置键、映射结果写错，用户对着手册排查就会走错路。
"""


def _read(repo, rel):
    return (repo / rel).read_text(encoding="utf-8")


def test_guide_covers_media_thinking_streaming(repo):
    g = _read(repo, "docs/USER-GUIDE.md")
    for s in ("{#media}", "{#thinking}", "{#streaming}",
              "media_fetch_failed", "address_denied", "too_large", "http_error", "media_source_rejected",
              "too_many_media_parts", "gateway_busy", "Retry-After", "media_storage_failed",
              "api.media_max_mb", "api.media_max_parts", "api.inflight_max_mb", "media_fetched",
              "max_completion_tokens", "thinking_budget_unsupported", ": keepalive",
              "client_disconnected", "`high` / `max`",
              # 审计 T9-GAP：适用范围、预算冲突、目标策略、默认值、落点、日志事件名
              "api.max_body_mb", "413", "upstream_aborted", "run/media/", "默认 81920",
              "调大 `api.inflight_max_mb`", "VPN"):
        assert s in g, f"手册缺「{s}」"
    for stale in ("本版暂不支持", "请传 `data:` URL", "超出模型模板接受的取值时后端报错",
                  "服务端上限 81920", "客户端收到 403 / 401 / 415"):
        assert stale not in g, f"手册仍有过时说法「{stale}」"


def test_known_issues_and_changelog(repo):
    issues = _read(repo, "docs/KNOWN-ISSUES.md")
    for s in ("非流式客户端无法保活", "TLS 下非流式请求的断连", "代取可以跨放行网段",
              "本机路径原样交给模型", "`model` 位置", "任何公网地址一律放行"):
        assert s in issues, f"KNOWN-ISSUES 缺「{s}」"
    head = _read(repo, "CHANGELOG.md").split("## [1.0.0]")[0]
    for s in ("上游一律流式", "reasoning_content", "max_completion_tokens", "api.media_max_mb",
              "api.media_max_parts", "api.inflight_max_mb", "keepalive", "effort",
              "upstream_aborted", "sse_stream_aborted", "502"):
        assert s in head, f"CHANGELOG Unreleased 缺「{s}」"
