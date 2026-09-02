"""T2 · 中间件：来源守卫 + 路由守卫。

**两个豁免集是分开的** —— 这是安全审查抓到的第一个 auth-bypass 的对治：
上一版只有一个 `EXEMPT_PATHS`，为了让 `/v1/models`（GET 无请求体）不被
`route_guard` 打掉而把它加进去，结果**连鉴权一起豁免了** ——
非本地绑定时，任何人都能拉到本机装了哪些模型。

两个 guard 挡的是不同的东西，豁免面必须各算各的：
  `route_guard` 豁免 = 「这个端点不带 `.model`」
  `source_guard` 豁免 = 「这个端点不含敏感信息，可以匿名访问」
"""
import hmac

# route_guard 的豁免：这些端点没有请求体，取不到 `.model` 是正常的
ROUTE_EXEMPT = frozenset({"/healthz", "/v1/models"})

# source_guard 的豁免：**只有 /healthz** —— 它只回 routes_state 与时间戳，
# 不泄漏装了哪些模型。`/v1/models` **不在此列**：模型列表是资产清单。
AUTH_EXEMPT = frozenset({"/healthz"})

LOOPBACK_ADDRS = frozenset({"127.0.0.1", "::1", "localhost"})


def exposure_level(bind_addr):
    # type: (str) -> str
    """暴露等级**由绑定地址唯一决定**，不得由第二个开关表达（`R-API-25`）。"""
    return "LOOPBACK" if bind_addr in LOOPBACK_ADDRS else "NONLOCAL"


def source_guard(path, headers, bind_addr, expected_token=None):
    """暴露等级 + 鉴权。**禁读转发头**（`X-Forwarded-For` 等可被伪造）。

    三处修复（安全审查）：
    ① `/v1/models` 不再豁免鉴权 —— 它是资产清单
    ② token **按值比对且用常数时间**，不是只看 `Bearer ` 前缀 ——
       上一版任何 `Bearer x` 都能过
    ③ `bind_addr` 与 `expected_token` 都由**装配段**注入，
       调用方（请求处理路径）改不了它们
    """
    if path in AUTH_EXEMPT:
        return None
    if exposure_level(bind_addr) == "LOOPBACK":
        return None                      # 回环上无鉴权是有意的，且已在 api info 里诚实陈述

    raw = headers.get("authorization", "")
    if not raw.startswith("Bearer "):
        return (401, {"error": {"message": "missing bearer token", "type": "auth"}})
    if not expected_token:
        # 非本地绑定却没配令牌 ⇒ **拒绝服务，而不是放行**
        return (503, {"error": {"message": "gateway token not configured", "type": "auth"}})
    if not hmac.compare_digest(raw[len("Bearer "):], expected_token):
        return (401, {"error": {"message": "invalid token", "type": "auth"}})
    return None


def route_guard(path, body, route_keys):
    """取请求体 `.model` → 查路由键集（**正向白名单**）。

    落在 `ROUTE_EXEMPT` 内的请求跳过 `.model` 取值 ——
    `/v1/models` 是 GET 无请求体，不豁免会被当作「`.model` 取不到」打掉。
    """
    if path in ROUTE_EXEMPT:
        return None
    model = (body or {}).get("model")
    if not model:
        return (400, {"error": {"message": "missing 'model'", "type": "invalid_request"}})
    if model not in route_keys:
        # **不按需启动**（决策 C1 第三条）：给诊断响应，后端零请求
        return (404, {"error": {
            "message": f"model {model!r} not found. available: {sorted(route_keys)}",
            "type": "model_not_found"}})
    return None
