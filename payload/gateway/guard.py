"""T2 · 中间件：来源、预检、鉴权、Content-Type、路由守卫（spec §4.1、D13）。

**两个豁免集是分开的** —— 这是安全审查抓到的第一个 auth-bypass 的对治：
上一版只有一个 `EXEMPT_PATHS`，为了让 `/v1/models`（GET 无请求体）不被
`route_guard` 打掉而把它加进去，结果**连鉴权一起豁免了**。
  `route_guard` 豁免 = 「这个端点不带 `.model`」
  `auth_guard` 豁免  = 「这个端点不含敏感信息，可以匿名访问」

1.0.0 在回环上免鉴权，理由是「同机上模型端口本就无鉴权监听」。D13 推翻了它：
mlx_vlm 后端现在有密钥，本机任意进程（含浏览器页面发起的请求）不再能绕过网关直达后端，
网关若对回环免鉴权就成了唯一的缺口。所以**所有来源都要令牌**，只有 CORS 预检例外。
"""
import hmac

ROUTE_EXEMPT = frozenset({"/healthz", "/v1/models"})
AUTH_EXEMPT = frozenset({"/healthz"})

CORS_ALLOW_METHODS = "GET, POST, DELETE, OPTIONS"
CORS_ALLOW_HEADERS = ("Authorization, x-api-key, Content-Type, anthropic-version, "
                      "anthropic-beta, openai-beta")
CORS_MAX_AGE = "600"


def _err(status, err_type, message):
    # type: (int, str, str) -> tuple
    return (status, {"error": {"message": message, "type": err_type}})


def peer_guard(peer_class):
    # type: (object) -> object
    """来源不在放行集合 ⇒ 403。**响应体不含地址与网段**（地址只进日志）。"""
    return None if peer_class else _err(403, "peer_not_allowed", "peer not allowed")


def preflight(method, headers):
    # type: (str, dict) -> object
    """CORS 预检在鉴权之前短路：浏览器的预检请求**按规范不带凭据**，要求它带令牌等于禁用跨源调用。"""
    if method != "OPTIONS":
        return None
    if headers.get("access-control-request-method"):
        return (204, None)
    return _err(405, "method_not_allowed", "method not allowed")


def auth_guard(path, headers, expected_token):
    # type: (str, dict, object) -> object
    """`Authorization: Bearer <t>` 或 `x-api-key: <t>`（Anthropic 客户端用后者）。

    两者同时出现时**任一匹配即通过**；每个候选都比较一遍（常数时间比较，不因首个匹配提前返回）。
    未配令牌 ⇒ 503 拒绝服务而不是放行。
    """
    if path in AUTH_EXEMPT:
        return None
    if not expected_token:
        return _err(503, "auth", "gateway token not configured")
    offered = []
    raw = headers.get("authorization", "")
    if raw.startswith("Bearer "):
        offered.append(raw[len("Bearer "):])
    if headers.get("x-api-key"):
        offered.append(headers["x-api-key"])
    if not offered:
        return _err(401, "auth", "missing token")
    want = expected_token.encode("utf-8")
    ok = False
    for t in offered:
        ok = hmac.compare_digest(t.encode("utf-8"), want) or ok
    return None if ok else _err(401, "auth", "invalid token")


def content_type_guard(method, headers):
    # type: (str, dict) -> object
    """POST 须是 JSON。`text/plain` 是浏览器「简单请求」的形态 —— 它不触发预检，放行它等于绕过 CORS。"""
    if method != "POST":
        return None
    ctype = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype == "application/json":
        return None
    return _err(415, "unsupported_media_type", "Content-Type must be application/json")


def route_guard(path, body, route_keys):
    # type: (str, object, list) -> object
    """取请求体 `.model` → 查路由键集（**正向白名单**）。"""
    if path in ROUTE_EXEMPT:
        return None
    model = (body or {}).get("model")
    if not model:
        return _err(400, "invalid_request", "missing 'model'")
    if model not in route_keys:
        # **不按需启动**（决策 C1 第三条）：给诊断响应，后端零请求
        return _err(404, "model_not_found",
                    f"model {model!r} not found. available: {sorted(route_keys)}")
    return None
